"""올리기 묶음: 여러 파일을 한 번에 받아 저장하고, 묶음별 작업 폴더(글 사본·줄인 사진)를 준비한다."""
from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import UploadFile
from PIL import Image, ImageOps
from sqlalchemy import select, update
from sqlalchemy.orm import Session, object_session

from app import labels
from app.ai.tasks import ActivityDraft, draft_summary_md, match_mentions, normalize_draft, normalize_name
from app.config import Settings
from app.extract import extract_text
from app.knowledge import safe_name
from app.models import AIJob, Customer, Intake, IntakeFile, Partner, Solution, User

if TYPE_CHECKING:
    from app.ai.intake_tasks import IntakePlanOut

CHUNK = 1024 * 1024
MAX_FILES = 20
MAX_NOTE = 500
MAX_IMAGE_SIDE = 2000
MAX_IMAGE_PIXELS = 80_000_000  # 이보다 큰 사진은 열지 않고 읽지 못한 것으로 둔다
MAX_COPY_NAME_BYTES = 150  # 작업 폴더 사본 이름의 UTF-8 바이트 상한 (파일 시스템 이름 길이 제한 대비)
MAX_RAW_TEXT = 200_000
MAX_TAGS = 5
NO_READABLE = "읽을 수 있는 파일이 없습니다."
NOT_PLACED = "정리 대상에 포함되지 않음"
NO_FILES = "올릴 파일을 선택하세요."
FILE_LIST_NAME = "파일목록.md"

_EXT = re.compile(r"\.[a-z0-9]{1,10}")
_KINDS = {
    "document": {".pptx", ".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv"},
    "image": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"},
    "audio": {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".wma"},
}
_IMAGE_ERRORS = (OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning)
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]+")


def clean_name(filename: str) -> str:
    """브라우저가 경로째 보낸 이름에서 파일명만 남긴다. 브라우저는 따옴표를 %22 로 보낸다."""
    filename = filename.replace("%22", '"').replace("%0D", "").replace("%0A", "")
    return Path(filename.replace("\\", "/")).name


def extension(name: str) -> str:
    """저장 이름에 붙일 확장자: 소문자 `[a-z0-9]{1,10}` 만, 아니면 빈 문자열."""
    ext = Path(name).suffix.lower()
    return ext if _EXT.fullmatch(ext) else ""


def file_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    for kind, exts in _KINDS.items():
        if ext in exts:
            return kind
    return "other"


def write_upload(upload: UploadFile, path: Path, limit: int) -> int | None:
    """업로드를 조각 단위로 `path` 에 쓴다. 쓴 바이트 수를, 크기 제한(`limit`)을 넘으면 파일을 지우고 None 을 돌려준다.
    쓰다 실패하면 부분 파일을 지우고 예외를 그대로 올린다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with path.open("wb") as out:
            while chunk := upload.file.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    break
                out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if size > limit:
        path.unlink(missing_ok=True)
        return None
    return size


def intake_upload_dir(settings: Settings, intake_id: int) -> Path:
    return settings.uploads_dir / "intake" / str(intake_id)


def stored_path(settings: Settings, f: IntakeFile) -> Path:
    return intake_upload_dir(settings, f.intake_id) / f.stored_name


def create_intake(db: Session, settings: Settings, user: User, note: str,
                  uploads: list[UploadFile | str]) -> tuple[Intake, list[str]]:
    """묶음을 만들고 파일을 저장한다. (묶음, 받지 못한 파일 안내 목록). 받은 파일이 없으면 ValueError."""
    limit_mb = settings.max_upload_mb
    intake = Intake(note=note.strip(), created_by_id=user.id)
    db.add(intake)
    db.flush()
    skipped: list[str] = []
    accepted = 0
    try:
        for upload in uploads:
            if isinstance(upload, str):  # 파일을 고르지 않고 보낸 빈 칸은 문자열로 들어온다
                continue
            name = clean_name(upload.filename or "")
            if not name:
                continue
            if accepted >= MAX_FILES:
                skipped.append(f"{name} (한 번에 {MAX_FILES}개까지)")
                continue
            f = IntakeFile(intake_id=intake.id, original_name=name, kind=file_kind(name))
            db.add(f)
            db.flush()  # file_id 를 얻는다
            f.stored_name = f"{f.id}{extension(name)}"
            size = write_upload(upload, stored_path(settings, f), limit_mb * 1024 * 1024)
            if size is None or size == 0:
                if size == 0:
                    stored_path(settings, f).unlink(missing_ok=True)
                db.delete(f)
                db.flush()
                skipped.append(f"{name} ({'빈 파일' if size == 0 else f'크기 제한 {limit_mb}MB 초과'})")
                continue
            f.size = size
            accepted += 1
        if not accepted:
            raise ValueError(NO_FILES)
        db.commit()
    except BaseException:
        db.rollback()
        shutil.rmtree(intake_upload_dir(settings, intake.id), ignore_errors=True)
        raise
    return intake, skipped


def file_number(intake: Intake, f: IntakeFile) -> int:
    """묶음 안에서 파일의 1부터의 순번 (id 순). 정리안의 "파일 번호"다."""
    return [x.id for x in sorted(intake.files, key=lambda x: x.id)].index(f.id) + 1


def work_dir(settings: Settings, intake: Intake) -> Path:
    return settings.intake_work_dir / str(intake.id)


def _flat(name: str) -> str:
    """줄바꿈·탭 같은 제어 문자를 공백 하나로 줄인다 (글 사본의 `# 이름` 줄과 파일목록 표가 깨지지 않게)."""
    return _CONTROL.sub(" ", name).strip()


def _copy_base(number: int, name: str, reserve: int = 5) -> str:
    """사본 이름의 앞부분 `<번호>-<안전한 이름>`. 뒤에 붙을 확장자 `reserve` 바이트를 남기고 전체를 UTF-8 150바이트 이하로 줄인다."""
    prefix = f"{number}-"
    room = MAX_COPY_NAME_BYTES - len(prefix.encode("utf-8")) - reserve
    safe = safe_name(name).encode("utf-8")[:max(room, 1)].decode("utf-8", errors="ignore").strip(" .")
    return prefix + (safe or "file")


def doc_copy_name(number: int, f: IntakeFile) -> str:
    """문서 글 사본의 이름."""
    return _copy_base(number, f.original_name) + ".md"


def _shrink_image(src: Path, dest_base: Path) -> Path | None:
    """사진을 열어 방향을 바로잡고 긴 변 2,000px 이하로 줄여 저장한다. 저장한 경로, 열지 못하거나 너무 크면 None."""
    try:
        with Image.open(src) as opened:
            width, height = opened.size  # 그림을 풀기 전에 PIL 이 알려 주는 크기
            if width * height > MAX_IMAGE_PIXELS:
                return None
            img = ImageOps.exif_transpose(opened)
            img.load()
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        if has_alpha:
            img = img.convert("RGBA")
            dest = dest_base.with_name(dest_base.name + ".png")
        else:
            img = img.convert("RGB")
            dest = dest_base.with_name(dest_base.name + ".jpg")
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        img.save(dest, "PNG" if has_alpha else "JPEG", **({} if has_alpha else {"quality": 88}))
        return dest
    except _IMAGE_ERRORS:
        return None


def _prepare_file(f: IntakeFile, number: int, src: Path, folder: Path) -> str:
    """파일 하나의 작업 폴더 사본을 만들고 읽기 상태를 정한다. 사본 이름(없으면 빈 글)."""
    copy_name = ""
    if f.kind == "document":
        result = extract_text(src)
        f.read_status = result.status
        if result.status == "ok":
            name = doc_copy_name(number, f)
            try:
                (folder / name).write_text(f"# {_flat(f.original_name)}\n\n{result.text}\n", encoding="utf-8")
            except OSError:
                (folder / name).unlink(missing_ok=True)  # 쓰다 만 사본을 AI 가 읽지 않게 한다
                raise
            copy_name = name
    elif f.kind == "image":
        dest = _shrink_image(src, folder / _copy_base(number, f.original_name))
        f.read_status = "ok" if dest else "failed"
        copy_name = dest.name if dest else ""
    elif f.kind == "audio":
        f.read_status = "needs_transcript"
    else:
        f.read_status = "unsupported"
    return copy_name


def prepare_workdir(settings: Settings, intake: Intake) -> None:
    """`intake_work/<id>/` 를 비우고 다시 만든다: 문서는 글 사본(.md), 사진은 줄인 사본, 파일목록.md.
    한 파일의 사본을 쓰지 못해도(OSError) 그 파일만 읽지 못함으로 두고 나머지를 계속한다."""
    folder = work_dir(settings, intake)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[int, IntakeFile, str]] = []
    for f in sorted(intake.files, key=lambda x: x.id):
        number = file_number(intake, f)
        try:
            copy_name = _prepare_file(f, number, stored_path(settings, f), folder)
        except OSError:
            f.read_status, copy_name = "failed", ""
        rows.append((number, f, copy_name))
    lines = ["# 파일 목록", "", "| 번호 | 원래 이름 | 종류 | 읽기 상태 | 사본 파일 |", "|---|---|---|---|---|"]
    for number, f, copy_name in rows:
        name = _flat(f.original_name).replace("|", "\\|")
        lines.append(
            f"| {number} | {name} | {labels.INTAKE_FILE_KINDS.get(f.kind, f.kind)} | "
            f"{labels.READ_STATUSES.get(f.read_status, f.read_status)} | {copy_name or '(없음)'} |"
        )
    (folder / FILE_LIST_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    session = object_session(intake)
    if session is not None:
        session.commit()


def remove_workdir(settings: Settings, intake: Intake) -> None:
    shutil.rmtree(work_dir(settings, intake), ignore_errors=True)


# ---- 정리안 검증 ----

def _ordered(numbers: list[int], valid: set[int]) -> list[int]:
    """파일 번호에서 이 묶음의 읽은 파일만, 중복 없이 처음 나온 순서로 남긴다."""
    seen: list[int] = []
    for n in numbers:
        if n in valid and n not in seen:
            seen.append(n)
    return seen


def _merge_into(base: dict, other: dict, fields: tuple[str, ...]) -> None:
    """같은 이름의 항목을 하나로 합친다: 파일은 합치고, 비어 있는 칸은 뒤 항목의 값으로 채운다."""
    base["files"] = base["files"] + [n for n in other["files"] if n not in base["files"]]
    for field in fields:
        if not base.get(field) and other.get(field):
            base[field] = other[field]


def _raw_text(settings: Settings | None, intake: Intake, number: int, f: IntakeFile) -> str | None:
    """문서 파일의 추출 글 전체 (작업 폴더 사본에서 `# 이름` 머리줄을 뺀 것). 읽지 못하면 None."""
    if settings is None or f.kind != "document":
        return None
    path = work_dir(settings, intake) / doc_copy_name(number, f)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    head, _, body = text.partition("\n")
    body = (body if head.startswith("# ") else text).strip()
    return body[:MAX_RAW_TEXT] or None


def clean_plan(db: Session, intake: Intake, out: "IntakePlanOut", today: date, settings: Settings | None = None) -> dict:
    """AI 정리안을 저장용으로 검증·보정한다: `{"items": [...]}`.

    항목 순서는 파트너, 고객, 새 솔루션, 솔루션에 추가, 활동, 정리하지 못한 파일. `settings` 가 있으면 활동의 원문을
    작업 폴더의 글 사본에서 읽는다 (없으면 정리본을 원문으로 쓴다)."""
    by_number = {file_number(intake, f): f for f in intake.files}
    readable = {n for n, f in by_number.items() if f.read_status == "ok"}
    used: set[int] = set()

    def files_of(numbers: list[int]) -> list[int]:
        return _ordered(numbers, readable)

    solutions = {normalize_name(s.name): s.id for s in db.scalars(select(Solution).order_by(Solution.id)) if normalize_name(s.name)}
    solution_ids = set(db.scalars(select(Solution.id)))
    customers = {normalize_name(c.name) for c in db.scalars(select(Customer))}
    partners = {normalize_name(p.name): p.id for p in db.scalars(select(Partner).order_by(Partner.id)) if normalize_name(p.name)}

    new_partners: dict[str, dict] = {}
    for p in out.partners:
        key = normalize_name(p.name)
        if not key or key in partners:
            continue
        item = {"kind": "new_partner", "reason": p.reason.strip(), "files": files_of(p.files), "name": p.name.strip(),
                "contact": p.contact.strip(), "description": p.description.strip(), "memo": p.memo.strip()}
        if key in new_partners:
            _merge_into(new_partners[key], item, ("contact", "description", "memo", "reason"))
        else:
            new_partners[key] = item

    new_customers: dict[str, dict] = {}
    for c in out.customers:
        key = normalize_name(c.name)
        if not key or key in customers:
            continue
        item = {"kind": "new_customer", "reason": c.reason.strip(), "files": files_of(c.files), "name": c.name.strip(),
                "industry": c.industry.strip(), "contact": c.contact.strip(), "memo": c.memo.strip()}
        if key in new_customers:
            _merge_into(new_customers[key], item, ("industry", "contact", "memo", "reason"))
        else:
            new_customers[key] = item

    new_solutions: dict[str, dict] = {}
    additions: dict[int, dict] = {}

    def add_addition(solution_id: int, numbers: list[int], note: str, reason: str) -> None:
        item = {"kind": "add_to_solution", "reason": reason.strip(), "files": files_of(numbers),
                "solution_id": solution_id, "note": note.strip()}
        if not item["files"]:
            return
        if solution_id in additions:
            _merge_into(additions[solution_id], item, ("note", "reason"))
        else:
            additions[solution_id] = item

    for s in out.solutions:
        key = normalize_name(s.name)
        if not key:
            continue
        if key in solutions:  # 이미 등록된 솔루션이면 새로 만들지 않고 자료만 더한다
            add_addition(solutions[key], s.files, s.summary, s.reason)
            continue
        partner_key = normalize_name(s.partner)
        item = {
            "kind": "new_solution", "reason": s.reason.strip(), "files": files_of(s.files), "name": s.name.strip(),
            "summary": s.summary.strip(), "description": s.description.strip(),
            "tags": list(dict.fromkeys(t.strip() for t in s.tags if t.strip()))[:MAX_TAGS],
            "partner_id": partners.get(partner_key) if partner_key else None,
            "partner_new": s.partner.strip() if partner_key and partner_key not in partners else "",
        }
        if key in new_solutions:
            _merge_into(new_solutions[key], item, ("summary", "description", "reason", "partner_id", "partner_new"))
            merged = new_solutions[key]
            merged["tags"] = list(dict.fromkeys(merged["tags"] + item["tags"]))[:MAX_TAGS]
        else:
            new_solutions[key] = item

    for a in out.additions:
        if a.solution_id in solution_ids:
            add_addition(a.solution_id, a.files, a.note, a.reason)

    activities: list[dict] = []
    for act in out.activities:
        draft = normalize_draft(act.draft, today)
        numbers = files_of(act.files)
        raw = None
        if act.raw_from in readable:
            raw = _raw_text(settings, intake, act.raw_from, by_number[act.raw_from])
            if act.raw_from not in numbers:
                numbers.append(act.raw_from)
        summary = draft_summary_md(draft)
        matches = match_mentions(db, draft.mentions)
        activities.append({
            "kind": "activity", "reason": act.reason.strip(), "files": numbers,
            "draft": draft.model_dump(), "summary_md": summary, "raw_text": raw or summary,
            "matched": {rel: m["matched"] for rel, m in matches.items()},
            "new": {rel: m["new"] for rel, m in matches.items()},
            "ambiguous": {rel: m["ambiguous"] for rel, m in matches.items()},
        })

    groups = [list(new_partners.values()), list(new_customers.values()), list(new_solutions.values()),
              list(additions.values()), activities]
    prefixes = ["p", "c", "s", "a", "act"]
    items: list[dict] = []
    for prefix, group in zip(prefixes, groups):
        for i, item in enumerate(group, 1):
            items.append({"kind": item["kind"], "key": f"{prefix}{i}", **{k: v for k, v in item.items() if k != "kind"}})
            used.update(item["files"])

    unused = [n for n in sorted(readable) if n not in used]
    unread = [n for n in sorted(by_number) if n not in readable]
    if unused or unread:
        reasons = []
        if unused:
            given = [u.reason.strip() for u in out.unplaced if u.reason.strip() and set(files_of(u.files)) & set(unused)]
            reasons.append(" / ".join(dict.fromkeys(given)) or NOT_PLACED)
        if unread:
            states = dict.fromkeys(labels.READ_STATUSES.get(by_number[n].read_status, by_number[n].read_status) for n in unread)
            reasons.append("읽을 수 없는 파일 (" + ", ".join(states) + ")")
        items.append({"kind": "unplaced", "key": "u1", "reason": " · ".join(reasons), "files": unused + unread})
    return {"items": items}


# ---- 정리 시작·복구 ----

def _fail_analysis(db: Session, intake_id: int, job_id: int | None, message: str) -> bool:
    """정리 중인 묶음(그 작업이 지금 작업일 때만)을 실패로 바꾼다. 조건부 UPDATE 라 한 번만 반영된다."""
    same_job = Intake.job_id.is_(None) if job_id is None else Intake.job_id == job_id
    result = db.execute(
        update(Intake).where(Intake.id == intake_id, Intake.status == "analyzing", same_job)
        .values(status="failed", error=message)
    )
    db.commit()
    return result.rowcount == 1


def job_failure_message(job: AIJob | None) -> str:
    """실패한 정리 작업의 화면용 한국어 문구."""
    if job is None or job.status != "failed":
        return labels.ERROR_KINDS["unknown"]
    if job.error_kind == "bad_output" and job.error:
        return job.error
    return labels.ERROR_KINDS.get(job.error_kind or "", labels.ERROR_KINDS["unknown"])


def start_analysis(db: Session, intake: Intake, user: User) -> bool:
    """묶음을 정리 중으로 바꾸고 정리 작업을 넣는다. 올림·확인 대기·실패 상태에서만, 동시에 와도 한 번만 시작된다.
    읽을 수 있는 파일이 없으면 작업 없이 실패로 둔다 (False)."""
    db.refresh(intake)
    intake_id = intake.id
    startable = Intake.status.in_(("uploaded", "ready", "failed"))
    if not any(f.read_status == "ok" for f in intake.files):
        db.rollback()
        db.execute(update(Intake).where(Intake.id == intake_id, startable).values(status="failed", error=NO_READABLE))
        db.commit()
        db.refresh(intake)
        return False
    db.rollback()  # 읽기 트랜잭션을 끝내고 쓰기를 새로 시작한다
    job = AIJob(kind="intake_analyze", input={"intake_id": intake_id}, requester_id=user.id, priority=0, status="pending")
    db.add(job)
    db.flush()
    claimed = db.execute(
        update(Intake).where(Intake.id == intake_id, startable)
        .values(status="analyzing", job_id=job.id, plan=None, error="")
    )
    if claimed.rowcount != 1:  # 이미 정리 중이거나 끝난 묶음이다
        db.rollback()
        db.refresh(intake)
        return False
    db.commit()
    db.refresh(intake)
    return True


def finish_failed_analysis(db: Session, intake_id: int) -> bool:
    """작업이 끝났는데 묶음이 아직 정리 중이면 그 작업의 실패(또는 이상 종료)를 묶음에 기록한다."""
    intake = db.get(Intake, intake_id)
    if intake is None or intake.status != "analyzing":
        return False
    job = db.get(AIJob, intake.job_id) if intake.job_id is not None else None
    if job is None or job.status not in ("failed", "done"):
        return False
    return _fail_analysis(db, intake_id, intake.job_id, job_failure_message(job))


def recover(db: Session) -> int:
    """앱을 시작할 때 정리 작업이 이미 실패했는데 정리 중으로 남은 묶음을 실패로 바꾼다."""
    ids = list(db.scalars(select(Intake.id).where(Intake.status == "analyzing")))
    return sum(finish_failed_analysis(db, i) for i in ids)

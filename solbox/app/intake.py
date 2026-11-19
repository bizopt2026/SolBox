"""올리기 묶음: 여러 파일을 한 번에 받아 저장하고, 묶음별 작업 폴더(글 사본·줄인 사진)를 준비한다."""
from __future__ import annotations

import logging
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import UploadFile
from PIL import Image, ImageOps
from sqlalchemy import select, update
from sqlalchemy.orm import Session, object_session

from app import labels
from app.ai.tasks import ActivityDraft, draft_summary_md, match_mentions, normalize_draft, normalize_name
from app.config import Settings
from app.extract import ExtractResult, extract_text
from app.knowledge import KnowledgeMirror, file_markdown, safe_name
from app.models import AIJob, Activity, Business, Customer, Intake, IntakeFile, Partner, Solution, User

if TYPE_CHECKING:
    from app.ai.intake_tasks import IntakePlanOut

log = logging.getLogger(__name__)

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
        try:
            img.save(dest, "PNG" if has_alpha else "JPEG", **({} if has_alpha else {"quality": 88}))
        except BaseException:
            dest.unlink(missing_ok=True)  # 쓰다 만 사본을 남기지 않는다
            raise
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
        if act.draft is None:  # 쓸 수 없는 초안은 그 항목만 버린다
            continue
        draft = normalize_draft(act.draft, today)
        numbers = files_of(act.files)
        raw = None
        if act.raw_from in readable:
            raw = _raw_text(settings, intake, act.raw_from, by_number[act.raw_from])
            if raw is not None and act.raw_from not in numbers:
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
    if job is not None and job.status not in ("failed", "done"):
        return False  # 아직 대기·진행 중이다 (작업이 없는 묶음은 멈춘 것이므로 아래에서 실패로 둔다)
    return _fail_analysis(db, intake_id, intake.job_id, job_failure_message(job))


def recover(db: Session) -> int:
    """앱을 시작할 때 정리 작업이 이미 실패했는데 정리 중으로 남은 묶음을 실패로 바꾼다."""
    ids = list(db.scalars(select(Intake.id).where(Intake.status == "analyzing")))
    return sum(finish_failed_analysis(db, i) for i in ids)


# ---- 정리안 확인과 저장 ----

SAVEABLE_KINDS = ("new_partner", "new_customer", "new_solution", "add_to_solution", "activity")
NOTHING_TO_SAVE = "저장할 항목이 없습니다."
APPLY_FAILED = "저장하지 못했습니다. 아무것도 저장되지 않았으니 잠시 후 다시 저장해 주세요. 계속 안 되면 관리자에게 알려 주세요."
APPLY_INTERRUPTED = "저장하는 도중 서버가 멈췄습니다. 아무것도 저장되지 않았으니 다시 저장해 주세요."
PLAN_CHANGED = "정리안이 그 사이 바뀌었습니다."
TARGET_GONE = "대상 솔루션이 삭제되어 자료를 추가하지 못했습니다."
_FILE_KIND_HINTS = (("intro", ("소개", "intro", "brochure", "카탈로그", "catalog")), ("spec", ("스펙", "spec", "사양", "규격", "datasheet")))


class NotReady(Exception):
    """확인 대기(ready) 상태가 아니거나 다른 요청이 먼저 저장을 시작했다."""


class ApplyFailed(Exception):
    """저장 도중 오류가 나 모두 되돌렸다. 메시지는 화면에 보일 한국어 문구."""


class _Fields:
    """제출된 폼. Starlette FormData 도, `{이름: 값 또는 값 목록}` dict 도 받는다 (파일 칸은 무시)."""

    def __init__(self, form):
        pairs = form.multi_items() if hasattr(form, "multi_items") else [
            (k, v) for k, vs in dict(form).items() for v in (vs if isinstance(vs, (list, tuple)) else [vs])
        ]
        self.data: dict[str, list[str]] = {}
        for name, value in pairs:
            if isinstance(value, str):
                self.data.setdefault(name, []).append(value)

    def flag(self, name: str) -> bool:
        return name in self.data

    def text(self, name: str, default: str = "") -> str:
        values = self.data.get(name)
        return values[0] if values else default

    def many(self, name: str) -> list[str]:
        return list(self.data.get(name, []))

    def has_prefix(self, prefix: str) -> bool:
        return any(name.startswith(prefix) for name in self.data)


def guess_file_kind(name: str) -> str:
    """파일 이름에서 솔루션 자료 종류를 짐작한다: 소개 / 스펙 / 관련."""
    lowered = name.lower()
    for kind, hints in _FILE_KIND_HINTS:
        if any(h in lowered for h in hints):
            return kind
    return "related"


def _digits(value) -> int | None:
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() and len(text) < 19 else None


def item_values(item: dict, fields: _Fields | None, file_names: dict[int, str]) -> dict:
    """항목 하나의 입력칸 값. `fields` 가 없으면 정리안의 기본값. 항목의 칸이 하나라도 제출됐으면 제출된 값을 쓰되
    빠진 글 칸은 기본값으로 채운다. 항목 종류·번호·파일 번호는 언제나 정리안(`item`)에서 온다."""
    key, kind = item["key"], item["kind"]
    use = fields is not None and fields.has_prefix(f"{key}__")

    def text(field: str, default) -> str:
        return fields.text(f"{key}__{field}", str(default or "")) if use else str(default or "")

    def many(field: str, default: list) -> list[str]:
        return fields.many(f"{key}__{field}") if use else [str(x) for x in default]

    v: dict = {"skip": bool(fields and fields.flag(f"skip_{key}"))}
    if kind in ("new_solution", "add_to_solution"):
        kinds = {}
        for n in item["files"]:
            guessed = guess_file_kind(file_names.get(n, ""))
            picked = text(f"kind_{n}", guessed)
            kinds[n] = picked if picked in labels.FILE_KINDS else guessed
        v["kinds"] = kinds
    if kind == "new_partner":
        v.update(name=text("name", item["name"]), contact=text("contact", item["contact"]),
                 description=text("description", item["description"]), memo=text("memo", item["memo"]))
    elif kind == "new_customer":
        v.update(name=text("name", item["name"]), industry=text("industry", item["industry"]),
                 contact=text("contact", item["contact"]), memo=text("memo", item["memo"]))
    elif kind == "new_solution":
        v.update(name=text("name", item["name"]), summary=text("summary", item["summary"]),
                 description=text("description", item["description"]), tags=text("tags", ", ".join(item["tags"])),
                 partner=text("partner", item["partner_id"] or ""), partner_new=text("partner_new", item["partner_new"]))
    elif kind == "add_to_solution":
        v["solution"] = text("solution", item["solution_id"])
    elif kind == "activity":
        d = item["draft"]
        fus = [f for f in d.get("follow_ups", []) if str(f.get("task", "")).strip()]
        dss = [x for x in d.get("demand_signals", []) if str(x.get("content", "")).strip()]
        matched = item.get("matched", {})
        v.update(
            kind=text("kind", d.get("kind")), date=text("date", d.get("date")), title=text("title", d.get("title")),
            summary_md=text("summary_md", item["summary_md"]), raw_text=text("raw_text", item["raw_text"]),
            fu_task=many("fu_task", [f.get("task", "") for f in fus]), fu_owner=many("fu_owner", [f.get("owner", "") for f in fus]),
            fu_due=many("fu_due", [f.get("due", "") for f in fus]),
            ds_customer=many("ds_customer", [x.get("customer", "") for x in dss]),
            ds_content=many("ds_content", [x.get("content", "") for x in dss]),
            solution_ids=many("solution_ids", matched.get("solutions", [])),
            customer_ids=many("customer_ids", matched.get("customers", [])),
            business_ids=many("business_ids", matched.get("businesses", [])),
            partner_ids=many("partner_ids", matched.get("partners", [])),
        )
        names = {rel: [n.strip() for n in many(f"new_{rel}", []) if n.strip()] for rel in ("solutions", "customers", "partners")}
        v["new_options"] = {}
        for rel, checked in names.items():
            candidates = list(item.get("new", {}).get(rel, []))
            v["new_options"][rel] = [(n, n in checked) for n in candidates] + [(n, True) for n in checked if n not in candidates]
        v["new_names"] = {f"new_{rel}": [n for n, on in opts if on] for rel, opts in v["new_options"].items()}
        v["fu_rows"] = [{"task": t, "owner": o, "due": d_} for t, o, d_ in _rows(v["fu_task"], v["fu_owner"], v["fu_due"])]
        v["ds_rows"] = [{"customer": c, "content": t} for c, t in _rows(v["ds_customer"], v["ds_content"])]
        v["selected"] = {t: [n for n in (_digits(i) for i in v[f"{t}_ids"]) if n is not None]
                         for t in ("solution", "customer", "business", "partner")}
    return v


def _rows(*columns: list[str]) -> list[tuple[str, ...]]:
    count = max((len(c) for c in columns), default=0)
    return [tuple((c[i] if i < len(c) else "").strip() for c in columns) for i in range(count)]


def item_error(item: dict, v: dict) -> str | None:
    """저장할 수 없는 입력이면 화면에 보일 문구."""
    label = labels.INTAKE_ITEM_KINDS.get(item["kind"], item["kind"])
    if item["kind"] in ("new_partner", "new_customer", "new_solution") and not v["name"].strip():
        return f"{label}: 이름을 입력하세요."
    if item["kind"] == "activity" and not v["raw_text"].strip() and not v["summary_md"].strip():
        return f"{label}: 메모(원문)나 정리본을 입력하세요."
    return None


def _file_names(intake: Intake) -> dict[int, str]:
    return {file_number(intake, f): f.original_name for f in intake.files}


def review_cards(db: Session, intake: Intake, form=None) -> list[dict]:
    """정리안 확인 화면의 항목 카드: `{"item", "v", "files": [(번호, 이름)], "ambiguous"}`. `form` 이 있으면 제출한 값을 유지한다."""
    from app import records

    fields = _Fields(form) if form is not None else None
    names = _file_names(intake)
    cards = []
    for item in (intake.plan or {}).get("items", []):
        v = item_values(item, fields, names)
        card = {"item": item, "v": v, "files": [(n, names.get(n, "")) for n in item.get("files", [])], "ambiguous": {}}
        if item["kind"] == "activity":
            for t, (model, rel) in records.LINK_TYPES.items():
                card["ambiguous"][t] = records.ambiguous_view(db, model, item.get("ambiguous", {}).get(rel, []))
        cards.append(card)
    return cards


def _release(db: Session, intake_id: int, message: str) -> None:
    """저장 중(applying)인 묶음을 다시 확인 대기로 돌린다 (오류 문구와 함께)."""
    db.execute(update(Intake).where(Intake.id == intake_id, Intake.status == "applying").values(status="ready", error=message))
    db.commit()


def recover_applying(db: Session) -> int:
    """앱을 시작할 때 저장 중으로 남은 묶음을 확인 대기로 되돌린다. 저장은 한 번에 커밋되므로 그 묶음의 기록은 만들어지지 않았다."""
    result = db.execute(update(Intake).where(Intake.status == "applying").values(status="ready", error=APPLY_INTERRUPTED))
    db.commit()
    return result.rowcount


def discard(db: Session, settings: Settings, intake: Intake) -> bool:
    """묶음을 버린다 (작업 폴더만 지우고 원본은 둔다). 저장 중이거나 저장이 끝난 묶음이면 False. 이미 버렸으면 True."""
    intake_id = intake.id
    result = db.execute(
        update(Intake).where(Intake.id == intake_id, Intake.status.in_(("uploaded", "analyzing", "ready", "failed")))
        .values(status="discarded")
    )
    db.commit()
    db.refresh(intake)
    if result.rowcount == 1:
        remove_workdir(settings, intake)
        return True
    return intake.status == "discarded"


class _Run:
    """저장 한 번의 진행 기록: 결과 목록, 이름으로 다시 찾을 기록, 지식 사본을 갱신할 기록."""

    def __init__(self, db: Session, settings: Settings, intake: Intake, user: User, extracted: dict[int, ExtractResult], copied: list[Path]):
        self.db, self.settings, self.intake, self.user = db, settings, intake, user
        self.extracted, self.copied = extracted, copied
        self.by_number = {file_number(intake, f): f for f in intake.files}
        self.result: list[dict] = []
        self.skipped: list[dict] = []
        self.known: dict[str, dict[str, object]] = {"solutions": {}, "customers": {}, "partners": {}}
        self.sync: list = []
        self.file_texts: list[tuple] = []
        self.attached: set[tuple[int, int]] = set()
        # 이 저장 전에 있던 기록의 번호. 그 사이 지워진 번호를 이 저장에서 새로 만든 기록이 물려받아도 (SQLite 는 번호를 다시 쓴다) 섞이지 않는다.
        self.existing = {m: set(db.scalars(select(m.id))) for m in (Solution, Customer, Partner, Business)}

    def existing_id(self, model, value) -> int | None:
        number = _digits(value)
        return number if number in self.existing[model] else None

    def remember(self, rel: str, obj) -> None:
        self.known[rel][normalize_name(obj.name)] = obj
        if obj not in self.sync:
            self.sync.append(obj)

    def attach(self, solution: Solution, item: dict, v: dict) -> int:
        """항목의 파일을 솔루션 자료로 복사한다 (원본은 그대로 둔다). 만든 자료 수."""
        from app import records

        made = 0
        for number in item["files"]:
            f = self.by_number.get(number)
            if f is None or f.read_status != "ok" or (solution.id, f.id) in self.attached:
                continue
            src = stored_path(self.settings, f)

            def write(path: Path, src: Path = src) -> int:
                path.parent.mkdir(parents=True, exist_ok=True)
                self.copied.append(path)  # 복사하다 실패해도 지울 수 있게 먼저 적어 둔다
                shutil.copyfile(src, path)
                return path.stat().st_size

            saved = records.new_solution_file(self.db, self.settings, solution, f.original_name, v["kinds"][number], self.user,
                                              write, extracted=self.extracted.get(f.id))
            sf, extract = saved  # 크기 제한 없이 복사하므로 None 이 아니다
            self.attached.add((solution.id, f.id))
            self.file_texts.append((sf, extract))
            if not f.target_type:
                f.target_type, f.target_id = "solution", solution.id
            made += 1
        return made

    def partner_for(self, v: dict):
        """새 솔루션의 파트너: 고른 등록 파트너, 없으면 새 이름 (같은 이름이 있으면 그것)."""
        from app import records

        chosen = self.existing_id(Partner, v["partner"])
        if chosen is not None:
            return self.db.get(Partner, chosen)
        if v["partner_new"].strip():
            partner, created = records.get_or_create(self.db, Partner, v["partner_new"], self.user)
            if created:
                self.result.append({"type": "partner", "id": partner.id, "label": partner.name})
            if partner is not None:
                self.remember("partners", partner)
            return partner
        return None


def _extract_for_solutions(settings: Settings, intake: Intake, chosen: list[tuple[dict, dict]]) -> dict[int, ExtractResult]:
    """솔루션 자료가 될 파일의 글을 미리 읽는다 (쓰기 트랜잭션을 길게 잡지 않으려고 저장 전에). 사진 등은 읽지 않고 보관만."""
    by_number = {file_number(intake, f): f for f in intake.files}
    out: dict[int, ExtractResult] = {}
    for item, _ in chosen:
        if item["kind"] not in ("new_solution", "add_to_solution"):
            continue
        for number in item["files"]:
            f = by_number.get(number)
            if f is None or f.read_status != "ok" or f.id in out:
                continue
            out[f.id] = extract_text(stored_path(settings, f)) if f.kind == "document" else ExtractResult("unsupported", "")
    return out


def _create_records(run: _Run, chosen: list[tuple[dict, dict]]) -> None:
    """고른 항목으로 기록을 만든다 (커밋하지 않는다): 파트너, 고객, 솔루션, 솔루션 자료, 활동 기록 순서."""
    from app import records

    db, user = run.db, run.user
    partners_for: dict[str, object] = {}
    for item, v in chosen:
        if item["kind"] == "new_partner":
            partner, created = records.get_or_create(db, Partner, v["name"], user, contact=v["contact"].strip(),
                                                     description=v["description"].strip(), memo=v["memo"].strip())
            if created:
                run.result.append({"type": "partner", "id": partner.id, "label": partner.name})
            run.remember("partners", partner)
    for item, v in chosen:
        if item["kind"] == "new_solution":
            partners_for[item["key"]] = run.partner_for(v)
    for item, v in chosen:
        if item["kind"] == "new_customer":
            customer, created = records.get_or_create(db, Customer, v["name"], user, industry=v["industry"].strip(),
                                                      contact=v["contact"].strip(), memo=v["memo"].strip())
            if created:
                run.result.append({"type": "customer", "id": customer.id, "label": customer.name})
            run.remember("customers", customer)

    for item, v in chosen:
        if item["kind"] != "new_solution":
            continue
        partner = partners_for.get(item["key"])
        tags = ",".join(t for t in (p.strip() for p in v["tags"].split(",")) if t)
        solution, created = records.get_or_create(
            db, Solution, v["name"], user, summary=v["summary"].strip(), description=v["description"].strip(), tags=tags,
            partner_id=partner.id if partner is not None else None,
        )
        made = run.attach(solution, item, v)
        run.remember("solutions", solution)
        label = solution.name if created else f"{solution.name} (이미 등록된 솔루션에 자료 {made}개 추가)"
        run.result.append({"type": "solution", "id": solution.id, "label": label})
    for item, v in chosen:
        if item["kind"] != "add_to_solution":
            continue
        target = run.existing_id(Solution, v["solution"]) or run.existing_id(Solution, item["solution_id"])
        if target is None:
            run.skipped.append({"type": "skipped", "id": None, "label": f"{labels.INTAKE_ITEM_KINDS['add_to_solution']}: {TARGET_GONE}"})
            continue
        solution = db.get(Solution, target)
        made = run.attach(solution, item, v)
        run.remember("solutions", solution)
        run.result.append({"type": "solution", "id": solution.id, "label": f"{solution.name} (자료 {made}개 추가)"})

    db.flush()
    for item, v in chosen:
        if item["kind"] != "activity":
            continue
        a = Activity(created_by_id=user.id, raw_text=v["raw_text"].strip())
        db.add(a)
        ids = {t: [i for i in v[f"{t}_ids"] if run.existing_id(model, i) is not None] for t, (model, _) in records.LINK_TYPES.items()}
        for rel, t in (("solutions", "solution"), ("customers", "customer"), ("partners", "partner")):
            for name in item.get("new", {}).get(rel, []):  # 이 묶음에서 만들었거나 같은 이름으로 찾은 기록과도 연결한다
                obj = run.known[rel].get(normalize_name(name))
                if obj is not None and str(obj.id) not in ids[t]:
                    ids[t].append(str(obj.id))
        records.apply_activity_fields(
            db, user, a, kind=v["kind"], date_=v["date"], title=v["title"], raw_text=v["raw_text"].strip(), summary_md=v["summary_md"],
            fu_task=v["fu_task"], fu_owner=v["fu_owner"], fu_due=v["fu_due"], fu_done=[],
            ds_customer=v["ds_customer"], ds_content=v["ds_content"],
            solution_ids=ids["solution"], customer_ids=ids["customer"], business_ids=ids["business"], partner_ids=ids["partner"],
            new_solutions=v["new_names"]["new_solutions"], new_customers=v["new_names"]["new_customers"],
            new_partners=v["new_names"]["new_partners"],
        )
        db.flush()
        for number in item["files"]:
            f = run.by_number.get(number)
            if f is not None and not f.target_type:
                f.target_type, f.target_id = "activity", a.id
        run.result.append({"type": "activity", "id": a.id, "label": a.title or "(제목 없음)"})
        run.sync.append(a)
    run.result.extend(run.skipped)


def _sync_knowledge(db: Session, mirror: KnowledgeMirror, run: _Run) -> None:
    """커밋 뒤에 만들거나 바뀐 기록과, 그 사본에 이름이 적히는 연결된 기록의 지식 사본을 다시 쓴다."""
    from app import records

    objs: list = []
    seen: set = set()

    def add(obj) -> None:
        if obj is not None and (type(obj), obj.id) not in seen:
            seen.add((type(obj), obj.id))
            objs.append(obj)

    for obj in run.sync:
        add(obj)
        if isinstance(obj, Activity):
            for related in records.linked(obj):
                add(related)
        if isinstance(obj, Solution):
            add(obj.partner)
    for obj in objs:
        db.refresh(obj)
    for sf, extract in run.file_texts:
        if extract.status == "ok":
            mirror.write_file_text(sf, file_markdown(sf, extract.text))
    for obj in objs:
        mirror.sync(obj)


def apply(db: Session, settings: Settings, mirror: KnowledgeMirror, intake: Intake, user: User, form) -> list[dict]:
    """확인 화면의 폼 값을 반영해 기록을 만든다. `[{"type", "id", "label"}]` (저장하지 못한 항목은 type "skipped").

    항목 종류·번호·파일 번호·솔루션 번호는 저장된 정리안(`intake.plan`)에서만 오고, 폼은 고친 글 값, 제외 표시,
    연결·대상 선택만 준다 (고른 번호는 실제로 있는 기록일 때만 쓴다). 입력 오류와 "저장할 항목이 없습니다." 는 ValueError,
    확인 대기 상태가 아니거나 다른 요청이 먼저 저장하면 NotReady, 저장 도중 오류는 ApplyFailed (아무것도 남기지 않고 ready 로 돌린다)."""
    if intake.status != "ready":
        raise NotReady
    plan = intake.plan
    fields, names = _Fields(form), _file_names(intake)
    chosen: list[tuple[dict, dict]] = []
    for item in (plan or {}).get("items", []):
        if item["kind"] not in SAVEABLE_KINDS or fields.flag(f"skip_{item['key']}"):
            continue
        v = item_values(item, fields, names)
        if error := item_error(item, v):
            raise ValueError(error)
        chosen.append((item, v))
    if not chosen:
        raise ValueError(NOTHING_TO_SAVE)

    intake_id, user_id = intake.id, user.id
    db.commit()  # 읽기 트랜잭션을 끝내고, 선점 UPDATE 를 짧은 쓰기 트랜잭션으로 한다
    claimed = db.execute(update(Intake).where(Intake.id == intake_id, Intake.status == "ready").values(status="applying", error=""))
    db.commit()
    if claimed.rowcount != 1:
        db.refresh(intake)
        raise NotReady
    db.refresh(intake)
    if intake.plan != plan:  # 선점 사이에 다시 정리되어 정리안이 바뀌었다
        _release(db, intake_id, PLAN_CHANGED)
        raise NotReady

    copied: list[Path] = []
    try:
        run = _Run(db, settings, intake, user, _extract_for_solutions(settings, intake, chosen), copied)
        _create_records(run, chosen)
        intake.result = list(run.result)
        intake.status, intake.applied_at, intake.error = "applied", datetime.now(), ""
        db.commit()
    except BaseException as e:
        db.rollback()
        for path in copied:
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()  # 새 솔루션의 빈 폴더 (자료가 남은 폴더는 그대로 둔다)
            except OSError:
                pass
        _release(db, intake_id, APPLY_FAILED)
        db.refresh(intake)
        if isinstance(e, Exception):
            log.exception("올리기 묶음 %s 저장 실패", intake_id)
            raise ApplyFailed(APPLY_FAILED) from e
        raise
    try:
        _sync_knowledge(db, mirror, run)
    except Exception:  # noqa: BLE001 - 기록은 이미 저장됐다. 지식 사본은 `rebuild-knowledge` 로 다시 만들 수 있다
        log.exception("올리기 묶음 %s 지식 사본 갱신 실패", intake_id)
    finally:
        remove_workdir(settings, intake)
    return run.result

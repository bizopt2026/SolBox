"""올리기 묶음: 여러 파일을 한 번에 받아 저장하고, 묶음별 작업 폴더(글 사본·줄인 사진)를 준비한다."""
from __future__ import annotations

import re
import shutil
import warnings
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, ImageOps
from sqlalchemy.orm import Session, object_session

from app.config import Settings
from app.extract import extract_text
from app.knowledge import safe_name
from app.models import Intake, IntakeFile, User

CHUNK = 1024 * 1024
MAX_FILES = 20
MAX_NOTE = 500
MAX_IMAGE_SIDE = 2000
NO_FILES = "올릴 파일을 선택하세요."
FILE_LIST_NAME = "파일목록.md"

_EXT = re.compile(r"\.[a-z0-9]{1,10}")
_KINDS = {
    "document": {".pptx", ".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv"},
    "image": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"},
    "audio": {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".wma"},
}
_IMAGE_ERRORS = (OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning)


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


def _shrink_image(src: Path, dest_base: Path) -> Path | None:
    """사진을 열어 방향을 바로잡고 긴 변 2,000px 이하로 줄여 저장한다. 저장한 경로, 열지 못하면 None."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(src) as opened:
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


def prepare_workdir(settings: Settings, intake: Intake) -> None:
    """`intake_work/<id>/` 를 비우고 다시 만든다: 문서는 글 사본(.md), 사진은 줄인 사본, 파일목록.md."""
    folder = work_dir(settings, intake)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[int, IntakeFile, str]] = []
    for f in sorted(intake.files, key=lambda x: x.id):
        number = file_number(intake, f)
        base = f"{number}-{safe_name(f.original_name)}"
        copy_name = ""
        src = stored_path(settings, f)
        if f.kind == "document":
            result = extract_text(src)
            f.read_status = result.status
            if result.status == "ok":
                copy_name = base + ".md"
                (folder / copy_name).write_text(f"# {f.original_name}\n\n{result.text}\n", encoding="utf-8")
        elif f.kind == "image":
            dest = _shrink_image(src, folder / base)
            f.read_status = "ok" if dest else "failed"
            copy_name = dest.name if dest else ""
        elif f.kind == "audio":
            f.read_status = "needs_transcript"
        else:
            f.read_status = "unsupported"
        rows.append((number, f, copy_name))
    from app import labels

    lines = ["# 파일 목록", "", "| 번호 | 원래 이름 | 종류 | 읽기 상태 | 사본 파일 |", "|---|---|---|---|---|"]
    for number, f, copy_name in rows:
        name = f.original_name.replace("|", "\\|")
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

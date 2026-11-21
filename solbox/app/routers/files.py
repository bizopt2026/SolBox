"""솔루션 자료 파일: 여러 개 올리기, 내려받기, 종류 변경, 삭제."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import labels
from app import notices
from app.db import get_db
from app.extract import extract_text
from app.guards import WriteBusy, is_busy_error
from app.intake import clean_name, write_upload
from app.knowledge import KnowledgeMirror, file_markdown, get_mirror
from app.models import Solution, SolutionFile, User
from app.records import DuplicateFile, new_solution_file, solution_file_path
from app.routers._common import get_or_404
from app.routers.solutions import detail_page
from app.security import ensure_can_delete, require_user

router = APIRouter()
BAD_KIND = "알 수 없는 자료 종류입니다."


def _stored_path(request: Request, f: SolutionFile) -> Path:
    return solution_file_path(request.app.state.settings, f)


def _save_upload(db: Session, mirror: KnowledgeMirror, request: Request, solution: Solution,
                 upload: UploadFile, name: str, kind: str, user: User, limit: int, same: list[str] | None = None) -> str:
    """파일 하나를 저장한다. 돌려주는 값: `"saved"`(저장함), `"too_big"`(크기 제한을 넘어 아무것도 남기지 않음),
    `"duplicate"`(같은 솔루션에 내용이 같은 자료가 있어 새로 만들지 않음: `same` 에 "올린 이름 (이미 있는 자료 이름)" 을 적는다. 실패도 저장도 아니다)."""
    saved = None
    try:
        saved = new_solution_file(db, request.app.state.settings, solution, name, kind, user,
                                  lambda path: write_upload(upload, path, limit))
        if saved is None:
            db.rollback()
            return "too_big"
        db.commit()
    except DuplicateFile as e:
        try:
            db.commit()  # 옛 파일의 해시만 채워졌을 수 있다 (못 채워도 중복 판정은 그대로다: 다음에 비교할 때 다시 채운다)
        except (OperationalError, WriteBusy):
            db.rollback()
        if same is not None:
            same.append(name if name == e.existing.original_name else f"{name} → 이미 있는 자료 {e.existing.original_name}")
        return "duplicate"
    except BaseException:
        if saved is not None:
            _stored_path(request, saved[0]).unlink(missing_ok=True)
        db.rollback()
        raise
    f, result = saved
    if result.status == "ok":
        mirror.write_file_text(f, file_markdown(f, result.text))
    return "saved"


@router.post("/solutions/{solution_id}/files")
def upload_files(
    solution_id: int, request: Request,
    files: list[UploadFile | str] = [], kind: str = Form("related"),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    solution = get_or_404(db, Solution, solution_id)
    if kind not in labels.FILE_KINDS:
        raise HTTPException(status_code=400, detail=BAD_KIND)
    max_mb = request.app.state.settings.max_upload_mb
    skipped: list[str] = []
    same: list[str] = []
    busy: list[str] = []  # 파일마다 따로 커밋한다: 중간에 잠금에 막히면 앞 파일은 저장돼 있다 (503 "아무것도 저장되지 않았다" 가 거짓이 된다)
    saved_n = 0
    for upload in files:
        if isinstance(upload, str):  # 파일을 고르지 않고 보낸 빈 칸은 문자열로 들어온다
            continue
        name = clean_name(upload.filename or "")
        if not name:
            continue
        if busy:
            busy.append(name)
            continue
        try:
            outcome = _save_upload(db, mirror, request, solution, upload, name, kind, user, max_mb * 1024 * 1024, same)
        except (OperationalError, WriteBusy) as e:
            # 쓰기 잠금에 막혔다 (`WriteBusy`: 번호 표시 잠금, `OperationalError`: 저장·커밋 잠금). 실제로 저장된 파일이 없으면(건너뛴 중복·큰 파일은 저장이 아니다) 그대로 503,
            # 앞 파일이 저장됐으면 어디까지 저장됐는지 알린다.
            if saved_n and (isinstance(e, WriteBusy) or is_busy_error(e)):
                busy.append(name)
                continue
            raise
        if outcome == "saved":
            saved_n += 1
        elif outcome == "too_big":
            skipped.append(name)
    db.refresh(solution)
    mirror.sync(solution)
    notice = ""
    if same:
        notice += f"이미 같은 내용의 자료가 있어 건너뛴 파일: {', '.join(same)}. "
    if busy:
        notice += f"다른 저장과 겹쳐 저장하지 못한 파일: {', '.join(busy)} (앞의 파일은 저장했습니다. 이 파일만 다시 올려 주세요). "
    if skipped:
        notice += f"크기 제한({max_mb}MB)을 넘어 건너뛴 파일: {', '.join(skipped)}"
    if notice:
        return detail_page(request, db, user, solution, notice=notice.strip())
    return notices.redirect(f"/solutions/{solution.id}", "files_uploaded")


@router.post("/files/{file_id}/kind")
def change_kind(
    file_id: int, request: Request, kind: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    f = get_or_404(db, SolutionFile, file_id)
    if kind not in labels.FILE_KINDS:
        raise HTTPException(status_code=400, detail=BAD_KIND)
    f.kind = kind
    db.commit()
    if f.extract_status == "ok":  # 사본 첫 줄에 종류 이름이 들어 있다. 다시 읽기에 실패하면 기존 사본을 그대로 둔다
        result = extract_text(_stored_path(request, f))
        if result.status == "ok":
            mirror.write_file_text(f, file_markdown(f, result.text))
    mirror.sync(f.solution)
    return notices.redirect(f"/solutions/{f.solution_id}", "file_kind")


@router.get("/files/{file_id}/download")
def download(
    file_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    f = get_or_404(db, SolutionFile, file_id)
    path = _stored_path(request, f)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    return FileResponse(
        path, media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(f.original_name)},
    )


@router.post("/files/{file_id}/delete")
def delete_file(
    file_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    f = get_or_404(db, SolutionFile, file_id)
    ensure_can_delete(user, f)
    solution = f.solution
    mirror.remove_file_text(f)
    _stored_path(request, f).unlink(missing_ok=True)
    db.delete(f)
    db.commit()
    db.refresh(solution)
    mirror.sync(solution)
    return notices.redirect(f"/solutions/{solution.id}", "file_deleted")

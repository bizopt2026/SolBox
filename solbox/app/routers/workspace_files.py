"""사업 방향·과제 공간의 자료 파일: 여러 개 올리기, 내려받기, 삭제 (AI 코워크 ①). 솔루션 자료(`routers/files.py`)와 같은 규칙."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import notices
from app.db import get_db
from app.guards import WriteBusy, is_busy_error
from app.intake import clean_name, write_upload
from app.knowledge import KnowledgeMirror, file_markdown, get_mirror
from app.models import Initiative, Strategy, User, WorkspaceFile
from app.records import DuplicateFile, new_workspace_file, workspace_file_path
from app.routers._common import get_or_404
from app.security import ensure_can_delete, require_user

router = APIRouter()


def _owner_url(owner) -> str:
    return f"/strategies/{owner.id}" if isinstance(owner, Strategy) else f"/initiatives/{owner.id}"


def _save_one(db: Session, mirror: KnowledgeMirror, request: Request, owner, upload: UploadFile, name: str, user: User,
              limit: int, same: list[str]) -> str:
    """파일 하나: "saved" / "too_big" / "duplicate"."""
    saved = None
    try:
        saved = new_workspace_file(db, request.app.state.settings, owner, name, user, lambda path: write_upload(upload, path, limit))
        if saved is None:
            db.rollback()
            return "too_big"
        db.commit()
    except DuplicateFile as e:
        try:
            db.commit()  # 옛 파일의 해시만 채워졌을 수 있다
        except (OperationalError, WriteBusy):
            db.rollback()
        same.append(name if name == e.existing.original_name else f"{name} → 이미 있는 자료 {e.existing.original_name}")
        return "duplicate"
    except BaseException:
        if saved is not None:
            workspace_file_path(request.app.state.settings, saved[0]).unlink(missing_ok=True)
        db.rollback()
        raise
    f, result = saved
    if result.status == "ok":
        mirror.write_file_text(f, file_markdown(f, result.text))
    return "saved"


def _upload(request: Request, db: Session, mirror: KnowledgeMirror, user: User, owner, files: list):
    max_mb = request.app.state.settings.max_upload_mb
    skipped: list[str] = []
    same: list[str] = []
    busy: list[str] = []
    saved_n = 0
    for upload in files:
        if isinstance(upload, str):  # 파일을 고르지 않고 보낸 빈 칸
            continue
        name = clean_name(upload.filename or "")
        if not name:
            continue
        if busy:
            busy.append(name)
            continue
        try:
            outcome = _save_one(db, mirror, request, owner, upload, name, user, max_mb * 1024 * 1024, same)
        except (OperationalError, WriteBusy) as e:
            if saved_n and (isinstance(e, WriteBusy) or is_busy_error(e)):
                busy.append(name)
                continue
            raise
        if outcome == "saved":
            saved_n += 1
        elif outcome == "too_big":
            skipped.append(name)
    db.refresh(owner)
    mirror.sync(owner)
    notice = ""
    if same:
        notice += f"이미 같은 내용의 자료가 있어 건너뛴 파일: {', '.join(same)}. "
    if busy:
        notice += f"다른 저장과 겹쳐 저장하지 못한 파일: {', '.join(busy)} (앞의 파일은 저장했습니다. 이 파일만 다시 올려 주세요). "
    if skipped:
        notice += f"크기 제한({max_mb}MB)을 넘어 건너뛴 파일: {', '.join(skipped)}"
    if notice:
        if isinstance(owner, Strategy):
            from app.routers.strategies import _detail_page as strategy_page

            return strategy_page(request, db, user, owner, upload_notice=notice.strip())
        from app.routers.initiatives import _detail_page as initiative_page

        return initiative_page(request, db, user, owner, upload_notice=notice.strip())
    return notices.redirect(_owner_url(owner) + "#files", "files_uploaded")


@router.post("/strategies/{strategy_id}/files")
def upload_strategy_files(
    strategy_id: int, request: Request, files: list[UploadFile | str] = [],
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    return _upload(request, db, mirror, user, get_or_404(db, Strategy, strategy_id), files)


@router.post("/initiatives/{initiative_id}/files")
def upload_initiative_files(
    initiative_id: int, request: Request, files: list[UploadFile | str] = [],
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    return _upload(request, db, mirror, user, get_or_404(db, Initiative, initiative_id), files)


@router.get("/workspace-files/{file_id}/download")
def download(file_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    f = get_or_404(db, WorkspaceFile, file_id)
    path = workspace_file_path(request.app.state.settings, f)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(f.original_name)})


@router.post("/workspace-files/{file_id}/delete")
def delete_file(
    file_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    f = get_or_404(db, WorkspaceFile, file_id)
    ensure_can_delete(user, f)
    owner = f.owner
    mirror.remove_file_text(f)
    workspace_file_path(request.app.state.settings, f).unlink(missing_ok=True)
    db.delete(f)
    db.commit()
    db.refresh(owner)
    mirror.sync(owner)
    return notices.redirect(_owner_url(owner) + "#files", "file_deleted")

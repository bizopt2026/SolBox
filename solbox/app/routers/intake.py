"""올리기 화면: 파일 여러 개를 한 번에 올리고, 묶음 목록·상세·원본 내려받기."""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import intake as intake_lib
from app import labels
from app.ai.queue import queue_position
from app.db import get_db
from app.main import render
from app.models import Intake, IntakeFile, Solution, User
from app.routers._common import get_or_404, paginate
from app.security import require_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/intake")

NOT_YOURS = "올린 사람과 관리자만 볼 수 있습니다."
NOTE_TOO_LONG = f"메모는 {intake_lib.MAX_NOTE}자까지 쓸 수 있습니다."
_ASCII_UNSAFE = re.compile(r'[^\x20-\x7e]|["\\%]')


def _ensure_owner(user: User, intake: Intake) -> None:
    if user.role != "admin" and intake.created_by_id != user.id:
        raise HTTPException(status_code=403, detail=NOT_YOURS)


def get_intake(db: Session, user: User, intake_id: int) -> Intake:
    intake = get_or_404(db, Intake, intake_id)
    _ensure_owner(user, intake)
    return intake


def _position(db: Session, intake: Intake) -> int:
    return queue_position(db, intake.job) if intake.job is not None else 0


def detail_page(request: Request, user: User, intake: Intake, db: Session, notice: str = "",
                skipped: list[str] | None = None, status_code: int = 200):
    items = (intake.plan or {}).get("items", []) if intake.status == "ready" else []
    solution_names = {s.id: s.name for s in db.scalars(select(Solution))} if items else {}
    return render(
        request, "intake/detail.html", status_code=status_code, user=user, intake=intake, notice=notice,
        skipped=skipped or [], has_audio=any(f.kind == "audio" for f in intake.files),
        position=_position(db, intake), items=items, solution_names=solution_names,
        file_names={i: f.original_name for i, f in enumerate(intake.files, 1)}, max_note=intake_lib.MAX_NOTE,
    )


@router.get("/new")
def new_page(request: Request, user: User = Depends(require_user)):
    return render(request, "intake/new.html", user=user, error="", note="", max_files=intake_lib.MAX_FILES,
                  max_mb=request.app.state.settings.max_upload_mb, max_note=intake_lib.MAX_NOTE)


@router.post("")
def create(
    request: Request, files: list[UploadFile | str] = [], note: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    settings = request.app.state.settings

    def again(error: str):
        return render(request, "intake/new.html", status_code=400, user=user, error=error, note=note,
                      max_files=intake_lib.MAX_FILES, max_mb=settings.max_upload_mb, max_note=intake_lib.MAX_NOTE)

    if len(note.strip()) > intake_lib.MAX_NOTE:
        return again(NOTE_TOO_LONG)
    try:
        intake, skipped = intake_lib.create_intake(db, settings, user, note, files)
    except ValueError as e:
        return again(str(e))
    intake_lib.prepare_workdir(settings, intake)
    try:
        intake_lib.start_analysis(db, intake, user)
    except Exception:  # noqa: BLE001 - 묶음은 이미 저장되었다. 상세 화면의 "정리 시작" 으로 다시 할 수 있다
        db.rollback()
        log.exception("올리기 묶음 %s 정리 시작 실패", intake.id)
    if skipped:
        return detail_page(request, user, intake, db, skipped=skipped)
    return RedirectResponse(f"/intake/{intake.id}", status_code=303)


@router.get("")
def list_page(request: Request, status: str = "", page: str = "1",
              user: User = Depends(require_user), db: Session = Depends(get_db)):
    stmt = select(Intake)
    if user.role != "admin":
        stmt = stmt.where(Intake.created_by_id == user.id)
    if status in labels.INTAKE_STATUSES:
        stmt = stmt.where(Intake.status == status)
    else:
        status = ""
    items = list(db.scalars(stmt.order_by(Intake.id.desc())))
    owners = {u.id: (u.name or u.username) for u in db.scalars(select(User))} if user.role == "admin" else {}
    return render(request, "intake/list.html", user=user, pg=paginate(items, page), status=status, owners=owners)


@router.get("/files/{file_id}/download")
def download(file_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    f = get_or_404(db, IntakeFile, file_id)
    _ensure_owner(user, f.intake)
    path = intake_lib.stored_path(request.app.state.settings, f)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    fallback = _ASCII_UNSAFE.sub("_", f.original_name) or "file"
    return FileResponse(
        path, media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(f.original_name, safe='')}"},
    )


@router.get("/{intake_id}")
def detail(intake_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return detail_page(request, user, get_intake(db, user, intake_id), db)


@router.get("/{intake_id}/status")
def status(intake_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """정리 중 안내 조각 (HTMX). 끝나면(정리 중이 아니게 되면) 상세 화면으로 보낸다."""
    intake = db.get(Intake, intake_id)
    if intake is None:  # 그 사이 지워졌다. 조회를 멈추고 목록으로 보낸다.
        if user.role != "admin":  # 남의 묶음과 없는 묶음을 구별할 수 없게 같은 응답을 준다
            raise HTTPException(status_code=403, detail=NOT_YOURS)
        return Response("올린 묶음이 없습니다.", headers={"HX-Redirect": "/intake"})
    _ensure_owner(user, intake)
    if intake.status == "analyzing" and intake_lib.finish_failed_analysis(db, intake.id):
        db.refresh(intake)  # 작업이 끝났는데 기록되지 않은 채 남은 묶음을 바로잡았다
    if intake.status != "analyzing":
        return Response("정리가 끝났습니다.", headers={"HX-Redirect": f"/intake/{intake.id}"})
    return render(request, "intake/_status.html", intake=intake, job=intake.job, position=_position(db, intake))


@router.post("/{intake_id}/analyze")
def analyze(
    intake_id: int, request: Request, note: str | None = Form(None),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """메모를 고치고 다시 정리한다."""
    intake = get_intake(db, user, intake_id)
    if intake.status in ("applied", "discarded"):
        raise HTTPException(status_code=400, detail="이미 끝난 묶음은 다시 정리할 수 없습니다.")
    if intake.status != "analyzing":
        if note is not None:
            if len(note.strip()) > intake_lib.MAX_NOTE:
                return detail_page(request, user, intake, db, notice=NOTE_TOO_LONG, status_code=400)
            intake.note = note.strip()
            db.commit()
        intake_lib.start_analysis(db, intake, user)
    return RedirectResponse(f"/intake/{intake.id}", status_code=303)

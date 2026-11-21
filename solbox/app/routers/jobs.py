"""AI 작업 상태 조각과 재시도."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.ai.gate import require_ai_on
from app.ai.queue import enqueue, queue_position
from app.db import get_db
from app.main import render
from app.models import AIJob, User
from app.security import require_user

router = APIRouter(prefix="/jobs")


def _job_for(db: Session, job_id: int, user: User) -> AIJob:
    job = db.get(AIJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    if user.role != "admin" and job.requester_id != user.id:
        raise HTTPException(status_code=403, detail="본인이 요청한 작업만 볼 수 있습니다.")
    return job


def _safe_next(value: str | None) -> str | None:
    """내부 경로만 허용한다 (`//host`, `/\\host` 같은 우회 차단)."""
    if value and value.startswith("/") and not value.startswith(("//", "/\\")) and "\\" not in value:
        return value
    return None


@router.get("/{job_id}/status")
def status(job_id: int, request: Request, next: str | None = None,
           user: User = Depends(require_user), db: Session = Depends(get_db)):
    job = _job_for(db, job_id, user)
    target = _safe_next(next)
    response = render(request, "jobs/_status.html", job=job, position=queue_position(db, job), next=target)
    if target and job.status in ("done", "failed"):
        response.headers["HX-Redirect"] = quote(target, safe="/?=&%#:@+,;~")
    return response


@router.post("/{job_id}/retry", dependencies=[Depends(require_ai_on)])
def retry(job_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    job = _job_for(db, job_id, user)
    if job.status != "failed":
        raise HTTPException(status_code=400, detail="실패한 작업만 다시 시도할 수 있습니다.")
    new = enqueue(db, job.kind, dict(job.input or {}), job.requester_id, job.priority)
    return {"job_id": new.id}

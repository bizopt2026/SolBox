"""웹 리서치: 지금 조사, 실행 상태, 실패한 분야 다시 시도 (보고서 화면은 다음 단계)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.ai.queue import queue_position
from app.db import get_db
from app.labels import RESEARCH_AREAS
from app.main import render
from app.models import AIJob, ResearchReport, ResearchRun, User
from app.research import start_run
from app.security import require_admin, require_user

router = APIRouter(prefix="/research")

ADMIN_PAGE = "/admin/research"


@router.post("/run")
def run_now(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        run = start_run(db, "manual", admin.id)
    except ValueError:
        return RedirectResponse(f"{ADMIN_PAGE}?notice=no_topics", status_code=303)
    return RedirectResponse(ADMIN_PAGE if run else f"{ADMIN_PAGE}?notice=busy", status_code=303)


@router.get("/runs/{run_id}/status")
def run_status(run_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="조사 실행을 찾을 수 없습니다.")
    rows = []
    for r in (r for r in run.reports if r.kind == "area"):
        job = db.get(AIJob, r.job_id) if r.job_id else None
        position = queue_position(db, job) if job is not None else 0
        rows.append({"report": r, "area": RESEARCH_AREAS.get(r.area, r.area), "job": job, "position": position})
    return render(request, "research/_run_status.html", run=run, rows=rows)


@router.post("/reports/{report_id}/retry")
def retry_report(report_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    report = db.get(ResearchReport, report_id)
    if report is None or report.kind != "area":
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")
    if report.status != "failed":
        raise HTTPException(status_code=400, detail="실패한 보고서만 다시 시도할 수 있습니다.")
    run = report.run
    if run.status != "running":
        other = db.query(ResearchRun.id).filter(ResearchRun.status == "running", ResearchRun.id != run.id).first()
        if other is not None:  # 조사는 한 번에 하나만 돈다.
            return RedirectResponse(f"{ADMIN_PAGE}?notice=busy", status_code=303)
    job = AIJob(kind="research_area", input={"report_id": report.id}, requester_id=admin.id, priority=10, status="pending")
    db.add(job)
    db.flush()
    report.job_id, report.status, report.error = job.id, "pending", ""
    run.status, run.finished_at = "running", None
    db.commit()
    return RedirectResponse(ADMIN_PAGE, status_code=303)

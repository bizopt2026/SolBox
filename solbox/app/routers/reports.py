"""팀 업무 보고 화면. 지금은 목록·상세의 최소 화면만 있다 (만들기·실적·점검·계획·확정 화면은 이후 작업에서 더한다)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import relations, ui
from app.db import get_db
from app.main import render
from app.models import Report, User
from app.routers._common import get_or_404, paginate
from app.security import require_user

router = APIRouter(prefix="/reports")
report_title = relations.report_label  # 보고 건 이름 (연결 목록과 같은 글)


@router.get("")
def list_reports(request: Request, page: str = "1", user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Report).order_by(Report.period_start.desc(), Report.id.desc())).all()
    return render(request, "reports/list.html", pg=paginate(rows, page), report_title=report_title)


@router.get("/{report_id}")
def detail(report_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    r = get_or_404(db, Report, report_id)
    title = report_title(r)
    return render(request, "reports/detail.html", report=r, title=title, crumbs=ui.crumbs("report", title))

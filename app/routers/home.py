"""홈 화면: 최근 활동, 곧 닥칠 후속 조치, 단계별 솔루션 수, AI 상태."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import labels
from app.db import get_db
from app.main import render
from app.models import AIJob, Activity, Proposal, Solution, User
from app.routers._common import newest_first
from app.security import require_user

router = APIRouter()
RECENT_LIMIT = 10
OPEN_PROPOSAL_LIMIT = 5
FOLLOW_UP_DAYS = 14
AI_PROBLEMS = ("not_installed", "auth", "limit", "network")


def _parse_due(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def upcoming_follow_ups(db: Session, today: date, days: int = FOLLOW_UP_DAYS) -> list[dict]:
    """끝나지 않은 후속 조치 중 기한이 `today + days` 이내(지난 것 포함)인 것. 기한순."""
    limit = today + timedelta(days=days)
    items = []
    for a in db.scalars(select(Activity).order_by(Activity.id)):
        for index, fu in enumerate(a.follow_ups or []):
            due = _parse_due(fu.get("due") or "")
            if fu.get("done") or due is None or due > limit:
                continue
            items.append({
                "activity": a, "index": index, "task": fu.get("task", ""),
                "owner": fu.get("owner", ""), "due": due, "overdue": due < today,
            })
    return sorted(items, key=lambda i: (i["due"], i["activity"].id, i["index"]))


def stage_counts(db: Session) -> list[tuple[str, str, int]]:
    counts = dict(db.execute(select(Solution.stage, func.count()).group_by(Solution.stage)).all())
    return [(code, name, counts.get(code, 0)) for code, name in labels.STAGES.items()]


def ai_problem(db: Session) -> str | None:
    """가장 최근에 끝난 AI 작업이 환경 문제로 실패했으면 안내문을 돌려준다."""
    job = db.scalars(
        select(AIJob).where(AIJob.finished_at.is_not(None)).order_by(AIJob.finished_at.desc(), AIJob.id.desc())
    ).first()
    if job and job.status == "failed" and job.error_kind in AI_PROBLEMS:
        return labels.ERROR_KINDS[job.error_kind]
    return None


def open_proposals(db: Session) -> list[Proposal]:
    """아직 제안서를 끝내지 않은 제안 건, 최근 수정순."""
    stmt = select(Proposal).where(Proposal.status != "deck")
    return list(db.scalars(stmt.order_by(Proposal.updated_at.desc(), Proposal.id.desc()).limit(OPEN_PROPOSAL_LIMIT)))


@router.get("/")
def home(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    recent = newest_first(db.scalars(select(Activity)))[:RECENT_LIMIT]
    return render(
        request, "home.html",
        recent=recent, follow_ups=upcoming_follow_ups(db, date.today()),
        stages=stage_counts(db), proposals=open_proposals(db), ai_problem=ai_problem(db),
    )

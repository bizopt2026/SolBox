"""제안 건 도우미."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Proposal, Solution


def candidate_solutions(db: Session, p: Proposal) -> list[Solution]:
    """제안에 조합할 수 있는 솔루션: 박스 등록 단계 + 제안 건에서 따로 고른 것. 이름순, 중복 없음."""
    ids = [i for i in (p.extra_solution_ids or []) if isinstance(i, int)]
    cond = Solution.stage == "boxed"
    if ids:
        cond = or_(cond, Solution.id.in_(ids))
    return list(db.scalars(select(Solution).where(cond).order_by(Solution.name, Solution.id)))


def display_title(p: Proposal) -> str:
    if p.title:
        return p.title
    if p.business is not None:
        return f"{p.customer.name} · {p.business.name} 제안"
    return f"{p.customer.name} 제안"

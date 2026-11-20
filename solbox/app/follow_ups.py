"""후속 조치 조회: 홈 카드와 목록 화면이 같은 줄 만들기를 쓴다 (활동 기록·담당·연결된 고객을 한꺼번에 읽는다)."""
from __future__ import annotations

from datetime import date
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ui
from app.models import Activity, Customer, FollowUp, activity_customers

FU_STATUSES = {"open": "남은 일", "overdue": "기한 지남", "done": "완료"}  # 목록의 구분 (기본은 남은 일)
FU_DEFAULT = "open"


def status_condition(status: str, today: date):
    """구분에 맞는 조건. 남은 일 = 끝나지 않은 것 모두, 기한 지남 = 그중 기한이 오늘 전인 것."""
    if status == "done":
        return FollowUp.done.is_(True)
    if status == "overdue":
        return (FollowUp.done.is_(False)) & (FollowUp.due.is_not(None)) & (FollowUp.due < today)
    return FollowUp.done.is_(False)


def customers_of(db: Session, activity_ids: Sequence[int]) -> dict[int, list[tuple[int, str]]]:
    """활동 기록 번호 -> 연결된 고객 [(번호, 이름)] (이름순). 질의 한 번."""
    out: dict[int, list[tuple[int, str]]] = {}
    if not activity_ids:
        return out
    stmt = (select(activity_customers.c.activity_id, Customer.id, Customer.name)
            .join(Customer, Customer.id == activity_customers.c.customer_id)
            .where(activity_customers.c.activity_id.in_(list(activity_ids))).order_by(Customer.name, Customer.id))
    for aid, cid, name in db.execute(stmt):
        out.setdefault(aid, []).append((cid, name))
    return out


def rows(db: Session, pairs: Sequence[tuple[FollowUp, Activity]], today: date, with_customers: bool = False) -> list[dict]:
    """(후속 조치, 활동 기록) 쌍들을 화면 줄로: 기한 글(ui.relative_due)과 활동 기록·고객."""
    customers = customers_of(db, [a.id for _, a in pairs]) if with_customers else {}
    out = []
    for fu, a in pairs:
        out.append({
            "id": fu.id, "task": fu.task, "owner": fu.owner, "due": fu.due, "done": fu.done, "activity": a,
            "overdue": bool(fu.due and fu.due < today and not fu.done),
            "rel": ui.relative_due(fu.due, today, fu.done), "customers": customers.get(a.id, []),
        })
    return out

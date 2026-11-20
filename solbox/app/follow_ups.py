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


NOTICE_PARAM = "fu_toggled"  # 방금 완료 표시를 바꾼 후속 조치 번호: 홈·목록이 "되돌리기" 안내를 보인다
NOTICE_PAGES = ("/", "/follow-ups")


def redirect_after_toggle(referer: str | None, follow_up_id: int) -> str | None:
    """완료 표시를 바꾼 요청이 홈·후속 조치 목록에서 왔으면 그 화면으로 돌아갈 주소(안내 번호 포함), 아니면 None (활동 기록 화면 등은 줄이 그대로 보여 안내가 필요 없다)."""
    from urllib.parse import parse_qsl, urlencode, urlsplit

    if not referer:
        return None
    parts = urlsplit(referer)
    if parts.path not in NOTICE_PAGES:
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != NOTICE_PARAM]
    return parts.path + "?" + urlencode(query + [(NOTICE_PARAM, str(follow_up_id))])


def toggled_notice(db: Session, raw: str) -> dict | None:
    """`?fu_toggled=<번호>` 가 가리키는 후속 조치의 안내: {"id", "task", "done"}. 없는 번호면 None."""
    from app.idparse import parse_id

    fu = db.get(FollowUp, parse_id(raw)) if parse_id(raw) is not None else None
    return {"id": fu.id, "task": fu.task, "done": fu.done} if fu is not None else None


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
            "id": fu.id, "task": fu.task, "owner": fu.owner, "owner_user_id": fu.owner_user_id, "due": fu.due, "done": fu.done, "activity": a,
            "overdue": bool(fu.due and fu.due < today and not fu.done),
            "rel": ui.relative_due(fu.due, today, fu.done), "customers": customers.get(a.id, []),
        })
    return out

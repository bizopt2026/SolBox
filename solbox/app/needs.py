"""고객니즈 관리의 행위 규칙 (2026-11-27, 설계 docs/superpowers/specs/2026-11-27-customer-needs-design.md).

- 보이는 상태 = 영업기회에 묶였으면 "영업기회로", 아니면 저장한 상태 (`effective_status`·`status_condition` 두 곳만 판단한다).
- 손대지 않은 니즈 = 저장 상태 "새 니즈" + 영업기회 없음 + 메모 없음. 활동 기록 폼에서 빠지거나 활동 기록이 지워질 때
  손대지 않은 니즈만 지우고, 나머지는 나온 곳(활동 기록)만 비워 고객니즈 목록에 남긴다.
- 영업기회에 묶을 때 "새 니즈" 는 "검토 중" 이 된다 (풀리면 검토 중으로 돌아간다).
"""
from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, object_session

from app import ontology
from app.models import Activity, CustomerNeed, User

STATUS_NEW = "new"
STATUS_REVIEWING = "reviewing"
STATUS_OPPORTUNITY = "opportunity"  # 저장하지 않는다: 영업기회에 묶인 니즈의 보이는 상태
STORED_STATUSES = tuple(c for c in ontology.NEED_STATUSES if c != STATUS_OPPORTUNITY)  # 사람이 고르는 상태


def effective_status(need: CustomerNeed) -> str:
    if need.opportunity_id is not None:
        return STATUS_OPPORTUNITY
    return need.status if need.status in STORED_STATUSES else STATUS_NEW


def status_condition(code: str):
    """`effective_status(n) == code` 와 같은 뜻의 SQL 조건."""
    if code == STATUS_OPPORTUNITY:
        return CustomerNeed.opportunity_id.is_not(None)
    unbound = CustomerNeed.opportunity_id.is_(None)
    if code == STATUS_NEW:  # 목록에 없는 옛 값도 새 니즈로 본다
        return and_(unbound, or_(CustomerNeed.status.is_(None), CustomerNeed.status.not_in(STORED_STATUSES[1:])))
    return and_(unbound, CustomerNeed.status == code)


def is_untouched(need: CustomerNeed) -> bool:
    return (need.status or STATUS_NEW) == STATUS_NEW and need.opportunity_id is None and not (need.memo or "").strip()


def drop_from_activity(need: CustomerNeed, session: Session | None = None) -> None:
    """활동 기록에서 빠진 니즈: 손대지 않았으면 지우고, 아니면 나온 곳만 비운다."""
    session = session or object_session(need)
    if is_untouched(need):
        if session is not None and need.id is not None:
            session.delete(need)
        return
    need.activity = None
    need.activity_id = None


def before_activity_delete(db: Session, activity: Activity) -> None:
    """활동 기록을 지우기 전에 부른다 (같은 트랜잭션). DB 규칙(SET NULL)은 비우기만 하므로 손대지 않은 니즈는 여기서 지운다."""
    for need in list(activity.needs):
        drop_from_activity(need, db)


def mark_bound(need: CustomerNeed) -> None:
    """영업기회에 묶을 때: 새 니즈는 검토 중으로 (풀려도 검토 중으로 보인다)."""
    if (need.status or STATUS_NEW) == STATUS_NEW:
        need.status = STATUS_REVIEWING


def can_delete_need(user: User, need: CustomerNeed) -> bool:
    """작성자·관리자. 작성자가 없는 옛 줄은 나온 활동 기록의 작성자."""
    if user.role == "admin":
        return True
    if need.created_by_id is not None:
        return need.created_by_id == user.id
    return need.activity is not None and need.activity.created_by_id == user.id


def source_label(need: CustomerNeed) -> str:
    """나온 곳 한 줄: 활동 기록 날짜·제목, 없으면 "직접 입력"."""
    a = need.activity
    if a is None:
        return ontology.NEED_SOURCE_DIRECT
    when = f"{a.date.month}/{a.date.day} " if a.date else ""
    return f"{when}{a.title or '제목 없음'}"

"""활동 기록의 후속 조치·고객 니즈 줄: 고객·담당 연결 규칙과 줄 번호 기준 맞추기(reconcile).

저장 화면, 올리기 저장, 시작할 때의 복사 이관(`migrate.copy_activity_json`)이 같은 연결 규칙을 쓴다.
커밋하지 않는다 (호출자가 커밋한다)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app import opportunities
from app.ai.tasks import normalize_name
from app.models import Activity, Customer, CustomerNeed, FollowUp, User


@dataclass(frozen=True)
class LinkIndex:
    """이름 → 번호 조회. 정확히 하나로 맞을 때만 번호를 돌려준다 (포함·비슷한 이름 비교는 하지 않는다)."""

    customers: dict[str, list[int]]  # normalize_name(이름) -> 고객 번호들
    users: dict[str, list[int]]  # 이름(앞뒤 공백만 뺌, 그대로 비교) -> 활성 사용자 번호들

    def customer_id(self, name: str) -> int | None:
        key = normalize_name(name or "")
        found = self.customers.get(key, []) if key else []
        return found[0] if len(found) == 1 else None

    def user_id(self, owner: str) -> int | None:
        key = (owner or "").strip()
        found = self.users.get(key, []) if key else []
        return found[0] if len(found) == 1 else None


def load_index(executor) -> LinkIndex:
    """`executor` 는 Session 이나 Connection (execute 를 가진 것)."""
    customers: dict[str, list[int]] = {}
    for cid, name in executor.execute(select(Customer.id, Customer.name).order_by(Customer.id)):
        customers.setdefault(normalize_name(name or ""), []).append(cid)
    users: dict[str, list[int]] = {}
    for uid, name in executor.execute(select(User.id, User.name).where(User.is_active.is_(True)).order_by(User.id)):
        users.setdefault((name or "").strip(), []).append(uid)
    return LinkIndex(customers, users)


def _as_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _pair_ids(raw_ids: Sequence[str], count: int, existing: Sequence) -> list[int | None]:
    """폼에 실려 온 줄 번호(숨은 칸)를 줄마다 하나씩 짝짓는다. 번호 칸이 없는 옛 화면이 보낸 것이면 (개수가 안 맞으면)
    줄 위치로 기존 줄과 짝짓는다."""
    if len(raw_ids) == count:
        return [int(r) if r.strip().isascii() and r.strip().isdecimal() else None for r in raw_ids]
    return [existing[i].id if i < len(existing) else None for i in range(count)]


def reconcile_follow_ups(
    a: Activity, raw_ids: Sequence[str], rows: Sequence[tuple[str, str, str, bool]], index: LinkIndex,
) -> None:
    """`rows` = 올라온 줄 전체 (할 일, 담당, 기한 글자, 완료) 를 활동 기록의 후속 조치에 맞춘다.
    번호가 같은 줄은 그 행을 고치고(번호 유지), 빠진 줄은 지우고, 새 줄은 만든다. 할 일이 빈 줄은 건너뛴다.
    담당이 새 줄이거나 글이 바뀌었으면 사용자 연결을 다시 정한다 (맞는 사용자가 정확히 하나일 때만 연결)."""
    existing = list(a.follow_ups)
    by_id = {f.id: f for f in existing if f.id is not None}
    ids = _pair_ids(raw_ids, len(rows), existing)
    result: list[FollowUp] = []
    for row_id, (task, owner, due, done) in zip(ids, rows):
        if not task.strip():
            continue
        fu = by_id.pop(row_id, None) if row_id is not None else None
        if fu is None:
            fu = FollowUp(owner_user_id=index.user_id(owner))
        elif (fu.owner or "").strip() != owner.strip():
            fu.owner_user_id = index.user_id(owner)
        fu.task, fu.owner, fu.due, fu.done = task, owner, _as_day(due), done
        fu.position = len(result)
        result.append(fu)
    a.follow_ups = result


def reconcile_needs(
    a: Activity, raw_ids: Sequence[str], rows: Sequence[tuple[str, str]], index: LinkIndex,
) -> None:
    """`rows` = 올라온 줄 전체 (고객 글, 내용) 를 활동 기록의 고객 니즈에 맞춘다 (`reconcile_follow_ups` 와 같은 방식)."""
    existing = list(a.needs)
    by_id = {n.id: n for n in existing if n.id is not None}
    ids = _pair_ids(raw_ids, len(rows), existing)
    result: list[CustomerNeed] = []
    for row_id, (customer, content) in zip(ids, rows):
        if not content.strip():
            continue
        need = by_id.pop(row_id, None) if row_id is not None else None
        if need is None:
            need = CustomerNeed(customer_id=index.customer_id(customer))
        elif (need.customer_name or "").strip() != customer.strip():
            opportunities.retarget_need_customer(need, index.customer_id(customer))  # 고객이 달라지면 묶인 영업기회에서 푼다
        need.customer_name, need.content = customer, content
        need.position = len(result)
        result.append(need)
    a.needs = result

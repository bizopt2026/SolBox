"""영업 › 고객관리의 접촉 이력 (2026-11-27): 한 고객과의 접촉(활동 기록)을 날짜 최신순으로 모은다.

고객에 연결된 활동 기록(R5), 그 고객의 담당자가 참석한 활동 기록(R53), 그 고객의 니즈가 나온 활동 기록(R9·R10)을 합친다.
줄마다 그 고객의 참석 담당자와 그 고객의 영업기회만 보인다. 질의 수는 기록 수와 무관하다 (활동 번호 한 번 + 한 쪽 읽기 한 번)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Activity, Contact, Customer, CustomerNeed, Opportunity

LIMIT = 20  # 상세 화면에 보이는 줄 수 (나머지는 활동 기록 목록의 고객 걸러 보기로)


@dataclass
class Row:
    activity: Activity
    attendees: list[Contact]
    opportunities: list[Opportunity]


def _condition(customer: Customer):
    return or_(
        Activity.customers.any(Customer.id == customer.id),
        Activity.contacts.any(Contact.customer_id == customer.id),
        Activity.needs.any(CustomerNeed.customer_id == customer.id),
    )


def history(db: Session, customer: Customer, limit: int = LIMIT) -> tuple[list[Row], int]:
    """(보일 줄, 전체 건수)."""
    ids = [r[0] for r in db.execute(
        select(Activity.id).where(_condition(customer))
        .order_by(Activity.date.is_(None), Activity.date.desc(), Activity.id.desc())).all()]
    shown = ids[:limit]
    if not shown:
        return [], 0
    acts = {a.id: a for a in db.scalars(select(Activity).where(Activity.id.in_(shown))
                                       .options(selectinload(Activity.contacts), selectinload(Activity.opportunities)))}
    rows = [Row(a, [p for p in a.contacts if p.customer_id == customer.id], [o for o in a.opportunities if o.customer_id == customer.id])
            for a in (acts[i] for i in shown)]
    return rows, len(ids)

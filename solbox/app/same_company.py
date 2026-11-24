"""파트너 ↔ 고객 "같은 회사" 연결 (`partners.customer_id`): 한 파트너에 고객 하나, 한 고객에 파트너 하나.

저장된 연결은 파트너 쪽 한 열 하나뿐이고, 양쪽 화면이 이 모듈로 같은 규칙을 쓴다. 오류 문구를 돌려주면 아무것도 바꾸지 않은 것이다.
이름이 같아도 자동으로 잇지 않는다 (`candidate_*` 가 안내할 후보만 찾는다). 커밋하지 않는다."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.idparse import parse_id
from app import aliases
from app.models import Customer, Partner

NO_CUSTOMER = "존재하지 않는 고객입니다."
NO_PARTNER = "존재하지 않는 파트너입니다."


def _id(raw) -> int | None:
    return parse_id(raw)


def conflict(partner: Partner, customer: Customer) -> str:
    return f"같은 회사로 연결할 수 없습니다. 이미 연결되어 있습니다: 고객 '{customer.name}' ↔ 파트너 '{partner.name}'"


def partner_of(db: Session, customer: Customer) -> Partner | None:
    return db.scalar(select(Partner).where(Partner.customer_id == customer.id)) if customer.id is not None else None


def set_from_partner(db: Session, partner: Partner, raw) -> str | None:
    """파트너 폼의 "같은 회사인 고객" 값: 비면 연결을 풀고, 아니면 그 고객과 잇는다 (원래 연결은 바뀐다). 오류 문구 또는 None."""
    if not str(raw or "").strip():
        partner.customer_id = None
        return None
    customer = db.get(Customer, _id(raw)) if _id(raw) is not None else None
    if customer is None:
        return NO_CUSTOMER
    other = partner_of(db, customer)
    if other is not None and other is not partner:
        return conflict(other, customer)
    partner.customer_id = customer.id
    return None


def set_from_customer(db: Session, customer: Customer, raw) -> str | None:
    """고객 폼의 "같은 회사인 파트너" 값. 비면 연결을 풀고, 아니면 그 파트너와 잇는다 (원래 연결된 파트너는 풀린다)."""
    current = partner_of(db, customer)
    if not str(raw or "").strip():
        if current is not None:
            current.customer_id = None
        return None
    partner = db.get(Partner, _id(raw)) if _id(raw) is not None else None
    if partner is None:
        return NO_PARTNER
    if partner.customer_id is not None and partner.customer_id != customer.id:
        return conflict(partner, db.get(Customer, partner.customer_id))
    if current is not None and current is not partner:
        current.customer_id = None
        db.flush()  # 유일 색인: 먼저 풀고 나서 잇는다
    partner.customer_id = customer.id
    return None


def link_unlinked(db: Session, partner: Partner, customer: Customer) -> str | None:
    """안내의 한 번 누르기: 둘 다 아직 다른 곳과 이어져 있지 않을 때만 잇는다."""
    if partner.customer_id == customer.id:
        return None
    if partner.customer_id is not None:
        return conflict(partner, db.get(Customer, partner.customer_id))
    other = partner_of(db, customer)
    if other is not None:
        return conflict(other, customer)
    partner.customer_id = customer.id
    return None


def candidate_customer(db: Session, partner: Partner) -> Customer | None:
    """이름(정규화, ㈜·(주)·주식회사 무시)이 같고 아직 파트너와 이어지지 않은 고객이 정확히 하나일 때 그 고객 (이미 이어진 파트너에는 없다)."""
    key = aliases.keys_of(partner, True)  # 이름과 다른 이름 (베타 AE)
    if partner.customer_id is not None or not key:
        return None
    linked = set(db.scalars(select(Partner.customer_id).where(Partner.customer_id.is_not(None))))
    found = [c for c in db.scalars(select(Customer).order_by(Customer.id)) if c.id not in linked and aliases.keys_of(c, True) & key]
    return found[0] if len(found) == 1 else None


def candidate_partner(db: Session, customer: Customer) -> Partner | None:
    key = aliases.keys_of(customer, True)
    if not key or partner_of(db, customer) is not None:
        return None
    found = [p for p in db.scalars(select(Partner).order_by(Partner.id)) if p.customer_id is None and aliases.keys_of(p, True) & key]
    return found[0] if len(found) == 1 else None


def customer_choices(db: Session, partner: Partner | None) -> list[dict]:
    """파트너 폼의 고객 선택지: (고객, 이미 다른 파트너와 이어졌으면 그 이름)."""
    taken = {p.customer_id: p for p in db.scalars(select(Partner).where(Partner.customer_id.is_not(None)))}
    return [{"id": c.id, "name": c.name, "taken_by": (taken[c.id].name if c.id in taken and taken[c.id] is not partner else "")}
            for c in db.scalars(select(Customer).order_by(Customer.name, Customer.id))]


def partner_choices(db: Session, customer: Customer | None) -> list[dict]:
    names = {c.id: c.name for c in db.scalars(select(Customer))}
    return [{"id": p.id, "name": p.name,
             "taken_by": names.get(p.customer_id, "") if p.customer_id is not None and (customer is None or p.customer_id != customer.id) else ""}
            for p in db.scalars(select(Partner).order_by(Partner.name, Partner.id))]

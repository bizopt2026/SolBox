"""담당자 변경 제안 (담당자 관리 부담 줄이기 ④, 2026-11-27).

명함·올리기에서 같은 사람으로 걸린 사람의 바뀐 직함·휴대폰·이메일을 제안으로 올리고, 사람이 [반영]·[무시] 한다 (올리기는 기존 기록을 직접 고치지 않는다).
이름만 같고 휴대폰·이메일이 모두 다른 새 명함은 `person` 제안: [같은 사람: 정보 반영] / [다른 사람: 새 담당자로 추가].
- 열린 제안은 (담당자, 칸, 정규화한 새 값)마다 하나.
- 반영할 때 지금 값이 제안 때 값(`old_value`)과 다르면 반영하지 않고 `changed` 로 닫는다.
- 직함은 바꾸고, 이메일은 덧붙이고, 휴대폰은 바꾸되 옛 번호는 메모 끝에 "이전 휴대폰 …" 으로 남긴다."""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import contacts as ct
from app import ontology
from app.models import Contact, ContactSuggestion

APPLIED, DISMISSED, CHANGED = "applied", "dismissed", "changed"


def _norm(field: str, value: str) -> str:
    if field == "phone":
        return ct.digits(value)
    return re.sub(r"\s+", "", value or "").lower()


def _emails(text: str) -> list[str]:
    return [e.lower() for e in ct.EMAIL_RE.findall(text or "")]


def _exists(db: Session, contact: Contact, field: str, new_value: str) -> bool:
    key = _norm(field, new_value)
    rows = db.scalars(select(ContactSuggestion).where(ContactSuggestion.contact_id == contact.id, ContactSuggestion.field == field,
                                                      ContactSuggestion.state == "pending"))
    return any(_norm(field, s.new_value) == key for s in rows)


def _add(db: Session, contact: Contact, field: str, old: str, new: str, source: tuple[str, int | None], payload: dict | None = None) -> bool:
    if _exists(db, contact, field, new):
        return False
    db.add(ContactSuggestion(contact_id=contact.id, field=field, old_value=old or "", new_value=new, payload=payload or {},
                             source_type=source[0], source_id=source[1]))
    db.flush()
    return True


def propose_changes(db: Session, contact: Contact, incoming: dict, source: tuple[str, int | None]) -> int:
    """같은 사람의 들어온 값(`contacts.from_intake` 모양)과 지금 값을 견줘 제안을 만든다. 만든 수."""
    n = 0
    title = (incoming.get("title") or "").strip()
    if title and contact.title and _norm("title", title) != _norm("title", contact.title):
        n += _add(db, contact, "title", contact.title, title, source)
    phone = (incoming.get("phone") or "").strip()
    if phone and ct.digits(phone) != ct.digits(contact.phone):
        n += _add(db, contact, "phone", contact.phone, phone, source)
    have = set(_emails(contact.email))
    new = next((e for e in ct.EMAIL_RE.findall(incoming.get("email") or "") if e.lower() not in have), "")
    if new:
        n += _add(db, contact, "email", contact.email, new, source)
    return n


def same_name(company, name: str) -> Contact | None:
    """같은 회사에 이름(공백 무시)이 같은 담당자 (첫 사람)."""
    key = re.sub(r"\s+", "", name or "").lower()
    return next((c for c in company.contacts if key and re.sub(r"\s+", "", c.name or "").lower() == key), None)


def propose_person(db: Session, contact: Contact, incoming: dict, source: tuple[str, int | None]) -> bool:
    """이름만 같고 휴대폰·이메일이 모두 다른 새 명함: 담당자를 만들지 않고 묻는다. 새 값은 휴대폰·이메일 요약 (같은 요약의 열린 제안이 있으면 만들지 않는다)."""
    summary = " · ".join(x for x in (incoming.get("title", ""), incoming.get("phone", ""), incoming.get("office_phone", ""), incoming.get("email", "")) if x)
    return _add(db, contact, "person", "", summary, source, payload={f: incoming.get(f, "") for f in ct.FIELDS})


def pending(db: Session) -> list[ContactSuggestion]:
    return list(db.scalars(select(ContactSuggestion).where(ContactSuggestion.state == "pending").order_by(ContactSuggestion.id.desc())))


def _close(s: ContactSuggestion, state: str, user) -> str:
    s.state, s.decided_at, s.decided_by_id = state, datetime.now(), user.id
    return state


def _set_field(c: Contact, field: str, value: str) -> None:
    if field == "title":
        c.title = value
    elif field == "email":
        c.email = ", ".join(x for x in (c.email.strip(), value) if x)
    elif field == "phone":
        if c.phone.strip():
            c.note = "\n".join(x for x in (c.note.strip(), f"이전 휴대폰 {c.phone.strip()}") if x)
        c.phone = value


def apply(db: Session, s: ContactSuggestion, user) -> str:
    """[반영] (person 은 [같은 사람]). 닫힌 상태를 돌려준다: applied / changed(그사이 바뀜) / 이미 닫혔으면 그 상태."""
    if s.state != "pending":
        return s.state
    c = s.contact
    if s.field == "person":
        incoming = s.payload or {}
        propose = {"title": incoming.get("title", ""), "phone": incoming.get("phone", ""), "email": incoming.get("email", "")}
        if propose["title"]:
            c.title = propose["title"]
        if propose["phone"] and ct.digits(propose["phone"]) != ct.digits(c.phone):
            _set_field(c, "phone", propose["phone"])
        for e in ct.EMAIL_RE.findall(propose["email"]):
            if e.lower() not in _emails(c.email):
                _set_field(c, "email", e)
        return _close(s, APPLIED, user)
    current = getattr(c, s.field) or ""
    if current.strip() != (s.old_value or "").strip():
        return _close(s, CHANGED, user)
    _set_field(c, s.field, s.new_value)
    return _close(s, APPLIED, user)


def apply_as_new(db: Session, s: ContactSuggestion, user) -> Contact | None:
    """person 제안의 [다른 사람]: 들어온 값으로 같은 회사에 새 담당자를 만든다 (유래 = 제안의 출처)."""
    if s.state != "pending" or s.field != "person":
        return None
    old = s.contact
    fields = {f: str((s.payload or {}).get(f, "")) for f in ct.FIELDS}
    fields["name"] = fields["name"] or old.name
    person = Contact(**fields, created_by_id=user.id, origin_type=s.source_type or ontology.ORIGIN_MANUAL, origin_id=s.source_id)
    setattr(person, old.company_type, old.company)
    db.add(person)
    _close(s, APPLIED, user)
    db.flush()
    return person


def dismiss(db: Session, s: ContactSuggestion, user) -> str:
    return s.state if s.state != "pending" else _close(s, DISMISSED, user)


def text(s: ContactSuggestion) -> str:
    """AI 제안함·상세의 한 줄: "직함: 차장 → 부장" / "같은 이름의 새 명함: 010-…"."""
    label = ontology.CONTACT_SUGGESTION_FIELDS.get(s.field, s.field)
    if s.field == "person":
        return f"{label}: {s.new_value} — 같은 사람인가요?"
    if s.field == "email":
        return f"{label} 더하기: {s.new_value}"
    return f"{label}: {s.old_value or '없음'} → {s.new_value}"

"""담당자 관리 부담 줄이기: 데이터 (R53 활동 기록 ↔ 담당자, 변경 제안 표)."""
from sqlalchemy import select

from app import ontology
from app.models import Activity, Contact, ContactSuggestion, Customer


def _world(db):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    p = Contact(name="최현수", customer=c)
    a = Activity(title="회의", raw_text="r")
    db.add_all([p, a])
    db.commit()
    a.contacts = [p]
    db.add(ContactSuggestion(contact_id=p.id, field="title", old_value="차장", new_value="부장", source_type="intake", source_id=1))
    db.commit()
    return c, p, a


def test_codes_and_relation():
    assert ontology.CONTACT_SUGGESTION_FIELDS["title"] == "직함" and "person" in ontology.CONTACT_SUGGESTION_FIELDS
    assert next(r for r in ontology.RELATIONS if r.key == "R53").physical == "activity_contacts"


def test_deleting_activity_keeps_contact(db):
    c, p, a = _world(db)
    db.delete(a)
    db.commit()
    db.refresh(p)
    assert p.activities == [] and db.get(Contact, p.id) is not None


def test_deleting_contact_removes_links_and_suggestions(db):
    c, p, a = _world(db)
    assert db.scalar(select(ContactSuggestion)).state == "pending"
    db.delete(p)
    db.commit()
    db.refresh(a)
    assert a.contacts == [] and db.scalar(select(ContactSuggestion)) is None

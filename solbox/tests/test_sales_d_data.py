"""영업 D 단계 데이터: 담당자 모델·역할 코드·관계·합치기·지우기."""
from sqlalchemy import select

from app import merge, ontology
from app.models import Contact, Customer, Opportunity, Partner, opportunity_contacts
from tests.conftest import make_user


def _c(db, name="한빛"):
    c = Customer(name=name)
    db.add(c)
    db.commit()
    return c


def test_roles_and_type():
    assert ontology.CONTACT_ROLES == {"decision": "의사결정자", "user": "실무자", "influencer": "영향자", "other": "기타"}
    t = ontology.TYPES["contact"]
    assert (t.label, t.table, t.detail_url) == ("담당자", "contacts", "/contacts/{id}")
    assert [ontology.prop("contact", k) for k in ("name", "title", "role", "phone", "office_phone", "email", "note")] == [
        "이름", "직함", "역할", "휴대폰", "사무실 전화", "이메일", "메모"]
    assert ontology.prop("customer", "contact") == "연락처 메모" and ontology.prop("partner", "contact") == "연락처 메모"


def test_contact_belongs_to_customer_or_partner_and_links_opportunity(db):
    c = _c(db)
    p = Partner(name="파트너")
    db.add(p)
    db.commit()
    a = Contact(name="최현수", customer_id=c.id, role="decision")
    b = Contact(name="박기사", partner_id=p.id)
    db.add_all([a, b])
    db.commit()
    o = Opportunity(name="건", customer_id=c.id)
    o.contacts = [a]
    db.add(o)
    db.commit()
    db.refresh(c)
    assert [x.name for x in c.contacts] == ["최현수"] and [x.name for x in p.contacts] == ["박기사"] and a.opportunities == [o]
    assert a.company is c and b.company is p


def test_deleting_customer_deletes_contacts_and_links(db):
    c = _c(db)
    a = Contact(name="최현수", customer_id=c.id)
    db.add(a)
    db.commit()
    o = Opportunity(name="건", customer_id=c.id)
    o.contacts = [a]
    db.add(o)
    db.commit()
    db.delete(o)
    db.commit()
    db.delete(db.get(Customer, c.id))
    db.commit()
    assert db.scalar(select(Contact)) is None and db.execute(select(opportunity_contacts)).first() is None


def test_merge_moves_contacts(db, settings):
    keep, gone = _c(db, "한빛"), _c(db, "한빛물류")
    db.add(Contact(name="최현수", customer_id=gone.id))
    db.commit()
    assert ("담당자", "R50", 1) in merge.link_counts(db, "customer", gone)
    user = make_user(db, "adm", role="admin")
    merge.execute(db, "customer", keep.id, gone.id, user, settings)
    db.expire_all()
    assert [x.customer_id for x in db.scalars(select(Contact))] == [keep.id]

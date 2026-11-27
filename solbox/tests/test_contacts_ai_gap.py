"""담당자 관리 부담 줄이기 ⑤: 의사결정자 접촉 공백."""
from datetime import date, datetime, timedelta

from app import opportunities as opps
from app import opportunity_ai
from app.models import Activity, Contact, Customer, Opportunity


def _world(db, stage="negotiating", met_days_ago=None, made_days_ago=0):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    boss = Contact(name="최현수", role="decision", customer=c, created_at=datetime.now() - timedelta(days=made_days_ago))
    staff = Contact(name="이정민", role="user", customer=c, created_at=datetime.now() - timedelta(days=200))
    o = Opportunity(name="태양광", customer_id=c.id, stage=stage)
    db.add_all([boss, staff, o])
    db.commit()
    o.contacts = [boss, staff]
    if met_days_ago is not None:
        a = Activity(title="회의", raw_text="r", date=date.today() - timedelta(days=met_days_ago))
        db.add(a)
        db.commit()
        a.contacts = [boss]
    db.commit()
    return o, boss


def test_gap_boundary(db):
    o, boss = _world(db, met_days_ago=30)
    assert opps.decision_gaps(db, o, date.today()) == []
    o2, boss2 = _world(db, met_days_ago=31)
    assert opps.decision_gaps(db, o2, date.today()) == [(boss2, 31)]


def test_no_attendance_counts_from_contact_creation_and_only_decision_role(db):
    o, boss = _world(db, made_days_ago=45)
    assert [(c.name, d) for c, d in opps.decision_gaps(db, o, date.today())] == [("최현수", 45)]


def test_closed_opportunity_has_no_gap(db):
    o, boss = _world(db, stage="won", made_days_ago=90)
    assert opps.decision_gaps(db, o, date.today()) == []


def test_detail_brief_line_and_prompt(user_client, db):
    o, boss = _world(db, met_days_ago=41)
    page = user_client.get(f"/opportunities/{o.id}").text
    assert "최현수</a>(의사결정자) 마지막 접촉 41일 전" in page
    prompt, _ = opportunity_ai.build_prompt(db, o, date.today())
    assert "최현수 의사결정자 (마지막 접촉 41일 전)" in prompt and "이정민 실무자 (참석 기록 없음)" in prompt

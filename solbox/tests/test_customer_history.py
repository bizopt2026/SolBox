"""영업 › 고객관리: 고객 상세의 접촉 이력 (2026-11-27). 고객에 연결된 활동·참석 담당자·그 고객의 니즈가 나온 활동을 날짜 최신순으로."""
from datetime import date

from app import contact_history
from app.models import Activity, Contact, Customer, CustomerNeed, Opportunity


def _world(db):
    c = Customer(name="가나제지"); other = Customer(name="다라물류"); db.add_all([c, other]); db.flush()
    kim = Contact(customer_id=c.id, name="김공장장", role="decision"); lee = Contact(customer_id=other.id, name="이과장")
    o = Opportunity(name="가나 태양광", customer_id=c.id); db.add_all([kim, lee, o]); db.flush()
    a1 = Activity(kind="meeting", date=date(2026, 11, 1), title="킥오프", raw_text="r", customers=[c], contacts=[kim, lee], opportunities=[o])
    a2 = Activity(kind="tapping", date=date(2026, 11, 20), title="방문", raw_text="r", contacts=[kim])  # 참석자로만 이어짐
    a3 = Activity(kind="meeting", date=None, title="날짜 없음", raw_text="r")
    a4 = Activity(kind="meeting", date=date(2026, 10, 1), title="다른 고객", raw_text="r", customers=[other])
    db.add_all([a1, a2, a3, a4]); db.flush()
    db.add(CustomerNeed(activity_id=a3.id, customer_name=c.name, customer_id=c.id, content="전기요금"))  # 니즈로만 이어짐
    db.commit()
    return c, kim, o, (a1, a2, a3, a4)


def test_history_collects_linked_attended_and_need_activities(db):
    c, kim, o, (a1, a2, a3, a4) = _world(db)
    rows, total = contact_history.history(db, c)
    assert [r.activity.id for r in rows] == [a2.id, a1.id, a3.id] and total == 3  # 날짜 최신순, 날짜 없는 것은 뒤
    first = rows[1]
    assert [p.name for p in first.attendees] == ["김공장장"]  # 다른 고객의 담당자는 빼고
    assert [x.name for x in first.opportunities] == ["가나 태양광"]


def test_history_limit_and_customer_page(user_client, db):
    c, kim, o, acts = _world(db)
    rows, total = contact_history.history(db, c, limit=1)
    assert len(rows) == 1 and total == 3
    page = user_client.get(f"/customers/{c.id}").text
    assert "접촉 이력" in page and "김공장장" in page and "방문" in page and "고객 태핑" in page
    assert f"/activities/new?customer_id={c.id}" in page
    assert page.index("접촉 이력") < page.index("rel-R27-opportunity")  # 연결 카드보다 위

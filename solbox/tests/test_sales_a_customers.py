"""영업 A 단계: 고객 목록·상세의 마지막 접촉과 진행 중 영업기회."""
import re
from datetime import date, timedelta

from app.models import Activity, Customer, Opportunity

TODAY = date.today()


def _customer(db, name, days_ago=None):
    c = Customer(name=name)
    db.add(c)
    db.commit()
    if days_ago is not None:
        a = Activity(kind="meeting", date=TODAY - timedelta(days=days_ago), title=f"{name} 회의", raw_text="r")
        a.customers = [c]
        db.add(a)
        db.commit()
    return c


def _row(page, name):
    return re.search(rf"<tr>(?:(?!</tr>).)*>{name}</a>.*?</tr>", page, re.S).group(0)


def test_last_contact_column_with_days_and_warning(user_client, db):
    _customer(db, "최근고객", 24)
    _customer(db, "경계고객", 90)
    _customer(db, "오랜고객", 91)
    _customer(db, "기록없음")
    page = user_client.get("/customers").text
    assert "마지막 접촉" in page.split("<thead>")[1].split("</thead>")[0]
    assert "24일 전" in _row(page, "최근고객") and "tone-warn" not in _row(page, "최근고객")
    assert "tone-warn" not in _row(page, "경계고객")
    assert "91일 전" in _row(page, "오랜고객") and "tone-warn" in _row(page, "오랜고객")
    assert "기록 없음" in _row(page, "기록없음")


def test_quiet_chip_keeps_old_and_never_contacted(user_client, db):
    _customer(db, "최근고객", 24)
    _customer(db, "오랜고객", 120)
    _customer(db, "기록없음")
    page = user_client.get("/customers?quiet=1").text
    assert "오랜고객" in page and "기록없음" in page and "최근고객" not in page
    assert "90일 넘게 연락 없음" in user_client.get("/customers").text


def test_quiet_sort_oldest_first_and_none_first(user_client, db):
    _customer(db, "최근고객", 3)
    _customer(db, "오랜고객", 120)
    _customer(db, "기록없음")
    page = user_client.get("/customers?sort=quiet").text
    assert page.index(">기록없음</a>") < page.index(">오랜고객</a>") < page.index(">최근고객</a>")


def test_open_opportunity_column_and_detail_line(user_client, db):
    c = _customer(db, "한빛물류", 24)
    db.add_all([Opportunity(name="a", customer_id=c.id, stage="proposing", amount=300_000_000),
                Opportunity(name="b", customer_id=c.id, stage="discovery", amount=100_000_000),
                Opportunity(name="c", customer_id=c.id, stage="won", amount=900_000_000)])
    db.commit()
    page = user_client.get("/customers").text
    assert "2건 · 4억" in _row(page, "한빛물류")
    detail = user_client.get(f"/customers/{c.id}").text
    assert "마지막 접촉 24일 전" in detail and "진행 중 영업기회 2건 (합계 4억)" in detail


def test_detail_line_is_quiet_without_contact_or_open_opportunities(user_client, db):
    c = _customer(db, "새고객")
    assert 'class="contact-line' not in user_client.get(f"/customers/{c.id}").text

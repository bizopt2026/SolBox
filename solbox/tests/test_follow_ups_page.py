"""후속 조치 목록(GET /follow-ups): 구분 탭 건수, 걸러 보기, 권한, 줄 내용, 홈·고객 연결에서 오는 길."""
import re
from datetime import date, timedelta

import pytest

from app import ontology
from app.models import Activity, Customer, FollowUp
from tests.conftest import fu_rows, make_user
from tests import relfix

TODAY = date.today()


def iso(days):
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture
def fus(db):
    kim = make_user(db, "kim", name="김과장")
    lee = make_user(db, "lee", name="이대리")
    c1, c2 = Customer(name="한빛병원"), Customer(name="대성물류")
    db.add_all([c1, c2])
    db.flush()
    a1 = Activity(raw_text="x", title="한빛 킥오프", date=TODAY, follow_ups=fu_rows(
        ("견적 보내기", "김과장", iso(-3), False), ("자료 수령", "이대리", iso(1), False), ("미팅록 정리", "김과장", iso(-10), True), ("기한 없는 일", "", "", False)))
    a1.follow_ups[0].owner_user_id = kim.id
    a1.follow_ups[1].owner_user_id = lee.id
    a1.customers = [c1]
    a2 = Activity(raw_text="x", title="대성 태핑", date=TODAY, follow_ups=fu_rows(("현장 방문", "", iso(5), False), ("먼 훗날", "", iso(90), False)))
    a2.customers = [c2]
    db.add_all([a1, a2])
    db.commit()
    return type("W", (), dict(kim=kim, lee=lee, c1=c1, c2=c2, a1=a1, a2=a2))


def login(app, username):
    from fastapi.testclient import TestClient
    c = TestClient(app)
    assert c.post("/login", data={"username": username, "password": "pw1234"}, follow_redirects=False).status_code == 303
    return c


def tasks(html):
    return re.findall(r'<span class="fu-task">([^<]*)</span>', html)


def test_requires_login(client):
    r = client.get("/follow-ups", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_default_is_open_with_counts(app, fus):
    c = login(app, "kim")
    html = c.get("/follow-ups").text
    assert tasks(html) == ["견적 보내기", "자료 수령", "현장 방문", "먼 훗날", "기한 없는 일"]  # 기한순, 기한 없는 것은 맨 뒤
    seg = re.search(r'<nav class="seg".*?</nav>', html, re.S).group(0)
    assert re.search(r'aria-current="page">남은 일<b>5</b>', seg)
    for text, n in (("기한 지남", 1), ("완료", 1)):
        assert re.search(rf"{text}\s*<b[^>]*>{n}</b>", seg), (text, seg)
    assert 'class="count">5건' in html


def test_overdue_and_done_tabs(app, fus):
    c = login(app, "kim")
    assert tasks(c.get("/follow-ups?status=overdue").text) == ["견적 보내기"]
    done = c.get("/follow-ups?status=done").text
    assert tasks(done) == ["미팅록 정리"] and 'class="check on"' in done
    assert done.count('name="status"') == 1  # 검색 칸이 구분을 이어 간다 (다른 폼에는 없다)
    assert tasks(c.get("/follow-ups?status=bogus").text)[0] == "견적 보내기"  # 모르는 값은 남은 일


def test_row_content(app, fus):
    html = login(app, "kim").get("/follow-ups").text
    assert f'action="/follow-ups/{fus.a1.follow_ups[0].id}/toggle"' in html
    assert "3일 지남" in html and 'class="due late"' in html
    assert "내일" in html and "한빛병원" in html and "김과장" in html
    assert f'href="/activities/{fus.a1.id}"' in html and "한빛 킥오프" in html
    assert f'href="/customers/{fus.c1.id}"' in html


def test_customer_filter_and_counts_follow_filter(app, fus):
    c = login(app, "kim")
    html = c.get(f"/follow-ups?customer_id={fus.c2.id}").text
    assert tasks(html) == ["현장 방문", "먼 훗날"]
    assert "대성물류" in html and 'class="count">2건' in html
    assert 'class="chip chip-x filter-note" href="/follow-ups"' in html
    seg = re.search(r'<nav class="seg".*?</nav>', html, re.S).group(0)
    assert f"customer_id={fus.c2.id}" in seg  # 탭 링크가 걸러 보기를 지킨다
    assert tasks(c.get("/follow-ups?customer_id=999999").text) == [] and tasks(c.get("/follow-ups?customer_id=abc").text) == []


def test_owner_me_filter(app, fus):
    c = login(app, "kim")
    html = c.get("/follow-ups?owner=me").text
    assert tasks(html) == ["견적 보내기"] and "내 담당" in html
    assert tasks(login(app, "lee").get("/follow-ups?owner=me").text) == ["자료 수령"]
    assert tasks(c.get("/follow-ups?owner=me&status=done").text) == []
    assert "owner=me" in re.search(r'<nav class="seg".*?</nav>', html, re.S).group(0)


def test_done_filter_with_customer_includes_done_ones(app, fus):
    assert tasks(login(app, "kim").get(f"/follow-ups?status=done&customer_id={fus.c1.id}").text) == ["미팅록 정리"]


def test_empty_states(app, db):
    make_user(db, "kim")
    c = login(app, "kim")
    empty = re.search(r'<div class="empty">.*?</div>', c.get("/follow-ups").text, re.S).group(0)
    assert "후속 조치" in empty
    html = c.get("/follow-ups?status=overdue").text
    assert "조건에 맞는 항목이 없습니다." in html and "조건 지우기" in html and 'class="empty"' not in html


def test_toggle_returns_to_filtered_list(app, fus):
    c = login(app, "kim")
    fid = fus.a1.follow_ups[0].id
    r = c.post(f"/follow-ups/{fid}/toggle", headers={"referer": "http://testserver/follow-ups?owner=me"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/follow-ups?owner=me&fu_toggled={fid}"  # 걸러 보기는 그대로, 안내 번호가 붙는다
    assert sorted(tasks(c.get("/follow-ups?status=done").text)) == ["견적 보내기", "미팅록 정리"]


def test_pagination(app, db):
    make_user(db, "kim")
    db.add(Activity(raw_text="x", title="많다", follow_ups=fu_rows(*[(f"일{i:03d}", "", iso(i), False) for i in range(60)])))
    db.commit()
    html = login(app, "kim").get("/follow-ups?page=2").text
    assert len(tasks(html)) == 10 and "51–60 / 전체 60건" in html
    assert "status=open" in html and "page=1" in html


def test_registered_in_ontology_and_customer_more_link(app, db):
    assert ontology.TYPES["follow_up"].list_url == "/follow-ups"
    w = relfix.build(db, owner=make_user(db, "kim", name="kim"))
    from app import relations
    groups = relations.related(db, w.c_hanbit, limit=1)
    g = next(g for g in groups if g.key == "D6")
    assert g.hidden >= 1 and g.more_url == f"/follow-ups?customer_id={w.c_hanbit.id}"

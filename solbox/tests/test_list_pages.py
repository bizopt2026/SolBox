"""목록 화면 공통 모양: 구분 탭 건수 = 목록 건수, 탭 링크가 조건을 지킴, 연결 칸, 빈 화면 변형, 쪽 범위, 관리 목록."""
import re
from datetime import date, datetime

import pytest

from app.models import AIJob, Intake, ResearchRun
from tests import relfix
from tests.conftest import make_user


@pytest.fixture
def world(db, user_client):
    return relfix.build(db, owner=make_user(db, "owner1", name="owner1"))


def seg(html):
    nav = re.search(r'<nav class="seg".*?</nav>', html, re.S).group(0)
    return re.findall(r'<a href="([^"]*)"( aria-current="page")?>([^<]*)<b>(\d+)</b>', nav)


def rows(html):
    return max(0, html.count("<tr>") - 1)


@pytest.mark.parametrize("url,param", [
    ("/solutions", "stage"), ("/opportunities", "stage"), ("/proposals", "status"), ("/activities", "kind"),
])
def test_tab_counts_equal_list_totals(user_client, world, url, param):
    tabs = seg(user_client.get(url).text)
    assert tabs[0][3] == str(rows(user_client.get(url).text))  # 전체
    for href, current, label, n in tabs:
        page = user_client.get(href).text
        assert int(n) == rows(page), (url, label)
        assert (' aria-current="page"' in current) == (page.count('aria-current="page"') >= 1 and bool(current))
    assert sum(1 for t in tabs if t[1]) == 1


def test_opportunity_tabs_open_is_sum_of_open_stages(user_client, world):
    tabs = {t[2]: int(t[3]) for t in seg(user_client.get("/opportunities").text)}
    assert tabs["진행 중"] == tabs["발굴"] + tabs["제안 중"] + tabs["협상"]


def test_tab_links_keep_search_and_filters(user_client, world):
    html = user_client.get(f"/activities?customer_id={world.c_daesung.id}&q=대성&page=1").text
    for href, _c, label, n in seg(html):
        assert f"customer_id={world.c_daesung.id}" in href and "q=%EB%8C%80%EC%84%B1" in href and "page=" not in href
    assert int(seg(html)[0][3]) == rows(html) == 2
    # 검색 칸은 지금의 구분·걸러 보기를 숨은 칸으로 들고 간다
    html = user_client.get("/solutions?stage=boxed&q=AI").text
    form = re.search(r'<form class="search tool-search".*?</form>', html, re.S).group(0)
    assert 'name="stage" value="boxed"' in form and 'for="list-q"' in form and 'class="sr-only"' in form and 'name="page"' not in form


def test_counts_follow_search_text(user_client, world):
    all_n = int(seg(user_client.get("/solutions").text)[0][3])
    found = seg(user_client.get("/solutions?q=공조").text)
    assert int(found[0][3]) < all_n and int(found[0][3]) == rows(user_client.get("/solutions?q=공조").text)


def test_filter_chip_is_removable(user_client, world):
    html = user_client.get(f"/activities?customer_id={world.c_hanbit.id}").text
    chip = re.search(r'<a class="chip chip-x filter-note" href="([^"]*)".*?</a>', html, re.S)
    assert chip.group(1) == "/activities" and "한빛병원" in chip.group(0) and "걸러 보기 해제" in chip.group(0)


def test_connection_column_has_real_counts(user_client, world):
    html = user_client.get("/solutions").text
    row = re.search(rf'<tr>\s*<td class="c-title"><a href="/solutions/{world.s_hvac.id}".*?</tr>', html, re.S).group(0)
    assert "고객 1 · 제안 1 · 영업기회 1" in row and "11월 14일" in row  # 최근 활동 = 연결된 활동 중 가장 늦은 일자
    html = user_client.get("/customers").text
    row = re.search(rf'<tr>\s*<td class="c-title"><a href="/customers/{world.c_hanbit.id}".*?</tr>', html, re.S).group(0)
    assert "프로젝트 1 · 영업기회 1 · 제안 1" in row
    assert "솔루션 2" in user_client.get("/partners").text
    html = user_client.get("/bms").text
    assert "솔루션 3 · 프로젝트 1 · 영업기회 1" in html
    assert "<th class=\"col-sec\">수정일</th>" in html and "최근 활동" not in html  # 연결된 활동이 없는 유형은 수정일


def test_titles_have_secondary_text_and_tags(user_client, world, db):
    world.s_hvac.summary, world.s_hvac.tags = "냉난방 자동 제어", "에너지,공조"
    db.commit()
    html = user_client.get("/solutions").text
    assert "냉난방 자동 제어" in html and '<span class="tag">에너지</span>' in html
    assert "한빛병원" in user_client.get("/proposals").text and "한빛병원" in user_client.get("/projects").text


def test_empty_states_variants(user_client, db):
    for url, text in (("/customers", "아직 등록된 고객이 없습니다."), ("/solutions", "아직 등록된 솔루션이 없습니다.")):
        html = user_client.get(url).text
        assert text in html and 'class="empty"' in html and "조건 지우기" not in html
        filtered = user_client.get(url + "?q=없는말").text
        assert "조건에 맞는 항목이 없습니다." in filtered and "조건 지우기" in filtered and 'class="empty"' not in filtered and text not in filtered
    html = user_client.get("/solutions?stage=boxed").text
    assert "조건 지우기" in html and 'href="/solutions"' in html


def test_pager_shows_range(user_client, db):
    from app.models import Customer
    db.add_all([Customer(name=f"고객{i:03d}") for i in range(120)])
    db.commit()
    html = user_client.get("/customers?page=3").text
    assert "101–120 / 전체 120건" in html and "3 / 3" in html


def test_intake_tabs_and_research_tabs(user_client, db):
    kim = make_user(db, "iu")
    for st in ("ready", "ready", "applied"):
        db.add(Intake(note="n", status=st, created_by_id=1))
    db.commit()
    from app.models import User
    mine = db.query(User).filter_by(username="kim").one()
    db.query(Intake).update({Intake.created_by_id: mine.id})
    db.commit()
    tabs = {t[2]: int(t[3]) for t in seg(user_client.get("/intake").text)}
    assert tabs["전체"] == 3 and tabs["확인 대기"] == 2 and tabs["저장 완료"] == 1
    assert rows(user_client.get("/intake?status=ready").text) == 2
    assert [t[2] for t in seg(user_client.get("/research").text)][:1] == ["전체"]


def test_admin_lists(admin_client, db):
    db.add_all([AIJob(kind="x", status="done", input={}), AIJob(kind="x", status="failed", input={}, error_kind="auth"), AIJob(kind="x", status="done", input={})])
    db.commit()
    html = admin_client.get("/admin/jobs").text
    tabs = {t[2]: int(t[3]) for t in seg(html)}
    assert tabs["전체"] == 3 and tabs["완료"] == 2 and tabs["실패"] == 1
    assert rows(admin_client.get("/admin/jobs?status=done").text) == 2 and 'data-code="failed"' in html
    users = admin_client.get("/admin/users").text
    assert {t[2] for t in seg(users)} >= {"전체", "관리자"} and "로그인 계정" in users


def test_search_results_use_row_style_and_stage_pills(user_client, world):
    html = user_client.get("/search?q=공조").text
    assert 'class="rr-main"' in html and 'data-code="boxed"' in html

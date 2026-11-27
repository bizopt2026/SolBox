"""현재 위치 표시: 상단 메뉴의 aria-current 와 화면 제목 위 경로."""
import re

from app.models import Customer, Partner, ResearchRun, Solution
from tests.conftest import refining_proposal


def _nav(html):
    return re.search(r'<aside class="side".*?</aside>', html, re.S).group(0)


def test_top_menu_marks_current_section(user_client):
    assert 'href="/" aria-current="page"' in _nav(user_client.get("/").text)
    nav = _nav(user_client.get("/solutions").text)
    assert 'href="/solutions" aria-current="page"' in nav
    assert nav.count("aria-current") == 1


def test_detail_page_belongs_to_its_section(user_client, db):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    nav = _nav(user_client.get(f"/customers/{c.id}").text)
    assert 'href="/customers" aria-current="page"' in nav


def test_admin_menu_is_current_under_admin(admin_client):
    html = admin_client.get("/admin/research").text
    assert 'aria-label="관리"' in html
    assert 'href="/admin/research" aria-current="page"' in html


def _crumbs(html):
    m = re.search(r'<nav class="breadcrumb" aria-label="경로">(.*?)</nav>', html, re.S)
    assert m, "경로가 없다"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()


def test_breadcrumbs_on_detail_and_form_pages(user_client, db):
    s = Solution(name="(예시) AI 공조", created_by_id=1)
    db.add(s)
    db.commit()
    html = user_client.get(f"/solutions/{s.id}").text
    assert _crumbs(html) == "소스 › 솔루션 › (예시) AI 공조"
    assert '<a href="/solutions">솔루션</a>' in html
    assert "&larr; 목록" not in html
    assert _crumbs(user_client.get(f"/solutions/{s.id}/edit").text) == "소스 › 솔루션 › (예시) AI 공조 › 수정"
    assert _crumbs(user_client.get("/solutions/new").text) == "소스 › 솔루션 › 등록"
    assert _crumbs(user_client.get("/customers/new").text) == "영업 › 고객관리 › 등록"
    assert _crumbs(user_client.get("/bms/new").text) == "사업기획 › 기존 BM › 등록"
    assert _crumbs(user_client.get("/bms/new?kind=new").text) == "사업기획 › 신규 BM › 등록"
    assert _crumbs(user_client.get("/activities/new").text) == "활동 기록 › 등록"  # 위쪽 줄 탭 묶음은 묶음 이름 없이


def test_breadcrumb_escapes_names(user_client, db):
    p = Partner(name="<b>x</b>")
    db.add(p)
    db.commit()
    html = user_client.get(f"/partners/{p.id}").text
    assert "<b>x</b>" not in _crumbs(html) and "&lt;b&gt;" in html


def test_list_pages_have_no_breadcrumb(user_client):
    assert "breadcrumb" not in user_client.get("/solutions").text


def test_proposal_outline_research_and_admin_breadcrumbs(admin_client, db):
    assert _crumbs(admin_client.get("/admin/research").text) == "관리 › 리서치 설정"
    assert _crumbs(admin_client.get("/admin/users").text) == "관리 › 사용자"
    run = ResearchRun(status="done", trigger="manual")
    db.add(run)
    db.commit()
    day = f"{run.started_at.month}월 {run.started_at.day}일"  # 화면의 날짜 모양 하나 (올해는 연도 없이)
    assert _crumbs(admin_client.get(f"/research/runs/{run.id}").text) == f"소스 › 리서치 › {day}"


def test_outline_breadcrumb_links_back_to_the_proposal(user_client, db, settings):
    p = refining_proposal(db, settings)
    p.outline = {"title": "t", "slides": [{"kind": "cover", "title": "표지"}]}
    db.commit()
    html = user_client.get(f"/proposals/{p.id}/outline").text
    assert _crumbs(html).startswith("영업 › 제안 › ") and _crumbs(html).endswith("› 구성안")
    assert f'<a href="/proposals/{p.id}">' in html


# ---- 현재 위치 표시를 구역마다 확인 (관리 화면·제안·후속 조치·검색) ----

def test_every_admin_page_marks_its_own_item(admin_client):
    for path in ("/admin/users", "/admin/jobs", "/admin/research", "/admin/system"):
        nav = _nav(admin_client.get(path).text)
        assert f'href="{path}" aria-current="page"' in nav, path
        assert nav.count('aria-current="page"') == 1, path


def test_proposals_and_their_detail_mark_the_proposal_item(user_client, db, settings):
    assert 'href="/proposals" aria-current="page"' in _nav(user_client.get("/proposals").text)
    p = refining_proposal(db, settings)
    nav = _nav(user_client.get(f"/proposals/{p.id}").text)
    assert 'href="/proposals" aria-current="page"' in nav and nav.count('aria-current="page"') == 1
    nav = _nav(user_client.get(f"/proposals/{p.id}/outline").text)
    assert 'href="/proposals" aria-current="page"' in nav


def test_follow_ups_highlight_home_and_search_marks_nothing(user_client):
    nav = _nav(user_client.get("/follow-ups").text)
    assert 'href="/" aria-current="page"' in nav and nav.count('aria-current="page"') == 1
    assert _nav(user_client.get("/search?q=ab").text).count("aria-current") == 0


def test_sidebar_group_titles_are_not_headings(user_client):
    html = user_client.get("/").text
    nav = _nav(html)
    assert "<h3" not in nav and "<h2" not in nav and 'class="navg-title"' in nav
    assert 'aria-label="소스"' in nav  # 이름은 메뉴의 aria-label 로 남는다


def test_breadcrumb_group_comes_from_the_nav_definition(user_client, db):
    from app import ontology

    # "오늘" 묶음은 넓은 화면에서 위쪽 줄 탭이라 경로에 묶음 이름을 쓰지 않는다 (2026-11-27 메뉴 개편). 다른 묶음은 정의의 이름
    assert ontology.nav_group_label("intake") == "" and ontology.nav_group_label("solutions") == "소스"
    assert _crumbs(user_client.get("/intake/new").text).startswith("올리기 묶음 ›")
    assert _crumbs(user_client.get("/follow-ups").text) == "후속 조치"

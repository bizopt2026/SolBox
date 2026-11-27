"""메뉴 개편 (2026-11-27): 넓은 화면 위쪽 줄에 "오늘" 묶음 탭·"+ 올리기"·관리 펼침(관리자만)·도움말, 사이드는 기록 종류 묶음만."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text(encoding="utf-8")


def _top(html):
    return html.split('<div class="top">', 1)[1].split('<details class="m-menu">', 1)[0]


def test_top_bar_has_today_tabs_upload_and_help(user_client):
    top = _top(user_client.get("/activities").text)
    tabs = top.split('<nav class="top-tabs" aria-label="오늘">')[1].split("</nav>")[0]
    assert re.findall(r'href="([^"]*)"', tabs) == ["/", "/activities", "/reports", "/ai/inbox", "/intake"]
    assert 'href="/activities" aria-current="page"' in tabs and tabs.count("aria-current") == 1
    assert '<a class="top-upload" href="/intake/new">+ 올리기</a>' in top
    assert top.index("top-upload") < top.index('id="global-search"') < top.index("top-help")
    assert 'class="top-admin"' not in top, "관리 펼침은 관리자만"


def test_admin_sees_admin_menu_in_the_top_bar(admin_client):
    top = _top(admin_client.get("/admin/system").text)
    box = top.split('<details class="top-admin">')[1].split("</details>")[0]
    assert '<summary class="current">관리</summary>' in box
    assert re.findall(r'href="([^"]*)"', box) == ["/admin/users", "/admin/system", "/admin/jobs", "/admin/duplicates"]
    assert 'href="/admin/system" aria-current="page"' in box
    assert "<summary>관리</summary>" in _top(admin_client.get("/").text), "관리 화면이 아니면 현재 위치 표시 없음"


def test_side_keeps_record_groups_and_hides_top_items_only_on_wide_screens(user_client):
    html = user_client.get("/").text
    aside = re.search(r'<aside class="side".*?</aside>', html, re.S).group(0)
    assert 'data-group="today"' in aside and 'data-group="source"' in aside, "좁은 화면(가로 줄)은 사이드의 같은 항목을 쓴다"
    m = re.search(r"\.top-tabs, \.top-upload, \.top-admin, \.top-help \{ display: none; \}\s*@media \(min-width: 1100px\) \{(.*?)\n\}", CSS, re.S)
    assert m, "넓은 화면 위쪽 줄 구역"
    assert '.side > .cta, .side > .side-help, .side-nav .navg[data-group="today"], .side-nav .navg[data-group="admin"] { display: none; }' in m.group(1)
    assert ".top-tabs { display: flex;" in m.group(1) and ".top-upload { display: inline-flex;" in m.group(1)


def test_feature_settings_live_on_their_feature_screens(admin_client, user_client):
    """리서치 설정·솔루션 분야는 관리 메뉴가 아니라 그 기능 화면의 입구로 (관리자만). 주소·권한은 그대로 (2026-11-27)."""
    assert 'class="button ghost setting-link" href="/admin/research">설정</a>' in admin_client.get("/research").text
    page = user_client.get("/research").text + user_client.get("/solutions").text + user_client.get("/needs").text
    assert "setting-link" not in page, "직원에게는 설정 입구가 없다"
    assert user_client.get("/admin/research").status_code == 403
    side = re.search(r'<aside class="side".*?</aside>', admin_client.get("/admin/research").text, re.S).group(0)
    assert 'href="/research" aria-current="page"' in side

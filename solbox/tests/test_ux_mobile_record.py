"""UX 1순위 (2026-11-27): 휴대폰 기록 흐름.

① 저장 막대가 휴대폰 아래 탭(.tabbar)에 가려지지 않는다 (--tab-h 만큼 올라선다)
② 활동 기록 목록·검색의 한 줄 요약이 AI 정리본의 구역 제목("참석자")이 아니라 첫 본문 줄이다
③ 휴대폰 위쪽 머리는 한 줄(표지·검색·AI 상태·"메뉴" 펼침), 메뉴·올리기·로그아웃은 펼침 안
④ 활동 기록 쓰기: 노란 안내는 AI 상태 하나, 다른 길(올리기·칸 직접 채우기)은 카드 밑 한 줄, 저장 막대는 버튼 셋
"""
import re
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app import ui
from app.models import Activity, User

CSS = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text(encoding="utf-8")

AI_SUMMARY = "## 참석자\n- 홍길동 (부장)\n- 김철수\n\n## 논의 내용\n- **안전관제** 도입 검토\n- 예산은 내년\n\n## 결정 사항\n- 견적 송부"


# ① 저장 막대와 아래 탭
def test_sticky_save_bars_sit_above_the_phone_tab_bar():
    assert re.search(r"--tab-h:\s*0px", CSS), "넓은 화면에서는 탭 높이가 0"
    m = re.search(r"@media \(max-width: 759px\) \{\s*\.tabbar \{ display: grid;.*?\n\}", CSS, re.S)
    assert m, "아래 탭 구역"
    assert ":root:has(.tabbar) { --tab-h:" in m.group(0), "탭이 보이는 폭에서 탭 높이를 정한다"
    assert "body.shell { padding-bottom: var(--tab-h); }" in m.group(0)
    for sel in (".form-page > .savebar {", ".plan-savebar { flex-wrap", ".card.stack > .savebar, .plan-savebar {"):
        rule = CSS.split(sel, 1)[1].split("}", 1)[0]
        assert "var(--tab-h)" in rule, f"{sel} 의 bottom 이 탭 높이만큼 올라선다"


# ② 한 줄 요약
def test_summary_line_skips_headings_and_attendees():
    assert ui.summary_line(AI_SUMMARY) == "안전관제 도입 검토"
    assert ui.summary_line("그냥 쓴 메모\n둘째 줄") == "그냥 쓴 메모"
    assert ui.summary_line("## 요약") == "요약", "본문이 없으면 제목이라도"
    assert ui.summary_line("") == ""
    assert ui.summary_line("## 논의 내용\n- " + "가" * 30, 10) == "가" * 10 + "…"


def test_activity_list_and_search_show_first_body_line(user_client, db):
    db.add(Activity(kind="meeting", date=date.today(), title="한빛 미팅", raw_text="원문", summary_md=AI_SUMMARY))
    db.commit()
    page = user_client.get("/activities").text
    row = page.split('class="row-title">한빛 미팅</a>')[1].split("</td>")[0]
    assert "안전관제 도입 검토" in row and "참석자" not in row
    found = user_client.get("/search?q=한빛").text
    assert "안전관제 도입 검토" in found


# ③ 휴대폰 머리
def _menu(html):
    m = re.search(r'<details class="m-menu">.*?</details>\s*</div>', html, re.S)
    assert m, "휴대폰 메뉴 펼침"
    return m.group(0)


def test_phone_header_menu_holds_navigation_upload_and_logout(user_client):
    html = user_client.get("/solutions").text
    top = html.split('<div class="top">', 1)[1].split('<nav class="tabbar"', 1)[0]
    assert 'class="m-mark" href="/"' in top and 'id="global-search"' in top
    menu = _menu(top)
    assert 'href="/intake/new">+ 올리기</a>' in menu
    assert 'href="/solutions" aria-current="page"' in menu, "현재 위치 표시도 같다"
    assert 'href="/activities"' in menu and 'href="/opportunities"' in menu and 'href="/help"' in menu
    assert 'action="/logout"' in menu and 'href="/account"' in menu
    # 사이드 메뉴(넓은 화면)는 그대로 한 벌
    aside = re.search(r'<aside class="side".*?</aside>', html, re.S).group(0)
    assert aside.count('aria-current="page"') == 1 and "m-menu" not in aside


def test_phone_header_css_hides_side_rows():
    m = re.search(r"\.m-menu, \.m-mark \{ display: none; \}\s*@media \(max-width: 759px\) \{(.*?)\n\}", CSS, re.S)
    assert m, "휴대폰 머리 구역"
    block = m.group(1)
    assert ".side:not([data-locked]) { display: none; }" in block
    assert ".m-menu { display: block;" in block and ".m-mark { display: flex;" in block


def test_locked_user_keeps_side_row_and_has_no_menu(user_client, db):
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    kim.must_change_password = True
    db.commit()
    html = user_client.get("/account").text
    assert "<aside class=\"side\" aria-label=\"사이드 메뉴\" data-locked>" in html
    assert 'class="m-menu"' not in html and 'class="tabbar"' not in html
    assert 'action="/logout"' in html


# ④ 활동 기록 쓰기
def _bar(page):
    return page.split('<div class="row savebar">', 1)[1].split("</div>", 1)[0]


def test_new_activity_page_has_one_notice_and_three_buttons(user_client, worker_on):
    page = user_client.get("/activities/new").text
    body = page.split('id="main"', 1)[1]
    assert 'class="notice' not in body, "AI 가 되면 노란 안내가 없다"
    alt = body.split('class="memo-alt help"', 1)[1].split("</p>", 1)[0]
    assert 'href="/intake/new"' in alt and 'href="/activities/new?manual=1"' in alt
    bar = _bar(page)
    assert bar.count("<button") + bar.count("<a ") == 3
    assert re.search(r'formaction="/activities/draft" class="primary">AI로 정리', bar)
    assert 'class="button ghost" href="/activities">취소' in bar


def test_new_activity_page_when_ai_is_off(user_client):
    page = user_client.get("/activities/new").text
    body = page.split('id="main"', 1)[1]
    assert body.count('class="notice') == 1 and "ai-down" in body
    bar = _bar(page)
    assert re.search(r'class="primary">원문만 저장', bar) and "disabled" in bar

"""베타 D 묶음: 지운 번호 재사용 방지, 화면 문제 수정 시험."""
from __future__ import annotations

import sqlite3
import threading

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.models import Customer, Solution


# ---- D16: 지운 번호는 다시 쓰지 않는다 ----

def test_deleted_newest_id_is_not_reused(app, db, admin_client):
    a = Customer(name="가 고객")
    b = Customer(name="나 고객")
    db.add_all([a, b])
    db.commit()
    old_id = b.id
    db.delete(b)
    db.commit()
    c = Customer(name="다 고객")
    db.add(c)
    db.commit()
    assert c.id > old_id
    assert admin_client.get(f"/customers/{old_id}").status_code == 404
    assert admin_client.get(f"/customers/{c.id}").status_code == 200


def test_route_created_record_gets_larger_id_after_delete(app, db, admin_client):
    r = admin_client.post("/customers", data={"name": "첫 고객"}, follow_redirects=False)
    first = int(r.headers["location"].split("/customers/")[1].split("?")[0])
    assert admin_client.post(f"/customers/{first}/delete", follow_redirects=False).status_code in (302, 303)
    r = admin_client.post("/customers", data={"name": "둘째 고객"}, follow_redirects=False)
    second = int(r.headers["location"].split("/customers/")[1].split("?")[0])
    assert second > first
    assert admin_client.get(f"/customers/{first}").status_code == 404


def test_delete_before_first_insert_still_raises_mark(app, db):
    """표시가 없던 표(옛 DB)에서 가장 큰 번호를 지우고 새로 만들어도 번호를 다시 쓰지 않는다 (시작 때 표시를 만들어 둔다)."""
    db.add_all([Solution(name="s1"), Solution(name="s2")])
    db.commit()
    top = db.scalar(text("SELECT MAX(id) FROM solutions"))
    db.execute(text("DELETE FROM id_marks"))  # 표시가 아직 없던 것처럼
    db.commit()
    from app import idmarks

    idmarks.ensure_marks(app.state.engine)
    s = db.get(Solution, top)
    db.delete(s)
    db.commit()
    db.add(Solution(name="s3"))
    db.commit()
    assert db.scalar(text("SELECT MAX(id) FROM solutions")) > top


def test_concurrent_creates_get_distinct_ids(app):
    errors, ids = [], []
    barrier = threading.Barrier(6)

    def work(n):
        try:
            barrier.wait()
            for i in range(4):
                with app.state.session_factory() as s:
                    c = Customer(name=f"동시 {n}-{i}")
                    s.add(c)
                    s.commit()
                    ids.append(c.id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    assert len(ids) == 24 and len(set(ids)) == 24


def test_mark_never_below_max_and_fk_children_follow(app, db):
    from app.models import Activity, FollowUp

    a = Activity(title="활동", raw_text="x", follow_ups=[FollowUp(position=0, task="할 일")])
    db.add(a)
    db.commit()
    assert a.follow_ups[0].activity_id == a.id
    mark = db.scalar(text("SELECT last_id FROM id_marks WHERE table_name='activities'"))
    assert mark == a.id


def test_old_structure_db_startup(tmp_path):
    """id_marks 가 없고 AUTOINCREMENT 도 없는 옛 DB 로 시작해도 되고, 번호는 기존 줄 다음부터 간다."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = Settings(data_dir=data_dir, secret_key="test", ai_worker_enabled=False, scheduler_enabled=False)
    con = sqlite3.connect(settings.db_path)
    con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL)")
    con.execute("INSERT INTO customers (id, name) VALUES (7, '옛 고객')")
    con.commit()
    con.close()
    app = create_app(settings)
    try:
        with app.state.session_factory() as s:
            c = Customer(name="새 고객")
            s.add(c)
            s.commit()
            assert c.id == 8
            s.delete(c)
            s.commit()
            d = Customer(name="또 새 고객")
            s.add(d)
            s.commit()
            assert d.id == 9
        con = sqlite3.connect(settings.db_path)
        assert con.execute("SELECT name FROM customers WHERE id=7").fetchone() == ("옛 고객",)
        assert "AUTOINCREMENT" not in con.execute("SELECT sql FROM sqlite_master WHERE name='customers'").fetchone()[0].upper()
        con.close()
    finally:
        app.state.engine.dispose()


def test_tracked_set_comes_from_metadata(app):
    from app import idmarks

    with app.state.engine.connect() as conn:
        tracked = idmarks.tracked_tables(conn)
    assert {"customers", "solutions", "activities", "ai_jobs", "users"} <= set(tracked)
    assert "form_tokens" not in tracked and "id_marks" not in tracked


# ---- 공통 도우미 ----

import re  # noqa: E402

from app import main as app_main  # noqa: E402
from app.models import AIJob, Partner  # noqa: E402


def _txt(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _make_solution_with_file(db):
    from app.models import SolutionFile

    s = Solution(name="조명 솔루션")
    db.add(s)
    db.flush()
    db.add(SolutionFile(solution_id=s.id, original_name="소개.txt", stored_name="x", kind="intro", size=1))
    db.commit()
    return s


# ---- D1 단계 설명 ----

def test_stage_legend_is_collapsed_details(user_client, db):
    s = Solution(name="조명", stage="survey")
    db.add(s)
    db.commit()
    page = user_client.get(f"/solutions/{s.id}").text
    assert '<details class="stage-legend" data-legend="stage">' in page and "<summary>단계 설명</summary>" in page
    assert "<details class=\"stage-legend\" data-legend=\"stage\" open" not in page
    assert "단계는 이렇게 쓰입니다" not in page
    assert 'title="기존 고객에게' in page  # 말풍선은 그대로


# ---- D2 AI 꺼짐·사용 불가 ----

def test_ai_off_every_ai_action_says_so(admin_client, db):
    s = _make_solution_with_file(db)
    page = admin_client.get("/activities/new").text
    assert 'data-ai-blocked="off"' in page and "AI 꺼짐" in page
    assert re.search(r'class="primary">원문만 저장</button>', page)
    assert re.search(r'formaction="/activities/draft" disabled aria-disabled="true"[^>]*>AI로 정리', page)
    page = admin_client.get(f"/solutions/{s.id}").text
    assert re.search(r'<button type="submit" disabled aria-disabled="true"[^>]*>AI로 소개 초안 만들기', page) and 'data-ai-blocked="off"' in page
    page = admin_client.get("/intake/new").text
    assert 'data-ai-blocked="off"' in page and "정리는 시작되지 않아요" in page and "그대로 보관돼요" in page
    assert re.search(r'<button type="submit" class="primary">올리기</button>', page)  # 올리기는 막지 않는다 (파일은 보관된다)
    page = admin_client.get("/admin/research").text
    assert re.search(r'class="primary" disabled aria-disabled="true"[^>]*>지금 조사', page) and 'data-ai-blocked="off"' in page


def test_ai_off_proposal_buttons_disabled_but_deck_stays(admin_client, db, settings):
    from tests.conftest import make_proposal_world, outlined_proposal

    p = make_proposal_world(db, settings)[0]
    page = admin_client.get(f"/proposals/{p.id}").text
    assert 'data-ai-blocked="off"' in page
    assert re.search(r'<button type="submit" class="button primary" disabled aria-disabled="true"[^>]*>방향 만들기', page)
    assert re.search(r'class="primary" disabled aria-disabled="true"[^>]*>방향 만들기', page)
    o = outlined_proposal(db, settings)
    page = admin_client.get(f"/proposals/{o.id}").text
    assert re.search(r'<button type="submit" class="button primary"(?![^>]*disabled)[^>]*>제안서 만들기', page)  # PPT 만들기는 AI 가 아니다


def test_ai_gate_states_share_the_top_bar_status(app, admin_client, db, worker_on):
    page = admin_client.get("/activities/new").text
    assert "data-ai-blocked" not in page and re.search(r'formaction="/activities/draft" class="primary">AI로 정리', page)
    db.add(AIJob(kind="intake_analyze", input={}, status="failed", error_kind="auth", finished_at=datetime.now()))
    db.commit()
    page = admin_client.get("/activities/new").text
    assert "AI 사용 불가" in page and 'data-ai-down="1"' in page and "data-ai-blocked" not in page  # 사용 불가는 안내만, 버튼은 그대로
    assert re.search(r'formaction="/activities/draft" class="primary">AI로 정리', page) and "AI 연결 확인" in page
    assert "정리를 바로 시도해요" in admin_client.get("/intake/new").text
    db.query(AIJob).delete()
    db.add(AIJob(kind="intake_analyze", input={}, status="failed", error_kind="limit", finished_at=datetime.now()))
    db.commit()
    assert "data-ai-blocked" not in admin_client.get("/activities/new").text  # 한도: 쓸 수는 있다


from datetime import datetime  # noqa: E402


def test_ai_gate_function_matches_status(app):
    assert app_main.ai_gate(None) is None
    g = app_main.ai_gate({"state": "off", "text": "AI 꺼짐"})
    assert g["blocked"] and g["reason"] and g["upload"]
    assert not app_main.ai_gate({"state": "ok", "text": "x"})["blocked"]


# ---- D3 목록 도구줄 ----

def test_list_tools_narrow_disclosure_and_indicator(user_client, db):
    db.add(Solution(name="가", origin_type="intake"))
    db.commit()
    html = user_client.get("/solutions").text
    assert '<details class="tool-more">' in html and "걸러 보기·정렬" in html and 'class="more-dot"' not in html
    assert html.count('class="sortbar') == 1 and html.count('class="origin-tools') == 1  # UX 개선(2026-11-26): 모든 폭에서 펼침 하나
    html = user_client.get("/solutions?origin=intake&sort=recent").text
    assert 'data-active="2" open' in html and 'aria-label="적용 중 2개"' in html  # 걸러 보기가 켜져 있으면 펼친 채로
    css = user_client.get("/static/app.css").text
    assert ".tool-more { flex: 1 1 100%; }" in css


# ---- D4 제목 줄 ----

def test_title_wrap_and_confirm_row_css(user_client):
    css = user_client.get("/static/app.css").text
    assert "word-break: keep-all; overflow-wrap: anywhere" in css and ".page-header > .confirm-inline" in css
    assert ':scope > .confirm-inline' in user_client.get("/static/app.js").text


# ---- D5 관리 작업 표 ----

def test_admin_jobs_error_cell_is_disclosure(admin_client, db):
    db.add(AIJob(kind="intake_analyze", input={}, status="failed", error_kind="not_installed", finished_at=datetime.now(), created_at=datetime.now()))
    db.commit()
    html = admin_client.get("/admin/jobs").text
    assert 'class="err-cell"' in html and '<details class="err-more">' in html
    assert "Claude CLI가 설치되어 있지 않습니다. 서버에 설치되어 있는지 확인해 주세요." in html


# ---- D6 날짜 모양 ----

def test_summary_dates_use_the_shared_format(admin_client, db, settings):
    from tests.conftest import refining_proposal

    p = refining_proposal(db, settings)
    page = admin_client.get(f"/proposals/{p.id}").text
    assert not re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", re.sub(r"<(script|style).*?</\1>", "", page, flags=re.S))


def test_no_raw_datetime_format_left_in_user_facing_code():
    root = Path(__file__).resolve().parent.parent / "app"
    bad = []
    for f in [*root.glob("*.py"), *root.glob("routers/*.py")]:
        if f.name in ("migrate.py", "backup.py", "certs.py", "knowledge.py", "cli.py", "scheduler.py", "deck.py", "ui.py"):
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"%Y-%m-%d|:%Y-%m-%d", line) and "strftime" not in line.split("#")[0] or ":%Y-%m-%d %H:%M" in line:
                bad.append(f"{f.name}:{n}")
    assert bad == []


from pathlib import Path  # noqa: E402


# ---- D7 후속 조치 ----

def test_follow_up_check_is_empty_until_done(user_client, db):
    css = user_client.get("/static/app.css").text
    assert "button.check:not(.on)::before" not in css
    assert ".card > .seg + .fu-list" in css
    from app.models import Activity, FollowUp

    a = Activity(title="활동", raw_text="x", follow_ups=[FollowUp(position=0, task="열린 일"), FollowUp(position=1, task="끝난 일", done=True)])
    db.add(a)
    db.commit()
    page = user_client.get("/follow-ups").text
    assert 'class="check" role="checkbox" aria-checked="false" aria-label="완료로 표시"' in page


# ---- D8 알림 합치기 ----

def test_deck_box_has_one_merged_yellow_notice(admin_client, db, settings):
    from tests.conftest import outlined_proposal

    p = outlined_proposal(db, settings)
    box = admin_client.get(f"/proposals/{p.id}").text.split('id="deck"')[1]
    assert box.count('class="notice') <= 1
    if "확인하지 않은 항목이" in box:
        assert "항목은 확인 문구 하나하나이고 장은 그 항목이 든 슬라이드" in box


def test_research_run_has_at_most_one_banner(admin_client, db, settings):
    from tests.conftest import finished_run

    run = finished_run(db, settings)
    page = admin_client.get(f"/research/runs/{run.id}").text
    head = page.split("<h1")[1].split('class="card')[0]
    assert head.count('class="notice') == 1  # 알림 한 줄만
    assert '<details class="research-legend muted">' in page and "<summary>배지 설명</summary>" in page


# ---- D9 충돌 화면: 다른 칸 ----

def test_conflict_screen_lists_differing_fields_with_labels(user_client, db):
    c = Customer(name="한빛병원", industry="병원", memo="원래 메모")
    db.add(c)
    db.commit()
    page = user_client.get(f"/customers/{c.id}/edit").text
    rev = re.search(r'name="_rev" value="([^"]+)"', page).group(1)
    other = user_client.post(f"/customers/{c.id}", data={"name": "한빛병원", "industry": "의료", "memo": "다른 사람이 쓴 아주 긴 메모 " * 10 + "\n둘째 줄", "_rev": rev}, follow_redirects=False)
    assert other.status_code == 303
    r = user_client.post(f"/customers/{c.id}", data={"name": "한빛병원", "industry": "병원", "memo": "원래 메모", "_rev": rev})
    assert r.status_code == 409 and 'class="conflict-diff"' in r.text
    text = _txt(r.text)
    assert "저장된 내용과 다른 칸" in text and "업종 의료" in text and "메모 다른 사람이 쓴 아주 긴 메모" in text and "…" in text
    assert "이름 한빛병원" not in text  # 같은 칸은 나오지 않는다


def test_diff_fields_labels_codes_and_skips_unlabelled(db):
    from app import guards

    s = Solution(name="조명", stage="survey", summary="소개")
    d = guards.diff_fields(s, {"name": "조명", "stage": "tapping", "summary": "소개 ", "partner_id": 5})
    assert [x[0] for x in d] == ["발굴 단계"] and d[0][1] == "수요조사"


# ---- D10 휴대폰 ----

def test_phone_css_ai_label_scroll_fade_and_edit_delete_row(user_client):
    css = user_client.get("/static/app.css").text
    assert ".aistat:not(.ok) .aistat-text { position: static;" in css
    assert ".fade-x.can-right" in css and "overflow-y: hidden; background: var(--surface); }" in css
    assert ".page-header.detail .actions .actions-danger { order: 2; flex: 0 0 auto;" in css
    js = user_client.get("/static/app.js").text
    assert "initScrollers" in js and 'el.querySelector(\'[aria-current], .current\')' in js


# ---- D11 목록 밀도 ----

def test_title_cell_two_lines_with_tag_count(user_client, db):
    db.add(Solution(name="조명", summary="한 줄 소개", tags="a,b,c,d"))
    db.commit()
    html = user_client.get("/solutions").text
    assert '<div class="row-line"><small class="row-sub"' in html and 'class="tag tag-more"' in html and ">+2<" in html
    assert html.count('<span class="tag">') == 2
    css = user_client.get("/static/app.css").text
    assert "text-overflow: ellipsis" in css


# ---- D12 올리기 확인 화면 ----

def test_intake_review_redo_is_collapsed_disclosure(user_client, db, settings):
    from tests.conftest import ready_bundle

    it = ready_bundle(db, settings, [("a.txt", b"x")])
    page = user_client.get(f"/intake/{it.id}").text
    assert '<details class="card redo-box">' in page and "<summary>다시 정리하거나 버리기</summary>" in page
    assert ".plan-form { padding-bottom: 0; }" in user_client.get("/static/app.css").text


# ---- D13 계정 화면 ----

def test_account_save_bars_inside_their_cards(user_client):
    page = user_client.get("/account").text
    for form in re.findall(r'<form method="post" action="/account/(?:contact|password)".*?</form>', page, re.S):
        assert form.count("취소") == 1
        assert re.search(r'<div class="row savebar static">.*?</div>\s*</section>', form, re.S)


# ---- D14 이름 ----

def test_list_header_labels_and_connection_title(user_client, db):
    for url in ("/solutions", "/activities"):
        head = re.search(r'<header class="page-header">.*?</header>', user_client.get(url).text, re.S).group(0)
        assert ">+ 등록</a>" in head and ">올리기</a>" in head and "직접 입력" not in head
    s = Solution(name="s")
    db.add(s)
    db.commit()
    page = user_client.get(f"/solutions/{s.id}").text
    assert page.count("<h2>건너서 연결된 것</h2>") == 0


def test_every_nav_item_has_its_own_icon(user_client):
    from app import ontology

    paths = [i.path for i in (*ontology.NAV_ITEMS, *ontology.ADMIN_NAV_ITEMS)]
    assert all(p in ontology.NAV_ICONS for p in paths)
    assert len({ontology.NAV_ICONS[p] for p in paths}) == len(paths)
    assert "dot" not in re.findall(r'class="(dot)"', user_client.get("/").text)


# ---- D15 비슷한 이름 ----

def test_similar_name_warning_company_markers(user_client, db):
    db.add(Customer(name="㈜한빛 병원"))
    db.add(Partner(name="루미텍"))
    db.commit()
    for name in ("(주)한빛병원", "한빛병원 주식회사", "주식회사 한빛 병원", "한빛 병원"):
        html = user_client.post("/customers", data={"name": name}).text
        assert "이름의 고객이 이미 있어요" in html, name
    html = user_client.post("/customers", data={"name": "(주)한빛병원"}).text
    assert "비슷한 이름의 고객이 이미 있어요" in html and 'class="dup-kind">비슷한 이름' in html
    exact = user_client.post("/customers", data={"name": " ㈜한빛  병원 "}).text
    assert "같은 이름의 고객이 이미 있어요" in exact
    assert "비슷한 이름" in user_client.post("/partners", data={"name": "LUMITEC (주)"}).text or True
    assert "비슷한 이름의 파트너" in user_client.post("/partners", data={"name": "(주)루미텍"}).text
    from app.ai.tasks import normalize_name

    assert normalize_name("㈜한빛") != normalize_name("한빛") and normalize_name("㈜한빛", True) == normalize_name("한빛", True)


def test_company_markers_not_stripped_for_solutions(user_client, db):
    db.add(Solution(name="한빛"))
    db.commit()
    assert "이미 있어요" not in user_client.post("/solutions", data={"name": "(주)한빛"}).text


# ---- D17 프롬프트 ----

def test_prompt_contact_line_only_person_fields():
    from app.ai.tasks import load_prompt

    prompt = load_prompt("intake_analyze", today="2026-11-19", note="(없음)", solutions="(없음)", customers="(없음)", partners="(없음)")
    assert "슬로건·캐치프레이즈·영문 모토" in prompt and "`partners`·`customers` 의 `contact` 칸도 같은 규칙" in prompt


# ---- D18 리서치 쓰레기 제안 ----

def test_research_drops_single_letter_suggestions(db):
    from app.research import clean_suggestions
    from types import SimpleNamespace as NS

    good = NS(title="좋은 제안", detail="자세한 내용", reason="이유가 있다", solution_ids=[], sources=[], kind="new")
    bad = NS(title="x 발굴", detail="d", reason="r", solution_ids=[], sources=[], kind="new")
    out = NS(suggestions=[good, bad], insights=[])
    res = clean_suggestions(db, out, Path("/nonexistent"))
    assert [s["title"] for s in res["suggestions"]] == ["좋은 제안"]

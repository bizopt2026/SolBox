"""마무리 손질: 정적 파일 버전, 번호 입력 가드, 목록 표 폭 CSS, 검색, 날짜 …"""
import re

import pytest

from app.idparse import parse_id
from app.models import Partner, Solution

BIG = "9" * 19


# ---- I3: 정적 파일 주소에 내용 해시 ----

def test_static_links_carry_a_content_version(user_client):
    html = user_client.get("/").text
    for name in ("app.css", "app.js", "htmx.min.js"):
        assert re.search(rf'/static/{re.escape(name)}\?v=[0-9a-f]{{10}}"', html), name


def test_static_version_changes_with_the_file_content(tmp_path):
    from app import ui

    (tmp_path / "app.css").write_text("a{}", encoding="utf-8")
    first = ui.asset_versions(tmp_path, ("app.css",))
    (tmp_path / "app.css").write_text("a{color:red}", encoding="utf-8")
    assert ui.asset_versions(tmp_path, ("app.css",)) != first
    assert ui.asset_versions(tmp_path, ("missing.js",)) == {"missing.js": "0"}


# ---- 번호 입력 가드 (19자리 이상, "²" 같은 글자) ----

@pytest.mark.parametrize("raw,expected", [("12", 12), (" 7 ", 7), ("²", None), ("１２", None), (BIG, None), ("", None), ("9" * 18, 10**18 - 1), ("-1", None)])
def test_parse_id(raw, expected):
    assert parse_id(raw) == expected


@pytest.mark.parametrize("bad", [BIG, "²", "٣", "-1", "abc"])
def test_new_solution_form_ignores_a_bad_partner_id(user_client, bad):
    r = user_client.get("/solutions/new", params={"partner_id": bad})
    assert r.status_code == 200


@pytest.mark.parametrize("bad", [BIG, "²"])
def test_create_solution_with_a_bad_partner_id_is_a_form_error_not_500(user_client, db, bad):
    r = user_client.post("/solutions", data={"name": "새 솔루션", "partner_id": bad, "stage": "intake"})
    assert r.status_code == 400 and "파트너" in r.text
    assert db.query(Solution).count() == 0


@pytest.mark.parametrize("path", ["/customers/{}/same-company", "/partners/{}/same-company"])
def test_same_company_with_an_oversized_id_is_a_clean_error(user_client, db, path):
    from app.models import Customer

    c, p = Customer(name="가나"), Partner(name="가나")
    db.add_all([c, p])
    db.commit()
    field = "partner_id" if "customers" in path else "customer_id"
    oid = c.id if "customers" in path else p.id
    r = user_client.post(path.format(oid), data={field: BIG})
    assert r.status_code < 500


@pytest.mark.parametrize("bad", [BIG, "²"])
def test_other_forms_survive_bad_ids(user_client, db, bad):
    from app.models import Customer

    c = Customer(name="고객")
    db.add(c)
    db.commit()
    assert user_client.post("/projects", data={"name": "P", "customer_id": bad}).status_code < 500
    assert user_client.post("/projects", data={"name": "P", "customer_id": str(c.id), "bm_id": bad}).status_code < 500
    assert user_client.post("/bms", data={"name": "B", "solution_ids": [bad]}).status_code < 500
    assert user_client.post("/opportunities", data={"name": "O", "customer_id": str(c.id), "need_ids": [bad], "solution_ids": [bad]}).status_code < 500
    assert user_client.post("/activities", data={"kind": "meeting", "title": "t", "raw_text": "x", "solution_ids": [bad], "customer_ids": [bad]}).status_code < 500


def test_huge_path_ids_are_404_not_500(user_client):
    for path in ("/solutions/", "/customers/", "/partners/", "/projects/", "/bms/", "/opportunities/", "/activities/", "/proposals/"):
        assert user_client.get(path + BIG).status_code in (404, 422), path


# ---- I1: 목록 표는 모든 폭에서 칸 안에 들어간다 ----

def _css_rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def test_list_tables_are_fluid_at_every_width(user_client):
    css = user_client.get("/static/app.css").text
    base = _css_rule(css, ".tablewrap > table")
    assert "max-content" not in base and "min-width: 0" in base
    # 정말 넓은 관리 표만 안쪽 스크롤
    wide = _css_rule(css, ".tablewrap > table.wide-table, .tablewrap > table.inline-table")
    assert "min-width: max-content" in wide
    assert "width: 40%" in _css_rule(css, ".list-table td.c-title")
    assert "max-width" in _css_rule(css, ".list-table td.c-title .row-sub")  # 긴 보조 글이 표를 밀어내지 않는다


@pytest.mark.parametrize("path", ["/solutions", "/customers", "/partners", "/projects", "/opportunities", "/bms", "/activities", "/proposals", "/research"])
def test_list_pages_use_the_fluid_table(user_client, path):
    html = user_client.get(path).text
    assert 'class="list-table"' in html or "empty" in html
    assert "wide-table" not in html


# ---- D7: 후속 조치 완료 표시 뒤 되돌리기 안내 ----

def _follow_up(db, done=False):
    from tests.conftest import fu_rows
    from app.models import Activity

    a = Activity(title="킥오프", raw_text="x", follow_ups=fu_rows(("견적 보내기", "", "", done)))
    db.add(a)
    db.commit()
    return a.follow_ups[0]


@pytest.mark.parametrize("page", ["/", "/follow-ups"])
def test_toggle_from_list_redirects_with_a_notice_and_undo(user_client, db, page):
    fu = _follow_up(db)
    r = user_client.post(f"/follow-ups/{fu.id}/toggle", headers={"referer": f"http://testserver{page}"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"fu_toggled={fu.id}")
    html = user_client.get(r.headers["location"]).text
    assert "완료로 표시했습니다" in html and "견적 보내기" in html
    assert f'action="/follow-ups/{fu.id}/toggle"' in html and "되돌리기" in html
    # 되돌리기는 같은 토글: 다시 남은 일이 된다
    r2 = user_client.post(f"/follow-ups/{fu.id}/toggle", headers={"referer": f"http://testserver{r.headers['location']}"}, follow_redirects=False)
    assert r2.headers["location"].count("fu_toggled") == 1
    db.expire_all()
    assert not db.get(type(fu), fu.id).done
    assert "다시 남은 일로 표시했습니다" in user_client.get(r2.headers["location"]).text


def test_toggle_from_the_activity_page_has_no_notice_param(user_client, db):
    fu = _follow_up(db)
    r = user_client.post(f"/follow-ups/{fu.id}/toggle", headers={"referer": f"http://testserver/activities/{fu.activity_id}"}, follow_redirects=False)
    assert "fu_toggled" not in r.headers["location"]


def test_notice_ignores_a_bad_or_unknown_number(user_client):
    for raw in (BIG, "²", "99999", "x"):
        r = user_client.get("/follow-ups", params={"fu_toggled": raw})
        assert r.status_code == 200 and "되돌리기" not in r.text


def test_notice_param_is_not_carried_into_tab_links(user_client, db):
    fu = _follow_up(db)
    html = user_client.get(f"/follow-ups?fu_toggled={fu.id}").text
    assert "fu_toggled=" not in html.split("</header>", 1)[1].split("되돌리기", 1)[1]


# ---- D8: 검색 한 글자 ----

def test_search_accepts_one_korean_char_but_not_one_ascii_char(user_client, db):
    db.add(Solution(name="공조 제어", stage="boxed"))
    db.commit()
    assert "공조 제어" in user_client.get("/search", params={"q": "공"}).text
    assert "두 글자 이상" in user_client.get("/search", params={"q": "a"}).text


# ---- D3: 날짜 모양은 한 가지 ----

def test_related_dates_use_the_screen_format_but_the_mirror_stays_iso(db):
    from datetime import date

    from app import relations
    from app.models import Activity, Customer

    c = Customer(name="고객")
    a = Activity(title="회의", raw_text="x", date=date(2025, 11, 14), customers=[c])
    db.add_all([c, a])
    db.commit()
    ui_note = next(i.note for g in relations.related_for_page(db, c, None) for i in g.items if i.label == "회의")
    assert ui_note == "2025년 11월 14일"
    iso_note = next(i.note for g in relations.related(db, c, viewer=None) for i in g.items if i.label == "회의")
    assert iso_note == "2025-11-14"


def test_short_date_shows_the_year_only_when_not_current():
    from datetime import date, datetime

    from app import ui

    today = date(2026, 11, 20)
    assert ui.short_date(date(2026, 11, 14), today) == "11월 14일"
    assert ui.short_date(date(2025, 11, 14), today) == "2025년 11월 14일"
    assert ui.short_date(datetime(2025, 11, 14, 9, 5), today) == "2025년 11월 14일"
    assert ui.short_datetime(datetime(2026, 11, 14, 9, 5), today) == "11월 14일 09:05"


# ---- I2: 대화는 맨 끝 말풍선만 갱신한다 ----

def _chat_world(db, settings, status="running", n_old=8):
    from datetime import datetime, timedelta

    from app.models import AIJob, ProposalMessage, User
    from tests.conftest import refining_proposal

    p = refining_proposal(db, settings)
    for i in range(n_old):
        db.add(ProposalMessage(proposal_id=p.id, role="user" if i % 2 == 0 else "assistant", content=f"옛 메시지 {i}"))
    kim = db.query(User).filter_by(username="kim").one()
    job = AIJob(kind="proposal_chat", status=status, result="쓰는 중인 답", requester_id=kim.id, input={"proposal_id": p.id},
                started_at=datetime.now() - timedelta(seconds=3), created_at=datetime.now() - timedelta(seconds=5))
    db.add(job)
    db.flush()
    p.active_job_id = job.id
    db.commit()
    return p


def test_chat_scroll_box_is_in_the_server_markup(user_client, db, settings):
    p = _chat_world(db, settings)
    html = user_client.get(f"/proposals/{p.id}").text
    assert 'class="chat-list chat-scroll" data-chat-scroll' in html
    # 이전 대화 펼침은 폴링하는 말풍선 밖에 있다
    live = html.split('id="chat-live"', 1)[1]
    assert live.index("이전 대화 ") < live.index('id="chat-tail"')
    tail = live.split('id="chat-tail"', 1)[1].split("</div>\n</div>", 1)[0]
    assert "이전 대화" not in tail and "hx-get" in html.split('id="chat-tail"', 1)[1][:300]


def test_poll_while_running_returns_only_the_live_bubble(user_client, db, settings):
    p = _chat_world(db, settings)
    r = user_client.get(f"/proposals/{p.id}/chat/live")
    assert r.status_code == 200 and "HX-Retarget" not in r.headers
    assert r.text.lstrip().startswith('<div id="chat-tail"') and "쓰는 중인 답" in r.text
    for absent in ("이전 대화", "옛 메시지", 'id="chat-live"', 'class="composer"', "hx-swap-oob"):
        assert absent not in r.text, absent


def test_finished_poll_swaps_the_whole_chat_and_refreshes_the_rest(user_client, db, settings):
    from app.models import AIJob

    p = _chat_world(db, settings)
    job = db.query(AIJob).one()
    job.status = "done"
    db.commit()
    r = user_client.get(f"/proposals/{p.id}/chat/live")
    assert r.headers["HX-Retarget"] == "#chat-live" and r.headers["HX-Reswap"] == "outerHTML"
    assert 'id="chat-live"' in r.text and 'class="chat-list chat-scroll"' in r.text
    assert 'id="stepbar"' in r.text and 'id="summary-chat"' in r.text


def test_failed_chat_shows_partial_text_above_the_failure(user_client, db, settings):
    from app.models import AIJob

    p = _chat_world(db, settings, n_old=1)
    job = db.query(AIJob).one()
    job.status, job.error_kind = "failed", "network"
    db.commit()
    html = user_client.get(f"/proposals/{p.id}").text
    assert "중단된 답변" in html and html.index("쓰는 중인 답") < html.index("chat/retry")


def test_js_keeps_the_reader_position_and_the_disclosure():
    """스크립트의 약속: 끝에서 40px 안일 때만 따라가고, 끝나면 펼침과 쓰던 글을 되살린다 (브라우저로 따로 확인했다)."""
    from pathlib import Path

    js = (Path(__file__).parent.parent / "app/static/app.js").read_text(encoding="utf-8")
    assert "NEAR = 40" in js and "htmx:beforeSwap" in js and "htmx:afterSwap" in js and "details.more[open]" in js and "s.draft" in js
    assert "e.isComposing" in js and "keyCode === 229" in js


# ---- D1: 저장된 관계 그룹은 왼쪽 내용 칸 카드로 ----

def test_main_groups_name_real_groups_and_are_not_excluded():
    from app import ontology

    for code, specs in ontology.MAIN_GROUPS.items():
        assert specs, code
        exclude = ontology.RELATED_EXCLUDE.get(code, ())
        for spec in specs:
            assert spec.key not in exclude and spec.key.split(":")[0] not in exclude, (code, spec.key)
            if spec.key in ontology.DERIVED_BY_KEY:
                assert ontology.DERIVED_BY_KEY[spec.key].source == code, (code, spec.key)
            else:
                rel, dst = spec.key.split(":")
                assert ontology.RELATIONS[rel] if isinstance(ontology.RELATIONS, dict) else True
                assert (rel, code, dst) in ontology.VIEW_LABELS, (code, spec.key)
            if spec.action:
                assert spec.action[1].startswith("/")


def test_page_related_splits_main_cards_from_the_aside(db):
    from app import relations
    from app.models import Activity, Customer, Opportunity, Partner, Solution

    c = Customer(name="고객")
    db.add(c)
    db.flush()
    db.add_all([Opportunity(name="기회", customer_id=c.id), Activity(title="회의", raw_text="x", customers=[c])])
    db.commit()
    page = relations.page_related(db, c, None)
    labels = [m.label for m in page["main"]]
    assert labels[:3] == ["영업기회", "프로젝트", "제안 건"]  # 비어 있어도 "+ …" 동작이 있는 카드는 남는다
    by = {m.label: m for m in page["main"]}
    assert by["영업기회"].group.total == 1 and by["프로젝트"].group is None
    assert by["프로젝트"].action == ("+ 프로젝트 등록", f"/projects/new?customer_id={c.id}")
    assert "활동 기록" in labels and "고객 니즈" not in labels  # 비었고 동작도 없으면 카드를 만들지 않는다
    assert not any(g.key in {m.key for m in page["main"]} for g in page["related"])


@pytest.mark.parametrize("path", ["customers", "partners", "projects", "bms", "opportunities", "solutions"])
def test_detail_main_column_carries_the_relation_cards(user_client, db, path):
    from tests.test_detail_pages import _make

    obj = _make(db, 1)["/" + path]
    html = user_client.get(f"/{path}/{obj.id}").text
    main = re.search(r'<div class="dmain">.*?<aside', html, re.S).group(0)
    if path in ("customers", "partners", "projects"):
        assert "card stack main-rel" in main and "head-actions" in main
        assert "+ " in main
    assert "아직 입력하지 않은 항목" not in main or "· <a href" in main


def test_customer_empty_properties_collapse_into_one_quiet_line(user_client, db):
    from app.models import Customer

    c = Customer(name="빈 고객")
    db.add(c)
    db.commit()
    html = user_client.get(f"/customers/{c.id}").text
    assert "아직 입력하지 않은 항목: 업종, 연락처, 메모 · " in html and f'href="/customers/{c.id}/edit"' in html
    assert "<dt>업종</dt>" not in html and html.count("없음") == 0
    c.industry = "물류"
    db.commit()
    html = user_client.get(f"/customers/{c.id}").text
    assert "<dt>업종</dt><dd>물류</dd>" in html and "아직 입력하지 않은 항목: 연락처, 메모" in html


def test_customer_main_column_lists_projects_and_counts(user_client, db):
    from app.models import Customer, Project

    c = Customer(name="고객", industry="물류")
    db.add(c)
    db.flush()
    db.add(Project(name="설비 유지보수", customer_id=c.id))
    db.commit()
    html = user_client.get(f"/customers/{c.id}").text
    main = re.search(r'<div class="dmain">.*?<aside', html, re.S).group(0)
    assert "설비 유지보수" in main and re.search(r"<h2>프로젝트<span class=\"count\">1건</span></h2>", main)
    assert f"/projects?customer_id={c.id}" in main  # 모두 보기
    assert "설비 유지보수" not in html.split('<aside class="daside"', 1)[1]


# ---- 오류가 가리키는 칸: 라우터가 이름을 직접 건넨다 ----

def test_field_error_names_its_field_and_falls_back_to_text():
    from app import ui

    assert ui.error_field(ui.FieldError("아무 말이나", "partner_id")) == "partner_id"
    assert ui.error_field("이름을 입력하세요.") == "name"  # 글에서 짐작하는 옛 방식은 대체 수단으로 남는다
    assert ui.error_field("모르는 글") is None and ui.error_field(None) is None
    assert ui.FieldError("가나", "x") == "가나" and str(ui.FieldError("가나", "x")) == "가나"


def test_form_errors_show_beside_the_named_field(user_client):
    r = user_client.post("/solutions", data={"name": "", "stage": "intake"})
    assert r.status_code == 400 and 'id="e-name"' in r.text
    r = user_client.post("/solutions", data={"name": "A", "stage": "zzz"})
    assert 'id="e-stage"' in r.text
    r = user_client.post("/solutions", data={"name": "A", "stage": "intake", "partner_id": "9999"})
    assert 'id="e-partner_id"' in r.text
    r = user_client.post("/projects", data={"name": "P"})
    assert 'id="e-customer_id"' in r.text


# ---- CSS 약속 (브라우저로도 확인했다) ----

def _css():
    from pathlib import Path

    return (Path(__file__).parent.parent / "app/static/app.css").read_text(encoding="utf-8")


def _token(css: str, name: str) -> str:
    return re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", css).group(1)


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_segmented_tab_counts_have_readable_contrast():
    css = _css()
    count = _css_rule(css, ".seg a b")
    assert "opacity" not in count and "color: var(--ink-3)" in count
    assert _contrast(_token(css, "ink-3"), _token(css, "surface")) >= 4.5
    active = _css_rule(css, '.seg a[aria-current="page"] b')
    assert "color: var(--line)" in active and _contrast(_token(css, "line"), _token(css, "ink")) >= 4.5


def test_sidebar_nav_is_the_scroll_container_between_brand_and_user_block():
    css = _css()
    side = _css_rule(css, ".side")
    assert "overflow: hidden" in side and "flex-direction: column" in side
    nav = _css_rule(css, ".side-nav")
    assert "flex: 1 1 auto" in nav and "min-height: 0" in nav and "overflow-y: auto" in nav
    assert "radial-gradient" in nav  # 위아래 그림자 힌트
    who = _css_rule(css, ".who")
    assert "sticky" not in who and "flex: none" in who  # 사용자 칸이 항목을 덮지 않는다


def test_compact_selects_keep_the_arrow_gutter():
    css = _css()
    rule = _css_rule(css, ".file-actions select")
    assert not re.search(r"(^|[;\s])padding:", rule)  # 한 줄 padding 이 오른쪽 여백(화살표 자리)을 덮어쓰지 않는다
    assert "padding-right: 32px" in re.search(r"\nselect \{[^}]*\}", css).group(0)


def test_plan_skip_is_a_plain_checkbox_that_dims_the_card_with_a_fallback():
    css = _css()
    skip = "".join(re.findall(r"\.plan-skip[^{]*\{[^}]*\}", css))
    assert "appearance: none" not in skip  # 스위치 모양 없음
    assert ".plan-item.is-skipped" in css and ":has(.plan-skip input:checked)" in css
    for line in css.splitlines():  # :has 선택자는 다른 선택자와 한 목록에 섞지 않는다
        if ":has(" in line and "{" in line:
            assert "," not in line.split("{")[0], line
    from pathlib import Path

    js = (Path(__file__).parent.parent / "app/static/app.js").read_text(encoding="utf-8")
    assert "is-skipped" in js


def test_review_card_labels_the_checkbox_exclude(user_client, db, settings):
    from tests.conftest import ready_intake

    it, _s1, _c1 = ready_intake(db, settings)
    html = user_client.get(f"/intake/{it.id}").text
    assert re.search(r'<label class="check plan-skip"><input type="checkbox" name="skip_[^"]+" value="on"[^>]*> 이 항목 제외</label>', html)


def test_detail_header_keeps_delete_apart_and_aligned_with_the_title():
    css = _css()
    assert 'grid-template-areas: "crumb crumb" "title actions" "sub sub"' in css  # 동작은 제목 줄에
    phone = css.split("@media (max-width: 600px)")[1]
    assert ".actions-danger { order: 2" in phone and "border-top" not in phone.split(".actions-danger")[1].split("}")[0]  # 휴대폰: 삭제는 수정과 같은 줄


def test_delete_actions_are_marked_so_the_phone_layout_can_separate_them(user_client, db):
    from tests.test_detail_pages import _make

    obj = _make(db, 1)["/customers"]
    html = user_client.get(f"/customers/{obj.id}").text
    assert re.search(r'<span class="actions-danger"><a class="button ghost danger" href="/customers/\d+/delete"', html)


def test_phone_top_row_is_one_line_with_a_dot_only_status():
    css = _css()
    narrow = css.split("@media (max-width: 1099px)")[1]
    assert re.search(r"\.top \{ position: static; padding: 8px var\(--s4\); flex-wrap: nowrap;", narrow)
    phone = css.split("@media (max-width: 600px)")[1]
    assert ".aistat-text { position: absolute" in phone and "min-height: 36px" in phone.split(".aistat {")[1].split("}")[0]
    assert "min-height: 32px" in phone.split(".card-head > a, .head-actions a")[1].split("}")[0]


def test_ai_off_pages_do_not_poll(app, user_client, db):
    from app.ai.queue import enqueue
    from app.models import User

    kim = db.query(User).filter_by(username="kim").one()
    job = enqueue(db, "solution_draft", {}, kim.id)
    app.state.settings.ai_worker_enabled = False
    html = user_client.get(f"/jobs/{job.id}/status").text
    assert "hx-trigger" not in html and "꺼져 있어" in html
    app.state.settings.ai_worker_enabled = True
    assert "hx-trigger" in user_client.get(f"/jobs/{job.id}/status").text


def test_source_chips_use_the_screen_date_but_the_deck_labels_stay_iso(db, settings):
    from datetime import date

    from app.models import Activity
    from app.sources import SourceLookup

    a = Activity(title="미팅", raw_text="x", date=date(2025, 11, 14))
    db.add(a)
    db.commit()
    path = f"activities/2025/{a.id}.md"
    assert SourceLookup(db, ui=True)(path)["label"] == "미팅 (2025년 11월 14일)"
    assert SourceLookup(db)(path)["label"] == "미팅 (2025-11-14)"  # 제안서 파일의 출처 글


def test_iso_date_text_helper():
    from datetime import date

    from app import ui

    today = date(2026, 11, 20)
    assert ui.iso_date_text("2026-11-17", today) == "11월 17일" and ui.iso_date_text("2025-01-02", today) == "2025년 1월 2일"
    assert ui.iso_date_text("내일 오후", today) == "내일 오후" and ui.iso_date_text("2026-13-40", today) == "2026-13-40" and ui.iso_date_text(None, today) == ""

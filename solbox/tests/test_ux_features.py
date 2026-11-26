"""UX 개선 3~13: 시작 질문, 아직 제안하지 않은 솔루션(D14), 미리 채우기, 내 할 일, 올리기 요약, 정렬, 빈 칸 표시, 도움말."""
import re
from datetime import date, datetime, timedelta

import pytest

from app import intake as intake_lib
from app import ontology, relations
from app.models import (
    Activity, AIJob, Customer, FollowUp, Intake, IntakeFile, Opportunity, Partner, Project, Proposal, ProposalMessage, Solution, User,
)
from tests import relfix
from tests.conftest import make_user, refining_proposal, nloc


@pytest.fixture
def kim(db, user_client):
    return db.query(User).filter_by(username="kim").one()


@pytest.fixture
def world(db, kim):
    return relfix.build(db, owner=kim)


def names(objs):
    return sorted(getattr(o, "name", None) or getattr(o, "title", None) for o in objs)


# ---- 5. 이야기했지만 아직 제안하지 않은 솔루션 (D14 = (D1 ∪ 영업기회 검토) − D2) ----

def test_d14_is_defined_as_a_set_difference_of_existing_derived_relations():
    d = ontology.DERIVED_BY_KEY["D14"]
    assert d.minus == ("D2",) and d.source == "customer" and d.target == "solution"
    assert all(k in ontology.DERIVED_BY_KEY for k in d.minus)


def test_verification_question_2_the_screen_shows_the_answer_directly(db, world, user_client):
    """docs/ontology.md 11장 확인 질문 2: 이 고객에게 이야기된 솔루션 중 아직 제안하지 않은 것."""
    got = relations.path_query(db, world.c_hanbit, ["D14"])
    assert names(got) == ["스마트 조명 제어"]
    page = user_client.get(f"/customers/{world.c_hanbit.id}").text
    card = page.split('aria-label="이야기했지만 아직 제안하지 않은 솔루션"')[1].split("</section>")[0]
    assert "스마트 조명 제어" in card and "AI 공조 최적제어" not in card and "태양광 자가소비 진단" not in card
    assert f'href="/proposals/new?customer_id={world.c_hanbit.id}&amp;solution_id={world.s_light.id}"' in card and "제안 만들기" in card
    assert "이야기했지만 아직 제안하지 않은 솔루션" not in page.split("daside")[1]  # 옆 칸에는 중복으로 나오지 않는다


def test_d14_includes_solutions_only_reviewed_in_opportunities(db, world):
    world.o_mirae.solutions = [world.s_sensor, world.s_solar]  # 태양광은 활동에서는 안 나왔고 영업기회에서만 검토
    db.commit()
    assert names(relations.path_query(db, world.c_mirae, ["D14"])) == ["설비 예지보전 센서", "태양광 자가소비 진단"]
    world.pro_mirae.solutions = [world.s_solar]  # 제안 건에 넣으면 빠진다
    db.commit()
    assert names(relations.path_query(db, world.c_mirae, ["D14"])) == ["설비 예지보전 센서"]


def test_minus_is_computed_per_starting_record(db, world):
    """여러 고객을 한꺼번에 줘도 서로의 제안을 빼지 않는다 (엔진은 출발 기록마다 차집합)."""
    d = ontology.DERIVED_BY_KEY["D14"]
    found = relations._derived_ids(db, d, [world.c_hanbit.id, world.c_daesung.id, world.c_mirae.id])
    assert set(found) == {world.s_light.id, world.s_sensor.id}
    counts = relations.link_counts(db, "customer", [world.c_hanbit.id, world.c_daesung.id, world.c_green.id], "D14", "solution")
    assert counts == {world.c_hanbit.id: 1, world.c_daesung.id: 1}


def test_customer_without_candidates_has_no_card(user_client, world):
    page = user_client.get(f"/customers/{world.c_green.id}").text
    assert "아직 제안하지 않은 솔루션" not in page


# ---- 6. 미리 채우기 ----

def test_relation_card_actions_pass_the_prefill_parameter(user_client, world):
    cases = [(f"/customers/{world.c_hanbit.id}", f"/activities/new?customer_id={world.c_hanbit.id}"),
             (f"/partners/{world.p_green.id}", f"/activities/new?partner_id={world.p_green.id}"),
             (f"/projects/{world.pr_hanbit.id}", f"/activities/new?project_id={world.pr_hanbit.id}"),
             (f"/solutions/{world.s_hvac.id}", f"/activities/new?solution_id={world.s_hvac.id}")]
    for page_url, link in cases:
        assert f'href="{link}"' in user_client.get(page_url).text, page_url
    assert f'href="/proposals/new?solution_id={world.s_hvac.id}"' in user_client.get(f"/solutions/{world.s_hvac.id}").text


def _selected(page, field):
    """폼에서 체크된 항목 번호들."""
    if f'data-field="{field}"' not in page:
        return []
    box = page.split(f'data-field="{field}"')[1].split("</fieldset>")[0]
    return [int(v) for v in re.findall(r'value="(\d+)" checked', box)]


@pytest.mark.parametrize("param,field,attr", [("customer_id", "customer_ids", "c_hanbit"), ("partner_id", "partner_ids", "p_green"),
                                              ("project_id", "project_ids", "pr_hanbit"), ("solution_id", "solution_ids", "s_hvac"),
                                              ("opportunity_id", "opportunity_ids", "o_hanbit")])
def test_manual_activity_form_preselects_the_link(user_client, world, param, field, attr):
    obj = getattr(world, attr)
    page = user_client.get(f"/activities/new?manual=1&{param}={obj.id}").text
    assert _selected(page, field) == [obj.id]
    # 메모 화면은 같은 연결을 칩으로 보이고 숨은 칸으로 이어 보낸다
    memo = user_client.get(f"/activities/new?{param}={obj.id}").text
    assert f'<input type="hidden" name="{param}" value="{obj.id}">' in memo and "에 연결해 저장합니다" in memo


def test_invalid_prefill_is_ignored(user_client, world):
    for bad in ("abc", "99999", "-1", "1;2", ""):
        page = user_client.get(f"/activities/new?manual=1&customer_id={bad}&solution_id={bad}").text
        assert _selected(page, "customer_ids") == [] and _selected(page, "solution_ids") == []
        assert "에 연결해 저장합니다" not in user_client.get(f"/activities/new?customer_id={bad}").text


def test_save_raw_keeps_the_prefilled_link(user_client, db, world):
    r = user_client.post("/activities/raw", data={"raw_text": "메모", "customer_id": str(world.c_green.id), "solution_id": "99999"}, follow_redirects=False)
    assert r.status_code == 303 and "notice=created" in nloc(r)
    a = db.query(Activity).order_by(Activity.id.desc()).first()
    assert [c.id for c in a.customers] == [world.c_green.id] and a.solutions == []


def test_ai_draft_path_keeps_working_and_carries_the_prefill(user_client, db, world):
    r = user_client.post("/activities/draft", data={"raw_text": "메모", "customer_id": str(world.c_green.id)}, follow_redirects=False)
    assert r.status_code == 303
    job = db.query(AIJob).order_by(AIJob.id.desc()).first()
    assert job.input["prefill"] == {"customer": [world.c_green.id]}
    page = user_client.get(r.headers["location"]).text
    assert "AI" in page  # 진행 화면
    job.status, job.result = "done", '{"kind":"meeting","date":"2026-11-01","title":"t","summary":"s","mentions":{}}'
    db.commit()
    assert _selected(user_client.get(r.headers["location"]).text, "customer_ids") == [world.c_green.id]


def test_proposal_new_prefill_for_a_solution_on_hold_checks_the_extra_picker(user_client, db, world):
    world.s_sensor.stage = "hold"
    db.commit()
    page = user_client.get(f"/proposals/new?customer_id={world.c_hanbit.id}&solution_id={world.s_sensor.id}").text
    assert _selected(page, "extra_solution_ids") == [world.s_sensor.id]
    assert "아래 &#34;직접 추가&#34; 칸에서 미리 골라 두었습니다" in page or "직접 추가" in page
    assert "설비 예지보전 센서 중심으로 제안" in page and '<details class="wide extra-pick" open>' in page
    assert f'<option value="{world.c_hanbit.id}" selected>' in page


def test_proposal_new_prefill_for_a_candidate_solution_writes_the_request_memo(user_client, world):
    page = user_client.get(f"/proposals/new?solution_id={world.s_hvac.id}").text
    assert "이 솔루션을 우선 검토하도록 요청 메모에 적었습니다" in page and "AI 공조 최적제어 중심으로 제안</textarea>" in page
    assert _selected(page, "extra_solution_ids") == []


def test_proposal_new_ignores_invalid_values_and_still_takes_customer_and_opportunity(user_client, world):
    page = user_client.get("/proposals/new?solution_id=999&customer_id=abc&project_id=77").text
    assert "중심으로 제안" not in page and 'selected>' not in page.split('name="customer_id"')[1].split("</select>")[0].replace('<option value="" selected>', "")
    page = user_client.get(f"/proposals/new?customer_id={world.c_hanbit.id}").text
    assert f'<option value="{world.c_hanbit.id}" selected>' in page
    page = user_client.get(f"/proposals/new?opportunity_id={world.o_hanbit.id}").text
    assert f'<option value="{world.o_hanbit.id}" selected>' in page


def test_opportunity_and_project_new_prefill_customer_only_when_it_exists(user_client, world):
    for url in ("/opportunities/new", "/projects/new"):
        assert f'<option value="{world.c_hanbit.id}" selected>' in user_client.get(f"{url}?customer_id={world.c_hanbit.id}").text
        bad = user_client.get(f"{url}?customer_id=99999").text.split('name="customer_id"')[1].split("</select>")[0]
        assert "selected" not in re.sub(r'<option value="" selected>', "", bad)
    need = world.a_hanbit2.needs[0]
    page = user_client.get(f"/opportunities/new?customer_id={world.c_hanbit.id}&need_id={need.id}").text
    assert f'value="{need.id}" checked' in page
    assert f'<option value="{world.p_green.id}" selected>' in user_client.get(f"/solutions/new?partner_id={world.p_green.id}").text


# ---- 4. 시작 질문 ----

def _chat_page(user_client, p):
    return user_client.get(f"/proposals/{p.id}").text


def test_chat_starters_appear_only_while_the_direction_has_no_messages(user_client, db, settings):
    p = refining_proposal(db, settings)
    page = _chat_page(user_client, p)
    assert len(ontology.PROPOSAL_CHAT_STARTERS) in (4, 5)
    for text in ontology.PROPOSAL_CHAT_STARTERS:
        assert f'name="message" value="{text}"' in page and f'data-starter="{text}"' in page
    assert f'action="/proposals/{p.id}/chat" class="starters"' in page  # 스크립트 없이도: 칩 하나가 그 글을 보내는 작은 폼 버튼
    db.add(ProposalMessage(proposal_id=p.id, role="user", content="공조만 하자"))
    db.commit()
    after = _chat_page(user_client, p)
    assert 'action="/proposals/%d/chat" class="starters" aria-label="이렇게 물어볼 수 있어요"' % p.id not in after  # 펼쳐 둔 시작 줄은 없고
    assert '<details class="starters-more">' in after and "<summary>예시 질문</summary>" in after  # 접힌 "예시 질문" 안에 칩이 남는다
    assert '<details class="starters-more" open' not in after and all(f'value="{t}"' in after.split("starters-more")[1] for t in ontology.PROPOSAL_CHAT_STARTERS)


def test_chat_starters_come_back_for_a_new_direction(user_client, db, settings):
    p = refining_proposal(db, settings)
    m = ProposalMessage(proposal_id=p.id, role="user", content="이전 방향 대화")
    db.add(m)
    db.commit()
    p.chat_from_id = m.id  # 방향을 바꾸면 이전 대화는 새 방향에 반영되지 않는다
    db.commit()
    assert 'aria-label="이렇게 물어볼 수 있어요"' in _chat_page(user_client, p)


def test_clicking_a_starter_without_javascript_sends_it_as_the_message(user_client, db, settings):
    p = refining_proposal(db, settings)
    text = ontology.PROPOSAL_CHAT_STARTERS[0]
    r = user_client.post(f"/proposals/{p.id}/chat", data={"message": text}, follow_redirects=False)
    assert r.status_code == 303
    assert [m.content for m in db.query(ProposalMessage).filter_by(proposal_id=p.id)] == [text]


def test_composer_has_the_shortcut_hint(user_client, db, settings):
    p = refining_proposal(db, settings)
    assert "Ctrl+Enter" in _chat_page(user_client, p)


# ---- 7. 홈 내 할 일 ----

def _home_fu(page):
    return page.split('id="h-fu"')[1].split("</section>")[0]


def test_home_my_follow_ups_switch(user_client, db, kim):
    a = Activity(kind="meeting", title="회의", raw_text="r")
    a.follow_ups = [
        FollowUp(position=0, task="내 일", owner="kim", owner_user_id=kim.id, due=date.today() + timedelta(days=1)),
        FollowUp(position=1, task="남의 일", owner="lee", due=date.today() + timedelta(days=2)),
        FollowUp(position=2, task="담당 없음", due=date.today() + timedelta(days=3)),
    ]
    db.add(a)
    db.commit()
    all_card = _home_fu(user_client.get("/").text)
    assert "내 일" in all_card and "남의 일" in all_card and "담당 없음" in all_card
    assert re.search(r'<a href="/\?fu=mine">내 할 일<b>1</b></a>', all_card) and re.search(r'<a href="/" aria-current="page">14일 이내<b>3</b></a>', all_card)
    mine = _home_fu(user_client.get("/?fu=mine").text)
    assert "내 일" in mine and "남의 일" not in mine and "담당 없음" not in mine
    assert re.search(r'<a href="/\?fu=mine" aria-current="page">내 할 일<b>1</b></a>', mine)


def test_home_my_follow_ups_empty_state_explains_how_to_become_mine(user_client, db):
    a = Activity(kind="meeting", title="회의", raw_text="r", follow_ups=[FollowUp(position=0, task="남의 일", owner="lee", due=date.today() + timedelta(days=1))])
    db.add(a)
    db.commit()
    mine = _home_fu(user_client.get("/?fu=mine").text)
    assert "내 담당 후속 조치가 없습니다" in mine and "담당" in mine and "내 이름(kim)" in mine and "14일 이내 전체에는 1건" in mine
    assert "남의 일" not in mine


def test_home_summary_sentence_ignores_the_switch(user_client, db):
    a = Activity(kind="meeting", title="회의", raw_text="r", follow_ups=[FollowUp(position=0, task="늦은 일", owner="lee", due=date.today() - timedelta(days=3))])
    db.add(a)
    db.commit()
    assert "기한이 지난 후속 조치 1건" in user_client.get("/?fu=mine").text


# ---- 8. 올리기 요약 ----

def _plan(intake_files, items):
    return {"items": items}


def test_plan_summary_is_built_from_the_stored_plan(db):
    it = Intake(note="n", status="ready")
    db.add(it)
    db.flush()
    for i in range(7):
        db.add(IntakeFile(intake_id=it.id, original_name=f"f{i}.txt", stored_name=f"s{i}", kind="document"))
    items = ([{"key": "a", "kind": "new_customer"}] + [{"key": f"b{i}", "kind": "add_to_solution"} for i in range(2)]
             + [{"key": "c", "kind": "add_contact"}] + [{"key": f"d{i}", "kind": "activity"} for i in range(2)] + [{"key": "e", "kind": "unplaced"}])
    it.plan = {"items": items}
    db.commit()
    db.refresh(it)
    text = intake_lib.plan_summary(it)
    assert text == "파일 7개에서 6건을 제안했습니다 · 솔루션에 자료 추가 2 · 연락처 추가 1 · 활동 기록 2 · 새 고객 1 · 정리하지 못한 파일 1"
    it.plan = {"items": [{"key": "a", "kind": "activity"}]}
    assert intake_lib.plan_summary(it) == "파일 7개에서 1건을 제안했습니다 · 활동 기록 1"  # 0 건인 종류는 뺀다
    assert intake_lib.outcome_text(it) == ""  # 옛 묶음·저장 전
    it.plan = {"items": [], "outcome": {"proposed": 6, "saved": 5, "excluded": 1}}
    assert intake_lib.outcome_text(it) == "6건 중 5건 저장 · 1건 제외"
    it.plan = {"items": [], "outcome": {"proposed": 2, "saved": 2, "excluded": 0}}
    assert intake_lib.outcome_text(it) == "2건 중 2건 저장"


def test_kind_names_come_from_the_ontology(db):
    it = Intake(note="n", status="ready", plan={"items": [{"key": k, "kind": k} for k in ontology.INTAKE_ITEM_KINDS]})
    db.add(it)
    db.commit()
    text = intake_lib.plan_summary(it)
    for name in ontology.INTAKE_ITEM_KINDS.values():
        assert f"{name} 1" in text


# ---- 9. 정렬 ----

def _order(html, titles):
    pos = [(html.index(t), t) for t in titles]
    return [t for _, t in sorted(pos)]


def test_sort_options_default_to_todays_order_and_whitelist(user_client, db):
    db.add_all([Customer(name="나", updated_at=datetime(2026, 1, 3)), Customer(name="가", updated_at=datetime(2026, 1, 1)), Customer(name="다", updated_at=datetime(2026, 1, 2))])
    db.commit()
    names_ = ["가", "나", "다"]
    default = user_client.get("/customers").text
    assert _order(default, ["row-title\">가", "row-title\">나", "row-title\">다"]) == ["row-title\">가", "row-title\">나", "row-title\">다"]
    assert _order(user_client.get("/customers?sort=recent").text, ["row-title\">가", "row-title\">나", "row-title\">다"]) == ["row-title\">나", "row-title\">다", "row-title\">가"]
    assert user_client.get("/customers?sort=<bad>").text.count("row-title") == default.count("row-title")
    assert _order(user_client.get("/customers?sort=bad").text, ["row-title\">가", "row-title\">나", "row-title\">다"]) == ["row-title\">가", "row-title\">나", "row-title\">다"]
    assert names_  # noqa


def test_default_sorts_match_the_lists_current_order():
    assert ontology.LIST_SORTS["opportunity"][0][0] == "recent" and ontology.LIST_SORTS["proposal"][0][0] == "recent"
    assert ontology.LIST_SORTS["activity"][0][0] == "date" and [c for c, _ in ontology.LIST_SORTS["activity"]] == ["date", "recent", "name"]
    for t in ("solution", "customer", "partner", "project", "bm"):
        assert ontology.LIST_SORTS[t][0][0] == "name"


def test_sort_control_links_keep_other_conditions(user_client, db):
    db.add(Solution(name="가 솔루션", stage="tapping"))
    db.commit()
    page = user_client.get("/solutions?stage=tapping&q=가&sort=recent").text
    nav = page.split('class="sortbar"')[1].split("</nav>")[0]
    assert 'href="/solutions?stage=tapping&amp;q=%EA%B0%80"' in nav  # 기본(이름순) 링크에는 sort 가 없다
    assert re.search(r'href="/solutions\?stage=tapping&amp;q=%EA%B0%80&amp;sort=recent" aria-current="true">최근 수정순', nav)
    tabs = page.split('class="seg"')[1].split("</nav>")[0]
    assert "sort=recent" in tabs  # 구분 탭으로 옮겨도 정렬이 남는다
    assert 'name="sort" value="recent"' in page  # 목록 검색도


def test_sort_preserved_across_pagination(user_client, db):
    db.add_all([Customer(name=f"고객{i:03d}") for i in range(60)])
    db.commit()
    page = user_client.get("/customers?sort=recent").text
    assert "sort=recent" in page.split('class="pager"')[1]


def test_activity_sorts(user_client, db):
    db.add_all([Activity(kind="meeting", title="가 회의", raw_text="r", date=date(2026, 1, 1), updated_at=datetime(2026, 3, 1)),
                Activity(kind="meeting", title="나 회의", raw_text="r", date=date(2026, 2, 1), updated_at=datetime(2026, 1, 1)),
                Activity(kind="meeting", title="다 회의", raw_text="r", date=date(2026, 3, 1), updated_at=datetime(2026, 2, 1))])
    db.commit()
    t = ["가 회의", "나 회의", "다 회의"]
    assert _order(user_client.get("/activities").text, t) == ["다 회의", "나 회의", "가 회의"]  # 일자순(최근 것이 위)이 기본
    assert _order(user_client.get("/activities?sort=name").text, t) == t
    assert _order(user_client.get("/activities?sort=recent").text, t) == ["가 회의", "다 회의", "나 회의"]
    for page in ("/solutions", "/partners", "/projects", "/opportunities", "/bms", "/proposals", "/activities", "/customers"):
        assert 'class="sortbar"' in user_client.get(page).text, page
    assert 'class="sortbar"' not in user_client.get("/follow-ups").text


# ---- 10. 조용한 빈 칸 ----

def test_empty_optional_cells_show_a_muted_dash(user_client, db):
    db.add(Solution(name="파트너 없는 솔루션"))
    db.commit()
    row = user_client.get("/solutions").text.split("<tbody>")[1]
    assert '<span class="dash-none muted" role="img" aria-label="없음">–</span>' in row
    assert "없음</span>" not in row.replace('aria-label="없음">–</span>', "")


def test_detail_pages_keep_the_hidden_blank_summary_line(user_client, db):
    c = Customer(name="빈 고객")
    db.add(c)
    db.commit()
    page = user_client.get(f"/customers/{c.id}").text
    assert "아직 입력하지 않은 항목: 업종, 연락처 메모, 메모" in page


# ---- 11·12. 스크립트 보강 (브라우저에서 확인한 동작의 표식) ----

def test_script_hooks_exist():
    from pathlib import Path

    js = Path("app/static/app.js").read_text(encoding="utf-8")
    for needle in ("table.row-click", "beforeunload", "askInline", "data-flash", "requestSubmit", "isComposing", "fitAll", "prefers-reduced-motion"):
        assert needle in js, needle


# ---- 13. 도움말 ----

def test_help_requires_login_and_is_linked_from_the_sidebar(client, user_client):
    r = client.get("/help", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    home = user_client.get("/").text
    assert re.search(r'<a class="side-help" href="/help">도움말</a>\s*<div class="who">', home)
    assert 'aria-current="page"' in user_client.get("/help").text.split('class="side-help')[1][:120]


def test_help_glossary_is_generated_from_the_ontology(user_client):
    page = user_client.get("/help").text
    gl = page.split('class="glossary"')[1].split("</dl>")[0]
    for code in ontology.HELP_GLOSSARY:
        t = ontology.TYPES[code]
        assert t.label in gl and t.description, code
        assert re.sub(r"<[^>]+>", "", t.description).strip() in page.replace("&#39;", "'") or t.description.split(".")[0] in gl, code
    assert [ontology.TYPES[c].label for c in ontology.HELP_GLOSSARY] == [
        "사업 방향", "과제", "솔루션", "파트너", "고객", "영업기회", "프로젝트", "BM", "활동 기록", "고객 니즈", "후속 조치", "제안 건", "올리기 묶음", "자료"]


def test_help_flow_howtos_and_shortcuts(user_client, admin_client):
    page = user_client.get("/help").text
    flow = page.split('class="card stack help-flow"')[1].split("</section>")[0]
    for stage in ontology.STAGE_FLOW:
        assert ontology.STAGES[stage] in flow
    for label in ("파트너", "솔루션", "활동 기록", "고객 니즈", "영업기회", "제안 건", "프로젝트", "BM"):
        assert label in flow
    how = page.split('class="howto"')[1]
    for title in ("자료를 올려 솔루션 등록하기", "미팅 메모 남기기", "니즈를 영업기회로 묶기", "제안서 만들기", "수주 후 프로젝트로 전환하기", "내 할 일 보기", "검색하기", "AI가 응답하지 않을 때 확인할 것"):
        assert title in how
    # 안내가 가리키는 화면은 실제로 열린다
    for href in sorted(set(re.findall(r'href="(/[^"#]*)"', page))):
        if href.startswith("/admin"):
            continue
        assert user_client.get(href).status_code == 200, href
    assert "Ctrl" in page and "<kbd>/</kbd>" in page
    assert "/admin/system" not in page and "/admin/system" in admin_client.get("/help").text


def test_first_run_guide_links_to_help(user_client):
    assert 'href="/help">도움말 보기</a>' in user_client.get("/").text

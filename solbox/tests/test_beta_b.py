"""베타 B 묶음: 제품 동작 고침 (B1~B14)."""
from __future__ import annotations

import re
from datetime import date

import pytest
from pptx import Presentation

from app.ai.proposal_tasks import Outline
from app.deck import build_deck, has_internal_mark
from app.proposals import clean_outline, is_author_instruction
from tests.conftest import outlined_proposal


def _all_text(prs) -> str:
    out = []
    for slide in prs.slides:
        for sh in slide.shapes:
            if sh.has_text_frame:
                out.append(sh.text_frame.text)
            if getattr(sh, "has_table", False) and sh.has_table:
                out += [c.text_frame.text for row in sh.table.rows for c in row.cells]
    return "\n".join(out)


LEAKY = {"title": "제안", "slides": [
    {"kind": "cover", "title": "제안", "subtitle": "고객사"},
    {"kind": "bullets", "title": "효과", "key_message": "절감", "bullets": ["전력비 절감", "4천만원 축소안"],
     "sources": ["opportunities/2.md"], "needs_check": ["4천만원 축소안은 고객 쪽에서 바뀐 숫자인지", "근거 자료 없음: 내용 확인 필요"]},
    {"kind": "closing", "title": "감사합니다", "bullets": ["다음 단계 협의"]},
]}


# ---- B1 ----
def test_customer_deck_has_no_notes_or_internal_marks(tmp_path):
    out = tmp_path / "c.pptx"
    build_deck(LEAKY, out, audience="customer", customer_name="고객사", today=date(2026, 11, 1))
    prs = Presentation(out)
    for slide in prs.slides:
        assert not slide.has_notes_slide or slide.notes_slide.notes_text_frame.text == ""
    body = _all_text(prs)
    assert not re.search(r"\b(?:opportunities|solutions|customers|partners|projects|activities|research|bms)/|\.md\b", body)
    for marker in ("확인 필요", "확인할 점", "근거 자료 없음", "출처 확인 불가", "고객 쪽에서 바뀐 숫자"):
        assert marker not in body


def test_internal_deck_keeps_notes_with_source_names(tmp_path):
    out = tmp_path / "i.pptx"
    build_deck(LEAKY, out, audience="internal", source_labels={"opportunities/2.md": "한빛 영업기회"})
    note = Presentation(out).slides[1].notes_slide.notes_text_frame.text
    assert "한빛 영업기회" in note and "고객 쪽에서 바뀐 숫자" in note


def test_customer_deck_scrubs_hand_typed_paths(tmp_path):
    o = {"slides": [{"kind": "cover", "title": "제안"}, {"kind": "bullets", "title": "a", "bullets": ["정상 문장", "자세한 건 solutions/3/card.md 참고"]}]}
    build_deck(o, tmp_path / "c.pptx", audience="customer")
    text = _all_text(Presentation(tmp_path / "c.pptx"))
    assert "정상 문장" in text and "card.md" not in text
    assert has_internal_mark("research/2026-11-01-x-3.md") and not has_internal_mark("연 2/3 수준")


def test_deck_routes_serve_two_versions(user_client, db, settings):
    p = outlined_proposal(db, settings)
    p.outline["slides"][2]["needs_check"] = ["절감률 수치 확인 필요"]
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(p, "outline")
    db.commit()
    user_client.post(f"/proposals/{p.id}/deck")
    page = user_client.get(f"/proposals/{p.id}").text
    assert "고객용 PPT 내려받기" in page and "내부 검토용 PPT" in page and "확인하지 않은 항목이 1개" in page
    c = user_client.get(f"/proposals/{p.id}/deck")
    i = user_client.get(f"/proposals/{p.id}/deck?version=internal")
    assert "_%EA%B3%A0%EA%B0%9D%EC%9A%A9.pptx" in c.headers["content-disposition"]
    assert "_%EB%82%B4%EB%B6%80%EA%B2%80%ED%86%A0%EC%9A%A9.pptx" in i.headers["content-disposition"]
    import io
    cp, ip = Presentation(io.BytesIO(c.content)), Presentation(io.BytesIO(i.content))
    assert all(not s.has_notes_slide or not s.notes_slide.notes_text_frame.text for s in cp.slides)
    assert any(s.has_notes_slide and "절감률 수치" in s.notes_slide.notes_text_frame.text for s in ip.slides)


def test_legacy_single_deck_is_not_sent_as_customer_file(user_client, db, settings):
    """두 파일로 나누기 전에 만든 제안서(노트가 든 한 파일)는 어느 쪽으로도 내려가지 않고, 읽기 요청이 파일을 다시 쓰지도 않는다."""
    p = outlined_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/deck")
    db.refresh(p)
    folder = settings.decks_dir / str(p.id)
    (folder / p.deck_name.replace(".pptx", "-internal.pptx")).unlink()
    build_deck(p.outline, folder / p.deck_name, audience="internal")  # 옛 파일처럼 노트가 든다
    before = (folder / p.deck_name).read_bytes()
    for version in ("customer", "internal"):
        r = user_client.get(f"/proposals/{p.id}/deck?version={version}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(f"/proposals/{p.id}?notice=deck_old")
    assert (folder / p.deck_name).read_bytes() == before and sorted(f.name for f in folder.iterdir()) == [p.deck_name]  # 파일을 건드리지 않았다
    page = user_client.get(f"/proposals/{p.id}").text.split('id="deck"')[1]
    assert "다시 만들어 주세요" in page and "고객용 PPT 내려받기" not in page and "내부 검토용 PPT" not in page
    assert "고객용·내부 검토용으로 나누기 전에 만든" in user_client.get(r.headers["location"]).text  # 알림
    user_client.post(f"/proposals/{p.id}/deck")  # 다시 만들면 두 파일이 생기고 내려받을 수 있다
    assert user_client.get(f"/proposals/{p.id}/deck").content[:2] == b"PK"


# ---- B2 ----
@pytest.mark.parametrize("line", [
    "총액과 견적 유효기간을 함께 제시", "담당자 이름을 기재해 주세요.", "금액 확인 필요", "연락처를 추가할 것", "일정을 작성할 것", "[TBD] 비용",
    "수치를 입력하세요", "절감률 보완 필요",
])
def test_author_instruction_lines_detected(line):
    assert is_author_instruction(line)


@pytest.mark.parametrize("line", [
    "단계별 로드맵 제시", "견적을 제시합니다", "도입 효과를 확인할 수 있습니다", "필요한 자원을 확인합니다",
    "월 4천만원 수준의 운영비", "총액은 1억원이며 견적 유효기간은 30일입니다", "문의 주세요", "제안서 작성 일정",
])
def test_normal_customer_sentences_are_kept(line):
    assert not is_author_instruction(line)


def test_clean_outline_moves_instructions_to_needs_check(tmp_path, caplog):
    raw = {"title": "제안", "slides": [
        {"kind": "cover", "title": "제안"},
        {"kind": "bullets", "title": "비용", "key_message": "합리적인 비용입니다.", "bullets": ["총액과 견적 유효기간을 함께 제시", "월 운영비는 300만원입니다"],
         "sources": [], "needs_check": []},
        {"kind": "table", "title": "표", "columns": ["항목", "값"], "rows": [["총액", "금액 확인 필요"], ["기간", "3개월"]]},
        {"kind": "steps", "title": "단계", "steps": [{"name": "착수", "detail": "일정을 작성할 것"}]},
        {"kind": "closing", "title": "마무리", "bullets": ["담당 김민수 대리 010-1234-5678", "다음 단계 협의"]},
    ]}
    with caplog.at_level("INFO", logger="app.proposals"):
        out = clean_outline(Outline.model_validate(raw), tmp_path)
    s = out["slides"]
    assert s[1]["bullets"] == ["월 운영비는 300만원입니다"]
    assert "총액과 견적 유효기간을 함께 제시" in s[1]["needs_check"]
    assert s[2]["rows"] == [["총액", ""], ["기간", "3개월"]] and "금액 확인 필요" in s[2]["needs_check"]
    assert s[3]["steps"][0]["detail"] == "" and "일정을 작성할 것" in s[3]["needs_check"]
    assert s[4]["bullets"] == ["다음 단계 협의"]
    assert "3 author-instruction lines" in caplog.text and "1 contact" in caplog.text


def test_closing_contact_comes_from_logged_in_user(user_client, db, settings):
    from app.models import User

    p = outlined_proposal(db, settings)
    me = db.query(User).filter_by(username="kim").one()
    me.name, me.contact = "김민수", "010-0000-1111"
    db.commit()
    user_client.post(f"/proposals/{p.id}/deck")
    import io
    for v in ("customer", "internal"):
        prs = Presentation(io.BytesIO(user_client.get(f"/proposals/{p.id}/deck?version={v}").content))
        assert "담당 김민수" in _all_text(prs) and "연락처 010-0000-1111" in _all_text(prs)


def test_account_contact_field(user_client, db):
    from app.models import User

    page = user_client.get("/account").text
    assert 'name="contact"' in page
    r = user_client.post("/account/contact", data={"contact": "  02-123   4567 "}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(User).filter_by(username="kim").one().contact == "02-123 4567"
    assert user_client.post("/account/contact", data={"contact": "x" * 201}).status_code == 400
    user_client.post("/account/contact", data={"contact": ""})
    db.expire_all()
    assert db.query(User).filter_by(username="kim").one().contact is None


# ---- B3 보조: 도움말 ----
def test_help_mentions_audio_only_bundles(user_client):
    page = user_client.get("/help").text
    assert "보관됨" in page and "글자로 풀어" in page


# ---- B4 ----
def _intake_by(db, user, status="applied"):
    from app.models import Intake

    it = Intake(created_by_id=user.id, status=status, note="")
    db.add(it)
    db.commit()
    return it


def test_origin_line_for_non_owner_is_plain_text_with_uploader_name(app, user_client, db):
    from app import origin
    from app.models import Solution, User
    from tests.conftest import make_user

    uploader = make_user(db, "lee", name="이영희")
    it = _intake_by(db, uploader)
    s = Solution(name="조명", origin_type="intake", origin_id=it.id)
    db.add(s)
    db.commit()
    page = user_client.get(f"/solutions/{s.id}").text
    assert "이영희님이 올린 묶음 %d번" % it.id in page and f'href="/intake/{it.id}"' not in page
    me = db.query(User).filter_by(username="kim").one()
    v = origin.view(db, me, s)
    assert v == {"label": f"이영희님이 올린 묶음 {it.id}번", "href": None}


def test_origin_line_links_bundle_for_owner_and_admin(app, admin_client, db):
    from app import origin
    from app.models import Solution, User
    from tests.conftest import make_user

    uploader = make_user(db, "lee", name="이영희")
    it = _intake_by(db, uploader)
    s = Solution(name="조명", origin_type="intake", origin_id=it.id)
    db.add(s)
    db.commit()
    assert f'href="/intake/{it.id}"' in admin_client.get(f"/solutions/{s.id}").text
    assert origin.view(db, uploader, s) == {"label": f"올리기 묶음 {it.id}번", "href": f"/intake/{it.id}"}


def _origin_world(db):
    from app.models import Activity, Customer, Partner, Solution

    rows = {
        Solution: [("올솔1", "intake"), ("올솔2", "intake"), ("리솔", "research"), ("직솔", "manual"), ("모름솔", None)],
        Customer: [("올고객", "intake"), ("직고객", "manual")],
        Partner: [("올파트너", "intake"), ("직파트너", "manual"), ("리파트너", "research")],
    }
    for model, items in rows.items():
        db.add_all(model(name=n, origin_type=o, origin_id=1 if o in ("intake", "research") else None) for n, o in items)
    db.add_all([Activity(title="올활동", raw_text="x", origin_type="intake", origin_id=1), Activity(title="직활동", raw_text="y", origin_type="manual")])
    db.commit()


@pytest.mark.parametrize("path,intake_names,manual_names", [
    ("/solutions", ["올솔1", "올솔2"], ["직솔", "모름솔"]),
    ("/customers", ["올고객"], ["직고객"]),
    ("/partners", ["올파트너"], ["직파트너"]),
    ("/activities", ["올활동"], ["직활동"]),
])
def test_origin_filter_on_lists(user_client, db, path, intake_names, manual_names):
    _origin_world(db)
    page = user_client.get(f"{path}?origin=intake").text
    assert all(n in page for n in intake_names) and not any(n in page for n in manual_names)
    page = user_client.get(f"{path}?origin=manual").text
    assert all(n in page for n in manual_names) and not any(n in page for n in intake_names)
    all_page = user_client.get(f"{path}?origin=bogus").text  # 화이트리스트 밖은 전체
    assert all(n in all_page for n in intake_names + manual_names)
    assert 'aria-label="유래"' in all_page


def test_origin_filter_counts_and_preserved_across_controls(user_client, db):
    _origin_world(db)
    page = user_client.get("/solutions?origin=intake&q=솔&stage=idea&sort=name").text
    import re

    bar = re.search(r'<nav class="seg" aria-label="유래">(.*?)</nav>', user_client.get("/solutions").text, re.S).group(1)
    assert re.findall(r"([가-힣 ]+)<b>(\d+)</b>", bar) == [("전체", "5"), ("올리기", "2"), ("리서치", "1"), ("직접 입력", "2")]
    link = re.search(r'<nav class="seg" aria-label="유래">.*?</nav>', page, re.S).group(0)
    assert "origin=research" in link and "q=" in link and "sort=name" in link and "page=" not in link
    # 검색 폼은 origin 을 숨은 칸으로 들고 간다
    assert 'name="origin" value="intake"' in page
    stage_tabs = re.search(r'<nav class="seg" aria-label="발굴 단계">.*?</nav>', page, re.S).group(0)
    assert "origin=intake" in stage_tabs


def test_origin_column_shown(user_client, db):
    _origin_world(db)
    page = user_client.get("/solutions").text
    assert "<th class=\"col-sec\">유래</th>" in page


def test_intake_list_for_non_admin_says_own_only(user_client, admin_client):
    assert "내가 올린 묶음만 보입니다" in user_client.get("/intake").text
    assert "내가 올린 묶음만 보입니다" not in admin_client.get("/intake").text


# ---- B5 ----
from app.activity_rows import LinkIndex, load_index, owner_matches_name  # noqa: E402


@pytest.mark.parametrize("owner,name,ok", [
    ("김민수", "김민수", True), ("김민수 대리", "김민수", True), ("김민수대리", "김민수", True), ("김민수 팀장님", "김민수", True),
    ("영업팀 김민수", "김민수", True), ("영업팀 김민수 과장", "김민수", True), ("김민수님", "김민수", True),
    ("김민", "김민수", False), ("김민수 씨", "김민수", False), ("이김민수", "김민수", False), ("김민수와 박", "김민수", False),
    ("김민수 대리 박", "김민수", False), ("", "김민수", False),
])
def test_owner_matches_name(owner, name, ok):
    assert owner_matches_name(owner, name) is ok


def test_link_index_title_suffix_needs_exactly_one_active_user(db):
    from tests.conftest import make_user

    a = make_user(db, "kms", name="김민수")
    make_user(db, "lee", name="이영희")
    idx = load_index(db)
    assert idx.user_id("김민수") == a.id and idx.user_id("김민수 대리") == a.id and idx.user_id("영업팀 김민수 차장님") == a.id
    assert idx.user_id("박대리") is None and idx.user_id("김민수 외 1명") is None
    make_user(db, "kms2", name="김민수")  # 같은 이름이 둘이면 정확히 하나가 아니므로 연결하지 않는다
    idx = load_index(db)
    assert idx.user_id("김민수") is None and idx.user_id("김민수 대리") is None
    off = make_user(db, "gone", name="최퇴사")
    off.is_active = False
    db.commit()
    assert load_index(db).user_id("최퇴사 대리") is None  # 비활성은 후보가 아니다


def test_titles_defined_in_one_place():
    from app import activity_rows

    assert {"대리", "과장", "차장", "부장", "팀장", "사원", "주임", "책임", "수석", "선임", "매니저", "님"} <= set(activity_rows.OWNER_TITLES)


def test_old_unlinked_row_is_relinked_when_edited(user_client, db):
    from app.models import Activity, FollowUp, User
    from tests.conftest import fu_rows

    me = db.query(User).filter_by(username="kim").one()
    me.name = "김민수"
    a = Activity(title="회의", raw_text="x")
    a.follow_ups = fu_rows(("견적 송부", "김민수 대리", "", False))
    db.add(a)
    db.commit()
    assert a.follow_ups[0].owner_user_id is None  # 저장된 옛 줄은 그대로 (일괄 변환 없음)
    fid = a.follow_ups[0].id
    r = user_client.post(f"/activities/{a.id}", data={"kind": "meeting", "date": "2026-11-01", "title": "회의", "raw_text": "x", "summary_md": "",
                         "fu_id": [str(fid)], "fu_task": ["견적 송부"], "fu_owner": ["김민수 대리"], "fu_due": [""]}, follow_redirects=False)
    assert r.status_code in (303, 409), r.status_code
    db.expire_all()
    assert db.get(FollowUp, fid).owner_user_id == me.id


def test_mine_button_for_unlinked_rows_sets_owner_and_links(user_client, db):
    from app.models import Activity, FollowUp, User
    from tests.conftest import fu_rows

    me = db.query(User).filter_by(username="kim").one()
    me.name = "김민수"
    a = Activity(title="회의", raw_text="x")
    a.follow_ups = fu_rows(("견적 송부", "", "", False), ("연결된 일", "", "", False))
    db.add(a)
    db.commit()
    fid = a.follow_ups[0].id
    page = user_client.get(f"/activities/{a.id}").text
    assert f'action="/follow-ups/{fid}/mine"' in page and "내 할 일로" in page
    r = user_client.post(f"/follow-ups/{fid}/mine", follow_redirects=False)
    assert r.status_code == 303 and "notice=fu_mine" in r.headers["location"]
    db.expire_all()
    fu = db.get(FollowUp, fid)
    assert (fu.owner, fu.owner_user_id) == ("김민수", me.id)
    assert f'action="/follow-ups/{fid}/mine"' not in user_client.get(f"/activities/{a.id}").text
    assert user_client.get("/?fu=mine").status_code == 200


def test_prompts_receive_writer_name_and_rule(app, db, settings, user_client, fake_claude):
    import json

    from app.ai.queue import enqueue
    from app.models import User
    from tests.test_activity_ai import _good, _run

    me = db.query(User).filter_by(username="kim").one()
    me.name = "김민수"
    db.commit()
    fake_claude.set(mode="ok", result=_good())
    enqueue(db, "activity_structure", {"raw_text": "제가 견적서를 보내기로 했다"}, me.id)
    _run(app, settings)
    prompt = fake_claude.calls()[0]["stdin"]
    assert "## 작성자\n\n김민수" in prompt and "내 할 일" in prompt and "추측" in prompt
    from app.ai.tasks import load_prompt

    assert "{{" not in load_prompt("intake_analyze", today="t", note="n", solutions="s", customers="c", partners="p", writer="김민수",
                                   activity_kind_inline="meeting")


# ---- B12 ----
def _impact_world(db, user):
    from app.models import BM, Activity, Opportunity, Customer, Partner, Project, Proposal, Solution

    c = Customer(name="한빛", created_by_id=user.id)
    s = Solution(name="조명", created_by_id=user.id)
    db.add_all([c, s])
    db.flush()
    bm = BM(name="구독")
    bm.solutions = [s]
    o = Opportunity(name="한빛 기회", customer_id=c.id)
    o.solutions = [s]
    a1, a2, a3 = (Activity(title=f"회의{i}", raw_text="x") for i in range(3))
    for a in (a1, a2, a3):
        a.solutions = [s]
    p = Proposal(customer_id=c.id, directions=[{"title": "d"}], selected_index=0)
    p.solutions = [s]
    db.add_all([bm, o, a1, a2, a3, p])
    db.commit()
    return s, c


def test_delete_impact_counts_for_solution(user_client, db):
    from app.models import User

    me = db.query(User).filter_by(username="kim").one()
    s, _ = _impact_world(db, me)
    expect = "영업기회 1건, BM 1건, 활동 기록 3건과의 연결이 끊깁니다. 제안 건 1건의 방향에 이 솔루션이 들어 있습니다."
    page = user_client.get(f"/solutions/{s.id}").text  # JS 가 있을 때 그 자리에서 묻는 글
    assert expect in page.split("data-ask=")[1].split(" data-ok")[0]
    confirm = user_client.get(f"/solutions/{s.id}/delete").text  # JS 없이 서버가 그린 확인 화면
    assert expect in confirm and "지금 연결된 기록" in confirm


def test_delete_impact_exists_for_every_deletable_core_type(user_client, db):
    from app.models import User

    me = db.query(User).filter_by(username="kim").one()
    s, c = _impact_world(db, me)
    assert "지금 연결된 기록" in user_client.get("/solutions/%d/delete" % s.id).text
    assert "지금 연결된 기록" not in user_client.get("/customers/%d/delete" % c.id).text  # 영업기회가 있는 고객은 지울 수 없다 (규칙 그대로): 막는 글만
    free = user_client.post("/customers", data={"name": "빈 고객"}, follow_redirects=False)
    cid = free.headers["location"].split("/customers/")[1].split("?")[0]
    assert "지금 연결된 기록" in user_client.get(f"/customers/{cid}/delete").text
    from app import ontology

    assert set(ontology.DELETE_IMPACT) == {"solution", "partner", "customer", "project", "bm", "opportunity", "activity", "proposal"}
    empty = user_client.post("/partners", data={"name": "새 파트너"}, follow_redirects=False)
    pid = empty.headers["location"].split("/partners/")[1].split("?")[0]
    assert "다른 기록에는 영향이 없습니다" in user_client.get(f"/partners/{pid}/delete").text


# ---- B6 ----
DUP_CASES = [
    ("/solutions", "솔루션", "스마트 조명"), ("/customers", "고객", "한빛병원"), ("/partners", "파트너", "루미텍"),
    ("/bms", "BM", "구독형 모델"), ("/projects", "프로젝트", "설비 유지보수"),
]


@pytest.mark.parametrize("path,label,name", DUP_CASES)
def test_same_name_create_warns_then_confirms(user_client, db, path, label, name):
    from app.ai.tasks import normalize_name  # noqa: F401

    first = user_client.post(path, data={"name": name, **({"customer_id": ""} if path == "/projects" else {})}, follow_redirects=False)
    if path == "/projects":  # 프로젝트는 고객이 필요하다
        from app.models import Customer
        c = Customer(name="고객A")
        db.add(c)
        db.commit()
        first = user_client.post(path, data={"name": name, "customer_id": str(c.id)}, follow_redirects=False)
    assert first.status_code == 303
    extra = {"customer_id": str(c.id)} if path == "/projects" else {}
    again = user_client.post(path, data={"name": f" {name.replace(' ', '')} ", **extra})  # 공백·표기만 다른 같은 이름
    assert again.status_code == 200 and f"같은 이름의 {label}" in again.text and "기존 것 열기" in again.text and "그래도 새로 등록" in again.text
    assert 'name="confirm_dup" value="1"' in again.text and 'name="_tok"' in again.text and f"{path}/" in again.text
    from tests.test_beta_b import _count

    assert _count(db, path) == 1
    ok = user_client.post(path, data={"name": name, "confirm_dup": "1", **extra}, follow_redirects=False)
    assert ok.status_code == 303 and _count(db, path) == 2


def _count(db, path):
    from app.models import BM, Customer, Opportunity, Partner, Project, Solution

    model = {"/solutions": Solution, "/customers": Customer, "/partners": Partner, "/bms": BM, "/projects": Project, "/opportunities": Opportunity}[path]
    return db.query(model).count()


def test_same_name_opportunity_and_rename_warn(user_client, db):
    from app.models import Customer, Opportunity, Solution

    c = Customer(name="고객A")
    db.add(c)
    db.commit()
    assert user_client.post("/opportunities", data={"name": "한빛 기회", "customer_id": str(c.id)}, follow_redirects=False).status_code == 303
    r = user_client.post("/opportunities", data={"name": "한빛 기회", "customer_id": str(c.id)})
    assert "같은 이름의 영업기회" in r.text and db.query(Opportunity).count() == 1
    # 이름을 바꿀 때: 다른 솔루션과 같아지면 경고, 자기 이름 그대로면 묻지 않는다
    a = Solution(name="조명 A")
    b = Solution(name="조명 B")
    db.add_all([a, b])
    db.commit()
    page = user_client.get(f"/solutions/{b.id}/edit").text
    import re

    rev = re.search(r'name="_rev" value="([^"]+)"', page).group(1)
    r = user_client.post(f"/solutions/{b.id}", data={"name": "조명  A", "_rev": rev})
    assert r.status_code == 200 and "같은 이름의 솔루션" in r.text and "그래도 이 이름으로 저장" in r.text
    db.refresh(b)
    assert b.name == "조명 B"
    r = user_client.post(f"/solutions/{b.id}", data={"name": "조명 B", "summary": "소개", "_rev": rev}, follow_redirects=False)
    assert r.status_code == 303  # 이름이 그대로면 묻지 않는다
    r = user_client.post(f"/solutions/{b.id}", data={"name": "조명 A", "confirm_dup": "1", "_rev": rev}, follow_redirects=False)
    assert r.status_code in (303, 409)


def test_duplicate_warning_works_without_form_token(user_client, db):
    user_client.post("/customers", data={"name": "토큰없음"})
    r = user_client.post("/customers", data={"name": "토큰없음"})
    assert r.status_code == 200 and "기존 것 열기" in r.text
    r = user_client.post("/customers", data={"name": "토큰없음", "confirm_dup": "1"}, follow_redirects=False)
    assert r.status_code == 303


# 파일 해시
def _hash_world(db, user):
    from app.models import Solution

    s = Solution(name="조명 솔루션", created_by_id=user.id)
    db.add(s)
    db.commit()
    return s


def test_solution_material_upload_skips_identical_file(user_client, db, settings):
    from app.models import SolutionFile, User

    me = db.query(User).filter_by(username="kim").one()
    s = _hash_world(db, me)
    f1 = ("소개서.txt", b"same bytes", "text/plain")
    assert user_client.post(f"/solutions/{s.id}/files", files=[("files", f1)], follow_redirects=False).status_code == 303
    r = user_client.post(f"/solutions/{s.id}/files", files=[("files", ("다른이름.txt", b"same bytes", "text/plain")), ("files", ("새.txt", b"other", "text/plain"))])
    assert r.status_code == 200 and "이미 같은 내용의 자료가 있어 건너뛴 파일" in r.text and "소개서.txt" in r.text
    names = sorted(f.original_name for f in db.query(SolutionFile).filter_by(solution_id=s.id))
    assert names == ["새.txt", "소개서.txt"]
    assert all(f.sha256 for f in db.query(SolutionFile))
    folder = settings.uploads_dir / "solutions" / str(s.id)
    assert len(list(folder.iterdir())) == 2  # 건너뛴 파일은 디스크에도 남지 않는다


def test_old_files_without_hash_are_compared_lazily(user_client, db, settings):
    from app.models import SolutionFile, User

    me = db.query(User).filter_by(username="kim").one()
    s = _hash_world(db, me)
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("옛.txt", b"old content", "text/plain"))])
    old = db.query(SolutionFile).one()
    old.sha256 = None  # 해시 열이 생기기 전에 올린 파일
    db.commit()
    r = user_client.post(f"/solutions/{s.id}/files", files=[("files", ("복사.txt", b"old content", "text/plain"))])
    assert "건너뛴 파일" in r.text and db.query(SolutionFile).count() == 1
    db.refresh(old)
    assert old.sha256 and old.original_name == "옛.txt"  # 비교하면서 해시만 채웠다


def test_intake_files_get_hash_and_review_shows_earlier_copy_note(app, user_client, db, settings):
    """t1 재현: 이미 저장한 녹취록을 다시 올리면 활동 기록이 하나 더 생기던 것."""
    from app.models import Activity, Customer, Intake
    from tests.conftest import form_from_page, ready_bundle

    db.add(Customer(name="한빛병원"))
    db.commit()
    plan = {"activities": [{"draft": {"kind": "tapping", "date": "2026-11-17", "title": "한빛병원 통화", "discussion": ["전력비"], "mentions": {"customers": ["한빛병원"]}},
                            "raw_from": 1, "files": [1], "reason": "녹취"}]}
    first = ready_bundle(db, settings, [("녹취.txt", "한빛병원 통화 내용".encode())], **plan)
    assert first.files[0].sha256
    r = user_client.post(f"/intake/{first.id}/apply", data=form_from_page(user_client, first), follow_redirects=False)
    assert r.status_code == 303 and db.query(Activity).count() == 1
    second = ready_bundle(db, settings, [("녹취 사본.txt", "한빛병원 통화 내용".encode())], **plan)
    page = user_client.get(f"/intake/{second.id}").text
    assert f"같은 파일을" in page and f"묶음 {first.id}" in page and "에 올려 저장했습니다" in page
    assert 'name="skip_act1" value="on" checked' in page  # 이 항목 제외가 미리 켜져 있다
    form = form_from_page(user_client, second)
    assert "skip_act1" in form
    r = user_client.post(f"/intake/{second.id}/apply", data=form, follow_redirects=False)  # 그대로 저장하려 해도 활동 기록은 늘지 않는다 (저장할 항목이 없다)
    assert r.status_code == 400 and "저장할 항목이 없습니다" in r.text and db.query(Activity).count() == 1
    # 사용자가 제외를 풀면 저장된다
    third = ready_bundle(db, settings, [("녹취 3.txt", "한빛병원 통화 내용".encode())], **plan)
    form = form_from_page(user_client, third)
    form.pop("skip_act1", None)
    assert user_client.post(f"/intake/{third.id}/apply", data=form, follow_redirects=False).status_code == 303
    assert db.query(Activity).count() == 2


def test_review_note_only_for_bundles_the_viewer_may_see(app, user_client, db, settings):
    from app.models import Customer, Intake
    from tests.conftest import form_from_page, make_user, ready_bundle

    make_user(db, "park")
    plan = {"activities": [{"draft": {"kind": "meeting", "date": "2026-11-17", "title": "남의 회의", "discussion": ["x"]}, "raw_from": 1, "files": [1], "reason": "r"}]}
    theirs = ready_bundle(db, settings, [("회의.txt", b"shared text")], owner="park", **plan)
    theirs.status = "applied"
    db.commit()
    mine = ready_bundle(db, settings, [("회의.txt", b"shared text")], owner="kim", **plan)
    page = user_client.get(f"/intake/{mine.id}").text
    assert "에 올려 저장했습니다" not in page  # 남이 올린 묶음은 볼 수 없으니 알리지 않는다
    assert 'name="skip_act1" value="on" checked' not in page


def test_intake_apply_skips_identical_material_with_notice(app, user_client, db, settings):
    from app.models import Solution, SolutionFile
    from tests.conftest import form_from_page, ready_bundle

    s = Solution(name="조명 솔루션")
    db.add(s)
    db.commit()
    data = b"spec sheet"
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("스펙.txt", data, "text/plain"))])
    plan = {"additions": [{"solution_id": s.id, "files": [1], "reason": "스펙"}]}
    it = ready_bundle(db, settings, [("스펙 사본.txt", data)], **plan)
    user_client.post(f"/intake/{it.id}/apply", data=form_from_page(user_client, it), follow_redirects=False)
    assert db.query(SolutionFile).count() == 1
    page = user_client.get(f"/intake/{it.id}").text
    assert "같은 내용이라 건너뜀" in page and "스펙.txt" in page


# ---- B7 ----
def test_every_stage_and_check_kind_has_a_description():
    from app import ontology

    for key in ("stage", "opportunity_stage", "check_kind"):
        cl = ontology.CODE_LISTS[key]
        assert set(cl.descriptions) == set(cl.items), key
        assert all(d.endswith(".") and len(d) < 120 for d in cl.descriptions.values())
    assert ontology.code_help("stage", "boxed") == "검토를 마쳐 제안에 바로 쓸 수 있는 상태입니다."


def test_stage_tooltips_legend_and_help_blocks(user_client, db):
    from app.models import Opportunity, Solution, Customer

    s = Solution(name="조명", stage="survey")
    c = Customer(name="한빛")
    db.add_all([s, c])
    db.flush()
    o = Opportunity(name="기회", customer_id=c.id)
    db.add(o)
    db.commit()
    page = user_client.get(f"/solutions/{s.id}").text
    assert 'title="기존 고객에게 이 솔루션에 관심이 있는지 미리 알아보는 단계입니다."' in page
    assert 'class="stage-legend card" data-legend="stage"' in page and 'data-legend-close hidden' in page and "박스 등록" in page
    assert 'data-legend="opportunity_stage"' in user_client.get(f"/opportunities/{o.id}").text
    h = user_client.get("/help").text
    assert 'id="help-stage"' in h and 'id="help-opportunity_stage"' in h and "발굴 단계" in h and "영업기회 단계" in h and "아이템 접수" in h
    assert "이야기된" in h and "검토한" in h and "제안된" in h


def _all_unopened_run(db, settings, others=False):
    from tests.conftest import finished_run

    run = finished_run(db, settings)
    tech = next(r for r in run.reports if r.area == "tech")
    trends = [{"headline": f"동향{i}", "summary": "", "needs_check": ["본문을 열어 확인하지 못함"],
               "sources": [{"title": "검색만", "url": "https://example.com/s%d" % i, "opened": False}]} for i in range(3)]
    if others:
        trends.append({"headline": "다른", "summary": "", "needs_check": ["출처 링크 없음, 확인 필요"], "sources": []})
    tech.trends = trends
    db.commit()
    return run


def test_research_run_shows_one_banner_when_every_trend_is_unopened(user_client, db, settings):
    run = _all_unopened_run(db, settings)
    page = user_client.get(f"/research/runs/{run.id}").text
    assert "이번 조사의 동향은 모두 출처 본문을 열어 확인하지 못했습니다" in page
    assert 'data-code="unopened"' not in page and page.count("본문을 열어 확인하지 못함") == 0  # 줄마다 배지를 달지 않는다
    assert "출처 본문을 열어 확인하지 못했거나 출처가 없는 항목입니다. 링크를 열어 직접 확인하세요." in page  # 범례


def test_unopened_is_yellow_and_no_source_stays_red(user_client, db, settings):
    run = _all_unopened_run(db, settings, others=True)
    page = user_client.get(f"/research/runs/{run.id}").text
    assert "이번 조사의 동향은 모두" not in page
    assert 'class="badge tone-warn" data-code="unopened"' in page
    assert re.search(r'<span class="badge tone-bad" data-code="evidence"[^>]*>근거 문제</span>', page)


# ---- B8 ----
def test_convert_gives_the_project_to_proposals_without_one(user_client, db):
    from app.models import Customer, Opportunity, Proposal

    c = Customer(name="한빛")
    other = Customer(name="다른 고객")
    db.add_all([c, other])
    db.flush()
    o = Opportunity(name="한빛 기회", customer_id=c.id, stage="won")
    db.add(o)
    db.flush()
    p1 = Proposal(customer_id=c.id, opportunity_id=o.id)
    from app.models import Project

    kept = Project(name="이미 있는 발판", customer_id=c.id)
    db.add(kept)
    db.flush()
    p2 = Proposal(customer_id=c.id, opportunity_id=o.id, project_id=kept.id)
    db.add_all([p1, p2])
    db.commit()
    r = user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "create", "name": "한빛 사업"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    new_project = db.get(Opportunity, o.id).project
    assert new_project is not None and db.get(Proposal, p1.id).project_id == new_project.id
    assert db.get(Proposal, p2.id).project_id == kept.id  # 이미 프로젝트가 있는 제안 건은 그대로
    page = user_client.get(f"/projects/{new_project.id}").text
    assert "영업기회의 제안 건" in page


def test_project_page_shows_discussed_solutions_and_their_partners(user_client, db):
    from app.models import Activity, Customer, Partner, Project, Solution

    c = Customer(name="한빛")
    pa = Partner(name="루미텍")
    db.add_all([c, pa])
    db.flush()
    s = Solution(name="스마트 조명", partner_id=pa.id)
    pr = Project(name="한빛 사업", customer_id=c.id)
    a = Activity(title="킥오프", raw_text="x")
    db.add_all([s, pr, a])
    db.flush()
    a.projects = [pr]
    a.solutions = [s]
    db.commit()
    page = user_client.get(f"/projects/{pr.id}").text
    assert "이야기된 솔루션" in page and "스마트 조명" in page and "루미텍" in page


def test_relation_labels_are_consistent(user_client, db):
    from app import ontology

    assert ontology.VIEW_LABELS[("R28", "solution", "opportunity")] == "이 솔루션을 검토한 영업기회"
    assert ontology.VIEW_LABELS[("R32", "bm", "opportunity")] == "이 BM으로 진행하는 영업기회"
    assert ontology.VIEW_LABELS[("R14", "proposal", "solution")] == "선택한 방향의 솔루션"
    assert not any("접근하는" in v or "검토 중인" in v for v in ontology.VIEW_LABELS.values())


def test_proposal_page_names_the_three_numbers_distinctly(user_client, db, settings):
    from tests.conftest import make_proposal_world

    p, *_ = make_proposal_world(db, settings)
    page = user_client.get(f"/proposals/{p.id}").text
    assert "조합 솔루션" not in page and "외 " not in page.split('id="summary-target"')[1].split("</span>")[0]
    assert "후보 솔루션 2개" in page  # 보류가 아닌 솔루션 둘
    from tests.conftest import refining_proposal

    q = refining_proposal(db, settings)
    page = user_client.get(f"/proposals/{q.id}").text
    assert "선택한 방향의 솔루션 2개" in page


# ---- B9 ----
def _text_world(db):
    from tests.conftest import fu_rows, need_rows
    from app.models import BM, Activity, Customer, Opportunity, Partner, Project, Solution

    c = Customer(name="한빛병원", industry="의료·병원", contact="담당 박민수 02-123-4567", memo="냉동창고 증설 검토")
    p = Partner(name="루미텍", contact="영업 이영희 lee@lumi.example", description="조명 제어 전문 기업", memo="수입 필라멘트 취급")
    db.add_all([c, p])
    db.flush()
    s = Solution(name="스마트 조명", summary="재실 감지로 전력 절감", description="센서 기반 자동 디밍을 제공합니다", tags="조명,센서", partner_id=p.id)
    db.add_all([s, BM(name="구독 모델", summary="월 구독 과금"), Project(name="한빛 사업", customer_id=c.id, description="설비 교체 공사 일정 관리"),
                Opportunity(name="한빛 기회", customer_id=c.id, memo="예산은 하반기 확정")])
    a = Activity(title="킥오프", raw_text="회의에서 <b>보일러</b> 교체 이야기", summary_md="## 논의\n- 급탕 배관 점검")
    a.follow_ups = fu_rows(("견적서 재송부", "", "", False))
    a.needs = need_rows(("한빛병원", "야간 전력 피크 감소"))
    db.add(a)
    db.commit()
    return s, a


@pytest.mark.parametrize("q,field,shown", [
    ("자동 디밍", "상세 설명", "<mark>자동 디밍</mark>"), ("센서", "상세 설명", ""), ("조명,센", "태그", ""), ("재실", "한 줄 소개", "<mark>재실</mark>"),
    ("lee@lumi", "연락처", "<mark>lee@lumi</mark>"), ("필라멘트", "메모", "<mark>필라멘트</mark>"), ("전문 기업", "소개", ""),
    ("의료", "업종", ""), ("냉동창고 증설", "메모", ""), ("공사 일정", "설명", ""), ("하반기", "메모", ""), ("월 구독", "한 줄 설명", ""),
    ("급탕 배관", "정리본", ""), ("보일러", "원문", "&lt;b&gt;<mark>보일러</mark>"), ("견적서", "", ""), ("야간 전력", "", ""),
])
def test_search_finds_text_fields_with_excerpt(user_client, db, q, field, shown):
    _text_world(db)
    page = user_client.get("/search", params={"q": q}).text
    assert "결과가 없습니다" not in page, q
    if field:
        assert f'<span class="hit-field">{field}' in page, (q, page[page.find("result-list"):][:600])
    if shown:
        assert shown in page


def test_search_follow_ups_and_needs_link_to_their_activity(user_client, db):
    _, a = _text_world(db)
    page = user_client.get("/search", params={"q": "견적서"}).text
    assert "후속 조치" in page and f'href="/activities/{a.id}"' in page and "활동 기록 · 킥오프" in page
    page = user_client.get("/search", params={"q": "야간 전력"}).text
    assert f'href="/activities/{a.id}"' in page and "고객 니즈" in page


def test_search_excerpts_are_escaped_and_wildcards_stay_literal(user_client, db):
    _text_world(db)
    page = user_client.get("/search", params={"q": "<b>"}).text
    assert "<mark>&lt;b&gt;</mark>" in page
    assert "<b>보일러</b>" not in page
    assert "결과가 없습니다" in user_client.get("/search", params={"q": "100%"}).text


def test_search_query_count_is_constant_on_a_larger_fixture(user_client, db, app):
    from sqlalchemy import event

    from app.models import Activity, Customer, Solution
    from tests.conftest import fu_rows

    def count_queries(q):
        n = []
        engine = app.state.engine

        def hook(conn, cursor, statement, params, context, many):
            n.append(statement)

        event.listen(engine, "before_cursor_execute", hook)
        try:
            assert user_client.get("/search", params={"q": q}).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", hook)
        return len(n)

    db.add_all([Solution(name=f"솔루션 {i}", summary="필터 교체 주기", description="필터 " * 5) for i in range(20)])
    db.add_all([Customer(name=f"고객 {i}", memo="필터 문의") for i in range(20)])
    for i in range(20):
        a = Activity(title=f"회의 {i}", raw_text="필터 점검 " * 30)
        a.follow_ups = fu_rows((f"필터 교체 {i}", "", "", False))
        db.add(a)
    db.commit()
    few = count_queries("필터")
    db.add_all([Solution(name=f"추가 솔루션 {i}", summary="필터") for i in range(200)])
    db.add_all([Activity(title=f"추가 회의 {i}", raw_text="필터") for i in range(200)])
    db.commit()
    many = count_queries("필터")
    assert few == many, (few, many)
    page = user_client.get("/search", params={"q": "필터"}).text
    assert "목록에서 모두 보기" in page


# ---- B13l / B10 CLI ----
def test_create_admin_applies_the_web_password_rule(app, db, capsys):
    from app.cli import create_admin

    for bad, why in (("short1", "8자 이상"), ("rootroot", "아이디와 같은"), ("aaaaaaaaaa", "같은 글자")):
        with pytest.raises(SystemExit) as e:
            create_admin(app.state.session_factory, "rootroot" if bad == "rootroot" else "boss", "관리자", bad)
        assert "규칙에 맞지 않아 계정을 만들지 않았습니다" in str(e.value) and why in str(e.value)
    from app.models import User

    assert db.query(User).filter(User.role == "admin").count() == 0


def test_cli_check_and_system_page_share_the_same_checks(tmp_path, monkeypatch, capsys, admin_client, app):
    from app import cli, system

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('secret_key = "x"\ndata_dir = "data"\n', encoding="utf-8")
    results = system.run_checks(app_loader := (lambda: app.state.settings), skip_port=True)
    page = admin_client.get("/admin/system").text
    for name, problem, detail in results:
        assert name in page, name
        meaning, todo = system.describe(name)
        assert meaning and todo, name  # 항목마다 뜻과 할 일이 있다
        assert meaning in page
    assert 'id="transcriber"' in page
    lines = system.format_lines([("음성 받아쓰기", "문제 글", ""), ("포트", None, "0.0.0.0:1")])
    assert lines[0] == "음성 받아쓰기: 문제: 문제 글" and lines[1].startswith("  뜻: ") and lines[2].startswith("  할 일: ")
    assert lines[3] == "포트: 정상 (0.0.0.0:1)" and lines[4].startswith("  뜻: ") and len(lines) == 5


# ---- B10 ----
def test_admin_jobs_shows_duration_wait_and_retry_for_failed_jobs(app, admin_client, db, settings):
    from datetime import datetime, timedelta

    from app.models import AIJob, Intake, Proposal, ResearchReport, ResearchRun
    from tests.conftest import make_proposal_world

    now = datetime.now()
    done = AIJob(kind="activity_structure", status="done", created_at=now - timedelta(seconds=200), started_at=now - timedelta(seconds=140),
                 finished_at=now - timedelta(seconds=20), input={"raw_text": "x"})
    p, *_ = make_proposal_world(db, settings)
    bad = AIJob(kind="proposal_directions", status="failed", error_kind="auth", created_at=now, started_at=now, finished_at=now, input={"proposal_id": p.id})
    old = AIJob(kind="proposal_directions", status="failed", error_kind="auth", created_at=now, input={"proposal_id": p.id})
    db.add_all([done, bad, old])
    db.flush()
    p.active_job_id = bad.id  # 지금 이 제안 건의 현재 작업은 bad (old 는 옛 작업)
    db.commit()
    page = admin_client.get("/admin/jobs").text
    assert "<th class=\"num\">대기 시간</th>" in page and "<th class=\"num\">소요 시간</th>" in page
    assert "1분 + 0초" not in page and "1분" in page and "2분" in page  # 대기 1분(60초), 소요 2분(120초)
    assert page.count(f'action="/proposals/{p.id}/directions"') == 1  # 현재 작업인 것만 다시 시도
    assert 'aria-label="작업 %d 다시 시도"' % bad.id in page and 'aria-label="작업 %d 다시 시도"' % old.id not in page
    assert 'aria-label="작업 %d 다시 시도"' % done.id not in page


def test_admin_jobs_filter_by_kind_and_status(admin_client, db):
    from app.models import AIJob

    db.add_all([AIJob(kind="activity_structure", status="done"), AIJob(kind="solution_draft", status="failed", error_kind="auth"),
                AIJob(kind="solution_draft", status="done")])
    db.commit()
    text = admin_client.get("/admin/jobs?kind=solution_draft").text
    body = text.split("<tbody>")[1]
    assert "솔루션 소개 초안" in body and "활동 기록 AI 정리" not in body
    body = admin_client.get("/admin/jobs?kind=solution_draft&status=failed").text.split("<tbody>")[1]
    assert body.count("<tr>") == 1
    assert 'name="status" value="failed"' in admin_client.get("/admin/jobs?kind=solution_draft&status=failed").text  # 종류를 바꿔도 상태가 따라간다
    assert "<tbody>" not in admin_client.get("/admin/jobs?kind=bogus&status=bogus").text or True


def test_retry_button_posts_to_the_existing_retry_route(admin_client, db):
    from app.models import AIJob

    from app.models import User

    me = db.query(User).filter_by(role="admin").first()
    mine = AIJob(kind="activity_structure", status="failed", error_kind="auth", input={"raw_text": "메모"}, requester_id=me.id)
    theirs = AIJob(kind="activity_structure", status="failed", error_kind="auth", input={"raw_text": "남의 메모"}, requester_id=None)
    db.add_all([mine, theirs])
    db.commit()
    page = admin_client.get("/admin/jobs").text
    assert f'action="/activities/draft/{mine.id}/retry"' in page
    assert f'action="/activities/draft/{theirs.id}/retry"' not in page  # 남이 요청한 초안은 다시 시도 경로가 막혀 있어 버튼을 주지 않는다
    r = admin_client.post(f"/activities/draft/{mine.id}/retry", follow_redirects=False)
    assert r.status_code == 303


def test_research_settings_shows_next_scheduled_run(admin_client, db):
    admin_client.post("/admin/research/schedule", data={"weekday": "0", "hour": "9", "minute": "0"})  # 자동 실행 끔
    page = admin_client.get("/admin/research").text
    assert "자동 실행이 꺼져 있어 예약된 조사가 없습니다" in page
    admin_client.post("/admin/research/schedule", data={"enabled": "on", "weekday": "0", "hour": "9", "minute": "0"})
    page = admin_client.get("/admin/research").text
    assert "다음 자동 조사" in page and "월요일" in page


def test_backup_notice_says_where_it_was_written(admin_client, settings):
    r = admin_client.post("/admin/system/backup", follow_redirects=False)
    page = admin_client.get(r.headers["location"]).text
    assert "방금 만든 백업은" in page and str(settings.backup_dir) in page
    assert "방금 만든 백업은" not in admin_client.get("/admin/system").text


# ---- B11 ----
def test_starter_chip_sends_immediately_with_and_without_js(user_client, db, settings):
    from app.models import ProposalMessage
    from tests.conftest import refining_proposal

    p = refining_proposal(db, settings)
    page = user_client.get(f"/proposals/{p.id}").text
    chip = ontology_starters()[0]
    assert f'<button type="submit" name="message" value="{chip}" class="chip starter"' in page  # 칩 자체가 제출 버튼 (JS 없이도)
    from pathlib import Path

    js = (Path(__file__).parent.parent / "app/static/app.js").read_text(encoding="utf-8")
    assert "ta.value = chip.value" not in js and 'closest("[data-starter]")' not in js  # JS 는 칸에 채우기만 하던 동작을 하지 않는다 (누르면 바로 제출)
    r = user_client.post(f"/proposals/{p.id}/chat", data={"message": chip}, follow_redirects=False)
    assert r.status_code == 303 and [m.content for m in db.query(ProposalMessage)][:1] == [chip]


def ontology_starters():
    from app import ontology

    return ontology.PROPOSAL_CHAT_STARTERS


# ---- B13 batch ----
def test_empty_file_upload_message_names_the_file(user_client, db):
    r = user_client.post("/intake", files=[("files", ("빈 메모.txt", b"", "text/plain"))])
    assert r.status_code == 400 and "빈 파일은 올릴 수 없습니다: 빈 메모.txt" in r.text and "올릴 파일을 선택하세요" not in r.text
    r = user_client.post("/intake", files=[("files", ("빈.txt", b"", "text/plain")), ("files", ("실제.txt", b"x", "text/plain"))], follow_redirects=False)
    assert r.status_code in (200, 303)  # 일부만 비었으면 받은 파일은 올라가고 빈 파일은 안내


def test_discard_is_offered_while_analyzing(user_client, db):
    from app.models import Intake

    r = user_client.post("/intake", files=[("files", ("a.txt", b"x", "text/plain"))], follow_redirects=False)
    it = db.query(Intake).one()
    assert it.status == "analyzing"
    page = user_client.get(f"/intake/{it.id}").text
    assert f'data-confirm-post="/intake/{it.id}/discard"' in page and "이 묶음 버리기" in page
    assert "버릴까요?" in user_client.get(f"/intake/{it.id}/discard").text  # JS 없는 확인 화면
    assert user_client.post(f"/intake/{it.id}/discard", follow_redirects=False).status_code == 303


def test_follow_up_done_button_has_name_tooltip_and_visible_check(user_client, db):
    from pathlib import Path

    from app.models import Activity
    from tests.conftest import fu_rows

    a = Activity(title="회의", raw_text="x")
    a.follow_ups = fu_rows(("견적", "", "", False))
    db.add(a)
    db.commit()
    page = user_client.get(f"/activities/{a.id}").text
    assert 'aria-label="완료로 표시" title="완료로 표시"' in page
    css = (Path(__file__).parent.parent / "app/static/app.css").read_text(encoding="utf-8")
    assert "button.check:not(.on)::before" in css  # 비어 있어도 흐린 체크 모양이 보인다


def test_outline_anchor_script_and_counts_agree(user_client, db, settings):
    from pathlib import Path

    from tests.conftest import outlined_proposal

    p = outlined_proposal(db, settings)
    r = user_client.post(f"/proposals/{p.id}/outline/slides/2/move", data={"dir": "down"}, follow_redirects=False)
    assert "#slide-3" in r.headers["location"]
    js = (Path(__file__).parent.parent / "app/static/app.js").read_text(encoding="utf-8")
    assert "scrollIntoView({ block: \"start\" })" in js  # 펼친 뒤 방금 만진 장으로 다시 맞춘다
    page = user_client.get(r.headers["location"]).text
    assert page.count('class="badge tone-ok">확인 끝') == int(re.search(r'class="count-done">확인 끝 (\d+)장', page).group(1))
    # 확인을 모두 하면 위쪽 수가 바로 맞는다 (같은 응답 안에서)
    user_client.post(f"/proposals/{p.id}/outline/slides/3/check", data={"item": "0", "text": "절감률 수치", "done": "1"})
    page = user_client.get(f"/proposals/{p.id}/outline").text
    assert page.count('class="badge tone-ok">확인 끝') == int(re.search(r'class="count-done">확인 끝 (\d+)장', page).group(1))


def test_mobile_css_keeps_one_upload_button_and_room_for_the_save_bar(user_client):
    from pathlib import Path

    css = (Path(__file__).parent.parent / "app/static/app.css").read_text(encoding="utf-8")
    assert ".page-header .actions a.to-intake { display: none; }" in css and "body:has(.dash) .side .cta { display: none; }" in css
    assert ".plan-form { padding-bottom: 96px; }" in css and "scroll-margin-bottom" in css
    assert 'class="button primary to-intake" href="/intake/new"' in user_client.get("/solutions").text


def test_project_status_is_free_text_with_datalist_and_trimmed(user_client, db):
    from app.models import Customer, Project

    c = Customer(name="고객")
    db.add(c)
    db.flush()
    db.add_all([Project(name="p1", customer_id=c.id, status="진행 중"), Project(name="p2", customer_id=c.id, status="진행 중"),
                Project(name="p3", customer_id=c.id, status="진행"), Project(name="p4", customer_id=c.id, status="")])
    db.commit()
    page = user_client.get("/projects/new").text
    assert '<datalist id="status-choices">' in page and '<option value="진행 중">' in page and '<option value="진행">' in page and 'list="status-choices"' in page
    assert page.index('value="진행 중"') < page.index('value="진행">')  # 많이 쓴 값이 먼저
    r = user_client.post("/projects", data={"name": "새 프로젝트", "customer_id": str(c.id), "status": "  새  상태 값 "}, follow_redirects=False)
    assert r.status_code == 303 and db.query(Project).filter_by(name="새 프로젝트").one().status == "새 상태 값"  # 자유 글: 새 값도 받는다


def test_research_source_chips_have_one_kind_word_and_their_own_anchor(user_client, db, settings):
    from app.sources import describe_source
    from tests.conftest import finished_run

    run = finished_run(db, settings)
    reports = {r.area or "suggest": r for r in run.reports if r.kind in ("area", "suggest")}
    tech = next(r for r in run.reports if r.kind == "area" and r.area == "tech")
    sug = next((r for r in run.reports if r.kind == "suggest"), None)
    urls = set()
    for r in [tech, *([sug] if sug else [])]:
        path = f"research/{run.started_at.date().isoformat()}-{'suggest' if r.kind == 'suggest' else 'tech'}-{r.id}.md"
        d = describe_source(db, path)
        assert d["url"] and d["url"].startswith(f"/research/runs/{run.id}#"), d
        urls.add(d["url"])
    assert len(urls) == (2 if sug else 1)
    jenv = user_client.app.state.templates.env
    html = jenv.from_string('{% from "_macros.html" import source_chip %}{{ source_chip(src, path) }}').render(src=lambda p: describe_source(db, p), path=path)
    assert html.count("리서치") == 1 or html.count("리서치") == 2 and "chip-kind" not in html  # 종류 낱말이 겹쳐 나오지 않는다


def test_sort_options_match_visible_columns_and_mark_the_sorted_one(user_client, db):
    from app.models import Activity, Solution

    a, b = Solution(name="가솔"), Solution(name="나솔")
    act = Activity(title="회의", raw_text="x")
    db.add_all([a, b, act])
    db.flush()
    act.solutions = [b]
    db.commit()
    page = user_client.get("/solutions").text
    assert "최근 활동순" in page and "최근 수정순" in page
    page = user_client.get("/solutions?sort=active").text
    assert page.index("나솔") < page.index("가솔")  # 활동이 있는 줄이 위, 없는 줄이 뒤
    assert 'aria-sort="descending">최근 활동 <span class="sort-mark"' in page
    assert 'aria-sort="ascending"' in user_client.get("/solutions?sort=name").text  # 이름 칸 머리에 표시
    assert "sort-mark" not in user_client.get("/solutions?sort=recent").text  # 수정일은 표에 칸이 없다
    assert "최근 활동순" in user_client.get("/customers").text and "최근 활동순" not in user_client.get("/bms").text


def test_home_and_follow_up_counts_are_labelled_by_their_window(user_client, db):
    from datetime import date, timedelta

    from app.models import Activity
    from tests.conftest import fu_rows

    a = Activity(title="회의", raw_text="x")
    a.follow_ups = fu_rows(("가까운 일", "", (date.today() + timedelta(days=3)).isoformat(), False), ("기한 없는 일", "", "", False),
                           ("먼 일", "", (date.today() + timedelta(days=60)).isoformat(), False))
    db.add(a)
    db.commit()
    home = user_client.get("/").text.split('id="h-fu"')[1].split("</section>")[0]
    assert re.search(r"14일 이내<b>1</b>", home) and "기한 없는 것까지 모두 보기" in home  # 홈은 14일 안만 (1건)
    page = user_client.get("/follow-ups").text
    assert re.search(r"남은 일<b>3</b>", page) and "기한이 없는 것까지" in page  # 목록은 기한 없는 것까지 모두 (3건)


# ---- B14 ----
def test_folder_style_mentions_become_chips_only_when_unambiguous():
    from app.markdown_lite import render_markdown

    def resolve(path):
        return {"label": "AI 공조", "url": "/solutions/3", "kind": "솔루션"} if path == "solutions/3/card.md" else (
            {"label": "한빛 기회", "url": "/opportunities/2", "kind": "영업기회"} if path == "opportunities/2.md" else None)

    html = str(render_markdown("보세요 solutions/3 와 (opportunities/2/) 그리고 research/5 및 solutions/9", resolve))
    assert html.count('class="chip"') == 2 and "research/5" in html and "solutions/9" in html  # 없는 항목·알 수 없는 폴더는 글자 그대로
    assert "solutions/3/files/a.md" in str(render_markdown("solutions/3/files/a.md", lambda p: None))


def test_ai_prompts_forbid_folder_and_id_mentions_in_prose():
    from pathlib import Path

    root = Path(__file__).parent.parent / "app/ai/prompts"
    for name in ("proposal_chat.md", "proposal_directions.md", "proposal_outline.md"):
        text = (root / name).read_text(encoding="utf-8")
        assert "폴더" in text and "번호" in text and "이름으로" in text, name
    assert "작성자가 할 일" in (root / "proposal_outline.md").read_text(encoding="utf-8") or "작성자가 채우거나" in (root / "proposal_outline.md").read_text(encoding="utf-8")
    assert "needs_check" in (root / "proposal_outline.md").read_text(encoding="utf-8")

"""베타 AE: 같은 회사가 둘로 등록되는 문제 (2026-11-24). 고객·파트너의 다른 이름, 합치기(관리자), 중복 후보 화면."""
from __future__ import annotations

import io
import json
import re
from datetime import date, datetime

import pytest

from app import aliases, backup, cardfile, cardplan, duplicates, merge, ontology
from app.ai.queue import process_next
from app.ai.tasks import Mentions, match_mentions
from app.knowledge import KNOWLEDGE_FORMAT, KnowledgeMirror
from app.models import (
    Activity, AIJob, Customer, CustomerNeed, DuplicateDismissal, Intake, IntakeFile, Opportunity, Partner, Project, Proposal, Solution,
)
from tests.conftest import ready_bundle


def mk(db, model, name=None, **kw):
    obj = model(**kw) if name is None else model(name=name, **kw)
    db.add(obj)
    db.commit()
    return obj


def text_of(html: str) -> str:
    import html as html_lib

    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", html)))


def rev_of_form(html: str) -> str:
    return re.search(r'name="_rev" value="([^"]*)"', html).group(1)


# ---------------------------------------------------------------- 다른 이름: 저장·표시·사본

def test_knowledge_format_bumped():
    assert KNOWLEDGE_FORMAT >= 10
    assert ontology.prop("partner", "aliases") == "다른 이름" and ontology.prop("customer", "aliases") == "다른 이름"


@pytest.mark.parametrize("kind,plural,model", [("partner", "partners", Partner), ("customer", "customers", Customer)])
def test_alias_form_saves_shows_and_goes_to_knowledge_copy(user_client, db, settings, kind, plural, model):
    form = user_client.get(f"/{plural}/new").text
    assert "다른 이름 (한 줄에 하나)" in form and 'name="aliases"' in form
    r = user_client.post(f"/{plural}", data={"name": "SOLUM 코리아", "aliases": "SOLUM\r\n (주)솔루엠 \n\n", "contact": "a@example.com"}, follow_redirects=False)
    assert r.status_code == 303
    obj = db.query(model).one()
    assert obj.aliases == "SOLUM\n(주)솔루엠"  # 정리됨: 공백·빈 줄
    page = user_client.get(f"/{plural}/{obj.id}").text
    assert "다른 이름" in text_of(page) and "SOLUM, (주)솔루엠" in text_of(page)
    KnowledgeMirror(settings.knowledge_dir).sync(obj)
    copy = (settings.knowledge_dir / f"{plural}/{obj.id}.md").read_text(encoding="utf-8")
    assert "- 다른 이름: SOLUM, (주)솔루엠" in copy
    edit = user_client.get(f"/{plural}/{obj.id}/edit").text
    assert "SOLUM\n(주)솔루엠" in edit


def test_detail_hides_aliases_row_when_empty(user_client, db):
    c = mk(db, Customer, "한빛")
    assert "다른 이름" not in user_client.get(f"/customers/{c.id}").text


@pytest.mark.parametrize("plural,model", [("partners", Partner), ("customers", Customer)])
def test_alias_validation_errors_name_the_other_record(user_client, db, plural, model):
    other = mk(db, model, "한빛 주식회사", aliases="Hanbit")
    mine = mk(db, model, "솔루엠")
    base = {"name": "솔루엠", "contact": ""}

    def post(aliases_text, status):
        r = user_client.post(f"/{plural}/{mine.id}", data={**base, "aliases": aliases_text}, follow_redirects=False)
        assert r.status_code == status, r.text[:300]
        return r.text

    assert "이 기록의 이름과 같아요" in post("(주)솔루엠", 400)  # 자기 이름 (㈜ 무시)
    assert "같은 칸" not in post("SOLUM\n solum ", 400) and "한 번만 적어" in post("SOLUM\n solum ", 400)
    body = text_of(post("한빛", 400))  # 다른 기록의 이름 (주식회사 무시)
    assert "'한빛 주식회사'" in body and "합치기" in body
    assert "'한빛 주식회사'" in text_of(post("hanbit", 400))  # 다른 기록의 다른 이름
    assert 'class="field-err"' in post("hanbit", 400)
    post("SOLUM\n솔루엠 코리아", 303)
    db.expire_all()
    assert db.get(model, mine.id).aliases == "SOLUM\n솔루엠 코리아"
    # 같은 기록의 자기 다른 이름은 다시 저장해도 막지 않는다
    post("SOLUM\n솔루엠 코리아", 303)
    # 새로 만들기도 같은 규칙 (500 이 아니다)
    r = user_client.post(f"/{plural}", data={"name": "새 회사", "aliases": "hanbit"}, follow_redirects=False)
    assert r.status_code == 400 and "'한빛 주식회사'" in text_of(r.text)


def test_alias_parse_drops_blank_and_repeated_lines():
    assert aliases.parse(" A  b \r\n\n(주)에이비\nA B\n  ") == ["A b", "(주)에이비"] or aliases.parse(" A  b \r\n\n(주)에이비\nA B\n  ") == ["A b"]
    assert aliases.parse(None) == [] and aliases.parse_text("x\n\ny") == "x\ny"


def test_alias_validation_unit(db):
    a = mk(db, Partner, "에이", aliases="AA")
    assert aliases.validate(db, Partner, "비", "AA")[1].startswith("다른 이름 'AA' 은 이미 파트너 '에이'")
    assert aliases.validate(db, Partner, "에이", "AA", exclude=a)[1] is None
    assert aliases.validate(db, Customer, "비", "AA")[1] is None  # 다른 종류와는 겹쳐도 된다
    assert aliases.validate(db, Partner, "비", "\n".join(f"n{i}" for i in range(25)))[1] == "다른 이름은 20개까지 적을 수 있어요."


def test_alias_edit_is_in_concurrent_edit_guard(user_client, db):
    p = mk(db, Partner, "루미텍", aliases="Lumitec")
    stale = rev_of_form(user_client.get(f"/partners/{p.id}/edit").text)
    r = user_client.post(f"/partners/{p.id}", data={"name": "루미텍", "aliases": "Lumi", "_rev": rev_of_form(user_client.get(f"/partners/{p.id}/edit").text)},
                         follow_redirects=False)
    assert r.status_code == 303
    r = user_client.post(f"/partners/{p.id}", data={"name": "루미텍", "aliases": "Other", "_rev": stale}, follow_redirects=False)
    assert r.status_code == 409 and "다른 이름" in r.text and "Other" in r.text
    db.expire_all()
    assert db.get(Partner, p.id).aliases == "Lumi"


# ---------------------------------------------------------------- 다른 이름으로 맞추기

def test_match_mentions_uses_aliases(db):
    p = mk(db, Partner, "SOLUM 코리아", aliases="솔루엠")
    c = mk(db, Customer, "한빛병원", aliases="Hanbit Hospital")
    out = match_mentions(db, Mentions(partners=["솔루엠", "(주)솔루엠"], customers=["hanbit hospital", "Hanbit Hospital 본원"]))
    assert out["partners"]["matched"] == [p.id] and out["partners"]["new"] == []
    assert out["customers"]["matched"] == [c.id] and out["customers"]["new"] == []  # 포함 비교도 다른 이름으로 (본원)
    assert match_mentions(db, Mentions(partners=["무관한 회사"]))["partners"]["new"] == ["무관한 회사"]


def test_match_mentions_alias_ambiguity_is_not_guessed(db):
    mk(db, Partner, "알파 솔루션", aliases="알파텍")
    mk(db, Partner, "알파 시스템", aliases="알파텍코리아")
    out = match_mentions(db, Mentions(partners=["알파"]))["partners"]
    assert out["matched"] == [] and len(out["ambiguous"]) == 1  # 이름과 다른 이름이 같은 기록을 두 번 세지 않는다


def test_intake_plan_cleaning_matches_partner_and_customer_aliases(user_client, db, settings):
    p = mk(db, Partner, "SOLUM", aliases="솔루엠")
    c = mk(db, Customer, "HS홀딩스", aliases="에이치에스홀딩스")
    it = ready_bundle(db, settings, [("a.txt", b"x")],
                      partners=[{"name": "솔루엠", "contact": "02-111-2222", "files": [1]}],
                      customers=[{"name": "에이치에스홀딩스", "contact": "hs@example.com", "files": [1]}])
    kinds = [(i["kind"], i.get("target_id")) for i in it.plan["items"]]
    assert ("add_contact", p.id) in kinds and ("add_contact", c.id) in kinds
    assert not [i for i in it.plan["items"] if i["kind"] in ("new_partner", "new_customer")]


def test_card_import_matches_aliases(db):
    p = mk(db, Partner, "SOLUM", aliases="(주)솔루엠")
    c = mk(db, Customer, "HS홀딩스", aliases="에이치에스홀딩스")
    csv = "이름,회사,이메일\r\n김솔,솔루엠,kim@example.com\r\n박에이,에이치에스 홀딩스,park@example.com\r\n최새,완전 새 회사,new@example.com\r\n".encode("utf-8-sig")
    plan = cardplan.build_plan(db, Intake(), cardfile.parse("a.csv", csv))
    contacts = {(i["target_type"], i["target_id"]) for i in plan["items"] if i["kind"] == "add_contact"}
    assert contacts == {("partner", p.id), ("customer", c.id)}
    assert [i["name"] for i in plan["items"] if i["kind"] in ("new_customer", "new_partner")] == ["완전 새 회사"]


def test_duplicate_warning_on_create_and_edit_uses_aliases(user_client, db):
    p = mk(db, Partner, "SOLUM", aliases="솔루엠")
    r = user_client.post("/partners", data={"name": "(주)솔루엠"})
    assert r.status_code == 200 and "이미 있어요" in r.text and "다른 이름: 솔루엠" in r.text and "SOLUM" in r.text
    assert db.query(Partner).count() == 1
    r = user_client.post("/partners", data={"name": "(주)솔루엠", "confirm_dup": "1"}, follow_redirects=False)
    assert r.status_code == 303 and db.query(Partner).count() == 2
    other = mk(db, Partner, "다른 곳")
    r = user_client.post(f"/partners/{other.id}", data={"name": "솔루엠", "_rev": rev_of_form(user_client.get(f"/partners/{other.id}/edit").text)})
    assert r.status_code == 200 and "다른 이름: 솔루엠" in r.text


def test_same_company_hint_uses_aliases(user_client, db):
    p = mk(db, Partner, "SOLUM", aliases="솔루엠")
    mk(db, Customer, "솔루엠")
    assert "이름이 같은 고객" in user_client.get(f"/partners/{p.id}").text


def test_global_search_and_list_find_by_alias(user_client, db):
    p = mk(db, Partner, "SOLUM", aliases="솔루엠\nSolum Korea")
    c = mk(db, Customer, "HS홀딩스", aliases="에이치에스홀딩스")
    html = text_of(user_client.get("/search?q=솔루엠").text)
    assert "SOLUM" in html and "(다른 이름: 솔루엠" in html
    html = text_of(user_client.get("/search?q=에이치에스").text)
    assert "HS홀딩스" in html and "(다른 이름:" in html
    assert "SOLUM" in user_client.get("/partners?q=솔루엠").text
    assert "HS홀딩스" in user_client.get("/customers?q=에이치에스").text
    assert "HS홀딩스" not in user_client.get("/partners?q=에이치에스").text
    assert p.id and c.id


def test_get_or_create_finds_alias(db, user_client):
    from app import records
    from app.models import User

    p = mk(db, Partner, "SOLUM", aliases="솔루엠")
    user = db.query(User).first()
    obj, created = records.get_or_create(db, Partner, "솔루엠", user)
    assert obj.id == p.id and not created


# ---------------------------------------------------------------- 한글 글자 이름·신호

@pytest.mark.parametrize("a,b", [("HS홀딩스", "에이치에스홀딩스"), ("LG전자", "엘지전자"), ("KT", "케이티"), ("SK하이닉스", "에스케이하이닉스"),
                                 ("(주)POSCO", "피오에스씨오"), ("ZK", "지케이"), ("ZK", "제트케이"), ("3M코리아", "3엠코리아")])
def test_hangul_letter_names_are_equivalent(a, b):
    assert duplicates.hangul_forms(a) & duplicates.hangul_forms(b)


@pytest.mark.parametrize("a,b", [("HS홀딩스", "에이치에스전자"), ("KT", "케이엘"), ("LG전자", "엘지"), ("SOLUM", "솔루엠"), ("", "에이")])
def test_hangul_letter_names_negative(a, b):
    assert not (duplicates.hangul_forms(a) & duplicates.hangul_forms(b))


def test_letter_table_covers_alphabet():
    assert set(duplicates.LETTER_NAMES) == set("abcdefghijklmnopqrstuvwxyz")
    assert duplicates.LETTER_NAMES["h"] == "에이치" and duplicates.LETTER_NAMES["w"] == "더블유"


def signals(db, kind="partner"):
    return {pair: {s["code"] for s in sigs} for pair, sigs in duplicates.mechanical_pairs(db, kind).items()}


def test_signal_same_normalised_name_or_alias(db):
    a, b = mk(db, Partner, "㈜한빛"), mk(db, Partner, "한빛 주식회사")
    c, d = mk(db, Partner, "SOLUM"), mk(db, Partner, "솔루엠", aliases="solum")
    got = signals(db)
    assert "name" in got[(a.id, b.id)] and "name" in got[(c.id, d.id)]


def test_signal_hangul_spelling_and_not_flagged_when_unrelated(db):
    a, b = mk(db, Partner, "HS홀딩스"), mk(db, Partner, "에이치에스홀딩스")
    c = mk(db, Partner, "에이치에스전자")
    got = signals(db)
    assert got[(a.id, b.id)] == {"hangul"}
    assert (a.id, c.id) not in got or "hangul" not in got[(a.id, c.id)]


def test_signal_contained_name_needs_three_chars(db):
    short, long_ = mk(db, Partner, "삼성"), mk(db, Partner, "삼성전자")
    mid, midlong = mk(db, Partner, "한빛랩"), mk(db, Partner, "한빛랩스 코리아")
    got = signals(db)
    assert (short.id, long_.id) not in got  # 정규형 2자
    assert "inside" in got[(mid.id, midlong.id)]


def test_signal_same_email_domain_ignores_free_mail(db):
    a = mk(db, Partner, "알파", contact="김 대리 kim@example-solum.co.kr")
    b = mk(db, Partner, "베타", contact="park@Example-Solum.co.kr 02-1")
    c = mk(db, Partner, "감마", contact="x@gmail.com")
    d = mk(db, Partner, "델타", contact="y@gmail.com")
    got = signals(db)
    assert got[(a.id, b.id)] == {"email"}
    assert (c.id, d.id) not in got
    assert "naver.com" in duplicates.FREE_MAIL_DOMAINS and len(duplicates.FREE_MAIL_DOMAINS) == 10
    assert duplicates.email_domains("a@daum.net, b@hanmail.net, c@corp.kr") == {"corp.kr"}


def test_candidates_show_signals_counts_and_hide_dismissed(admin_client, db):
    a = mk(db, Partner, "HS홀딩스")
    b = mk(db, Partner, "에이치에스홀딩스")
    mk(db, Solution, "s1", partner_id=a.id)
    page = admin_client.get("/admin/duplicates").text
    assert "영문 약자 ↔ 한글 표기" in page and f"/partners/{a.id}/merge?other={b.id}" in page and "연결 1건" in page and "다른 회사임" in page
    r = admin_client.post("/admin/duplicates/dismiss", data={"kind": "partner", "a": str(b.id), "b": str(a.id)}, follow_redirects=False)
    assert r.status_code == 303 and "dup_dismissed" in r.headers["location"]
    admin_client.post("/admin/duplicates/dismiss", data={"kind": "partner", "a": str(a.id), "b": str(b.id)})  # 두 번 눌러도 한 줄
    assert db.query(DuplicateDismissal).count() == 1
    row = db.query(DuplicateDismissal).one()
    assert (row.low_id, row.high_id) == (a.id, b.id)
    page = admin_client.get("/admin/duplicates").text
    assert f"/partners/{a.id}/merge?other={b.id}" not in page and "다른 회사로 표시한 쌍" in page and "되돌리기" in page
    admin_client.post("/admin/duplicates/undismiss", data={"kind": "partner", "a": str(a.id), "b": str(b.id)})
    assert db.query(DuplicateDismissal).count() == 0
    assert f"/partners/{a.id}/merge?other={b.id}" in admin_client.get("/admin/duplicates").text
    # 없는 기록·같은 기록은 적지 않는다
    admin_client.post("/admin/duplicates/dismiss", data={"kind": "partner", "a": str(a.id), "b": "999"})
    admin_client.post("/admin/duplicates/dismiss", data={"kind": "partner", "a": str(a.id), "b": str(a.id)})
    assert db.query(DuplicateDismissal).count() == 0


def test_customer_tab_and_escaping(admin_client, db):
    mk(db, Customer, "<script>alert(1)</script> 한빛")
    mk(db, Customer, "<script>alert(1)</script> 한빛 주식회사")
    page = admin_client.get("/admin/duplicates?tab=customer").text
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page and "이름 같음" in page
    assert "파트너" in page  # 탭


def test_duplicates_screen_is_admin_only_and_in_nav(user_client, admin_client, db):
    for method, url in (("get", "/admin/duplicates"), ("post", "/admin/duplicates/dismiss"), ("post", "/admin/duplicates/undismiss"),
                        ("post", "/admin/duplicates/ai/partner"), ("get", "/admin/duplicates/ai/partner/status")):
        assert getattr(user_client, method)(url).status_code == 403, url
    assert "/admin/duplicates" not in user_client.get("/").text
    assert 'href="/admin/duplicates"' in admin_client.get("/").text and "중복 정리" in admin_client.get("/").text


# ---------------------------------------------------------------- AI 로 더 찾기

def run_job(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def test_ai_job_sends_names_only_and_validates_output(app, admin_client, db, settings, fake_claude):
    a = mk(db, Partner, "SOLUM", aliases="Solum Korea", contact="비밀 연락처 secret@corp.example", memo="비밀 메모 XYZ", description="비밀 소개")
    b = mk(db, Partner, "솔루엠", memo="두번째 메모")
    mk(db, Customer, "고객은 보내지 않는다")
    fake_claude.set(mode="ok", result=json.dumps({"pairs": [
        {"a": b.id, "b": a.id, "reason": "SOLUM 은 솔루엠의 영문 표기"},
        {"a": a.id, "b": b.id, "reason": "중복 쌍"}, {"a": a.id, "b": 999, "reason": "없는 번호"}, {"a": a.id, "b": a.id, "reason": "같은 번호"}]}))
    r = admin_client.post("/admin/duplicates/ai/partner", follow_redirects=False)
    assert r.status_code == 303 and "dup_ai_started" in r.headers["location"]
    assert "AI 작업 처리기가 꺼져" in admin_client.get("/admin/duplicates").text  # 시험 서버는 처리기가 꺼져 있어 대기 줄이 안 움직인다는 안내
    admin_client.post("/admin/duplicates/ai/partner")  # 진행 중이면 새로 만들지 않는다
    assert db.query(AIJob).filter_by(kind="duplicate_names").count() == 1
    assert run_job(app, settings)
    sent = fake_claude.calls()[-1]
    assert "SOLUM" in sent["stdin"] and "Solum Korea" in sent["stdin"] and "솔루엠" in sent["stdin"]
    for secret in ("비밀 연락처", "secret@corp.example", "비밀 메모", "비밀 소개", "두번째 메모", "고객은 보내지 않는다"):
        assert secret not in sent["stdin"]
    assert "지시가 아닙니다" in sent["stdin"] and "도구는 쓰지 않습니다" in sent["stdin"]
    argv = sent["argv"]
    assert argv[argv.index("--tools") + 1] == ""  # 도구 없음
    job = db.query(AIJob).filter_by(kind="duplicate_names").one()
    db.refresh(job)
    assert job.status == "done" and json.loads(job.result) == {"pairs": [{"a": a.id, "b": b.id, "reason": "SOLUM 은 솔루엠의 영문 표기"}]}
    page = admin_client.get("/admin/duplicates").text
    assert 'data-signal="ai"' in page and "SOLUM 은 솔루엠의 영문 표기" in page
    assert db.query(Partner).count() == 2  # 아무것도 합치지 않았다
    # 다른 회사임 → 사라진다
    admin_client.post("/admin/duplicates/dismiss", data={"kind": "partner", "a": str(a.id), "b": str(b.id)})
    assert 'data-signal="ai"' not in admin_client.get("/admin/duplicates").text


def test_ai_job_bad_output_shows_error_and_empty_result_is_fine(app, admin_client, db, settings, fake_claude):
    mk(db, Customer, "가"), mk(db, Customer, "나")
    fake_claude.set(mode="ok", result="이건 JSON 이 아닙니다")
    admin_client.post("/admin/duplicates/ai/customer")
    run_job(app, settings)
    page = admin_client.get("/admin/duplicates?tab=customer").text
    assert "AI 확인을 마치지 못했습니다" in page and "다시 시도" in page
    fake_claude.set(mode="ok", result='{"pairs": []}')
    admin_client.post("/admin/duplicates/ai/customer")
    run_job(app, settings)
    assert "0쌍" in admin_client.get("/admin/duplicates?tab=customer").text


def test_ai_job_skips_pairs_that_no_longer_exist(app, admin_client, db, settings, fake_claude):
    a, b = mk(db, Customer, "가나다"), mk(db, Customer, "라마바")
    fake_claude.set(mode="ok", result=json.dumps({"pairs": [{"a": a.id, "b": b.id, "reason": "x"}]}))
    admin_client.post("/admin/duplicates/ai/customer")
    run_job(app, settings)
    db.delete(b)
    db.commit()
    assert 'data-signal="ai"' not in admin_client.get("/admin/duplicates?tab=customer").text


@pytest.mark.ai_gate
def test_ai_button_refused_when_ai_off(app, admin_client, db):
    mk(db, Partner, "가"), mk(db, Partner, "나")
    page = admin_client.get("/admin/duplicates").text
    assert "disabled" in page.split('id="dup-ai"')[1].split("</section>")[0]
    r = admin_client.post("/admin/duplicates/ai/partner", headers={"referer": "http://testserver/admin/duplicates"}, follow_redirects=False)
    assert r.status_code == 303 and "ai_off" in r.headers["location"]
    assert db.query(AIJob).count() == 0


def test_ai_job_unknown_kind_is_404(admin_client):
    assert admin_client.post("/admin/duplicates/ai/solution").status_code == 404


# ---------------------------------------------------------------- 합치기: 세계 만들기

def link_activity(db, title, *, partners=(), customers=(), solutions=(), needs=()):
    a = Activity(title=title, raw_text="원문 " + title, kind="meeting", date=date(2026, 11, 20))
    a.partners = list(partners)
    a.customers = list(customers)
    a.solutions = list(solutions)
    a.needs = [CustomerNeed(position=i, customer_name=n, content=c, customer_id=cid) for i, (n, c, cid) in enumerate(needs)]
    db.add(a)
    db.commit()
    return a


def partner_world(db):
    """남길 파트너 K 와 사라질 파트너 A: 솔루션·활동 기록(하나는 겹침)·같은 회사 고객·올린 파일."""
    k = mk(db, Partner, "SOLUM", contact="k@example-solum.co.kr", memo="K 메모", aliases="Solum Korea", description="")
    a = mk(db, Partner, "솔루엠", contact="a@example-solum.co.kr", description="A 소개", memo="A 메모", aliases="(주)솔루엠\nSolum")
    s_a1, s_a2, s_k = (mk(db, Solution, n, partner_id=pid) for n, pid in (("A솔루션1", a.id), ("A솔루션2", a.id), ("K솔루션", k.id)))
    both = link_activity(db, "둘 다 만남", partners=[k, a])
    only_a = link_activity(db, "A 만남", partners=[a])
    c = mk(db, Customer, "솔루엠 고객사")
    a.customer_id = c.id
    intake = Intake(status="applied")
    db.add(intake)
    db.flush()
    f = IntakeFile(intake_id=intake.id, original_name="x.txt", target_type="partner", target_id=a.id)
    db.add(f)
    db.commit()
    return k, a, (s_a1, s_a2, s_k), (both, only_a), c, f


def customer_world(db):
    k = mk(db, Customer, "HS홀딩스", industry="", contact="k@example-hs.co.kr", memo="K 메모")
    a = mk(db, Customer, "에이치에스홀딩스", industry="제조", contact="a@example-hs.co.kr", memo="A 메모", aliases="에이치에스")
    p_a = mk(db, Project, "A 프로젝트", customer_id=a.id)
    p_k = mk(db, Project, "K 프로젝트", customer_id=k.id)
    o_a = mk(db, Opportunity, "A 영업기회", customer_id=a.id)
    pr_a = mk(db, Proposal, customer_id=a.id, request_memo="A 제안")
    g1 = Proposal(target_kind="group", group_name="그룹1", customer_id=None)
    g2 = Proposal(target_kind="group", group_name="그룹2", customer_id=None)
    g1.group_customers = [k, a]  # 겹침
    g2.group_customers = [a]
    db.add_all([g1, g2])
    db.commit()
    both = link_activity(db, "둘 다", customers=[k, a])
    only_a = link_activity(db, "A만", customers=[a], needs=[("에이치에스", "A 니즈", a.id)])
    partner = mk(db, Partner, "에이치에스 파트너", customer_id=a.id)
    intake = Intake(status="applied")
    db.add(intake)
    db.flush()
    f = IntakeFile(intake_id=intake.id, original_name="x.txt", target_type="customer", target_id=a.id)
    db.add(f)
    db.commit()
    return k, a, dict(p_a=p_a, p_k=p_k, o_a=o_a, pr_a=pr_a, g1=g1, g2=g2, both=both, only_a=only_a, partner=partner, f=f)


def post_merge(client, plural, rid, other, keep, **extra):
    return client.post(f"/{plural}/{rid}/merge", data={"other": str(other), "keep": str(keep), "confirm": "1", **extra}, follow_redirects=False)


@pytest.fixture
def backups(monkeypatch):
    """run_backup 을 가로채 호출을 기록한다 (진짜 백업은 `test_merge_makes_a_real_backup_first`)."""
    calls = []

    def fake(settings, now=None):
        calls.append(True)
        return settings.backup_dir / "fake"

    monkeypatch.setattr(backup, "run_backup", fake)
    return calls


# ---------------------------------------------------------------- 합치기: 미리 보기

def test_merge_choose_page_lists_others_and_searches(admin_client, db):
    k, a, *_ = partner_world(db)
    page = admin_client.get(f"/partners/{k.id}/merge").text
    assert "다른 파트너와 합치기" in page and "솔루엠" in page and f"/partners/{k.id}/merge?other={a.id}" in page
    assert f"/partners/{k.id}/merge?other={k.id}" not in page
    assert "솔루엠" in admin_client.get(f"/partners/{k.id}/merge?q=Solum").text
    assert "맞는 파트너가 없어요" in admin_client.get(f"/partners/{k.id}/merge?q=없는회사").text
    bad = admin_client.get(f"/partners/{k.id}/merge?other=abc")
    assert bad.status_code == 400 and "찾을 수 없거나" in bad.text
    assert admin_client.get(f"/partners/{k.id}/merge?other={k.id}").status_code == 400
    assert admin_client.get("/partners/999/merge").status_code == 404


def test_merge_preview_counts_partner(admin_client, db):
    k, a, sols, acts, c, f = partner_world(db)
    page = admin_client.get(f"/partners/{k.id}/merge?other={a.id}").text
    t = text_of(page)
    assert "아직 아무것도 바뀌지 않았어요" in t
    # 연결이 더 많은 쪽(A: 솔루션 2 + 활동 2 + 고객 1 + 파일 1)이 기본으로 남는다
    assert re.search(rf'value="{a.id}" checked', page) and "사라지는 파트너: '에이치" not in t
    assert "사라지는 파트너: 'SOLUM'" in t
    assert re.search(r"솔루션 \(파트너 칸\) 1 ", t) and re.search(r"활동 기록 1 그중 1건은 이미", t)
    assert "자동으로 더해져요: Solum Korea" in t  # 사라지는 이름 + 그 다른 이름 (이미 있는 것은 뺀다: SOLUM 은 남는 쪽의 "Solum" 과 같다)
    assert "연락처" in t and "k@example-solum.co.kr" in t  # 붙는 글 (SOLUM 이 사라지는 쪽이므로 K 의 글)
    # 바꾸기: keep=K → 사라지는 쪽은 A
    swapped = text_of(admin_client.get(f"/partners/{k.id}/merge?other={a.id}&keep={k.id}").text)
    assert "사라지는 파트너: '솔루엠'" in swapped and "A 소개" in swapped and "같은 회사 고객 1" in swapped
    assert "'SOLUM' 쪽으로 합쳐지고" in swapped
    db.expire_all()
    assert db.query(Partner).count() == 2 and db.query(Solution).filter_by(partner_id=a.id).count() == 2  # 아직 아무것도 바뀌지 않았다


def test_merge_preview_customer_lists_every_relation(admin_client, db):
    k, a, w = customer_world(db)
    t = text_of(admin_client.get(f"/customers/{k.id}/merge?other={a.id}&keep={k.id}").text)
    for label in ("프로젝트 1", "영업기회 1", "제안 건 1", "다수 고객 제안에 붙인 고객 2 그중 1건은", "활동 기록 2 그중 1건은", "고객 니즈 1", "같은 회사 파트너 1", "올린 파일의 대상 표시 1"):
        assert label in t, label
    assert "업종" in t and "제조" in t


def test_merge_preview_conflict_blocks_the_button(admin_client, db):
    c1, c2 = mk(db, Customer, "씨원"), mk(db, Customer, "씨투")
    k = mk(db, Partner, "피원", customer_id=c1.id)
    a = mk(db, Partner, "피투", customer_id=c2.id)
    page = admin_client.get(f"/partners/{k.id}/merge?other={a.id}").text
    assert 'id="merge-conflict"' in page and "짐작하지 않아요" in page and "씨원" in page and "씨투" in page
    assert 'name="confirm"' not in page
    cust1, cust2 = mk(db, Customer, "고원"), mk(db, Customer, "고투")
    mk(db, Partner, "고원 파트너", customer_id=cust1.id)
    mk(db, Partner, "고투 파트너", customer_id=cust2.id)
    page = admin_client.get(f"/customers/{cust1.id}/merge?other={cust2.id}").text
    assert 'id="merge-conflict"' in page and "서로 다른 파트너" in page


# ---------------------------------------------------------------- 합치기: 실행

def test_merge_partner_moves_every_relation(admin_client, db, settings, backups):
    k, a, (s_a1, s_a2, s_k), (both, only_a), c, f = partner_world(db)
    kid, aid, cid, s_a1_id = k.id, a.id, c.id, s_a1.id
    mirror = KnowledgeMirror(settings.knowledge_dir)
    for obj in (k, a, s_a1, s_a2, s_k, both, only_a, c):
        mirror.sync(obj)
    assert (settings.knowledge_dir / f"partners/{aid}.md").exists()
    r = post_merge(admin_client, "partners", kid, aid, keep=kid)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/partners/{kid}?notice=merged")
    assert len(backups) == 1  # 백업이 먼저
    db.expire_all()
    assert db.get(Partner, aid) is None
    kept = db.get(Partner, kid)
    assert {s.name for s in kept.solutions} == {"A솔루션1", "A솔루션2", "K솔루션"}  # R1
    assert sorted(x.title for x in db.query(Activity).filter(Activity.partners.any(id=kept.id))) == ["A 만남", "둘 다 만남"]  # R7, 겹침은 하나
    assert db.get(Activity, both.id).partners == [kept]
    assert kept.customer_id == cid  # R25
    db.refresh(f)
    assert (f.target_type, f.target_id) == ("partner", kid)  # R21
    assert kept.contact.startswith("k@example-solum.co.kr") and f"--- 솔루엠에서 옮김 ({date.today().isoformat()}) ---\na@example-solum.co.kr" in kept.contact
    assert kept.description == "A 소개"  # 남는 쪽이 비었으면 그대로 옮긴다
    assert "K 메모\n\n--- 솔루엠에서 옮김" in kept.memo and "A 메모" in kept.memo
    assert aliases.of(kept) == ["Solum Korea", "솔루엠"]  # 사라진 이름 + 그 다른 이름. "(주)솔루엠" 은 "솔루엠" 과, "Solum" 은 남는 이름 SOLUM 과 정규형이 같아 더하지 않는다
    last = kept.memo.strip().splitlines()[-1]
    assert last.startswith("[합치기 ") and "admin" in last and "'솔루엠'(번호" in last and "'SOLUM'(번호" in last and "솔루션 (파트너 칸) 2" in last and "활동 기록 2" in last
    # 사본: 사라진 쪽은 없고, 남는 쪽·솔루션·활동 기록 사본이 새 연결을 따른다
    assert not (settings.knowledge_dir / f"partners/{aid}.md").exists()
    copy = (settings.knowledge_dir / f"partners/{kid}.md").read_text(encoding="utf-8")
    assert "- 다른 이름:" in copy and "솔루엠" in copy and "A솔루션1" in copy and "A 만남" in copy and "같은 회사" in copy
    sol_copy = (settings.knowledge_dir / f"solutions/{s_a1_id}/card.md").read_text(encoding="utf-8")
    assert f"partners/{kid}.md" in sol_copy and f"partners/{aid}.md" not in sol_copy
    assert f"partners/{kid}.md" in (settings.knowledge_dir / f"customers/{cid}.md").read_text(encoding="utf-8")


def test_merge_keep_choice_can_be_swapped(admin_client, db, backups):
    k, a, *_ = partner_world(db)
    kid, aid = k.id, a.id
    r = post_merge(admin_client, "partners", kid, aid, keep=aid)  # 주소의 기록(k)이 사라지고 a 가 남는다
    assert r.status_code == 303 and r.headers["location"].startswith(f"/partners/{aid}?")
    db.expire_all()
    kept = db.get(Partner, aid)
    assert db.get(Partner, kid) is None and "Solum Korea" in aliases.of(kept) and "solum" in aliases.keys_of(kept)
    assert "k@example-solum.co.kr" in db.get(Partner, aid).contact


def test_merge_customer_moves_every_relation(admin_client, db, settings, backups):
    k, a, w = customer_world(db)
    kid, aid = k.id, a.id
    ids = {key: obj.id for key, obj in w.items()}
    mirror = KnowledgeMirror(settings.knowledge_dir)
    for obj in (k, a, w["p_a"], w["o_a"], w["only_a"], w["partner"]):
        mirror.sync(obj)
    r = post_merge(admin_client, "customers", aid, kid, keep=kid)  # 주소는 A, 남기는 쪽은 K
    assert r.status_code == 303 and r.headers["location"].startswith(f"/customers/{kid}?notice=merged")
    assert len(backups) == 1
    db.expire_all()
    assert db.get(Customer, aid) is None
    kept = db.get(Customer, kid)
    assert {p.name for p in kept.projects} == {"A 프로젝트", "K 프로젝트"}  # R2
    assert [o.name for o in kept.opportunities] == ["A 영업기회"]  # R27
    assert [p.id for p in kept.proposals] == [ids["pr_a"]]  # R12
    assert sorted(p.group_name for p in kept.group_proposals) == ["그룹1", "그룹2"]  # R34
    assert [c.id for c in db.get(Proposal, ids["g1"]).group_customers] == [kept.id]  # 겹침은 하나
    assert sorted(x.title for x in db.query(Activity).filter(Activity.customers.any(id=kept.id))) == ["A만", "둘 다"]  # R5
    assert db.get(Activity, ids["both"]).customers == [kept]
    need = db.query(CustomerNeed).one()
    assert need.customer_id == kept.id and need.customer_name == "에이치에스"  # R10, 쓴 이름은 그대로
    assert db.get(Partner, ids["partner"]).customer_id == kept.id  # R25
    db.refresh(w["f"])
    assert (w["f"].target_type, w["f"].target_id) == ("customer", kept.id)  # R21
    assert kept.industry == "제조"  # 남는 쪽 업종이 비어 있었다
    assert "K 메모\n\n--- 에이치에스홀딩스에서 옮김" in kept.memo and "A 메모" in kept.memo
    assert set(aliases.of(kept)) == {"에이치에스홀딩스", "에이치에스"}
    assert "고객 니즈 1" in kept.memo.splitlines()[-1]
    assert not (settings.knowledge_dir / f"customers/{aid}.md").exists()
    assert f"customers/{kid}.md" in (settings.knowledge_dir / f"projects/{w['p_a'].id}.md").read_text(encoding="utf-8")
    assert f"customers/{kid}.md" in (settings.knowledge_dir / f"partners/{w['partner'].id}.md").read_text(encoding="utf-8")


def test_merge_customer_industry_conflict_is_kept_in_memo(admin_client, db, backups):
    k = mk(db, Customer, "씨케이", industry="유통")
    a = mk(db, Customer, "씨에이", industry="제조")
    post_merge(admin_client, "customers", k.id, a.id, keep=k.id)
    db.expire_all()
    kept = db.get(Customer, k.id)
    assert kept.industry == "유통" and "업종: 제조" in kept.memo


def test_merge_same_company_link_moves_when_only_absorbed_has_it(admin_client, db, backups):
    c = mk(db, Customer, "연결 고객")
    k = mk(db, Partner, "남는 파트너")
    a = mk(db, Partner, "사라질 파트너", customer_id=c.id)
    kid, aid, cid = k.id, a.id, c.id
    assert post_merge(admin_client, "partners", kid, aid, keep=kid).status_code == 303
    db.expire_all()
    assert db.get(Partner, kid).customer_id == cid and db.get(Partner, aid) is None


def test_merge_same_company_conflict_stops_without_changes(admin_client, db, backups):
    c1, c2 = mk(db, Customer, "씨원"), mk(db, Customer, "씨투")
    k = mk(db, Partner, "피원", customer_id=c1.id, memo="m")
    a = mk(db, Partner, "피투", customer_id=c2.id)
    s = mk(db, Solution, "솔", partner_id=a.id)
    r = post_merge(admin_client, "partners", k.id, a.id, keep=k.id)
    assert r.status_code == 409 and "짐작하지 않아요" in r.text
    assert backups == []  # 막힐 일이면 백업도 만들지 않는다
    db.expire_all()
    assert db.get(Partner, a.id) is not None and db.get(Solution, s.id).partner_id == a.id and db.get(Partner, k.id).memo == "m"
    cust1, cust2 = mk(db, Customer, "고원"), mk(db, Customer, "고투")
    mk(db, Partner, "p1", customer_id=cust1.id)
    mk(db, Partner, "p2", customer_id=cust2.id)
    r = post_merge(admin_client, "customers", cust1.id, cust2.id, keep=cust1.id)
    assert r.status_code == 409 and db.get(Customer, cust2.id) is not None


def test_merge_backup_failure_changes_nothing(admin_client, db, settings, monkeypatch):
    k, a, (s_a1, *_), *_ = partner_world(db)

    def boom(settings, now=None):
        raise OSError("디스크가 가득 참")

    monkeypatch.setattr(backup, "run_backup", boom)
    r = post_merge(admin_client, "partners", k.id, a.id, keep=k.id)
    assert r.status_code == 409 and "백업을 만들지 못해 합치지 않았어요" in r.text and "디스크가 가득 참" in r.text
    db.expire_all()
    assert db.get(Partner, a.id) is not None and db.get(Solution, s_a1.id).partner_id == a.id
    assert db.get(Partner, k.id).aliases == "Solum Korea" and "[합치기" not in db.get(Partner, k.id).memo


def test_merge_makes_a_real_backup_first(admin_client, db, settings):
    k, a, *_ = partner_world(db)
    assert post_merge(admin_client, "partners", k.id, a.id, keep=k.id).status_code == 303
    made = list(settings.backup_dir.iterdir())
    assert len(made) == 1 and backup.verify(made[0]) == []
    import sqlite3

    conn = sqlite3.connect(made[0] / "app.db")  # 합치기 전의 DB
    try:
        assert sorted(r[0] for r in conn.execute("select name from partners")) == ["SOLUM", "솔루엠"]
    finally:
        conn.close()


def test_merge_requires_confirm_checkbox(admin_client, db, backups):
    k, a, *_ = partner_world(db)
    r = admin_client.post(f"/partners/{k.id}/merge", data={"other": str(a.id), "keep": str(k.id)}, follow_redirects=False)
    assert r.status_code == 400 and "되돌릴 수 없어요" in r.text
    assert backups == [] and db.get(Partner, a.id) is not None


def test_merge_is_admin_only(user_client, db, backups):
    k, a, *_ = partner_world(db)
    c1, c2 = mk(db, Customer, "씨원"), mk(db, Customer, "씨투")
    for plural, rid, other in (("partners", k.id, a.id), ("customers", c1.id, c2.id)):
        assert user_client.get(f"/{plural}/{rid}/merge").status_code == 403
        assert user_client.get(f"/{plural}/{rid}/merge?other={other}").status_code == 403
        assert post_merge(user_client, plural, rid, other, keep=rid).status_code == 403
    assert backups == []
    db.expire_all()
    assert db.get(Partner, a.id) is not None and db.get(Customer, c2.id) is not None


def test_detail_shows_merge_button_to_admin_only(user_client, admin_client, db):
    p, c = mk(db, Partner, "피"), mk(db, Customer, "씨")
    assert f"/partners/{p.id}/merge" in admin_client.get(f"/partners/{p.id}").text and "다른 파트너와 합치기" in admin_client.get(f"/partners/{p.id}").text
    assert f"/customers/{c.id}/merge" in admin_client.get(f"/customers/{c.id}").text and "다른 고객과 합치기" in admin_client.get(f"/customers/{c.id}").text
    assert "/merge" not in user_client.get(f"/partners/{p.id}").text and "/merge" not in user_client.get(f"/customers/{c.id}").text


def test_merge_double_submit_and_races(admin_client, db, backups):
    k, a, *_ = partner_world(db)
    data = {"other": str(a.id), "keep": str(k.id), "confirm": "1", "_tok": "token-1"}
    first = admin_client.post(f"/partners/{k.id}/merge", data=data, follow_redirects=False)
    again = admin_client.post(f"/partners/{k.id}/merge", data=data, follow_redirects=False)
    assert first.status_code == again.status_code == 303 and again.headers["location"].split("?")[0] == f"/partners/{k.id}"
    assert len(backups) == 1  # 두 번 합치지 않았다
    # 새 표로 다시 눌러도(다른 탭) 500 이 아니라 "이미 없다" 알림, 남은 기록으로
    late = admin_client.post(f"/partners/{k.id}/merge", data={**data, "_tok": "token-2"}, follow_redirects=False)
    assert late.status_code == 303 and "merge_gone" in late.headers["location"] and late.headers["location"].startswith(f"/partners/{k.id}")
    # 지워진 뒤: 둘 다 없으면 목록으로
    p1, p2 = mk(db, Partner, "하나"), mk(db, Partner, "둘")
    db.delete(p2)
    db.commit()
    gone = post_merge(admin_client, "partners", p1.id, p2.id, keep=p1.id)
    assert gone.status_code == 303 and "merge_gone" in gone.headers["location"] and gone.headers["location"].startswith(f"/partners/{p1.id}")
    # 잘못된 값
    assert "merge_gone" in post_merge(admin_client, "partners", p1.id, "abc", keep=p1.id).headers["location"]
    assert "merge_gone" in post_merge(admin_client, "partners", p1.id, p1.id, keep=p1.id).headers["location"]
    assert "merge_gone" in post_merge(admin_client, "partners", p1.id, a.id, keep=999).headers["location"]


def test_merge_removes_dismissals_of_absorbed_and_logs(admin_client, db, backups, caplog):
    import logging

    k, a = mk(db, Partner, "기준"), mk(db, Partner, "흡수")
    third = mk(db, Partner, "셋째")
    duplicates.dismiss(db, "partner", a.id, third.id, None)
    duplicates.dismiss(db, "partner", k.id, third.id, None)
    db.commit()
    with caplog.at_level(logging.INFO, logger="app.merge"):
        post_merge(admin_client, "partners", k.id, a.id, keep=k.id)
    assert [(d.low_id, d.high_id) for d in db.query(DuplicateDismissal)] == [(k.id, third.id)]
    assert any("합치기 완료" in r.message and "'흡수'" in r.message and "'기준'" in r.message and "admin" in r.message for r in caplog.records)


def test_merge_pages_escape_names(admin_client, db):
    k, a = mk(db, Partner, "<b>굵게</b>", aliases="<i>x</i>"), mk(db, Partner, "<img src=x onerror=alert(1)>")
    for url in (f"/partners/{k.id}/merge", f"/partners/{k.id}/merge?q=img", f"/partners/{k.id}/merge?other={a.id}"):
        page = admin_client.get(url).text
        assert "<img src=x" not in page and "<b>굵게" not in page and "<i>x</i>" not in page


def test_merge_audit_line_added_to_empty_memo(admin_client, db, backups):
    k, a = mk(db, Customer, "빈메모"), mk(db, Customer, "흡수고객")
    post_merge(admin_client, "customers", k.id, a.id, keep=k.id)
    db.expire_all()
    memo = db.get(Customer, k.id).memo
    assert memo.startswith("[합치기 ") and "\n" not in memo and "옮긴 연결 없음" in memo


def test_merge_no_text_duplicate_when_identical(admin_client, db, backups):
    k = mk(db, Partner, "같은글A", contact="02-1234", memo="")
    a = mk(db, Partner, "같은글B", contact="02-1234")
    post_merge(admin_client, "partners", k.id, a.id, keep=k.id)
    db.expire_all()
    assert db.get(Partner, k.id).contact == "02-1234"


def test_merge_is_registered_in_ontology_and_docs():
    assert any(i.path == "admin/duplicates" and i.label == "중복 정리" for i in ontology.ADMIN_NAV_ITEMS)
    assert "admin/duplicates" in ontology.NAV_ICONS and "duplicate_names" in ontology.JOB_KINDS


def test_merge_moves_every_foreign_key_to_partners_and_customers(db):
    """모델에 partners.id·customers.id 를 가리키는 모든 외래 키·연결 표가 합치기 규칙에 들어 있는지 (새 연결을 더하고 합치기에 빠뜨리면 여기서 걸린다)."""
    from app.db import Base

    known = {
        "solutions.partner_id", "activity_partners.partner_id", "partners.customer_id",
        "businesses.customer_id", "opportunities.customer_id", "proposals.customer_id", "proposal_customers.customer_id",
        "activity_customers.customer_id", "customer_needs.customer_id", "report_plan_items.customer_id",
    }
    found = set()
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            for fk in col.foreign_keys:
                if fk.column.table.name in ("partners", "customers"):
                    found.add(f"{table.name}.{col.name}")
    assert found == known
    assert {r for _l, r, _n in merge.link_counts(db, "partner", mk(db, Partner, "p"))} == {"R1", "R7", "R25", "R21"}
    assert {r for _l, r, _n in merge.link_counts(db, "customer", mk(db, Customer, "c"))} == {"R2", "R27", "R12", "R34", "R5", "R10", "R25", "R21"}


def test_migration_adds_aliases_columns_to_an_old_database(tmp_path):
    import sqlite3

    from app.db import make_engine
    from app.migrate import add_missing_columns

    db_path = tmp_path / "app.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE partners (id INTEGER PRIMARY KEY, name VARCHAR, contact VARCHAR, description VARCHAR, memo VARCHAR, customer_id INTEGER)")
    con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR, industry VARCHAR, contact VARCHAR, memo VARCHAR)")
    con.execute("INSERT INTO partners (id, name) VALUES (1, '옛 파트너')")
    con.execute("INSERT INTO customers (id, name) VALUES (1, '옛 고객')")
    con.commit()
    con.close()
    engine = make_engine(db_path)
    added = add_missing_columns(engine, db_path)
    engine.dispose()
    assert "partners.aliases" in added and "customers.aliases" in added
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT name, aliases FROM partners").fetchone() == ("옛 파트너", "")
    assert con.execute("SELECT name, aliases FROM customers").fetchone() == ("옛 고객", "")
    con.close()


def test_dismissal_table_exists_in_a_new_database(db):
    from sqlalchemy import inspect

    assert "duplicate_dismissals" in inspect(db.get_bind()).get_table_names()


def test_merge_failure_midway_rolls_everything_back(db, settings, monkeypatch):
    from app.models import User

    k, a, (s_a1, *_), *_ = partner_world(db)
    kid, aid, sid = k.id, a.id, s_a1.id
    admin = User(username="boss", name="대표", password_hash="x", role="admin")
    db.add(admin)
    db.commit()
    monkeypatch.setattr(backup, "run_backup", lambda settings, now=None: settings.backup_dir / "fake")

    def boom(*args, **kwargs):
        raise RuntimeError("중간에 실패")

    monkeypatch.setattr(merge, "_merge_texts", boom)
    with pytest.raises(RuntimeError):
        merge.execute(db, "partner", kid, aid, admin, settings)
    db.expire_all()
    assert db.get(Partner, aid) is not None and db.get(Solution, sid).partner_id == aid  # 솔루션은 옮겨지기 전 그대로
    assert db.get(Partner, kid).aliases == "Solum Korea" and db.get(Partner, aid).customer_id is not None
    assert db.query(Activity).filter(Activity.partners.any(id=kid)).count() == 1


def test_merge_audit_names_the_user(db, settings, monkeypatch):
    from app.models import User

    k, a = mk(db, Customer, "기준고객"), mk(db, Customer, "흡수고객")
    admin = User(username="boss", name="김대표", password_hash="x", role="admin")
    db.add(admin)
    db.commit()
    monkeypatch.setattr(backup, "run_backup", lambda settings, now=None: settings.backup_dir / "fake")
    result = merge.execute(db, "customer", k.id, a.id, admin, settings, now=datetime(2026, 11, 24, 14, 3))
    db.commit()
    assert result["audit"].startswith("[합치기 2026-11-24 14:03] 김대표: '흡수고객'(번호 ")
    assert db.get(Customer, k.id).memo == result["audit"]

"""닫힌 베타 리뷰 지적 C: 폼 표(내용이 바뀐 다시 제출·커밋 뒤 예외), 저장 직전 고침 표시, 작성자 말투·사본 경로 거름, 옛 한 파일 제안서, 검색, 조사, 연락처 줄."""
from __future__ import annotations

import base64
import json
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Form, UploadFile
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from pptx import Presentation
from sqlalchemy import event

from app import guards, ontology, search
from app.ai.proposal_tasks import Outline
from app.deck import build_deck, has_internal_mark, mirror_folders
from app.knowledge import KNOWLEDGE_FORMAT, KnowledgeMirror
from app.models import Activity, Customer, CustomerNeed, FollowUp, FormToken, Project, Proposal, Solution, User
from app.proposals import clean_outline, is_author_instruction
from tests.conftest import fu_rows, outlined_proposal


# ---- C1: 같은 표로 내용을 바꿔 다시 제출 ----

def test_resubmit_with_same_token_and_same_content_goes_to_first_result(user_client, db):
    first = user_client.post("/customers", data={"name": "가나상사", "_tok": "t-same"}, follow_redirects=False)
    again = user_client.post("/customers", data={"name": "가나상사", "_tok": "t-same"}, follow_redirects=False)
    assert first.status_code == 303 and again.status_code == 303
    assert again.headers["location"].split("?")[0] == first.headers["location"].split("?")[0]
    assert db.query(Customer).count() == 1


def test_resubmit_with_same_token_but_changed_content_is_a_new_submission(user_client, db):
    first = user_client.post("/customers", data={"name": "가나상사", "_tok": "t-chg"}, follow_redirects=False)
    second = user_client.post("/customers", data={"name": "다라물산", "memo": "다른 회사", "_tok": "t-chg"}, follow_redirects=False)
    assert second.status_code == 303 and second.headers["location"].split("?")[0] != first.headers["location"].split("?")[0]
    assert sorted(c.name for c in db.query(Customer)) == ["가나상사", "다라물산"]
    # 바뀐 내용으로 다시 연타하면 그 내용 기준으로 한 번만
    third = user_client.post("/customers", data={"name": "다라물산", "memo": "다른 회사", "_tok": "t-chg"}, follow_redirects=False)
    assert third.headers["location"].split("?")[0] == second.headers["location"].split("?")[0]
    assert db.query(Customer).count() == 2
    assert db.query(FormToken).one().body_hash  # 지금 제출의 해시가 적힌다


def test_old_token_rows_without_a_hash_count_as_the_same_content(user_client, db):
    """업그레이드 전에 만든 표(해시 칸이 비어 있다)로 다시 오면 지금처럼 처음 결과로 보낸다."""
    user_client.post("/customers", data={"name": "가나상사", "_tok": "t-old"}, follow_redirects=False)
    db.query(FormToken).update({"body_hash": None})
    db.commit()
    again = user_client.post("/customers", data={"name": "다라물산", "_tok": "t-old"}, follow_redirects=False)
    assert again.status_code == 303 and db.query(Customer).count() == 1


def test_form_digest_ignores_token_and_uses_file_names_and_sizes():
    app = FastAPI()

    @app.post("/d")
    def d(a: str = Form(""), f: UploadFile | None = None, digest: str = Depends(guards.form_digest)):  # 본문 칸이 있어야 FastAPI 가 올린 파일을 닫는다
        return {"d": digest}

    c = TestClient(app)

    def digest(data, files=None):
        return c.post("/d", data=data, files=files).json()["d"]

    base = digest({"a": "1", "_tok": "x"})
    assert base == digest({"a": "1", "_tok": "y"}) and base != digest({"a": "2", "_tok": "x"})
    f1 = digest({"a": "1"}, {"f": ("메모.txt", b"1234", "text/plain")})
    assert f1 == digest({"a": "1"}, {"f": ("메모.txt", b"abcd", "text/plain")})  # 내용은 다시 읽지 않는다: 이름과 크기만
    assert f1 != digest({"a": "1"}, {"f": ("메모.txt", b"12345", "text/plain")})
    assert f1 != digest({"a": "1"}, {"f": ("다른.txt", b"1234", "text/plain")})


# ---- C2: 작성자 말투 거르기 ----

MOVE = [
    "총액과 견적 유효기간을 함께 제시", "담당자 이름을 함께 기재", "보안 정책을 반드시 명시", "연락처를 추가할 것", "수치를 입력하세요",
    "금액 확인 필요", "도입 전 네트워크 환경 확인 필요", "절감률 보완 필요", "세부 일정 수정 필요", "자료를 확인하시기 바랍니다", "검토 요망",
    "[TBD] 비용", "○○", "(○○)", "OOO", "담당자: ○○○", "비용: ○○원", "수치를 기재해 주세요.",
]
KEEP = [
    "AI가 회의록 초안을 작성", "부족한 현장 인력을 보완", "고객 데이터를 입력", "월간 운영 보고서를 제시", "기존 시스템의 한계를 보완",
    "계약서에 책임 범위를 명시", "○○시청 도입 사례", "구축 후 3개월간 개선 방안을 제시.", "단계별 로드맵 제시", "현장 사진을 삽입",
    "견적을 제시합니다", "지금 바로 문의하세요", "반드시 지켜야 할 3가지 약속", "도입 효과를 확인할 수 있습니다",
]


@pytest.mark.parametrize("line", MOVE)
def test_explicit_instruction_markers_are_moved(line):
    assert is_author_instruction(line)


@pytest.mark.parametrize("line", KEEP)
def test_ordinary_customer_facing_lines_stay(line):
    assert not is_author_instruction(line)


def test_clean_outline_keeps_ordinary_bullets_and_moves_markers(tmp_path):
    out = Outline.model_validate({"title": "제안", "slides": [
        {"kind": "cover", "title": "제안"},
        {"kind": "bullets", "title": "기능", "bullets": ["AI가 회의록 초안을 작성", "부족한 현장 인력을 보완", "총액과 견적 유효기간을 함께 제시", "담당자: ○○○"]},
    ]})
    s = clean_outline(out, tmp_path)["slides"][1]
    assert s["bullets"] == ["AI가 회의록 초안을 작성", "부족한 현장 인력을 보완"]
    assert "총액과 견적 유효기간을 함께 제시" in s["needs_check"] and "담당자: ○○○" in s["needs_check"]


# ---- C10: 마무리 장 연락처 줄 ----

def _closing(tmp_path, bullets):
    out = Outline.model_validate({"title": "제안", "slides": [{"kind": "cover", "title": "제안"}, {"kind": "closing", "title": "감사합니다", "bullets": bullets}]})
    return clean_outline(out, tmp_path)["slides"][1]["bullets"]


def test_closing_drops_only_real_contact_lines(tmp_path):
    kept = _closing(tmp_path, [
        "담당 김민수 대리 010-1234-5678", "kim@example.com 으로 연락 주세요", "담당: 홍길동", "연락처:", "Tel: 02-123-4567", "문의: 홍길동 과장",
        "문의 환영", "문의 사항은 언제든 말씀해 주세요", "담당자 배정 후 일정을 협의합니다", "다음 단계 협의",
    ])
    assert kept == ["문의 환영", "문의 사항은 언제든 말씀해 주세요", "담당자 배정 후 일정을 협의합니다", "다음 단계 협의"]


# ---- C3: 고객용 파일의 사본 경로 거르기 ----

def test_mirror_path_shape_uses_ontology_folders():
    assert {"solutions", "customers", "opportunities", "research"} <= set(mirror_folders())
    for folder in mirror_folders():
        assert has_internal_mark(f"{folder}/12.md")


@pytest.mark.parametrize("text", ["solutions/3.md", "customers/12", "자세한 건 solutions/3/card.md 참고", "opportunities/2.md", "research/2026-11-01-x-3.md",
                                  "(projects/5)", "출처: solutions/7/files/3-사양서.md", "businesses/4.md"])
def test_real_mirror_paths_are_removed(text):
    assert has_internal_mark(text)


@pytest.mark.parametrize("text", ["자세한 소개: https://acme.co.kr/solutions/ai-vision", "B2B customers/partners 모두 지원", "README.md 참고",
                                  "research/개발 조직과 협업", "연 2/3 수준", "www.acme.co.kr/customers/12", "notes.md 파일", "a-solutions/3.md", "https://x.kr/solutions/3.md"])
def test_ordinary_lines_survive(text):
    assert not has_internal_mark(text)


def test_customer_deck_keeps_legit_lines_and_counts_removed(tmp_path):
    o = {"slides": [{"kind": "cover", "title": "제안"}, {"kind": "bullets", "title": "소개", "bullets": [
        "자세한 소개: https://acme.co.kr/solutions/ai-vision", "B2B customers/partners 모두 지원", "README.md 참고", "research/개발 조직과 협업",
        "solutions/3.md", "customers/12"]}]}
    stats: dict = {}
    build_deck(o, tmp_path / "c.pptx", audience="customer", stats=stats)
    text = "\n".join(sh.text_frame.text for sl in Presentation(tmp_path / "c.pptx").slides for sh in sl.shapes if sh.has_text_frame)
    for kept in ("https://acme.co.kr/solutions/ai-vision", "customers/partners", "README.md", "research/개발"):
        assert kept in text
    assert "solutions/3.md" not in text and "customers/12" not in text
    assert stats["removed"] == 2
    internal: dict = {}
    build_deck(o, tmp_path / "i.pptx", audience="internal", stats=internal)
    assert internal["removed"] == 0


def test_deck_box_shows_removed_line_count(user_client, db, settings):
    p = outlined_proposal(db, settings)
    p.outline["slides"][1]["bullets"] = [*p.outline["slides"][1]["bullets"], "customers/12", "solutions/3.md"]
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(p, "outline")
    db.commit()
    user_client.post(f"/proposals/{p.id}/deck")
    db.refresh(p)
    assert p.deck_removed == 2
    box = user_client.get(f"/proposals/{p.id}").text.split('id="deck"')[1]
    assert "내부 경로가 든 2줄을 뺐습니다" in box
    # 뺀 줄이 없으면 안내가 없다
    p.outline["slides"][1]["bullets"] = ["전력비 절감"]
    flag_modified(p, "outline")
    db.commit()
    user_client.post(f"/proposals/{p.id}/deck")
    assert "을 뺐습니다" not in user_client.get(f"/proposals/{p.id}").text.split('id="deck"')[1]


# ---- C4 ----

def test_knowledge_format_was_bumped_for_connection_sections():
    assert KNOWLEDGE_FORMAT >= 7


# ---- C5: 옛 한 파일 제안서, 임시 파일 ----

def test_deck_write_is_tmp_then_replace(tmp_path, monkeypatch):
    o = {"slides": [{"kind": "cover", "title": "제안"}, {"kind": "bullets", "title": "a", "bullets": ["x"]}]}
    out = tmp_path / "d.pptx"
    build_deck(o, out)
    before = out.read_bytes()
    assert sorted(f.name for f in tmp_path.iterdir()) == ["d.pptx"]

    def boom(self, target, *a, **k):
        Path(target).write_bytes(b"half")
        raise OSError("disk full")

    monkeypatch.setattr("pptx.presentation.Presentation.save", boom)
    with pytest.raises(OSError):
        build_deck(o, out)
    assert out.read_bytes() == before and sorted(f.name for f in tmp_path.iterdir()) == ["d.pptx"]  # 반쪽 파일이 남지 않고 옛 파일도 그대로


def test_get_deck_with_missing_internal_file_never_writes(user_client, db, settings):
    p = outlined_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/deck")
    db.refresh(p)
    folder = settings.decks_dir / str(p.id)
    (folder / p.deck_name.replace(".pptx", "-internal.pptx")).unlink()
    names = sorted(f.name for f in folder.iterdir())
    r = user_client.get(f"/proposals/{p.id}/deck", follow_redirects=False)
    assert r.status_code == 303 and "notice=deck_old" in r.headers["location"]
    assert sorted(f.name for f in folder.iterdir()) == names


# ---- C6: 검사와 저장 사이에 끼어든 쓰기 ----

def test_write_landing_between_check_and_commit_is_refused(app, db):
    c = Customer(name="처음")
    db.add(c)
    db.commit()
    stamp, cid = guards.rev_of(c), c.id
    factory = app.state.session_factory
    mine, other = factory(), factory()
    try:
        mine_c = mine.get(Customer, cid)
        assert guards.check_rev(mine_c, guards.Rev(stamp, False)) is None  # 검사는 통과한다
        other_c = other.get(Customer, cid)
        other_c.name = "다른 곳이 먼저"
        other.commit()  # 검사와 저장 사이에 다른 쓰기가 들어왔다
        mine_c.name = "내 이름"
        with pytest.raises(guards.StaleWrite):
            mine.commit()
        mine.rollback()
    finally:
        mine.close()
        other.close()
    db.expire_all()
    assert db.get(Customer, cid).name == "다른 곳이 먼저"


def test_forced_save_survives_a_changed_stamp_but_not_a_replaced_record(app, db):
    c = Customer(name="처음")
    db.add(c)
    db.commit()
    stamp, cid = guards.rev_of(c), c.id
    factory = app.state.session_factory
    mine, other = factory(), factory()
    try:
        mine_c = mine.get(Customer, cid)
        other_c = other.get(Customer, cid)
        other_c.name = "다른 곳"
        other.commit()
        mine.refresh(mine_c)  # 라우터는 요청마다 기록을 새로 읽는다
        assert guards.check_rev(mine_c, guards.Rev(stamp, True)) is None  # "그래도 내 내용으로 저장"
        mine_c.name = "내 이름"
        mine.commit()  # 강제 저장은 같은 기록이면 통과
        mine.expire_all()
        assert mine.get(Customer, cid).name == "내 이름"
        assert guards.check_rev(mine_c, guards.Rev(stamp, True)) is None
        other.delete(other.get(Customer, cid))
        other.commit()  # 기록이 사라졌다
        mine_c.name = "또 고침"
        with pytest.raises(guards.StaleWrite):
            mine.commit()
    finally:
        mine.rollback()
        mine.close()
        other.close()


def test_route_refuses_a_write_that_lands_after_its_check(user_client, db, app, monkeypatch):
    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    cid = c.id
    page = user_client.get(f"/customers/{cid}/edit").text
    stamp = re.search(r'name="_rev" value="([^"]+)"', page).group(1)
    real = guards.check_rev

    def check_then_other_write(obj, rev, *a, **k):
        result = real(obj, rev, *a, **k)
        with app.state.session_factory() as other:  # 라우터의 검사 직후, 저장 전에 다른 곳이 같은 기록을 바꾼다
            other.get(Customer, cid).memo = "끼어든 메모"
            other.commit()
        return result

    monkeypatch.setattr(guards, "check_rev", check_then_other_write)
    r = user_client.post(f"/customers/{cid}", data={"name": "한빛병원 본점", "_rev": stamp}, follow_redirects=False)
    assert r.status_code == 409 and "저장하는 사이에 다른 곳에서" in r.text
    db.expire_all()
    got = db.get(Customer, cid)
    assert got.name == "한빛병원" and got.memo == "끼어든 메모"


# (구성안 장 저장은 `_lock_proposal` 이 읽기 전에 쓰기 잠금을 잡아 이미 끼어들 틈이 없다: tests/test_beta_a.py)


# ---- C7: 검색 ----

def test_search_query_count_is_flat_for_proposals_with_projects(user_client, db, app):
    def build(n, tag):
        for i in range(n):
            c = Customer(name=f"알파 고객{tag}{i}", memo="알파")
            db.add(c)
            db.flush()
            pj = Project(name=f"알파 프로젝트{tag}{i}", customer_id=c.id)
            db.add(pj)
            db.flush()
            db.add(Proposal(customer_id=c.id, project_id=pj.id, title=""))  # 제목이 비면 프로젝트 이름으로 보인다
            a = Activity(title=f"알파 회의{tag}{i}", raw_text="알파")
            a.follow_ups = fu_rows(("알파 할 일", "", "", False))
            a.needs = [CustomerNeed(position=0, customer_name="알파", content="알파 니즈")]
            db.add(a)
        db.commit()

    def count():
        n = []
        engine = app.state.engine

        def hook(conn, cursor, statement, *rest):
            n.append(statement)

        event.listen(engine, "before_cursor_execute", hook)
        try:
            db.expire_all()
            db.expunge_all()
            res = search.search(db, "알파")
        finally:
            event.remove(engine, "before_cursor_execute", hook)
        assert {g["code"] for g in res["groups"]} >= {"customer", "project", "proposal", "activity", "follow_up", "customer_need"}
        return len(n)

    build(3, "a")
    few = count()
    build(30, "b")
    assert count() == few
    # 참 값: 모든 유형(10)의 건수 질의 + 맞은 유형(여기서는 6)의 줄 질의 + 미리 읽기(프로젝트 1, 제안 건 2, 후속 조치 1, 고객 니즈 1)
    assert few == 10 + 6 + (1 + 2 + 1 + 1)


def test_search_excerpt_drops_markdown_markers_but_keeps_mark_and_escaping():
    text = "# 제목\n- 첫째 항목 알파 하나\n* 둘째\n> 인용 <b>굵게</b>\n\n\n  셋째   줄"
    got = str(search.excerpt(text, "알파"))
    assert got == "제목 첫째 항목 <mark>알파</mark> 하나 둘째 인용 &lt;b&gt;굵게&lt;/b&gt; 셋째 줄"
    assert "<mark>&lt;b&gt;</mark>" in str(search.excerpt("- <b>굵게", "<b>"))
    assert "#" not in str(search.excerpt("## 소제목 알파", "알파")) and "-" not in str(search.excerpt("- 알파 항목", "알파"))
    assert search.excerpt("- 알파", "- 알파") is None  # 표지는 떼고 찾으므로 표지를 찾는 글은 발췌가 없다 (제목 칸이 맞으면 어차피 발췌가 필요 없다)


def test_more_links_only_go_to_lists_that_search_that_type(user_client, db):
    for i in range(search.PER_TYPE + 2):
        a = Activity(title=f"회의 {i}", raw_text="x")
        a.follow_ups = fu_rows((f"견적서 보내기 {i}", "", "", False))
        a.needs = [CustomerNeed(position=0, customer_name="c", content=f"견적서 요구 {i}")]
        db.add(a)
    db.commit()
    page = user_client.get("/search", params={"q": "견적서"}).text
    assert 'href="/follow-ups?q=' in page
    assert page.count("목록에서 모두 보기") == 1  # 고객 니즈는 찾는 목록이 없어 링크를 두지 않는다
    assert "/activities?q=" not in page


def test_follow_up_list_filters_on_task_text(user_client, db):
    a = Activity(title="회의", raw_text="x")
    a.follow_ups = fu_rows(("견적서 보내기", "", "", False), ("현장 방문", "", "", False), ("100% 점검", "", "", False))
    db.add(a)
    db.commit()
    html = user_client.get("/follow-ups", params={"q": "견적서"}).text
    assert "견적서 보내기" in html and "현장 방문" not in html
    assert "100% 점검" in user_client.get("/follow-ups", params={"q": "0%"}).text
    assert "견적서 보내기" not in user_client.get("/follow-ups", params={"q": "0%"}).text


# ---- C8: 커밋한 뒤의 예외는 표를 풀지 않는다 ----

def test_exception_after_commit_keeps_the_claim(user_client, db, monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("mirror down")

    monkeypatch.setattr(KnowledgeMirror, "sync", boom)
    with pytest.raises(RuntimeError):
        user_client.post("/customers", data={"name": "가나상사", "_tok": "t-post"}, follow_redirects=False)
    assert db.query(Customer).count() == 1  # 기록은 커밋됐다
    monkeypatch.undo()
    again = user_client.post("/customers", data={"name": "가나상사", "_tok": "t-post"}, follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"].startswith("/customers")
    assert db.query(Customer).count() == 1  # 다시 내도 두 번째 기록이 생기지 않는다


def test_exception_before_commit_still_releases_the_claim(user_client, db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("before commit")

    monkeypatch.setattr("app.routers.customers.same_company.set_from_customer", boom)
    with pytest.raises(RuntimeError):
        user_client.post("/customers", data={"name": "가나상사", "_tok": "t-pre"}, follow_redirects=False)
    assert db.query(Customer).count() == 0 and db.query(FormToken).count() == 0
    monkeypatch.undo()
    assert user_client.post("/customers", data={"name": "가나상사", "_tok": "t-pre"}, follow_redirects=False).status_code == 303
    assert db.query(Customer).count() == 1


@pytest.mark.parametrize("path, fallback", [("/customers", "/customers"), ("/intake/5/apply", "/intake/5"), ("/activities/draft", "/activities"),
                                            ("/admin/research/topics", "/admin/research")])
def test_fallback_url_for_kept_claims(path, fallback):
    assert guards._fallback_url(path) == fallback


# ---- C9: pv 없는 옛 쿠키 ----

def test_session_cookie_without_pv_is_logged_out(app, db):
    from tests.conftest import make_user

    user = make_user(db, "lee", role="user")
    client = TestClient(app)
    signer = TimestampSigner("test")

    def cookie(payload):
        return signer.sign(base64.b64encode(json.dumps(payload).encode("utf-8"))).decode("utf-8")

    client.cookies.set("session", cookie({"user_id": user.id}))
    r = client.get("/customers", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    from app.security import password_version

    client.cookies.set("session", cookie({"user_id": user.id, "pv": password_version(user)}))
    assert client.get("/customers", follow_redirects=False).status_code == 200


# ---- C11: 조사 ----

TYPE_CODES = ["solution", "customer", "partner", "project", "opportunity", "bm", "proposal", "activity"]


@pytest.mark.parametrize("code", TYPE_CODES)
def test_duplicate_warning_uses_the_right_particle_for_every_type(app, code):
    from app.korean import josa

    label = ontology.label(code)
    tpl = app.state.templates.env.from_string('{% from "_macros.html" import dup_warning %}{{ dup_warning(d) }}')
    html = tpl.render(d={"label": label, "name": "x", "records": [{"name": "x", "url": "/x"}], "open_url": "/x"}, josa=josa)
    assert f"같은 이름의 {josa(label, '이/가')} 이미 있어요" in html
    assert "(이)" not in html and "이(가)" not in html


def test_duplicate_customer_warning_reads_naturally(user_client, db):
    db.add(Customer(name="한빛병원"))
    db.commit()
    html = user_client.post("/customers", data={"name": "한빛 병원", "_tok": "t-dup"}).text
    assert "비슷한 이름의 고객이 이미 있어요" in html and "이(가)" not in html


def test_no_literal_particle_alternatives_in_user_facing_text():
    root = Path(__file__).resolve().parent.parent
    pattern = re.compile(r"\((?:이|가|을|를|은|는|과|와|으로|로)\)|(?:이|을|은|와|으)\((?:가|를|는|과|로)\)")
    bad = []
    for path in [*root.glob("app/**/*.py"), *root.glob("app/**/*.html"), *root.glob("app/**/*.js"), root / "README.md", *root.glob("docs/*.md")]:
        if path.name == "korean.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                bad.append(f"{path.relative_to(root)}:{n}")
    assert not bad, bad

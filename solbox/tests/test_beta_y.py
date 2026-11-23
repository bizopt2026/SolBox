"""베타 Y: 사업방식 (구축형·임대형·투자형, 사용자 결정 2026-11-23).
솔루션 = 가능한 사업방식(0..3, 방식별 메모), BM = 대표 사업방식(하나), 영업기회·제안 건 = 검토 사업방식(0..3). AI 는 제안만 하고 저장하지 않는다."""
import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest

from app import business_types as bts
from app import migrate, ontology
from app.ai.proposal_tasks import DirectionsOut, directions_prompt
from app.ai.queue import process_next
from app.config import Settings
from app.db import Base, make_engine
from app.main import create_app
from app.models import AIJob, BM, Customer, Opportunity, Project, Proposal, Solution, SolutionBusinessType
from app.proposals import candidate_solutions, clean_directions
from tests.conftest import form_from_page, make_proposal_world, make_user, nloc, ready_bundle
from tests.test_beta_w import _old_db

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DRAFTS = {
    "build": "고객이 설비·시스템을 구매해 소유하는 방식",
    "lease": "고객이 소유하지 않고 이용료(렌탈·구독)를 내고 쓰는 방식",
    "invest": "당사(또는 투자자)가 투자·소유하고 절감액·전력 판매 등 수익으로 회수하는 방식 (IPP 등)",
}


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def _solution_form(name="AI 에너지 절감", types=(), memos=None, **over):
    data = {"name": name, "stage": "boxed", "business_types_present": "1", "business_types": list(types)}
    for code, memo in (memos or {}).items():
        data[f"bt_memo_{code}"] = memo
    data.update(over)
    return data


def _rows(db, solution):
    db.expire_all()
    return [(r.business_type, r.memo) for r in db.get(Solution, solution.id).business_type_rows]


# ---- 코드 목록은 한 곳 ----

def test_code_list_is_defined_once_in_ontology():
    assert list(ontology.BUSINESS_TYPES.items()) == [("build", "구축형"), ("lease", "임대형"), ("invest", "투자형")]
    cl = ontology.CODE_LISTS["business_type"]
    assert cl.items == ontology.BUSINESS_TYPES and set(cl.descriptions) == set(cl.items)
    assert ontology.BUSINESS_TYPE_DESCRIPTIONS == DRAFTS  # 사용자가 아직 확인하지 않은 초안: 바꾸면 여기와 ontology 한 곳만 고친다
    assert ontology.code_help("business_type", "invest") == DRAFTS["invest"]
    assert ontology.clean_business_types(["invest", "bogus", "build", "build"]) == ["build", "invest"]  # 정의된 순서, 중복·모르는 값 제거
    assert ontology.clean_business_types("lease,build") == ["build", "lease"] and ontology.clean_business_types(None) == []
    assert ontology.business_types_text(["invest", "build"]) == "구축형, 투자형"
    assert ontology.COLUMN_CODES[("bms", "business_type")] == "business_type"
    assert ontology.TYPES["solution"].props["business_types"] == "가능한 사업방식"
    assert ontology.TYPES["bm"].props["business_type"] == "대표 사업방식"
    assert ontology.TYPES["opportunity"].props["business_types"] == ontology.TYPES["proposal"].props["business_types"] == "검토 사업방식"


def test_labels_and_descriptions_are_not_redefined_elsewhere():
    for path in sorted(APP_DIR.rglob("*")):
        if path.suffix not in (".py", ".html", ".js", ".md") or "__pycache__" in path.parts or path.name == "ontology.py":
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":  # 설명 글(독스트링)·주석은 빼고 코드 속 글자만 본다
            tree = ast.parse(text)
            docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
                    and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            text = "\n".join(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs)
        if path.suffix == ".html":
            text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
        for word in ("구축형", "임대형", "투자형", *DRAFTS.values()):
            assert word not in text, f"{word!r} 가 ontology.py 밖에 다시 적혀 있다: {path.relative_to(APP_DIR)}"


def test_help_glossary_shows_the_three_types_from_ontology(user_client):
    page = user_client.get("/help").text
    for code, name in ontology.BUSINESS_TYPES.items():
        assert name in page and ontology.BUSINESS_TYPE_DESCRIPTIONS[code] in page


# ---- 솔루션 ----

def test_solution_create_with_types_and_memos(user_client, db, settings):
    r = user_client.post("/solutions", data=_solution_form(types=["invest", "build"], memos={"invest": "  투자형은\n100kW 이상만 ", "build": "", "lease": "고르지 않은 방식의 메모"}),
                         follow_redirects=False)
    assert r.status_code == 303
    s = db.query(Solution).one()
    assert _rows(db, s) == [("build", ""), ("invest", "투자형은 100kW 이상만")]  # 정의된 순서, 메모는 한 줄로, 고르지 않은 방식의 메모는 버린다
    assert db.query(SolutionBusinessType).count() == 2
    card = (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")
    assert "- 가능한 사업방식: 구축형, 투자형 (투자형: 투자형은 100kW 이상만)" in card
    page = user_client.get(f"/solutions/{s.id}").text
    assert "가능한 사업방식" in page and "구축형" in page and "투자형은 100kW 이상만" in page and "임대형" not in page
    assert DRAFTS["invest"] in page  # 배지 툴팁에 한 줄 뜻


def test_solution_without_types_has_no_line_and_no_rows(user_client, db, settings):
    user_client.post("/solutions", data={"name": "X"}, follow_redirects=False)
    s = db.query(Solution).one()
    assert _rows(db, s) == []
    assert "가능한 사업방식" not in (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")
    assert "가능한 사업방식: " not in user_client.get(f"/solutions/{s.id}").text.split("아직 입력하지 않은 항목")[0]


def test_solution_edit_changes_removes_and_keeps(user_client, db, settings):
    user_client.post("/solutions", data=_solution_form(types=["build", "lease"], memos={"lease": "렌탈만"}), follow_redirects=False)
    s = db.query(Solution).one()
    page = user_client.get(f"/solutions/{s.id}/edit").text
    assert page.count('name="business_types"') == 3 and 'value="build" checked' in page and 'value="lease" checked' in page and 'value="invest" checked' not in page
    assert 'value="렌탈만"' in page
    # 바꾸기: lease 빼고 invest 더하고 build 는 그대로
    r = user_client.post(f"/solutions/{s.id}", data=_solution_form(types=["build", "invest"], memos={"invest": "IPP"}), follow_redirects=False)
    assert r.status_code == 303 and _rows(db, s) == [("build", ""), ("invest", "IPP")]
    # 사업방식 칸이 오지 않으면(옛 화면·API) 그대로 둔다
    r = user_client.post(f"/solutions/{s.id}", data={"name": "이름만 바꿈", "stage": "boxed"}, follow_redirects=False)
    assert r.status_code == 303 and _rows(db, s) == [("build", ""), ("invest", "IPP")]
    # 칸은 왔는데 아무것도 고르지 않으면 모두 지운다
    r = user_client.post(f"/solutions/{s.id}", data=_solution_form(types=[]), follow_redirects=False)
    assert r.status_code == 303 and _rows(db, s) == [] and db.query(SolutionBusinessType).count() == 0
    assert "가능한 사업방식" not in (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")


def test_solution_validation_errors(user_client, db):
    r = user_client.post("/solutions", data=_solution_form(types=["build", "bogus"]))
    assert r.status_code == 400 and "알 수 없는 사업방식" in r.text and db.query(Solution).count() == 0
    assert 'value="build" checked' in r.text  # 폼이 쓴 값으로 다시 그려진다
    r = user_client.post("/solutions", data=_solution_form(types=["invest"], memos={"invest": "가" * 301}))
    assert r.status_code == 400 and "300자까지" in r.text and db.query(Solution).count() == 0
    assert "가" * 301 in r.text  # 쓴 메모는 그대로
    r = user_client.post("/solutions", data=_solution_form(types=["invest"], memos={"invest": "가" * 300}), follow_redirects=False)
    assert r.status_code == 303  # 300자는 된다
    s = db.query(Solution).one()
    # 고르지 않은 방식의 긴 메모는 버려지고 오류가 아니다
    r = user_client.post(f"/solutions/{s.id}", data=_solution_form(types=["build"], memos={"lease": "가" * 999}), follow_redirects=False)
    assert r.status_code == 303 and _rows(db, s) == [("build", "")]
    # 고치기에서 틀린 값은 400 이고 저장된 값은 그대로
    r = user_client.post(f"/solutions/{s.id}", data=_solution_form(types=["nope"]))
    assert r.status_code == 400 and _rows(db, s) == [("build", "")]


def test_memo_is_html_escaped(user_client, db):
    evil = '<script>alert(1)</script> & "q"'
    user_client.post("/solutions", data=_solution_form(types=["invest"], memos={"invest": evil}), follow_redirects=False)
    s = db.query(Solution).one()
    for url in (f"/solutions/{s.id}", f"/solutions/{s.id}/edit"):
        page = user_client.get(url).text
        assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    r = user_client.post("/solutions", data=_solution_form(name="y", types=["invest"], memos={"invest": "가" * 301 + "<i>zzq</i>"}))
    assert r.status_code == 400 and "<i>zzq</i>" not in r.text and "&lt;i&gt;zzq&lt;/i&gt;" in r.text


def test_solution_list_badges_and_filter(user_client, db):
    for name, types in (("A 솔루션", ["build"]), ("B 솔루션", ["build", "invest"]), ("C 솔루션", []), ("D 솔루션", ["lease"])):
        user_client.post("/solutions", data=_solution_form(name=name, types=types), follow_redirects=False)
    page = user_client.get("/solutions").text
    assert "사업방식" in page and "구축형" in page and "투자형" in page and 'data-code="invest"' in page
    for code, expect in (("build", {"A 솔루션", "B 솔루션"}), ("invest", {"B 솔루션"}), ("lease", {"D 솔루션"})):
        page = user_client.get(f"/solutions?business_type={code}").text
        assert {n for n in ("A 솔루션", "B 솔루션", "C 솔루션", "D 솔루션") if f">{n}</a>" in page} == expect, code
    # 알 수 없는 값은 전체, 탭의 건수는 걸러 보기 전 목록 기준
    page = user_client.get("/solutions?business_type=zzz").text
    assert all(f">{n} 솔루션</a>" in page for n in "ABCD")
    tabs = re.search(r'aria-label="사업방식">(.*?)</nav>', user_client.get("/solutions").text, re.S).group(1)
    assert re.findall(r"(구축형|임대형|투자형)<b>(\d+)</b>", tabs) == [("구축형", "2"), ("임대형", "1"), ("투자형", "1")]
    # 다른 걸러 보기와 함께 쓰면 탭 링크가 조건을 이어 간다
    assert "business_type=invest" in user_client.get("/solutions?q=B").text


def test_solution_guard_detects_concurrent_type_change(user_client, db):
    user_client.post("/solutions", data=_solution_form(types=["build"]), follow_redirects=False)
    s = db.query(Solution).one()
    page = user_client.get(f"/solutions/{s.id}/edit").text
    rev = re.search(r'name="_rev" value="([^"]+)"', page).group(1)
    # 다른 사람이 사업방식만 바꾼다 (다른 칸은 그대로)
    r = user_client.post(f"/solutions/{s.id}", data=_solution_form(types=["build", "lease"]), follow_redirects=False)
    assert r.status_code == 303
    # 처음 폼을 열어 둔 사람이 저장하면 덮어쓰지 않고 충돌로 돌려준다
    r = user_client.post(f"/solutions/{s.id}", data={**_solution_form(types=["invest"]), "_rev": rev})
    assert r.status_code == 409 and "다른 곳에서 이 기록이 바뀌었습니다" in r.text
    assert _rows(db, s) == [("build", ""), ("lease", "")]
    assert "가능한 사업방식" in r.text and 'value="invest" checked' in r.text  # 쓴 값은 그대로 남아 있다
    # 메모만 바뀌어도 같다
    page = user_client.get(f"/solutions/{s.id}/edit").text
    rev = re.search(r'name="_rev" value="([^"]+)"', page).group(1)
    user_client.post(f"/solutions/{s.id}", data=_solution_form(types=["build", "lease"], memos={"lease": "렌탈"}), follow_redirects=False)
    r = user_client.post(f"/solutions/{s.id}", data={**_solution_form(types=["build", "lease"]), "_rev": rev})
    assert r.status_code == 409


def test_deleting_a_solution_deletes_its_type_rows(user_client, db):
    user_client.post("/solutions", data=_solution_form(types=["build", "invest"]), follow_redirects=False)
    s = db.query(Solution).one()
    assert user_client.post(f"/solutions/{s.id}/delete", follow_redirects=False).status_code == 303
    assert db.query(SolutionBusinessType).count() == 0


# ---- AI 초안: 제안만, 저장하지 않는다 ----

def test_solution_draft_suggests_types_as_prechecked_but_does_not_save(app, user_client, db, settings, fake_claude, worker_on):
    s = Solution(name="AI 에너지 절감", summary="기존 소개")
    db.add(s)
    db.commit()
    s.business_types.append(SolutionBusinessType(business_type="build", memo="기존 메모"))
    db.commit()
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("소개.txt", "IPP 방식으로 운영 가능".encode(), "text/plain"))])
    result = {"summary": "전력 절감", "description": "설명", "tags": ["에너지"],
              "business_types": [{"code": "invest", "reason": "IPP 방식 언급"}, {"code": "bogus", "reason": "x"}, "lease", 7, {"reason": "코드 없음"}]}
    fake_claude.set(mode="ok", result=json.dumps(result, ensure_ascii=False))
    url = user_client.post(f"/solutions/{s.id}/draft", follow_redirects=False).headers["location"]
    _run(app, settings)
    page = user_client.get(url).text
    assert "AI 초안입니다" in page
    assert 'value="invest" checked' in page and 'value="lease" checked' in page and 'value="build" checked' in page  # 제안 + 이미 있던 선택
    assert page.count("AI 제안</span>") == 2 and "IPP 방식 언급" in page and "bogus" not in page
    assert 'value="기존 메모"' in page  # 있던 메모는 지우지 않는다
    assert _rows(db, s) == [("build", "기존 메모")]  # 저장하지 않았다
    prompt = fake_claude.calls()[0]["stdin"]
    assert all(name in prompt for name in ontology.BUSINESS_TYPES.values()) and DRAFTS["invest"] in prompt
    # 사용자가 바꿔 저장해야 반영된다
    r = user_client.post(f"/solutions/{s.id}", data=_solution_form(name="AI 에너지 절감", types=["invest"], memos={"invest": "IPP"}), follow_redirects=False)
    assert r.status_code == 303 and _rows(db, s) == [("invest", "IPP")]


def test_draft_without_types_or_with_garbage_keeps_form_working(app, user_client, db, settings, fake_claude, worker_on):
    s = Solution(name="A")
    db.add(s)
    db.commit()
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("소개.txt", b"x", "text/plain"))])
    for result in ({"summary": "한 줄"}, {"summary": "한 줄", "business_types": "invest"}, {"summary": "한 줄", "business_types": [None, [], {"code": ["x"]}]}):
        fake_claude.set(mode="ok", result=json.dumps(result, ensure_ascii=False))
        url = user_client.post(f"/solutions/{s.id}/draft", follow_redirects=False).headers["location"]
        _run(app, settings)
        page = user_client.get(url).text
        assert "AI 초안입니다" in page and 'name="business_types"' in page and "AI 제안</span>" not in page


# ---- 올리기 (정리안): 제안만 ----

def test_intake_plan_suggests_types_and_apply_saves_only_what_user_keeps(app, user_client, db, settings):
    files = [("소개서.txt", "IPP 조명 소개".encode())]
    plan = {"solutions": [{"name": "스마트 조명", "summary": "조명", "partner": "루미텍", "files": [1], "reason": "소개서",
                           "business_types": [{"code": "invest", "reason": "IPP 언급"}, {"code": "bogus", "reason": ""}, "build"]}]}
    it = ready_bundle(db, settings, files, **plan)
    item = it.plan["items"][0]
    assert item["business_types"] == [{"code": "build", "reason": ""}, {"code": "invest", "reason": "IPP 언급"}]
    page = user_client.get(f"/intake/{it.id}").text
    assert f'name="{item["key"]}__business_types"' in page and "IPP 언급" in page and page.count("AI 제안</span>") == 2
    form = form_from_page(user_client, it)
    assert form[f'{item["key"]}__business_types'] == ["build", "invest"]  # 미리 체크
    form[f'{item["key"]}__business_types'] = ["invest"]  # 사용자가 하나를 뺀다
    r = user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    assert r.status_code == 303
    s = db.query(Solution).filter_by(name="스마트 조명").one()
    assert _rows(db, s) == [("invest", "")]
    assert "- 가능한 사업방식: 투자형" in (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")


def test_intake_without_suggestions_is_unchanged(app, user_client, db, settings):
    it = ready_bundle(db, settings, [("소개서.txt", b"x")], solutions=[{"name": "조명", "files": [1], "reason": "r"}])
    assert it.plan["items"][0]["business_types"] == []
    user_client.post(f"/intake/{it.id}/apply", data=form_from_page(user_client, it), follow_redirects=False)
    assert _rows(db, db.query(Solution).filter_by(name="조명").one()) == []


# ---- BM ----

def test_bm_representative_type(user_client, db, settings):
    r = user_client.post("/bms", data={"name": "구독 BM", "business_type": "lease"}, follow_redirects=False)
    assert r.status_code == 303
    bm = db.query(BM).one()
    assert bm.business_type == "lease"
    assert "- 대표 사업방식: 임대형" in (settings.knowledge_dir / "bms" / f"{bm.id}.md").read_text(encoding="utf-8")
    assert "대표 사업방식" in user_client.get(f"/bms/{bm.id}").text and "임대형" in user_client.get(f"/bms/{bm.id}").text
    edit = user_client.get(f"/bms/{bm.id}/edit").text
    assert '<option value="lease" selected>' in edit and DRAFTS["lease"] in edit
    # 칸이 오지 않으면 그대로, 비워 보내면 지운다, 틀린 값은 400
    assert user_client.post(f"/bms/{bm.id}", data={"name": "구독 BM"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(BM, bm.id).business_type == "lease"
    r = user_client.post(f"/bms/{bm.id}", data={"name": "구독 BM", "business_type": "x"})
    assert r.status_code == 400 and "알 수 없는 사업방식" in r.text
    r = user_client.post("/bms", data={"name": "또", "business_type": "x"})
    assert r.status_code == 400 and db.query(BM).count() == 1
    assert user_client.post(f"/bms/{bm.id}", data={"name": "구독 BM", "business_type": ""}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(BM, bm.id).business_type is None
    assert "대표 사업방식" not in (settings.knowledge_dir / "bms" / f"{bm.id}.md").read_text(encoding="utf-8")


def test_bm_list_badge_and_filter(user_client, db):
    for name, t in (("가 BM", "build"), ("나 BM", "invest"), ("다 BM", "")):
        user_client.post("/bms", data={"name": name, "business_type": t}, follow_redirects=False)
    page = user_client.get("/bms").text
    assert 'data-code="build"' in page and 'data-code="invest"' in page
    page = user_client.get("/bms?business_type=invest").text
    assert ">나 BM</a>" in page and ">가 BM</a>" not in page and ">다 BM</a>" not in page


# ---- 영업기회 ----

def test_opportunity_review_types(user_client, db, settings):
    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    r = user_client.post("/opportunities", data={"name": "냉동창고", "customer_id": str(c.id), "business_types_present": "1", "business_types": ["invest", "build", "invest"]},
                         follow_redirects=False)
    assert r.status_code == 303
    o = db.query(Opportunity).one()
    assert o.business_types == "build,invest" and o.business_type_codes == ["build", "invest"]
    assert "- 검토 사업방식: 구축형, 투자형" in (settings.knowledge_dir / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")
    detail = user_client.get(f"/opportunities/{o.id}").text
    assert "검토 사업방식" in detail and 'data-code="invest"' in detail
    assert 'data-code="build"' in user_client.get("/opportunities").text
    assert 'value="invest" checked' in user_client.get(f"/opportunities/{o.id}/edit").text
    # 안 오면 그대로, 틀린 값은 400, 비우면 지운다
    base = {"name": "냉동창고", "customer_id": str(c.id)}
    assert user_client.post(f"/opportunities/{o.id}", data=base, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Opportunity, o.id).business_types == "build,invest"
    r = user_client.post(f"/opportunities/{o.id}", data={**base, "business_types_present": "1", "business_types": ["zzz"]})
    assert r.status_code == 400 and "알 수 없는 사업방식" in r.text
    assert user_client.post(f"/opportunities/{o.id}", data={**base, "business_types_present": "1"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Opportunity, o.id).business_types == ""
    assert "검토 사업방식" not in (settings.knowledge_dir / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")


def test_opportunity_list_filter(user_client, db):
    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    for name, types in (("기회 가", ["lease"]), ("기회 나", ["lease", "invest"]), ("기회 다", [])):
        user_client.post("/opportunities", data={"name": name, "customer_id": str(c.id), "business_types_present": "1", "business_types": types}, follow_redirects=False)
    page = user_client.get("/opportunities?business_type=invest").text
    assert ">기회 나</a>" in page and ">기회 가</a>" not in page
    page = user_client.get("/opportunities?business_type=lease").text
    assert ">기회 나</a>" in page and ">기회 가</a>" in page and ">기회 다</a>" not in page


def test_opportunity_guard_detects_concurrent_type_change(user_client, db):
    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    user_client.post("/opportunities", data={"name": "기회", "customer_id": str(c.id)}, follow_redirects=False)
    o = db.query(Opportunity).one()
    rev = re.search(r'name="_rev" value="([^"]+)"', user_client.get(f"/opportunities/{o.id}/edit").text).group(1)
    user_client.post(f"/opportunities/{o.id}", data={"name": "기회", "customer_id": str(c.id), "business_types_present": "1", "business_types": ["build"]}, follow_redirects=False)
    r = user_client.post(f"/opportunities/{o.id}", data={"name": "기회", "customer_id": str(c.id), "business_types_present": "1", "business_types": ["invest"], "_rev": rev})
    assert r.status_code == 409


# ---- 제안 건: 기본값, 수정, 방향 ----

def _world(db):
    c = Customer(name="한빛병원")
    bm = BM(name="구독 BM", business_type="lease")
    bm2 = BM(name="BM 투자", business_type="invest")
    db.add_all([c, bm, bm2])
    db.flush()
    pr = Project(name="프로젝트", customer_id=c.id, bm_id=bm.id)
    opp = Opportunity(name="기회", customer_id=c.id, business_types="build,invest")
    opp_bm = Opportunity(name="기회 BM", customer_id=c.id, bm_id=bm2.id)
    opp_none = Opportunity(name="기회 없음", customer_id=c.id)
    db.add_all([pr, opp, opp_bm, opp_none])
    db.commit()
    return c, bm, bm2, pr, opp, opp_bm, opp_none


def test_new_proposal_form_defaults_from_opportunity_then_bm(user_client, db):
    c, bm, bm2, pr, opp, opp_bm, opp_none = _world(db)
    page = user_client.get(f"/proposals/new?opportunity_id={opp.id}").text
    assert 'value="build" checked' in page and 'value="invest" checked' in page and 'value="lease" checked' not in page
    assert 'name="business_types_auto" value="1"' in page and 'id="bt-defaults"' in page
    page = user_client.get(f"/proposals/new?opportunity_id={opp_bm.id}").text  # 영업기회에 사업방식이 없으면 그 BM 의 대표 사업방식
    assert 'value="invest" checked' in page and 'value="build" checked' not in page
    page = user_client.get(f"/proposals/new?customer_id={c.id}&project_id={pr.id}").text  # 프로젝트의 BM
    assert 'value="lease" checked' in page
    page = user_client.get(f"/proposals/new?opportunity_id={opp_none.id}").text
    assert "checked" not in re.search(r'<fieldset class="picker bt-picker.*?</fieldset>', page, re.S).group(0)
    page = user_client.get("/proposals/new").text
    assert "checked" not in re.search(r'<fieldset class="picker bt-picker.*?</fieldset>', page, re.S).group(0)


def test_create_proposal_defaults_and_explicit_choice(user_client, db):
    c, bm, bm2, pr, opp, opp_bm, opp_none = _world(db)

    def create(**data):
        r = user_client.post("/proposals", data={"customer_id": str(c.id), **data}, follow_redirects=False)
        assert r.status_code == 303
        return db.query(Proposal).order_by(Proposal.id.desc()).first()

    assert create(opportunity_id=str(opp.id)).business_types == "build,invest"  # 칸이 없는 API 호출: 영업기회에서
    assert create(opportunity_id=str(opp_bm.id)).business_types == "invest"  # 영업기회에 없으면 BM
    assert create(project_id=str(pr.id)).business_types == "lease"  # 영업기회가 없으면 프로젝트의 BM
    assert create().business_types == ""
    p = create(opportunity_id=str(opp.id), business_types_present="1", business_types_auto="1")
    assert p.business_types == "build,invest"  # 건드리지 않은 채 보냈다 (JS 가 없어도 서버가 같은 기본값을 쓴다)
    p = create(opportunity_id=str(opp.id), business_types_present="1", business_types_auto="0", business_types=["lease"])
    assert p.business_types == "lease"  # 직접 고른 값이 우선
    p = create(opportunity_id=str(opp.id), business_types_present="1", business_types_auto="0")
    assert p.business_types == ""  # 직접 모두 비웠다
    r = user_client.post("/proposals", data={"customer_id": str(c.id), "business_types_present": "1", "business_types": ["bogus"]})
    assert r.status_code == 400 and "알 수 없는 사업방식" in r.text


def test_create_group_proposal_defaults_from_new_business_bm(user_client, db):
    c, bm, bm2, *_ = _world(db)
    data = {"target_kind": "group", "group_name": "전국 지자체"}
    assert user_client.post("/proposals", data={**data, "bm_id": str(bm.id)}, follow_redirects=False).status_code == 303
    assert db.query(Proposal).order_by(Proposal.id.desc()).first().business_types == "lease"
    user_client.post("/proposals", data={**data, "bm_id": str(bm2.id), "business_types_present": "1", "business_types_auto": "0", "business_types": ["build", "lease"]}, follow_redirects=False)
    assert db.query(Proposal).order_by(Proposal.id.desc()).first().business_types == "build,lease"
    user_client.post("/proposals", data=data, follow_redirects=False)
    assert db.query(Proposal).order_by(Proposal.id.desc()).first().business_types == ""


def test_edit_target_types_keeps_directions_and_outline(user_client, db, settings):
    p, c, b, s1, s2, s3 = make_proposal_world(db, settings)
    p.directions = [{"title": "방향 A", "solutions": [{"solution_id": s1.id, "name": s1.name, "role": ""}], "business_type": "build"}]
    p.outline = {"title": "t", "slides": [{"kind": "cover", "title": "t"}]}
    p.status = "outlined"
    p.selected_index = 0
    db.commit()
    page = user_client.get(f"/proposals/{p.id}").text
    assert "검토 사업방식" in page and "지정하지 않았습니다" in page and 'name="business_types"' in page and "다음 방향 만들기" in page
    r = user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "메모", "business_types_present": "1", "business_types": ["invest", "build"]}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r).endswith("?notice=target_saved")
    db.expire_all()
    p = db.get(Proposal, p.id)
    assert p.business_types == "build,invest" and p.status == "outlined" and p.selected_index == 0 and len(p.directions) == 1 and p.outline["slides"]
    assert 'data-code="invest"' in user_client.get(f"/proposals/{p.id}").text
    # 칸이 오지 않으면 그대로 / 틀린 값은 400 이고 그대로 / 비우면 지운다
    assert user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "메모"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Proposal, p.id).business_types == "build,invest"
    r = user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "메모", "business_types_present": "1", "business_types": ["bogus"]})
    assert r.status_code == 400 and "알 수 없는 사업방식" in r.text
    db.expire_all()
    assert db.get(Proposal, p.id).business_types == "build,invest"
    assert user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "메모", "business_types_present": "1"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Proposal, p.id).business_types == ""


def test_proposal_guard_detects_concurrent_type_change(user_client, db, settings):
    p, *_ = make_proposal_world(db, settings)
    page = user_client.get(f"/proposals/{p.id}").text
    rev = re.search(r'name="_rev" value="([^"]+)"', page).group(1)
    user_client.post(f"/proposals/{p.id}/target", data={"request_memo": p.request_memo, "business_types_present": "1", "business_types": ["lease"]}, follow_redirects=False)
    r = user_client.post(f"/proposals/{p.id}/target", data={"request_memo": p.request_memo, "business_types_present": "1", "business_types": ["invest"], "_rev": rev})
    assert r.status_code == 409 and "다른 곳에서 이 기록이 바뀌었습니다" in r.text
    db.expire_all()
    assert db.get(Proposal, p.id).business_types == "lease"


def test_group_proposal_target_form_edits_types(user_client, db):
    c, bm, *_ = _world(db)
    user_client.post("/proposals", data={"target_kind": "group", "group_name": "지자체", "bm_id": str(bm.id)}, follow_redirects=False)
    p = db.query(Proposal).one()
    r = user_client.post(f"/proposals/{p.id}/target", data={"group_name": "지자체", "request_memo": "", "business_types_present": "1", "business_types": ["build", "lease", "invest"]},
                         follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Proposal, p.id).business_types == "build,lease,invest"


# ---- 프롬프트 ----

def _typed_world(db, settings):
    p, c, b, s1, s2, s3 = make_proposal_world(db, settings)
    s1.business_types.append(SolutionBusinessType(business_type="build", memo=""))
    s1.business_types.append(SolutionBusinessType(business_type="invest", memo="100kW 이상만"))
    s2.business_types.append(SolutionBusinessType(business_type="lease", memo=""))
    db.commit()
    return p, c, b, s1, s2, s3


def test_directions_prompt_has_types_descriptions_and_per_solution_types(db, settings):
    p, c, b, s1, s2, s3 = _typed_world(db, settings)
    p.business_types = "build,invest"
    db.commit()
    prompt = directions_prompt(db, p)
    assert "이 제안 건이 검토하는 사업방식: 구축형, 투자형" in prompt
    for code, name in ontology.BUSINESS_TYPES.items():
        assert f"- {name}: {DRAFTS[code]}" in prompt
    assert f"{s1.name} (`solutions/{s1.id}/`) · 가능한 사업방식: 구축형, 투자형 (투자형: 100kW 이상만)" in prompt
    assert f"{s2.name} (`solutions/{s2.id}/`) · 가능한 사업방식: 임대형" in prompt
    assert "나누어 다루거나 서로 비교" in prompt and "`business_type`" in prompt and '"business_type": "build"' in prompt


def test_directions_prompt_without_selection_lets_ai_propose_and_marks_unknown_solution_types(db, settings):
    p, c, b, s1, s2, s3 = _typed_world(db, settings)
    prompt = directions_prompt(db, p)
    assert "이 제안 건이 검토하는 사업방식: 지정되지 않음" in prompt and "어울리는 사업방식을 방향마다 제안해도 됩니다" in prompt
    bare = Solution(name="사업방식 미기재", stage="boxed")
    db.add(bare)
    db.commit()
    assert "사업방식 미기재" in directions_prompt(db, p) and "가능한 사업방식: 적혀 있지 않음" in directions_prompt(db, p)
    p.business_types = "lease"
    db.commit()
    assert "모두 임대형을 전제로" in directions_prompt(db, p)


def test_chat_and_outline_prompts_carry_types_and_invest_slide_rule(app, user_client, db, settings, fake_claude, worker_on):
    from tests.conftest import refining_proposal, sample_outline

    p = refining_proposal(db, settings)
    s1 = db.query(Solution).filter_by(name="AI 에너지 절감").one()
    s1.business_types.append(SolutionBusinessType(business_type="invest", memo="IPP 가능"))
    p.business_types = "invest,lease"
    p.directions = [{**p.directions[0], "business_type": "invest"}, p.directions[1]]
    db.commit()
    fake_claude.set(mode="ok", result="답변입니다")
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "투자형으로 가면?"})
    _run(app, settings)
    chat = fake_claude.calls()[0]["stdin"]
    assert "이 제안 건이 검토하는 사업방식: 임대형, 투자형" in chat and "선택한 방향이 전제하는 사업방식: 투자형" in chat
    assert f"{s1.name} (`solutions/{s1.id}/`) · 가능한 사업방식: 투자형 (투자형: IPP 가능)" in chat and DRAFTS["lease"] in chat
    fake_claude.set(mode="ok", result=json.dumps(sample_outline(8), ensure_ascii=False))
    user_client.post(f"/proposals/{p.id}/outline")
    _run(app, settings)
    outline = fake_claude.calls()[-1]["stdin"]
    assert "투자·회수 구조" in outline and "투자 금액·수익률·회수 기간" in outline and "needs_check" in outline.split("투자·회수 구조")[1]
    assert "이 제안 건이 검토하는 사업방식: 임대형, 투자형" in outline
    # 투자형이 전혀 없으면 그 규칙은 들어가지 않는다
    db.expire_all()
    p = db.get(Proposal, p.id)
    p.business_types = "build"
    p.directions = [{**p.directions[0], "business_type": "build"}, p.directions[1]]
    db.commit()
    from app.ai.proposal_tasks import INVEST_SLIDE_RULE

    user_client.post(f"/proposals/{p.id}/outline")
    _run(app, settings)
    assert INVEST_SLIDE_RULE not in fake_claude.calls()[-1]["stdin"] and "투자형을 검토하지 않습니다" in fake_claude.calls()[-1]["stdin"]


def test_outline_includes_invest_rule_when_only_the_direction_assumes_invest(app, user_client, db, settings, fake_claude, worker_on):
    from app.ai.proposal_tasks import INVEST_SLIDE_RULE
    from tests.conftest import refining_proposal, sample_outline

    p = refining_proposal(db, settings)
    p.directions = [{**p.directions[0], "business_type": "invest"}, p.directions[1]]
    db.commit()
    fake_claude.set(mode="ok", result=json.dumps(sample_outline(8), ensure_ascii=False))
    user_client.post(f"/proposals/{p.id}/outline")
    _run(app, settings)
    assert INVEST_SLIDE_RULE in fake_claude.calls()[-1]["stdin"]


# ---- 방향의 사업방식 ----

def _directions_out(s1, s2, bt1, bt2):
    return DirectionsOut.model_validate({"directions": [
        {"title": "A", "solutions": [{"solution_id": s1.id}], "rationale": [{"text": "r", "sources": []}], "business_type": bt1},
        {"title": "B", "solutions": [{"solution_id": s2.id}], "rationale": [{"text": "r", "sources": []}], "business_type": bt2}]})


def test_direction_business_type_is_parsed_and_invalid_is_ignored(app, user_client, db, settings, fake_claude, worker_on):
    p, c, b, s1, s2, s3 = make_proposal_world(db, settings)
    data = {"directions": [
        {"title": "구축 방향", "solutions": [{"solution_id": s1.id}], "rationale": [{"text": "r", "sources": []}], "business_type": "build"},
        {"title": "엉뚱 방향", "solutions": [{"solution_id": s2.id}], "rationale": [{"text": "r", "sources": []}], "business_type": "크라우드"},
        {"title": "숫자 방향", "solutions": [{"solution_id": s2.id}], "rationale": [{"text": "r", "sources": []}], "business_type": 5}]}
    fake_claude.set(mode="ok", result=json.dumps(data, ensure_ascii=False))
    user_client.post(f"/proposals/{p.id}/directions")
    _run(app, settings)
    db.expire_all()
    p = db.get(Proposal, p.id)
    assert p.status == "directions" and [d["business_type"] for d in p.directions] == ["build", "", ""]
    page = user_client.get(f"/proposals/{p.id}").text
    assert 'class="dir-bt"' in page and page.count('class="dir-bt"') == 1 and 'data-code="build"' in page
    # 옛 방향(키 없음)도 화면이 그려진다
    p.directions = [{k: v for k, v in d.items() if k != "business_type"} for d in p.directions]
    db.commit()
    assert user_client.get(f"/proposals/{p.id}").status_code == 200


def test_clean_directions_drops_invalid_type(db, settings):
    p, c, b, s1, s2, s3 = make_proposal_world(db, settings)
    out = _directions_out(s1, s2, "invest", "bogus")
    assert [d["business_type"] for d in clean_directions(db, p, out, settings.knowledge_dir)] == ["invest", ""]


# ---- 후보 순서 ----

def test_candidates_prefer_solutions_supporting_a_selected_type(db, settings):
    p, c, b, s1, s2, s3 = make_proposal_world(db, settings)
    extra = Solution(name="가나다 솔루션", stage="boxed")
    db.add(extra)
    db.commit()
    names = lambda: [s.name for s in candidate_solutions(db, p)]  # noqa: E731
    base = names()
    assert base == sorted(base)  # 선택이 없으면 이름순 그대로
    s2.business_types.append(SolutionBusinessType(business_type="invest", memo=""))
    db.commit()
    p.business_types = "invest,lease"
    db.commit()
    assert names()[0] == "스마트 조명" and names()[1:] == [n for n in base if n != "스마트 조명"]  # 지원하는 것만 앞으로, 나머지 순서·집합은 그대로
    assert set(names()) == set(base)
    p.business_types = "build"  # 아무도 지원하지 않으면 바뀌지 않는다
    db.commit()
    assert names() == base


def test_candidate_priority_groups_keep_order_and_prefer_within_group(db, settings):
    p, c, b, s1, s2, s3 = make_proposal_world(db, settings)
    bm = BM(name="BM")
    bm.solutions = [s1, s2]
    opp = Opportunity(name="기회", customer_id=c.id, bm=bm)
    opp.solutions = [s2]  # 영업기회의 검토 솔루션이 먼저
    db.add_all([bm, opp])
    db.flush()
    p.opportunity_id = opp.id
    extra = Solution(name="나머지", stage="boxed")
    extra.business_types.append(SolutionBusinessType(business_type="build", memo=""))
    s1.business_types.append(SolutionBusinessType(business_type="build", memo=""))
    db.add(extra)
    db.commit()
    p.business_types = "build"
    db.commit()
    order = [s.name for s in candidate_solutions(db, p)]
    # 묶음 1: 영업기회 검토 솔루션(스마트 조명), 묶음 2: BM 의 나머지(AI 에너지 절감), 나머지 묶음: 지원하는 것이 먼저
    assert order[:2] == ["스마트 조명", "AI 에너지 절감"] and order[2:] == ["나머지"]
    p.business_types = ""
    db.commit()
    assert [s.name for s in candidate_solutions(db, p)] == ["스마트 조명", "AI 에너지 절감", "나머지"]


# ---- 옛 DB 이관 ----

def test_old_database_gets_new_table_and_columns_without_changing_rows(tmp_path):
    db_path = tmp_path / "app.db"
    _old_db(db_path)
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO bms (id, name, summary, target_customers, revenue_model, memo) VALUES (1, '옛 BM', '', '', '', '')")
    con.commit()
    cols = {"solutions": "id, name, summary, stage", "opportunities": "id, name, customer_id, stage, memo", "bms": "id, name, summary, memo",
            "proposals": "id, title, customer_id, request_memo, status"}
    before = {t: con.execute(f"SELECT {c} FROM {t} ORDER BY id").fetchall() for t, c in cols.items()}
    assert con.execute("SELECT name FROM sqlite_master WHERE name='solution_business_types'").fetchone() is None
    for table, col in (("bms", "business_type"), ("opportunities", "business_types"), ("proposals", "business_types")):
        assert col not in [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    con.close()
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    migrate.run_startup_migrations(engine, db_path)
    engine.dispose()
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='solution_business_types'").fetchone() is not None
        pk = [r[1] for r in con.execute("PRAGMA table_info(solution_business_types)") if r[5]]
        assert sorted(pk) == ["business_type", "solution_id"]
        assert con.execute("SELECT COUNT(*) FROM solution_business_types").fetchone()[0] == 0
        info = {t: {r[1]: r for r in con.execute(f"PRAGMA table_info({t})")} for t in ("bms", "opportunities", "proposals")}
        assert info["bms"]["business_type"][3] == 0  # NULL 허용
        assert info["opportunities"]["business_types"][3] == 1 and info["opportunities"]["business_types"][4] == "''"  # 기본값 빈 글
        assert info["proposals"]["business_types"][3] == 1 and info["proposals"]["business_types"][4] == "''"
        assert {t: con.execute(f"SELECT {c} FROM {t} ORDER BY id").fetchall() for t, c in cols.items()} == before  # 옛 줄은 그대로
        assert con.execute("SELECT business_type FROM bms").fetchall() == [(None,)]
        assert con.execute("SELECT DISTINCT business_types FROM proposals").fetchall() == [("",)]
        assert con.execute("SELECT DISTINCT business_types FROM opportunities").fetchall() == [("",)]
    finally:
        con.close()


def test_real_start_on_old_database_keeps_everything_working(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _old_db(settings.db_path)
    app = create_app(settings)
    try:
        from fastapi.testclient import TestClient

        c = TestClient(app)
        assert c.post("/login", data={"username": "kim", "password": "pw1234"}, follow_redirects=False).status_code == 303
        for url in ("/solutions", "/solutions/1", "/solutions/1/edit", "/opportunities", "/opportunities/1", "/proposals", "/proposals/1", "/proposals/5", "/bms", "/proposals/new"):
            assert c.get(url).status_code == 200, url
        assert c.post("/solutions/1", data=_solution_form(name="AI 공조", types=["lease"]), follow_redirects=False).status_code == 303
        assert c.post("/proposals/1/target", data={"request_memo": "전력 절감", "business_types_present": "1", "business_types": ["invest"]}, follow_redirects=False).status_code == 303
        s = app.state.session_factory()
        try:
            assert [(r.business_type) for r in s.get(Solution, 1).business_type_rows] == ["lease"] and s.get(Proposal, 1).business_types == "invest"
        finally:
            s.close()
    finally:
        app.state.engine.dispose()


# ---- 지식 사본 형식 ----

def test_knowledge_format_was_bumped_and_rebuild_writes_lines(app, db, settings):
    from app.knowledge import KNOWLEDGE_FORMAT, KnowledgeMirror

    assert KNOWLEDGE_FORMAT >= 9
    s = Solution(name="S", stage="boxed")
    s.business_types = [SolutionBusinessType(business_type="invest", memo="a (b); c"), SolutionBusinessType(business_type="build", memo="")]
    bm = BM(name="B", business_type="build")
    db.add_all([s, bm])
    db.commit()
    KnowledgeMirror(settings.knowledge_dir).rebuild_all(db, settings.uploads_dir)
    assert "- 가능한 사업방식: 구축형, 투자형 (투자형: a (b); c)" in (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")
    assert "- 대표 사업방식: 구축형" in (settings.knowledge_dir / "bms" / f"{bm.id}.md").read_text(encoding="utf-8")


def test_helpers_have_sane_edge_cases():
    assert bts.clean_suggestions("invest") == [] and bts.clean_suggestions(None) == []
    assert bts.clean_suggestions([{"code": "lease", "reason": "a\nb " + "x" * 300}])[0]["reason"].startswith("a b x") and len(bts.clean_suggestions([{"code": "lease", "reason": "x" * 300}])[0]["reason"]) == 200
    assert bts.clean_suggestions(["invest", {"code": "invest", "reason": "중복"}, {"type": "build"}]) == [{"code": "build", "reason": ""}, {"code": "invest", "reason": ""}]
    assert bts.proposal_default(None, None) == []

"""베타 R: AI 가 만든 기록(유래가 올리기·리서치)은 화면과 지식 사본에 "AI 생성" 으로 표시한다 (사용자 결정 2026-11-22).

기준은 기록의 유래 하나다(`ontology.is_ai_generated`). 직접 입력·유래 모름은 표시하지 않는다. 열이 새로 생기지 않는다."""
import re
from datetime import datetime
from pathlib import Path

import pytest

from app import ontology
from app.knowledge import KnowledgeMirror
from app.models import Activity, Customer, Intake, Partner, ResearchReport, ResearchRun, Solution
from tests.conftest import make_user

BADGE = 'data-code="ai_generated"'
APP = Path(__file__).resolve().parent.parent / "app"

TYPES = {  # 경로, 모델, 이름 칸
    "solution": ("/solutions", Solution, {"name": "{n}"}),
    "activity": ("/activities", Activity, {"title": "{n}", "raw_text": "원문"}),
    "customer": ("/customers", Customer, {"name": "{n}"}),
    "partner": ("/partners", Partner, {"name": "{n}"}),
}


def _make(db, kind, name, origin_type, origin_id=None):
    _, model, fields = TYPES[kind]
    values = {k: v.format(n=name) for k, v in fields.items()}
    obj = model(origin_type=origin_type, origin_id=origin_id, **values)
    db.add(obj)
    db.commit()
    return obj


def _origins(db):
    """올리기 묶음 하나와 리서치 보고서 하나 (유래 링크가 그려지게)."""
    owner = make_user(db, "lee", name="이영희")
    intake = Intake(created_by_id=owner.id, status="applied", note="")
    run = ResearchRun(trigger="manual", status="done", started_at=datetime(2026, 11, 20, 9, 0))
    db.add_all([intake, run])
    db.flush()
    report = ResearchReport(run_id=run.id, kind="suggest", status="done")
    db.add(report)
    db.commit()
    return intake, report


def test_helper_definition():
    class Row:
        def __init__(self, origin_type):
            self.origin_type = origin_type

    assert ontology.is_ai_generated(Row("intake")) and ontology.is_ai_generated(Row("research"))
    assert not ontology.is_ai_generated(Row("manual")) and not ontology.is_ai_generated(Row(None)) and not ontology.is_ai_generated(Row(""))
    assert not ontology.is_ai_generated(object())  # 유래 열이 없는 유형 (프로젝트 …)
    assert ontology.AI_GENERATED_LABEL == "AI 생성"


@pytest.mark.parametrize("kind", list(TYPES))
@pytest.mark.parametrize("origin_type", ["intake", "research"])
def test_badge_on_list_and_detail_for_ai_origin(user_client, db, kind, origin_type):
    intake, report = _origins(db)
    obj = _make(db, kind, "AI기록", origin_type, intake.id if origin_type == "intake" else report.id)
    base = TYPES[kind][0]
    listing = user_client.get(base).text
    assert listing.count(BADGE) == 2  # 좁은 화면의 보조 줄 + 넓은 화면의 유래 칸
    assert listing.count(ontology.AI_GENERATED_LABEL) >= 2
    assert f"{ontology.ORIGINS[origin_type]} <span class=\"badge" in listing  # 기존 유래 글자 바로 옆
    detail = user_client.get(f"{base}/{obj.id}").text
    assert detail.count(BADGE) == 1
    line = re.search(r'<p class="origin muted">(.*?)</p>', detail, re.S).group(1)
    assert line.startswith("유래:") and BADGE in line  # 기존 "유래:" 줄 안에서 유래 글 뒤에


@pytest.mark.parametrize("kind", list(TYPES))
@pytest.mark.parametrize("origin_type", ["manual", None])
def test_no_badge_for_manual_or_empty_origin(user_client, db, kind, origin_type):
    obj = _make(db, kind, "손기록", origin_type)
    base = TYPES[kind][0]
    assert BADGE not in user_client.get(base).text
    assert BADGE not in user_client.get(f"{base}/{obj.id}").text


@pytest.mark.parametrize("kind", list(TYPES))
def test_origin_text_stays_and_deleted_bundle_still_marked(user_client, db, kind):
    intake, _ = _origins(db)
    obj = _make(db, kind, "올린기록", "intake", intake.id)
    detail = user_client.get(f"{TYPES[kind][0]}/{obj.id}").text
    assert "올리기 묶음" in detail and "유래 올리기" in user_client.get(TYPES[kind][0]).text
    gone = _make(db, kind, "묶음잃은기록", "intake", None)  # 묶음이 지워져 번호가 빈 기록도 AI 가 만든 것이다
    assert BADGE in user_client.get(f"{TYPES[kind][0]}/{gone.id}").text


def test_only_marked_rows_get_badge_in_mixed_list(user_client, db):
    intake, report = _origins(db)
    _make(db, "solution", "올리기솔", "intake", intake.id)
    _make(db, "solution", "리서치솔", "research", report.id)
    _make(db, "solution", "직접솔", "manual")
    _make(db, "solution", "모름솔", None)
    assert user_client.get("/solutions").text.count(BADGE) == 4  # 두 기록 x (보조 줄 + 칸)


def test_related_lists_do_not_show_the_badge(user_client, db):
    """연결된 것 카드는 유래를 보이지 않으므로 배지도 더하지 않는다."""
    intake, _ = _origins(db)
    partner = _make(db, "partner", "만든파트너", "manual")
    s = _make(db, "solution", "올린솔", "intake", intake.id)
    s.partner_id = partner.id
    db.commit()
    assert BADGE not in user_client.get(f"/partners/{partner.id}").text


def test_name_shown_next_to_badge_is_escaped(user_client, db):
    """열 수 없는 묶음의 올린 사람 이름이 유래 글에 들어가므로 이스케이프를 확인한다."""
    evil = make_user(db, "evil", name="<i>x</i>&")
    intake = Intake(created_by_id=evil.id, status="applied", note="")
    db.add(intake)
    db.commit()
    s = _make(db, "solution", "남의솔", "intake", intake.id)
    page = user_client.get(f"/solutions/{s.id}").text
    assert "&lt;i&gt;x&lt;/i&gt;&amp;님이 올린 묶음" in page and "<i>x</i>&" not in page
    assert BADGE in page


def test_knowledge_line_for_each_type_and_origin(app, db, tmp_path):
    intake, report = _origins(db)
    m = KnowledgeMirror(tmp_path)
    for kind in TYPES:
        for origin_type, number, want in (
            ("intake", intake.id, f"- AI 생성: 예 (올리기 묶음 #{intake.id} 에서 AI 가 정리)"),
            ("research", report.id, f"- AI 생성: 예 (리서치 보고서 #{report.id} 에서 AI 가 제안)"),
        ):
            obj = _make(db, kind, f"{kind}-{origin_type}", origin_type, number)
            m.sync(obj)
            text = m.path_for(obj).read_text(encoding="utf-8")
            lines = text.splitlines()
            assert want in lines
            assert lines.index(want) <= 3  # 제목 줄 · 빈 줄 바로 다음 (맨 위 속성 줄)


def test_knowledge_line_absent_for_manual_empty_and_other_types(db, tmp_path):
    m = KnowledgeMirror(tmp_path)
    for kind in TYPES:
        for origin_type in ("manual", None):
            obj = _make(db, kind, f"{kind}-{origin_type}", origin_type)
            m.sync(obj)
            text = m.path_for(obj).read_text(encoding="utf-8")
            assert "AI 생성" not in text and "아니오" not in text


def test_knowledge_line_without_number_when_origin_deleted(db, tmp_path):
    m = KnowledgeMirror(tmp_path)
    s = _make(db, "solution", "번호없음", "research", None)
    m.sync(s)
    assert "- AI 생성: 예 (리서치 보고서 에서 AI 가 제안)" in m.path_for(s).read_text(encoding="utf-8").splitlines()


def test_existing_copy_changes_only_when_saved_again(db, tmp_path):
    """유래가 있는 옛 기록의 사본은 다시 저장(sync)·`rebuild_all` 때 바뀐다 (형식 번호를 올리지 않는다)."""
    m = KnowledgeMirror(tmp_path)
    s = _make(db, "solution", "옛기록", "intake", None)
    path = m.path_for(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 옛기록\n", encoding="utf-8")
    assert "AI 생성" not in path.read_text(encoding="utf-8")
    m.sync(s)
    assert "- AI 생성: 예" in path.read_text(encoding="utf-8")


def test_label_comes_from_the_single_constant(user_client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(ontology, "AI_GENERATED_LABEL", "AI 작성")
    intake, _ = _origins(db)
    s = _make(db, "solution", "이름바꿈", "intake", intake.id)
    assert ">AI 작성</span>" in user_client.get(f"/solutions/{s.id}").text
    assert ">AI 작성</span>" in user_client.get("/solutions").text
    m = KnowledgeMirror(tmp_path)
    m.sync(s)
    assert "- AI 작성: 예 (" in m.path_for(s).read_text(encoding="utf-8")


def test_label_literal_is_defined_only_once_in_code_and_templates():
    """"AI 생성" 글자는 ontology 의 상수 하나만 정의한다. 템플릿은 주석을 빼고, 코드는 설명(주석·독스트링)을 빼고 글자값에 없어야 한다."""
    import ast

    literal = ontology.AI_GENERATED_LABEL
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel == "ontology.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        bad = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and literal in n.value and id(n) not in docs]
        assert not bad, (rel, bad)
    for path in APP.rglob("*.html"):
        text = re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.S)
        if path.name != "help.html":  # 도움말 글은 손으로 쓴 설명이다
            assert literal not in text, path.name


def test_prompt_tells_ai_that_marked_lines_are_unverified(monkeypatch):
    from app.ai.tasks import load_prompt

    text = load_prompt("proposal_directions", customer_path="x", project_line="", opportunity_line="", request_memo="", solutions="",
                       priority_section="")
    assert "- AI 생성: 예" in text and "확인되지 않은" in text and "{{ai_label}}" not in text
    monkeypatch.setattr(ontology, "AI_GENERATED_LABEL", "AI 작성")
    assert "- AI 작성: 예" in load_prompt("proposal_directions")


def test_no_new_columns_for_ai_generated():
    for model in (Solution, Activity, Customer, Partner):
        assert not [c for c in model.__table__.c.keys() if "ai_gen" in c]

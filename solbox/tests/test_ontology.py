"""온톨로지 단일 정의(`app/ontology.py`)의 일관성 검사: 정의가 실제 모델·표·주소·코드와 맞고, 같은 목록이 다른 곳에 다시 정의되지 않았는지."""
import re
from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect

from app import models, ontology
from app.db import Base

APP_DIR = Path(__file__).resolve().parent.parent / "app"
EMBEDDED_ONLY = {"direction", "slide", "check_item", "plan_item", "suggestion"}


def test_every_type_matches_orm_and_tables():
    for code, t in ontology.TYPES.items():
        if t.model_name is None:
            assert t.embedded_in, f"{code}: 모델이 없으면 어디에 들어 있는지 적어야 한다"
            assert t.table is None
            continue
        model = t.model
        assert model.__tablename__ == t.table, code
        assert t.table in Base.metadata.tables, code
    assert {c for c, t in ontology.TYPES.items() if t.model_name is None} == EMBEDDED_ONLY


def test_every_orm_model_is_a_type_or_listed():
    mapped = {m.class_.__name__ for m in Base.registry.mappers}
    defined = {t.model_name for t in ontology.TYPES.values() if t.model_name}
    # 쓰이는 대상이 아닌 보조 표: 제안 대화, 설정, 한 번 쓰는 폼 표, 번호 표시, 솔루션의 사업방식 줄 (연결 표, 베타 Y)
    assert mapped - defined == {"ProposalMessage", "AppSetting", "FormToken", "IdMark", "SolutionBusinessType"}


def _route_paths(app) -> set[str]:
    return {re.sub(r"\{\w+\}", "{}", p) for p, v in app.openapi()["paths"].items() if "get" in v}


def test_type_urls_are_real_routes(app):
    paths = _route_paths(app)
    for code, t in ontology.TYPES.items():
        for url in (t.list_url, t.detail_url):
            if url:
                assert re.sub(r"\{\w+\}", "{}", url) in paths, f"{code}: {url}"


def test_project_physical_names_are_unchanged():
    t = ontology.TYPES["project"]
    assert t.table == "businesses" and t.model is models.Project
    assert t.physical == {"fk_column": "business_id", "link_table": "activity_businesses"}
    assert "business_id" in Base.metadata.tables["proposals"].c
    assert "business_id" in Base.metadata.tables["activity_businesses"].c
    assert models.Proposal.project_id.property.columns[0].name == "business_id"


def test_mirror_paths_round_trip_and_legacy():
    assert ontology.rel_path("project", id=3) == "projects/3.md"
    assert ontology.parse_rel("projects/3.md") == ("project", {"id": "3"})
    assert ontology.parse_rel("businesses/3.md") == ("project", {"id": "3"})  # 저장된 옛 경로
    assert ontology.parse_rel("solutions/2/card.md") == ("solution", {"id": "2"})
    assert ontology.parse_rel("activities/2026/5.md") == ("activity", {"year": "2026", "id": "5"})
    assert ontology.parse_rel("solutions/2/files/9-a b.md")[0] == "solution_file"
    assert ontology.parse_rel("research/2026-11-02-area-4.md")[0] == "research_report"
    assert ontology.parse_rel("nope/1.md") is None
    for code, t in ontology.TYPES.items():
        if t.mirror_path:
            assert ontology.parse_rel(t.mirror_path.format(id=1, year=2026, solution_id=1, name="n", day="2026-01-01", tag="area"))[0] == code


def test_mirror_dirs():
    assert ontology.mirror_dir("solution", id=4) == "solutions/4/"
    assert ontology.mirror_dir("solution_file", solution_id=4) == "solutions/4/files/"
    assert ontology.mirror_folder("project") == "projects"


def test_code_lists_have_labels_and_defaults():
    for key, cl in ontology.CODE_LISTS.items():
        assert cl.items and all(isinstance(k, str) and isinstance(v, str) and v for k, v in cl.items.items()), key
        if cl.default is not None:
            assert cl.default in cl.items, key


def test_model_column_defaults_are_defined_codes():
    for (table, column), key in ontology.COLUMN_CODES.items():
        col = Base.metadata.tables[table].c[column]
        assert col is not None
        default = col.default.arg if col.default is not None else None
        if default not in (None, ""):
            assert default in ontology.CODE_LISTS[key].items, (table, column, default)


def test_namespaces_distinguish_colliding_codes():
    assert "intake" in ontology.STAGES and "intake" in ontology.TYPES
    assert "partner" in ontology.ACTIVITY_KINDS and "partner" in ontology.TYPES
    assert ontology.STAGES["tapping"] != ontology.ACTIVITY_KINDS["tapping"]
    assert ontology.CODE_LISTS["stage"].label != ontology.CODE_LISTS["activity_kind"].label


def test_proposal_steps_come_from_status_codes():
    assert set(ontology.PROPOSAL_STATUS_STEP) == set(ontology.PROPOSAL_STATUSES)
    assert set(ontology.PROPOSAL_STATUS_STEP.values()) <= set(ontology.PROPOSAL_STEPS)
    assert set(ontology.JOB_KIND_STEP.values()) <= set(ontology.PROPOSAL_STEPS)
    assert set(ontology.JOB_KIND_STEP) <= set(ontology.JOB_KINDS)


def test_relations_use_defined_types_and_real_storage():
    keys = [r.key for r in ontology.RELATIONS]
    assert keys == [f"R{i}" for i in range(1, 36)]
    tables = Base.metadata.tables
    for r in ontology.RELATIONS:
        assert r.source and r.target and r.label
        for code in (*r.source, *r.target):
            assert code in ontology.TYPES, (r.key, code)
        assert r.cardinality in ("1:1", "1:N", "N:1", "N:M")
        assert r.storage in ontology.STORAGE_KINDS
        if r.storage in ("fk", "link"):
            for ref in r.physical.split():
                table, _, column = ref.partition(".")
                assert table in tables, r.key
                assert not column or column in tables[table].c, (r.key, ref)
        if r.storage == "json":
            table, _, column = r.physical.split()[0].partition(".")
            assert column in tables[table].c, r.key


def test_lookup_helpers():
    assert ontology.label("project") == "프로젝트"
    assert ontology.label("proposal") == "제안 건"
    assert ontology.prop("solution", "summary") == "한 줄 소개"
    assert ontology.prop("activity", "date") == "일자"
    assert ontology.term("source") == "출처"
    assert ontology.model_for("project") is models.Project
    assert ontology.type_of(models.Project(name="x")) == "project"


def _source_files():
    for path in sorted(APP_DIR.rglob("*")):
        if path.suffix in (".py", ".html", ".js") and "__pycache__" not in path.parts and path.name != "ontology.py":
            yield path


# 정의가 ontology.py 한 곳에만 있어야 하는 글자 (코드 값 이름표와 옛 용어). labels.py 는 다시 내보낼 뿐이다.
SINGLE_DEFINITION = [
    "아이템 접수", "사전 태핑", "수요조사\"", "박스 등록\"", "파트너 미팅", "고객 태핑", "\"솔루션에 자료 추가\"", "연락처 추가\"",
    "\"받아쓰기 대기\"", "보관만 함", "일반 사용자", "제안서 완료", "방향 검토", "2단 비교", "새로 찾을 솔루션", "기존 솔루션 밀기",
    "대상 지정", "\"저장 완료\"", "정리하지 못한 파일\"",
]


@pytest.mark.parametrize("text", SINGLE_DEFINITION)
def test_label_literals_live_only_in_ontology(text):
    found = [str(p.relative_to(APP_DIR)) for p in _source_files() if text in p.read_text(encoding="utf-8")]
    assert found == [], f"{text!r} 가 ontology.py 밖에 다시 적혀 있다: {found}"


def test_no_second_definition_of_code_lists():
    banned = {
        "proposals.py": [r"^SLIDE_KINDS\s*=", r"^STEPS\s*=", r"^_STATUS_STEP\s*=", r"^_JOB_STEP\s*="],
        "intake.py": [r"^SAVEABLE_KINDS\s*=", r"^CONTACT_MODELS\s*="],
        "routers/admin.py": [r"^ROLES\s*="],
        "records.py": [r"^LINK_TYPES\s*=", r"^NEW_TYPES\s*="],
        "knowledge.py": [r"^REL_FORMATS\s*="],
        "sources.py": [r"^_SIMPLE\s*="],
        "ai/tasks.py": [r"^_MENTION_MODELS\s*="],
    }
    for name, patterns in banned.items():
        text = (APP_DIR / name).read_text(encoding="utf-8")
        for pat in patterns:
            m = re.search(pat, text, re.M)
            assert m is None or "ontology" in text[m.start():m.start() + 160], f"{name}: {pat} 가 ontology 에서 파생되지 않았다"
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        assert "stage == \"boxed\"" not in text and "stage != \"boxed\"" not in text, path
        assert '"businesses/' not in text and "businesses/{id}" not in text, path


def test_no_old_business_names_left_in_code():
    for path in _source_files():
        if path.suffix == ".py":
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"\bBusiness\b", text), path
    names = {p.name for p in (APP_DIR / "routers").iterdir()}
    assert "businesses.py" not in names
    assert not (APP_DIR / "templates" / "businesses").exists()

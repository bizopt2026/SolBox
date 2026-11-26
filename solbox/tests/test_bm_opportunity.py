"""단위 4: BM 과 영업기회 — 정의, 행위 조건, 제안 후보 순서, 지식 사본, 화면, 옛 구조 DB 시작."""
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import ontology
from app.db import Base, make_engine
from app.knowledge import KNOWLEDGE_FORMAT, KnowledgeMirror
from app.main import create_app
from app.models import BM, Activity, Customer, CustomerNeed, Opportunity, Project, Proposal, Solution
from app.sources import describe_source
from tests.conftest import _PageForm, make_user, need_rows, nloc

DOCS = Path(__file__).resolve().parent.parent / "docs" / "ontology.md"


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ---- 세계 만들기 ----

def _customer(db, name="한빛병원"):
    c = Customer(name=name)
    db.add(c)
    db.commit()
    return c


def _need(db, customer, content="전기요금 절감", title="한빛 미팅", linked=True, name=None):
    """고객 니즈가 한 줄 든 활동 기록. linked 이면 고객 기록과 연결된 줄."""
    a = Activity(kind="meeting", date=date(2026, 11, 10), title=title, raw_text="r", needs=need_rows((name or customer.name, content)))
    db.add(a)
    db.flush()
    if linked:
        a.needs[0].customer_id = customer.id
    db.commit()
    return a, a.needs[0]


def _sol(db, name, stage="boxed"):
    s = Solution(name=name, stage=stage)
    db.add(s)
    db.commit()
    return s


def _has_need(page, need):
    return f'name="need_ids" value="{need.id}"' in page


def _opp_via_post(client, db, customer, name="전력 절감 사업", **data):
    r = client.post("/opportunities", data={"name": name, "customer_id": str(customer.id), **data}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return db.scalars(select(Opportunity).order_by(Opportunity.id.desc())).first()


def _bm(db, name="에너지 절감 BM", solutions=()):
    b = BM(name=name)
    b.solutions = list(solutions)
    db.add(b)
    db.commit()
    return b


# ---- 정의 ----

def test_types_defined_with_labels_urls_and_mirror_paths():
    bm, op = ontology.TYPES["bm"], ontology.TYPES["opportunity"]
    assert (bm.label, bm.table, bm.model_name, bm.list_url, bm.detail_url, bm.mirror_path, bm.nav) == (
        "BM", "bms", "BM", "/bms", "/bms/{id}", "bms/{id}.md", "BM")
    assert (op.label, op.table, op.model_name, op.list_url, op.detail_url, op.mirror_path, op.nav) == (
        "영업기회", "opportunities", "Opportunity", "/opportunities", "/opportunities/{id}", "opportunities/{id}.md", "영업기회")
    assert ontology.prop("bm", "revenue_model") == "수익 구조" and ontology.prop("bm", "target_customers") == "대상 고객 유형"
    assert ontology.prop("bm", "summary") == "한 줄 설명"
    assert ontology.prop("opportunity", "stage") == "단계" and ontology.prop("opportunity", "solutions") == "검토 솔루션"
    assert ontology.parse_rel("bms/3.md") == ("bm", {"id": "3"})
    assert ontology.parse_rel("opportunities/12.md") == ("opportunity", {"id": "12"})
    assert ontology.TYPES["bm"].kind_label == "BM" and ontology.TYPES["opportunity"].kind_label == "영업기회"
    assert ontology.mirror_folder("bm") == "bms" and ontology.mirror_folder("opportunity") == "opportunities"


def test_opportunity_stage_code_list():
    cl = ontology.CODE_LISTS["opportunity_stage"]
    assert cl.items == {"discovery": "발굴", "proposing": "제안 중", "negotiating": "협상", "won": "수주", "lost": "실패", "hold": "보류"}
    assert cl.default == "discovery"
    assert set(ontology.OPPORTUNITY_STAGE_CLASS) == set(cl.items)
    assert len(set(ontology.OPPORTUNITY_STAGE_CLASS.values())) == len(cl.items)  # 단계마다 다른 색
    assert ontology.OPPORTUNITY_FLOW == ("discovery", "proposing", "negotiating", "won")
    assert ontology.OPPORTUNITY_SIDE_STAGES == ("lost", "hold")
    assert ontology.OPPORTUNITY_OPEN_STAGES == ("discovery", "proposing", "negotiating")
    assert ontology.COLUMN_CODES[("opportunities", "stage")] == "opportunity_stage"


def test_new_relations_r26_to_r33():
    rels = {r.key: r for r in ontology.RELATIONS}
    assert list(rels)[:36] == [f"R{i}" for i in range(1, 37)]
    assert rels["R25"].label == "같은 회사다"  # 단위 3 의 키는 그대로
    expect = {
        "R26": ("customer_need", "opportunity", "N:1", "fk", "customer_needs.opportunity_id"),
        "R27": ("opportunity", "customer", "N:1", "fk", "opportunities.customer_id"),
        "R28": ("opportunity", "solution", "N:M", "link", "opportunity_solutions"),
        "R29": ("proposal", "opportunity", "N:1", "fk", "proposals.opportunity_id"),
        "R30": ("opportunity", "project", "1:1", "fk", "opportunities.project_id"),
        "R31": ("project", "bm", "N:1", "fk", "businesses.bm_id"),
        "R32": ("opportunity", "bm", "N:1", "fk", "opportunities.bm_id"),
        "R33": ("bm", "solution", "N:M", "link", "bm_solutions"),
    }
    for key, (src, dst, card, storage, phys) in expect.items():
        r = rels[key]
        assert (r.source, r.target, r.cardinality, r.storage, r.physical) == ((src,), (dst,), card, storage, phys), key
    assert rels["R28"].label == "검토한다" and rels["R33"].label == "구성된다" and rels["R31"].label == "따른다"
    assert "activity_opportunities" in rels["R6"].physical and "opportunity" in rels["R6"].target  # R6'


def test_document_uses_same_relation_keys():
    text = DOCS.read_text(encoding="utf-8")
    sec = text[text.index("## 13."):]
    for key in (f"R{i}" for i in range(26, 34)):
        assert f"| {key} |" in sec, key
    assert "| R25 | 고객 니즈" not in sec  # 단위 3 이 R25 를 "같은 회사다"로 썼다
    assert "R33" in sec and "결정" in sec[:900]


def test_models_tables_and_columns(app):
    tables = Base.metadata.tables
    for name in ("bms", "opportunities", "bm_solutions", "opportunity_solutions", "activity_opportunities"):
        assert name in tables, name
    assert {"name", "summary", "target_customers", "revenue_model", "memo", "created_by_id", "created_at", "updated_at"} <= set(tables["bms"].c.keys())
    assert {"origin_type", "origin_id"}.isdisjoint(tables["bms"].c.keys()) and {"origin_type", "origin_id"}.isdisjoint(tables["opportunities"].c.keys())
    assert {"name", "customer_id", "stage", "bm_id", "project_id", "memo", "created_by_id"} <= set(tables["opportunities"].c.keys())
    assert not tables["opportunities"].c.customer_id.nullable and tables["opportunities"].c.stage.default.arg == "discovery"
    assert "bm_id" in tables["businesses"].c and "opportunity_id" in tables["proposals"].c
    assert not tables["customer_needs"].c.opportunity_id.foreign_keys  # 단위 2 의 열 그대로: 제약 없는 번호
    assert "expected_amount" not in tables["opportunities"].c
    assert Project.bm_id.property.columns[0].name == "bm_id"
    assert ontology.type_of(BM(name="x")) == "bm" and ontology.type_of(Opportunity(name="x")) == "opportunity"


def test_nav_has_opportunity_and_bm_in_order(user_client):
    html = user_client.get("/").text
    nav = re.search(r'<aside class="side".*?</aside>', html, re.S).group(0)
    names = re.findall(r'<a href="/([a-z]*)"[^>]*><svg class="ni".*?</svg>([^<]+)', nav, re.S)
    order = [h for h, _ in names]
    # 영업 묶음: 고객 → 영업기회 → 제안 → 프로젝트 → BM
    assert order.index("customers") + 1 == order.index("opportunities") and order.index("opportunities") + 1 == order.index("proposals")
    assert order.index("proposals") + 1 == order.index("projects") and order.index("projects") + 1 == order.index("bms")
    assert ("opportunities", "영업기회") in [(h, n.strip()) for h, n in names] and ("bms", "BM") in [(h, n.strip()) for h, n in names]


# ---- 영업기회 만들기·고치기 ----

def test_create_requires_name_and_existing_customer(user_client, db):
    c = _customer(db)
    r = user_client.post("/opportunities", data={"name": " ", "customer_id": str(c.id)})
    assert r.status_code == 400 and "이름을 입력하세요." in r.text
    r = user_client.post("/opportunities", data={"name": "사업", "customer_id": ""})
    assert r.status_code == 400 and "고객을 선택하세요." in r.text
    r = user_client.post("/opportunities", data={"name": "사업", "customer_id": "999"})
    assert r.status_code == 400
    assert db.query(Opportunity).count() == 0


def test_create_defaults_stage_and_writes_mirror(user_client, db, settings):
    c = _customer(db)
    o = _opp_via_post(user_client, db, c, memo="메모 글")
    assert o.stage == "discovery" and o.created_by_id is not None and o.project_id is None and o.bm_id is None
    md = (settings.knowledge_dir / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")
    assert "전력 절감 사업" in md and f"한빛병원 (customers/{c.id}.md)" in md
    assert f"opportunities/{o.id}.md" in (settings.knowledge_dir / "customers" / f"{c.id}.md").read_text(encoding="utf-8")


def test_bundle_only_same_customer_unbundled_needs(user_client, db):
    c1, c2 = _customer(db), _customer(db, "남도물산")
    _, n1 = _need(db, c1, "한빛 니즈")
    _, n2 = _need(db, c2, "남도 니즈")
    _, n3 = _need(db, c1, "한빛 니즈 둘")
    page = user_client.get(f"/opportunities/new?customer_id={c1.id}").text
    assert _has_need(page, n1) and _has_need(page, n3) and not _has_need(page, n2)
    r = user_client.post("/opportunities", data={"name": "묶음", "customer_id": str(c1.id), "need_ids": [str(n1.id), str(n2.id)]})
    assert r.status_code == 400 and "묶을 수 없는" in r.text
    assert db.query(Opportunity).count() == 0
    db.refresh(n1)
    assert n1.opportunity_id is None
    o = _opp_via_post(user_client, db, c1, need_ids=[str(n1.id)])
    db.refresh(n1)
    assert n1.opportunity_id == o.id
    # 이미 다른 영업기회에 묶인 니즈는 또 묶을 수 없다
    r = user_client.post("/opportunities", data={"name": "또", "customer_id": str(c1.id), "need_ids": [str(n1.id)]})
    assert r.status_code == 400 and db.query(Opportunity).count() == 1
    page = user_client.get(f"/opportunities/new?customer_id={c1.id}").text
    assert not _has_need(page, n1) and _has_need(page, n3)


def test_new_form_prefills_customer_and_checks_need(user_client, db):
    c = _customer(db)
    _, n = _need(db, c)
    page = user_client.get(f"/opportunities/new?customer_id={c.id}&need_id={n.id}").text
    assert re.search(rf'<option value="{c.id}"\s+selected', page)
    assert re.search(rf'name="need_ids" value="{n.id}" checked', page)
    other = _customer(db, "남도물산")
    page = user_client.get(f"/opportunities/new?customer_id={other.id}&need_id={n.id}").text  # 다른 고객의 니즈는 체크하지 않는다
    assert not _has_need(page, n)


def test_edit_detach_need_and_customer_change_rules(user_client, db):
    c1, c2 = _customer(db), _customer(db, "남도물산")
    _, n1 = _need(db, c1)
    o = _opp_via_post(user_client, db, c1, need_ids=[str(n1.id)])
    page = user_client.get(f"/opportunities/{o.id}/edit").text
    assert re.search(rf'name="need_ids" value="{n1.id}" checked', page)
    r = user_client.post(f"/opportunities/{o.id}", data={"name": "고침", "customer_id": str(c2.id), "need_ids": [str(n1.id)]})
    assert r.status_code == 400 and "묶은" in r.text  # 묶은 니즈가 있으면 고객을 바꿀 수 없다
    db.refresh(o)
    assert o.customer_id == c1.id and o.name == "전력 절감 사업"
    r = user_client.post(f"/opportunities/{o.id}", data={"name": "고침", "customer_id": str(c1.id)}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(o), db.refresh(n1)
    assert o.name == "고침" and n1.opportunity_id is None  # 체크를 풀면 해제
    r = user_client.post(f"/opportunities/{o.id}", data={"name": "고침", "customer_id": str(c2.id)}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(o)
    assert o.customer_id == c2.id  # 묶은 니즈·프로젝트·제안이 없으면 고객을 바꿀 수 있다


def test_unlinked_needs_note_shown_for_same_named_customer(user_client, db):
    c = _customer(db)
    _need(db, c, linked=False)  # 이름 글자만 있는 니즈
    page = user_client.get(f"/opportunities/new?customer_id={c.id}").text
    assert "고객에 연결되지 않은 니즈는 활동 기록에서 고객 이름을 등록된 이름으로 고치면 여기에 나옵니다" in _text(page)
    other = _customer(db, "다른회사")
    assert "고객에 연결되지 않은" not in _text(user_client.get(f"/opportunities/new?customer_id={other.id}").text)


def test_solutions_picker_saves_review_solutions(user_client, db):
    c = _customer(db)
    s1, s2 = _sol(db, "에너지 관리"), _sol(db, "스마트 조명")
    o = _opp_via_post(user_client, db, c, solution_ids=[str(s1.id), str(s2.id)])
    assert {s.id for s in o.solutions} == {s1.id, s2.id}
    user_client.post(f"/opportunities/{o.id}", data={"name": "n", "customer_id": str(c.id), "solution_ids": [str(s1.id)]})
    db.refresh(o)
    assert [s.id for s in o.solutions] == [s1.id]


# ---- 활동 기록 화면의 니즈 행위 ----

def test_activity_detail_need_actions(user_client, db):
    c = _customer(db)
    a, n = _need(db, c)
    page = user_client.get(f"/activities/{a.id}").text
    assert f'href="/opportunities/new?customer_id={c.id}&amp;need_id={n.id}"' in page and "영업기회 만들기" in page
    o = _opp_via_post(user_client, db, c, need_ids=[str(n.id)])
    page = user_client.get(f"/activities/{a.id}").text
    assert f'href="/opportunities/{o.id}"' in page and "영업기회 만들기" not in page
    a2, n2 = _need(db, c, linked=False, title="미연결")
    assert "영업기회 만들기" not in user_client.get(f"/activities/{a2.id}").text  # 고객에 연결된 니즈에만


def test_activity_form_has_opportunity_picker_and_links(user_client, db):
    c = _customer(db)
    o = _opp_via_post(user_client, db, c)
    page = user_client.get("/activities/new").text
    assert "/activities/draft" in page  # 새 활동 시작 화면은 그대로
    form = user_client.get(f"/activities/{_need(db, c)[0].id}/edit").text
    assert 'name="opportunity_ids"' in form and "전력 절감 사업" in form
    assert 'name="new_opportunities"' not in form and 'name="new_bms"' not in form  # 이름만으로 만들지 않는다
    a = db.query(Activity).first()
    r = user_client.post(f"/activities/{a.id}", data={"raw_text": "r", "opportunity_ids": [str(o.id)]}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(a)
    assert [x.id for x in a.opportunities] == [o.id]
    assert f"/opportunities/{o.id}" in user_client.get(f"/activities/{a.id}").text
    user_client.post(f"/activities/{a.id}", data={"raw_text": "r"})  # 영업기회 칸이 아예 안 온 저장 (옛 화면): 연결은 그대로
    db.refresh(a)
    assert [x.id for x in a.opportunities] == [o.id]
    user_client.post(f"/activities/{a.id}", data={"raw_text": "r", "opportunity_ids": ""})  # 새 화면에서 모두 풀면 빈 칸이 온다
    db.refresh(a)
    assert a.opportunities == []


def test_ai_draft_and_intake_do_not_offer_opportunities_or_bms():
    from app.ai.tasks import ActivityDraft
    assert "opportunit" not in str(ActivityDraft.model_json_schema()).lower() and "bm" not in str(ActivityDraft.model_json_schema()).lower()
    assert set(ontology.MENTION_KEYS) == {"solutions", "customers", "projects", "partners"}
    assert "opportunity" not in ontology.ACTIVITY_LINKS and "opportunity" not in ontology.ACTIVITY_NEW_TYPES


# ---- 단계 ----

def test_stage_any_to_any_and_bad_value(user_client, db):
    c = _customer(db)
    o = _opp_via_post(user_client, db, c)
    for stage in ("won", "lost", "discovery", "hold", "negotiating", "proposing"):
        url, data = (f"/opportunities/{o.id}/close", {"stage": stage, "reason": "other"}) if stage in ("won", "lost") else (f"/opportunities/{o.id}/stage", {"stage": stage})  # 수주·실패는 사유와 함께 (영업 A)
        r = user_client.post(url, data=data, follow_redirects=False)
        assert r.status_code == 303
        db.refresh(o)
        assert o.stage == stage
    assert user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "nope"}).status_code == 400
    page = user_client.get(f"/opportunities/{o.id}").text
    assert 'class="stagebar"' in page and 'name="stage" value="won"' in page and 'name="stage" value="lost"' in page
    assert 'class="badge tone-' in user_client.get("/opportunities").text


# ---- 프로젝트로 전환 ----

def test_convert_only_when_won_and_once(user_client, db, settings):
    c = _customer(db)
    bm = _bm(db)
    o = _opp_via_post(user_client, db, c, bm_id=str(bm.id))
    assert user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "create", "name": "프로젝트"}).status_code == 400
    assert db.query(Project).count() == 0
    assert "프로젝트로 전환" not in user_client.get(f"/opportunities/{o.id}").text
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": "other"})
    page = user_client.get(f"/opportunities/{o.id}").text
    assert f'href="/opportunities/{o.id}/convert"' in page and "프로젝트로 전환" in page
    form = user_client.get(f"/opportunities/{o.id}/convert").text
    assert 'value="전력 절감 사업"' in form and 'name="mode"' in form  # 이름을 채워 둔다 (고칠 수 있다)
    r = user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "create", "name": "한빛 전력 절감"}, follow_redirects=False)
    assert r.status_code == 303
    p = db.query(Project).one()
    db.refresh(o)
    assert (p.name, p.customer_id, p.bm_id, o.project_id) == ("한빛 전력 절감", c.id, bm.id, p.id)
    assert nloc(r) == f"/projects/{p.id}?notice=converted" and p.created_by_id is not None
    # 두 번째 시도는 새로 만들지 않고 있던 프로젝트를 보여 준다
    r = user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "create", "name": "또 하나"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/projects/{p.id}" and db.query(Project).count() == 1
    r = user_client.get(f"/opportunities/{o.id}/convert", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/projects/{p.id}"
    # 단계를 바꿔도 프로젝트와 연결은 그대로
    user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "hold"})
    db.refresh(o)
    assert o.project_id == p.id and db.query(Project).count() == 1
    assert "프로젝트로 전환" not in user_client.get(f"/opportunities/{o.id}").text
    assert f'href="/projects/{p.id}"' in user_client.get(f"/opportunities/{o.id}").text
    # 프로젝트 쪽에서도 어느 영업기회에서 왔는지 보인다
    proj = user_client.get(f"/projects/{p.id}").text
    assert f'href="/opportunities/{o.id}"' in proj and "에너지 절감 BM" in proj and f'href="/bms/{bm.id}"' in proj


def test_convert_requires_name(user_client, db):
    c = _customer(db)
    o = _opp_via_post(user_client, db, c)
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": "other"})
    r = user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "create", "name": " "})
    assert r.status_code == 400 and "이름을 입력하세요." in r.text and db.query(Project).count() == 0


def test_link_existing_project_same_customer_only(user_client, db):
    c1, c2 = _customer(db), _customer(db, "남도물산")
    mine, theirs = Project(name="한빛 기존", customer_id=c1.id), Project(name="남도 기존", customer_id=c2.id)
    db.add_all([mine, theirs])
    db.commit()
    o = _opp_via_post(user_client, db, c1)
    o2 = _opp_via_post(user_client, db, c1, name="둘째")
    for x in (o, o2):
        user_client.post(f"/opportunities/{x.id}/close", data={"stage": "won", "reason": "other"})
    form = user_client.get(f"/opportunities/{o.id}/convert").text
    assert f'<option value="{mine.id}"' in form and f'<option value="{theirs.id}"' not in form
    r = user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "link", "project_id": str(theirs.id)})
    assert r.status_code == 400
    db.refresh(o)
    assert o.project_id is None
    r = user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "link", "project_id": str(mine.id)}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(o)
    assert o.project_id == mine.id and db.query(Project).count() == 2
    # 이미 다른 영업기회와 이어진 프로젝트는 또 이을 수 없다 (1:1)
    r = user_client.post(f"/opportunities/{o2.id}/convert", data={"mode": "link", "project_id": str(mine.id)})
    assert r.status_code == 400
    assert f'<option value="{mine.id}"' not in user_client.get(f"/opportunities/{o2.id}/convert").text


# ---- 지우기 ----

def test_customer_delete_blocked_by_opportunity(user_client, db):
    c = Customer(name="막힌 고객", created_by_id=make_user(db, "owner").id)
    db.add(c)
    db.commit()
    o = Opportunity(name="진행 건", customer_id=c.id)
    db.add(o)
    db.commit()
    admin = _admin(user_client.app, db)
    r = admin.post(f"/customers/{c.id}/delete")
    assert r.status_code == 400 and "영업기회" in r.text and "삭제할 수 없습니다" in r.text
    assert db.get(Customer, c.id) is not None
    db.delete(o)
    db.commit()
    assert admin.post(f"/customers/{c.id}/delete", follow_redirects=False).status_code == 303


def _admin(app, db, name="boss"):
    from tests.conftest import _logged_in_client
    return _logged_in_client(app, db, name, "admin")


def test_opportunity_delete_rules(user_client, db, settings):
    c = _customer(db)
    s = _sol(db, "에너지 관리")
    a, n = _need(db, c)
    o = _opp_via_post(user_client, db, c, need_ids=[str(n.id)], solution_ids=[str(s.id)])
    a.opportunities = [o]
    p = Project(name="프로젝트", customer_id=c.id)
    db.add(p)
    db.flush()
    o.project_id = p.id
    prop = Proposal(customer_id=c.id, opportunity_id=o.id)
    db.add(prop)
    db.commit()
    stranger = _logged_in(user_client.app, db, "stranger2")
    assert stranger.post(f"/opportunities/{o.id}/delete").status_code == 403
    oid = o.id
    r = user_client.post(f"/opportunities/{oid}/delete", follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == "/opportunities?notice=deleted"
    db.expire_all()
    assert db.get(Opportunity, oid) is None
    assert db.get(CustomerNeed, n.id).opportunity_id is None  # 니즈는 남고 묶음만 풀린다
    assert db.get(Proposal, prop.id).opportunity_id is None and db.get(Proposal, prop.id) is not None
    assert db.get(Project, p.id) is not None  # 프로젝트는 지우지 않는다
    con = sqlite3.connect(settings.db_path)
    assert con.execute("SELECT count(*) FROM opportunity_solutions").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM activity_opportunities").fetchone()[0] == 0
    con.close()
    assert not (settings.knowledge_dir / "opportunities" / f"{oid}.md").exists()
    assert "opportunities/" not in (settings.knowledge_dir / "customers" / f"{c.id}.md").read_text(encoding="utf-8")


def test_admin_can_delete_others_opportunity(user_client, db):
    c = _customer(db)
    o = _opp_via_post(user_client, db, c)
    assert _admin(user_client.app, db).post(f"/opportunities/{o.id}/delete", follow_redirects=False).status_code == 303


def test_project_delete_clears_opportunity_link(user_client, db, settings):
    c = _customer(db)
    o = _opp_via_post(user_client, db, c)
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": "other"})
    user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "create", "name": "프로젝트"})
    p = db.query(Project).one()
    pid = p.id
    admin = _admin(user_client.app, db)
    assert admin.post(f"/projects/{pid}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    o = db.get(Opportunity, o.id)
    assert o is not None and o.project_id is None
    assert f'href="/projects/{pid}"' not in user_client.get(f"/opportunities/{o.id}").text
    assert f"projects/{pid}.md" not in (settings.knowledge_dir / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")


def test_solution_delete_removes_link_rows(user_client, db, settings):
    c = _customer(db)
    s = _sol(db, "에너지 관리")
    o = _opp_via_post(user_client, db, c, solution_ids=[str(s.id)])
    bm = _bm(db, solutions=[s])
    admin = _admin(user_client.app, db)
    assert admin.post(f"/solutions/{s.id}/delete", follow_redirects=False).status_code == 303
    con = sqlite3.connect(settings.db_path)
    assert con.execute("SELECT count(*) FROM opportunity_solutions").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM bm_solutions").fetchone()[0] == 0
    con.close()
    assert f"solutions/{s.id}" not in (settings.knowledge_dir / "bms" / f"{bm.id}.md").read_text(encoding="utf-8")
    assert f"solutions/{s.id}" not in (settings.knowledge_dir / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")


# ---- BM ----

def test_bm_create_edit_with_solutions_picker(user_client, db, settings):
    s1, s2 = _sol(db, "에너지 관리"), _sol(db, "스마트 조명")
    page = user_client.get("/bms/new").text
    assert 'name="solution_ids"' in page and 'name="revenue_model"' in page and 'name="target_customers"' in page
    r = user_client.post("/bms", data={"name": "에너지 BM", "summary": "절감", "target_customers": "병원", "revenue_model": "구독",
                                       "memo": "메모", "solution_ids": [str(s1.id)]}, follow_redirects=False)
    assert r.status_code == 303
    bm = db.query(BM).one()
    assert (bm.summary, bm.target_customers, bm.revenue_model, bm.memo) == ("절감", "병원", "구독", "메모")
    assert [s.id for s in bm.solutions] == [s1.id] and bm.created_by_id is not None
    md = (settings.knowledge_dir / "bms" / f"{bm.id}.md").read_text(encoding="utf-8")
    assert f"에너지 관리 (solutions/{s1.id}/card.md)" in md and "구독" in md and "병원" in md
    assert f"bms/{bm.id}.md" in (settings.knowledge_dir / "solutions" / str(s1.id) / "card.md").read_text(encoding="utf-8")
    user_client.post(f"/bms/{bm.id}", data={"name": "에너지 BM 2", "solution_ids": [str(s2.id)]})
    db.refresh(bm)
    assert bm.name == "에너지 BM 2" and [s.id for s in bm.solutions] == [s2.id]
    assert "bms/" not in (settings.knowledge_dir / "solutions" / str(s1.id) / "card.md").read_text(encoding="utf-8")
    assert user_client.post("/bms", data={"name": " "}).status_code == 400
    assert "에너지 BM 2" in user_client.get("/bms").text and "에너지 BM 2" in user_client.get(f"/bms/{bm.id}").text


def test_bm_delete_blocked_while_followed(user_client, db, settings):
    c = _customer(db)
    s = _sol(db, "에너지 관리")
    bm = _bm(db, solutions=[s])
    bm.created_by_id = make_user(db, "creator").id
    db.commit()
    admin = _admin(user_client.app, db)
    p = Project(name="프로젝트", customer_id=c.id, bm_id=bm.id)
    db.add(p)
    db.commit()
    r = admin.post(f"/bms/{bm.id}/delete")
    assert r.status_code == 400 and "삭제할 수 없습니다" in r.text and "프로젝트" in r.text
    assert db.get(BM, bm.id) is not None
    p.bm_id = None
    o = Opportunity(name="기회", customer_id=c.id, bm_id=bm.id)
    db.add(o)
    db.commit()
    r = admin.post(f"/bms/{bm.id}/delete")
    assert r.status_code == 400 and "영업기회" in r.text
    o.bm_id = None
    db.commit()
    stranger = _logged_in(user_client.app, db)
    assert stranger.post(f"/bms/{bm.id}/delete").status_code == 403
    bid = bm.id
    assert admin.post(f"/bms/{bid}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(BM, bid) is None
    assert db.get(Project, p.id) is not None and db.get(Opportunity, o.id) is not None
    con = sqlite3.connect(settings.db_path)
    assert con.execute("SELECT count(*) FROM bm_solutions").fetchone()[0] == 0
    con.close()
    assert not (settings.knowledge_dir / "bms" / f"{bid}.md").exists()


def _logged_in(app, db, name="outsider"):
    from tests.conftest import _logged_in_client
    return _logged_in_client(app, db, name, "user")


def test_project_form_bm_select_and_detail_line(user_client, db, settings):
    c = _customer(db)
    bm = _bm(db)
    form = user_client.get("/projects/new").text
    assert 'name="bm_id"' in form and "에너지 절감 BM" in form
    r = user_client.post("/projects", data={"name": "프로젝트", "customer_id": str(c.id), "bm_id": str(bm.id)}, follow_redirects=False)
    assert r.status_code == 303
    p = db.query(Project).one()
    assert p.bm_id == bm.id
    assert f'href="/bms/{bm.id}"' in user_client.get(f"/projects/{p.id}").text
    assert f"에너지 절감 BM (bms/{bm.id}.md)" in (settings.knowledge_dir / "projects" / f"{p.id}.md").read_text(encoding="utf-8")
    assert f"projects/{p.id}.md" in (settings.knowledge_dir / "bms" / f"{bm.id}.md").read_text(encoding="utf-8")
    assert user_client.post("/projects", data={"name": "x", "customer_id": str(c.id), "bm_id": "999"}).status_code == 400
    user_client.post(f"/projects/{p.id}", data={"name": "프로젝트", "customer_id": str(c.id), "bm_id": ""})
    db.refresh(p)
    assert p.bm_id is None
    assert f"projects/{p.id}.md" not in (settings.knowledge_dir / "bms" / f"{bm.id}.md").read_text(encoding="utf-8")


def test_customer_detail_opportunity_section(user_client, db):
    c = _customer(db)
    o = _opp_via_post(user_client, db, c)
    page = user_client.get(f"/customers/{c.id}").text
    assert f'href="/opportunities/{o.id}"' in page and f'href="/opportunities/new?customer_id={c.id}"' in page
    assert "영업기회 만들기" in _text(page)


def test_opportunity_pages(user_client, db):
    c = _customer(db)
    a, n = _need(db, c, "전기요금 절감 요청", title="한빛 첫 미팅")
    s = _sol(db, "에너지 관리")
    bm = _bm(db)
    o = _opp_via_post(user_client, db, c, need_ids=[str(n.id)], solution_ids=[str(s.id)], bm_id=str(bm.id))
    prop = Proposal(customer_id=c.id, opportunity_id=o.id, title="한빛 제안")
    db.add(prop)
    db.commit()
    page = user_client.get(f"/opportunities/{o.id}").text
    for needle in ("전기요금 절감 요청", f'href="/activities/{a.id}"', f'href="/solutions/{s.id}"', f'href="/bms/{bm.id}"',
                   f'href="/customers/{c.id}"', f'href="/proposals/{prop.id}"', "한빛 제안"):
        assert needle in page, needle
    lst = user_client.get("/opportunities").text
    assert "전력 절감 사업" in lst and "한빛병원" in lst and "에너지 절감 BM" not in lst  # BM 은 상세에서만 (영업 A: 목록에서 BM 칸을 뺌, 사용자 결정)
    assert "전력 절감 사업" in user_client.get("/opportunities?q=전력").text
    assert "전력 절감 사업" not in user_client.get("/opportunities?q=없음").text
    assert "전력 절감 사업" in user_client.get("/opportunities?stage=discovery").text
    assert "전력 절감 사업" not in user_client.get("/opportunities?stage=won").text
    assert "전력 절감 사업" in user_client.get("/opportunities?stage=open").text
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "lost", "reason": "other"})
    assert "전력 절감 사업" not in user_client.get("/opportunities?stage=open").text
    bmpage = user_client.get(f"/bms/{bm.id}").text
    assert f'href="/opportunities/{o.id}"' in bmpage
    assert user_client.get("/opportunities/9999").status_code == 404 and user_client.get("/bms/9999").status_code == 404


def test_home_open_opportunity_line(user_client, db):
    c = _customer(db)
    _opp_via_post(user_client, db, c)
    o2 = _opp_via_post(user_client, db, c, name="둘째")
    user_client.post(f"/opportunities/{o2.id}/close", data={"stage": "lost", "reason": "other"})
    home = _text(user_client.get("/").text)
    assert "진행 중인 영업기회 1건" in home
    assert 'href="/opportunities?stage=open"' in user_client.get("/").text


# ---- 제안 ----

def test_proposal_opportunity_must_belong_to_customer(user_client, db):
    c1, c2 = _customer(db), _customer(db, "남도물산")
    p1 = Project(name="한빛 프로젝트", customer_id=c1.id)
    db.add(p1)
    db.commit()
    mine, theirs = _opp_via_post(user_client, db, c1), _opp_via_post(user_client, db, c2, name="남도 건")
    page = user_client.get("/proposals/new").text
    assert 'name="opportunity_id"' in page and "한빛병원" in page
    r = user_client.post("/proposals", data={"customer_id": str(c1.id), "opportunity_id": str(theirs.id)})
    assert r.status_code == 400 and "영업기회" in r.text and db.query(Proposal).count() == 0
    r = user_client.post("/proposals", data={"customer_id": str(c1.id), "opportunity_id": "abc"})
    assert r.status_code == 400
    r = user_client.post("/proposals", data={"customer_id": str(c1.id), "opportunity_id": str(mine.id)}, follow_redirects=False)
    assert r.status_code == 303
    p = db.query(Proposal).one()
    assert p.opportunity_id == mine.id
    r = user_client.post("/proposals", data={"customer_id": str(c1.id), "opportunity_id": ""}, follow_redirects=False)
    assert db.query(Proposal).order_by(Proposal.id.desc()).first().opportunity_id is None
    # 대상 수정 화면: 그 고객의 영업기회만
    page = user_client.get(f"/proposals/{p.id}").text
    assert 'name="opportunity_id"' in page and f'href="/opportunities/{mine.id}"' in page
    assert user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "m", "opportunity_id": str(theirs.id)}).status_code == 400
    db.refresh(p)
    assert p.opportunity_id == mine.id
    user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "m", "opportunity_id": ""})
    db.refresh(p)
    assert p.opportunity_id is None
    user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "m2"})  # 칸이 없으면 그대로 둔다
    user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "m", "opportunity_id": str(mine.id)})
    user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "m3"})
    db.refresh(p)
    assert p.opportunity_id == mine.id


def test_proposal_form_prefills_project_from_opportunity(user_client, db):
    c = _customer(db)
    p = Project(name="한빛 프로젝트", customer_id=c.id)
    db.add(p)
    db.commit()
    o = _opp_via_post(user_client, db, c)
    o.project_id = p.id
    db.commit()
    page = user_client.get(f"/proposals/new?opportunity_id={o.id}").text
    assert re.search(rf'<option value="{c.id}"\s+selected', page)
    assert re.search(rf'<option value="{p.id}"\s+selected', page)  # 미리 채우지만 고칠 수 있다 (select)
    assert re.search(rf'<option value="{o.id}"\s+selected', page)
    r = user_client.post("/proposals", data={"customer_id": str(c.id), "opportunity_id": str(o.id), "project_id": ""}, follow_redirects=False)
    assert db.query(Proposal).one().project_id is None  # 사용자가 비우면 그대로 비운다


def _order_world(db):
    c = _customer(db)
    a, b, cc, d, e = (_sol(db, "가나", "boxed"), _sol(db, "다라", "boxed"), _sol(db, "마바", "intake"),
                      _sol(db, "사아", "hold"), _sol(db, "자차", "boxed"))
    bm = _bm(db, solutions=[cc, d, e])
    o = Opportunity(name="기회", customer_id=c.id, bm_id=bm.id)
    o.solutions = [b]
    db.add(o)
    db.commit()
    return c, (a, b, cc, d, e), bm, o


def test_candidate_order_opportunity_then_bm_then_rest_same_set(db):
    from app.proposals import candidate_solutions
    c, (a, b, cc, d, e), bm, o = _order_world(db)
    plain = Proposal(customer_id=c.id)
    withopp = Proposal(customer_id=c.id, opportunity_id=o.id)
    db.add_all([plain, withopp])
    db.commit()
    base = [s.name for s in candidate_solutions(db, plain)]
    assert base == ["가나", "다라", "마바", "자차"]  # 보류(사아) 제외, 이름순
    got = [s.name for s in candidate_solutions(db, withopp)]
    assert got == ["다라", "마바", "자차", "가나"]  # 검토 솔루션 → BM 솔루션(이름순) → 나머지. 집합은 같다
    assert set(got) == set(base)
    withopp.extra_solution_ids = [d.id]  # 직접 추가한 보류 솔루션은 후보이고 BM 솔루션이면 BM 묶음에 든다
    db.commit()
    assert [s.name for s in candidate_solutions(db, withopp)] == ["다라", "마바", "사아", "자차", "가나"]


def test_candidate_order_uses_project_bm_when_no_opportunity(db):
    from app.proposals import candidate_solutions
    c, (a, b, cc, d, e), bm, o = _order_world(db)
    pr = Project(name="프로젝트", customer_id=c.id, bm_id=bm.id)
    db.add(pr)
    db.commit()
    p = Proposal(customer_id=c.id, project_id=pr.id)
    db.add(p)
    db.commit()
    assert [s.name for s in candidate_solutions(db, p)] == ["마바", "자차", "가나", "다라"]
    p.opportunity_id = o.id  # 영업기회가 있으면 그 영업기회의 BM 이 기준이다
    o.bm_id = None
    db.commit()
    db.expire_all()
    assert [s.name for s in candidate_solutions(db, p)] == ["다라", "가나", "마바", "자차"]


def test_proposal_page_and_directions_prompt_show_priority(user_client, db, settings):
    from app.ai.proposal_tasks import directions_prompt
    c, (a, b, cc, d, e), bm, o = _order_world(db)
    p = Proposal(customer_id=c.id, opportunity_id=o.id)
    db.add(p)
    db.commit()
    page = user_client.get(f"/proposals/{p.id}").text
    names = re.findall(r'href="/solutions/\d+">([^<]+)</a>', page.split("<dt>후보 솔루션 ")[1].split("</dl>")[0])
    assert names == ["다라", "마바", "자차", "가나"]
    prompt = directions_prompt(db, p)
    assert "우선 검토할 솔루션" in prompt
    assert prompt.index("id %d: 다라" % b.id) < prompt.index("id %d: 마바" % cc.id) < prompt.index("id %d: 자차" % e.id) < prompt.index("id %d: 가나" % a.id)
    assert "영업기회" in prompt and f"opportunities/{o.id}.md" in prompt and f"bms/{bm.id}.md" in prompt
    plain = Proposal(customer_id=c.id)
    db.add(plain)
    db.commit()
    assert "우선 검토할 솔루션" not in directions_prompt(db, plain)


# ---- 지식 사본 ----

def test_mirror_files_and_source_resolution(user_client, db, settings):
    c = _customer(db)
    a, n = _need(db, c, "전기요금 절감 요청")
    s = _sol(db, "에너지 관리")
    bm = _bm(db, solutions=[s])
    o = _opp_via_post(user_client, db, c, need_ids=[str(n.id)], solution_ids=[str(s.id)], bm_id=str(bm.id), memo="여기가 메모")
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": "other"})
    user_client.post(f"/opportunities/{o.id}/convert", data={"mode": "create", "name": "한빛 전력"})
    p = db.query(Project).one()
    root = settings.knowledge_dir
    md = (root / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")
    assert md.startswith("# 전력 절감 사업")
    for needle in (f"한빛병원 (customers/{c.id}.md)", "수주", f"에너지 절감 BM (bms/{bm.id}.md)", f"한빛 전력 (projects/{p.id}.md)",
                   "전기요금 절감 요청", f"(activities/2026/{a.id}.md)", f"에너지 관리 (solutions/{s.id}/card.md)", "여기가 메모"):
        assert needle in md, needle
    assert f"전력 절감 사업 (opportunities/{o.id}.md)" in (root / "customers" / f"{c.id}.md").read_text(encoding="utf-8")
    assert f"opportunities/{o.id}.md" in (root / "projects" / f"{p.id}.md").read_text(encoding="utf-8")
    assert f"opportunities/{o.id}.md" in (root / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")
    assert f"bms/{bm.id}.md" in (root / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")
    bmmd = (root / "bms" / f"{bm.id}.md").read_text(encoding="utf-8")
    assert f"한빛 전력 (projects/{p.id}.md)" in bmmd and f"opportunities/{o.id}.md" in bmmd
    assert f"opportunities/{o.id}.md" in (root / "activities" / "2026" / f"{a.id}.md").read_text(encoding="utf-8")
    # 출처 칩 해석
    d = describe_source(db, f"opportunities/{o.id}.md")
    assert d == {"label": "전력 절감 사업", "url": f"/opportunities/{o.id}", "kind": "영업기회"}
    d = describe_source(db, f"bms/{bm.id}.md")
    assert d == {"label": "에너지 절감 BM", "url": f"/bms/{bm.id}", "kind": "BM"}
    assert describe_source(db, "bms/9999.md")["url"] is None
    # 다시 만들기가 같은 파일을 만든다
    db.expire_all()
    KnowledgeMirror(root).rebuild_all(db, settings.uploads_dir)
    assert (root / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8") == md
    assert (root / "bms" / f"{bm.id}.md").read_text(encoding="utf-8") == bmmd
    assert KNOWLEDGE_FORMAT >= 5


def test_renames_resync_dependent_mirrors(user_client, db, settings):
    c = _customer(db)
    bm = _bm(db)
    o = _opp_via_post(user_client, db, c, bm_id=str(bm.id))
    root = settings.knowledge_dir
    user_client.post(f"/customers/{c.id}", data={"name": "한빛종합병원"})
    assert "한빛종합병원" in (root / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")
    user_client.post(f"/bms/{bm.id}", data={"name": "새 BM 이름"})
    assert "새 BM 이름" in (root / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")
    s = _sol(db, "에너지 관리")
    user_client.post(f"/opportunities/{o.id}", data={"name": "n", "customer_id": str(c.id), "solution_ids": [str(s.id)], "bm_id": str(bm.id)})
    user_client.post(f"/solutions/{s.id}", data={"name": "에너지 관리 2"})
    assert "에너지 관리 2" in (root / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")


def test_prompts_describe_new_knowledge_folders():
    from app.ai.tasks import load_prompt
    for name in ("proposal_directions", "proposal_chat", "proposal_outline", "research_suggest"):
        text = load_prompt(name)
        assert "opportunities/" in text and "bms/" in text, name


# ---- 화면: JS 없이 ----

def test_forms_work_without_javascript(user_client, db):
    c = _customer(db)
    s = _sol(db, "에너지 관리")
    _, n = _need(db, c)
    html = user_client.get(f"/opportunities/new?customer_id={c.id}&need_id={n.id}").text
    f = _PageForm("/opportunities")
    f.feed(html)
    assert f.fields["customer_id"] == [str(c.id)] and f.fields["need_ids"] == [str(n.id)]
    f.fields["name"] = ["JS 없이 만든 건"]
    f.fields["solution_ids"] = [str(s.id)]
    r = user_client.post("/opportunities", data=f.fields, follow_redirects=False)
    assert r.status_code == 303
    o = db.query(Opportunity).one()
    assert o.name == "JS 없이 만든 건" and [x.id for x in o.solutions] == [s.id]
    db.refresh(n)
    assert n.opportunity_id == o.id
    # 단계 막대와 전환 화면은 모두 일반 form 제출이다
    detail = user_client.get(f"/opportunities/{o.id}").text
    assert f'action="/opportunities/{o.id}/stage"' in detail and "<script" not in detail.split("<main ")[1].split("</main>")[0]
    for url in ("/bms/new", "/opportunities", "/bms", f"/opportunities/{o.id}/edit"):
        assert user_client.get(url).status_code == 200
    f2 = _PageForm("/bms")
    f2.feed(user_client.get("/bms/new").text)
    assert {"name", "summary", "target_customers", "revenue_model", "memo"} <= set(f2.fields)


def test_no_horizontal_overflow_rules_exist():
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.css").read_text(encoding="utf-8")
    for tone in set(ontology.OPPORTUNITY_STAGE_CLASS.values()):
        assert f".badge.{tone}" in css, tone


# ---- 옛 구조 DB ----

def _old_schema_db(settings):
    """BM·영업기회 표와 businesses.bm_id, proposals.opportunity_id 가 없는 옛 DB."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    new_tables = {"bms", "opportunities", "bm_solutions", "opportunity_solutions", "activity_opportunities", "solution_business_types"}
    rebuilt = {"businesses": ("bm_id",), "proposals": ("opportunity_id", "target_kind", "group_name", "bm_id", "business_types")}  # 베타 W 의 열(와 bms 를 가리키는 외래 키)과 베타 Y 의 열도 뺀다
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        sqls = {t: conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = ?", (t,)).scalar() for t in rebuilt}
    engine.dispose()
    settings.db_path.unlink()
    for ext in ("-wal", "-shm"):
        Path(str(settings.db_path) + ext).unlink(missing_ok=True)
    engine = make_engine(settings.db_path)
    keep = [t for t in Base.metadata.sorted_tables if t.name not in new_tables and t.name not in rebuilt]
    Base.metadata.create_all(engine, tables=keep)
    with engine.begin() as conn:
        for table, col in rebuilt.items():
            lines = [ln for ln in sqls[table].split("\n") if not any(c in ln for c in col)]
            sql = re.sub(r",\s*\n\)", "\n)", "\n".join(lines))
            conn.exec_driver_sql(sql)
        conn.exec_driver_sql("INSERT INTO customers (id, name, industry, contact, memo) VALUES (1, '한빛병원', '', '', '')")
        conn.exec_driver_sql("INSERT INTO businesses (id, name, customer_id, description, period, status, memo) VALUES (1, '옛 프로젝트', 1, '', '', '', '')")
        conn.exec_driver_sql("INSERT INTO proposals (id, title, customer_id, business_id, request_memo, status, extra_solution_ids, directions, deck_name) "
                             "VALUES (1, '옛 제안', 1, 1, '', 'new', '[]', '[]', '')")
        conn.exec_driver_sql("INSERT INTO activities (id, kind, title, raw_text, summary_md, follow_ups, demand_signals) VALUES (1, 'meeting', 't', 'r', '', '[]', '[]')")
        conn.exec_driver_sql("INSERT INTO customer_needs (id, activity_id, position, customer_name, customer_id, content, opportunity_id) VALUES (1, 1, 0, '한빛병원', 1, '니즈', NULL)")
    engine.dispose()


def test_old_schema_database_gets_new_tables_and_columns(settings):
    _old_schema_db(settings)
    con = sqlite3.connect(settings.db_path)
    assert "bm_id" not in [r[1] for r in con.execute("PRAGMA table_info(businesses)")]
    assert con.execute("SELECT name FROM sqlite_master WHERE name='bms'").fetchone() is None
    con.close()
    app = create_app(settings)
    try:
        con = sqlite3.connect(settings.db_path)
        assert "bm_id" in [r[1] for r in con.execute("PRAGMA table_info(businesses)")]
        assert "opportunity_id" in [r[1] for r in con.execute("PRAGMA table_info(proposals)")]
        for t in ("bms", "opportunities", "bm_solutions", "opportunity_solutions", "activity_opportunities"):
            assert con.execute("SELECT name FROM sqlite_master WHERE name=?", (t,)).fetchone() is not None, t
        assert con.execute("SELECT name, bm_id FROM businesses").fetchall() == [("옛 프로젝트", None)]
        assert con.execute("SELECT title, opportunity_id, business_id FROM proposals").fetchall() == [("옛 제안", None, 1)]
        assert len(list(settings.data_dir.glob("app.db.before-migrate-*"))) >= 1
        con.close()
        c = TestClient(app)
        make_user(app.state.session_factory(), "kim")
        assert c.post("/login", data={"username": "kim", "password": "pw1234"}, follow_redirects=False).status_code == 303
        # 옛 데이터로 새 기능이 돈다
        r = c.post("/opportunities", data={"name": "새 영업기회", "customer_id": "1", "need_ids": ["1"]}, follow_redirects=False)
        assert r.status_code == 303
        assert c.get("/projects/1").status_code == 200 and c.get("/proposals/1").status_code == 200
        assert c.post("/projects/1", data={"name": "옛 프로젝트", "customer_id": "1", "bm_id": ""}, follow_redirects=False).status_code == 303
    finally:
        app.state.engine.dispose()

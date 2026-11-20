"""온톨로지 수정 묶음: 영업기회 불변식, 동시 전환, 되돌리기 사본, 옛 근거 경로, 사본 연결 구역, 어긋난 JSON, 후보 상한 …"""
import threading
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Activity, Customer, CustomerNeed, Opportunity, Project
from tests.conftest import make_user, need_rows


def _customer(db, name="한빛병원"):
    c = Customer(name=name)
    db.add(c)
    db.commit()
    return c


def _need(db, customer, content="전기요금 절감", title="미팅"):
    a = Activity(kind="meeting", date=date(2026, 11, 10), title=title, raw_text="r", needs=need_rows((customer.name, content)))
    db.add(a)
    db.flush()
    a.needs[0].customer_id = customer.id
    db.commit()
    return a, a.needs[0]


def _opp(client, db, customer, name="전력 절감", **data):
    r = client.post("/opportunities", data={"name": name, "customer_id": str(customer.id), **data}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return db.scalars(select(Opportunity).order_by(Opportunity.id.desc())).first()


# ---- I1 ----

def test_customer_change_refused_while_needs_bundled_even_without_need_ids(user_client, db):
    c1, c2 = _customer(db), _customer(db, "남도물산")
    _, n1 = _need(db, c1)
    _, n2 = _need(db, c1, "둘째 니즈")
    o = _opp(user_client, db, c1, need_ids=[str(n1.id), str(n2.id)])
    r = user_client.post(f"/opportunities/{o.id}", data={"name": "고침", "customer_id": str(c2.id)}, follow_redirects=False)
    assert r.status_code == 400 and "묶은" in r.text
    db.expire_all()
    assert o.customer_id == c1.id
    assert {n.opportunity_id for n in (n1, n2)} == {o.id}


def test_apply_needs_clears_every_need_not_wanted_regardless_of_customer(db, user_client):
    from app import opportunities as opps

    c1, c2 = _customer(db), _customer(db, "남도물산")
    _, n1 = _need(db, c1)
    o = _opp(user_client, db, c1, need_ids=[str(n1.id)])
    o.customer_id = c2.id  # 어긋난 상태를 직접 만든다 (예전 저장이 남긴 것)
    db.flush()
    assert opps.apply_needs(db, o, []) is None
    db.commit()
    db.refresh(n1)
    assert n1.opportunity_id is None


# ---- I2 ----

def _edit_need_customer(client, activity, customer_text):
    data = {"kind": activity.kind, "date": activity.date.isoformat(), "title": activity.title, "raw_text": activity.raw_text,
            "summary_md": activity.summary_md, "ds_id": [str(n.id) for n in activity.needs],
            "ds_customer": [customer_text for _ in activity.needs], "ds_content": [n.content for n in activity.needs]}
    r = client.post(f"/activities/{activity.id}", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text


def test_need_customer_edit_detaches_from_opportunity(user_client, db, settings):
    c1, c2 = _customer(db), _customer(db, "남도물산")
    a, n = _need(db, c1)
    o = _opp(user_client, db, c1, need_ids=[str(n.id)])
    _edit_need_customer(user_client, a, "남도물산")
    db.expire_all()
    assert n.customer_id == c2.id and n.opportunity_id is None
    assert "전기요금 절감" not in user_client.get(f"/opportunities/{o.id}").text
    card = next((settings.data_dir / "knowledge").rglob(f"*opportunities*/{o.id}*.md"), None) or next((settings.data_dir / "knowledge" / "opportunities").glob("*.md"))
    assert "전기요금 절감" not in card.read_text(encoding="utf-8")


def test_need_customer_text_cleared_detaches_and_same_customer_keeps(user_client, db):
    c1 = _customer(db)
    a, n = _need(db, c1)
    o = _opp(user_client, db, c1, need_ids=[str(n.id)])
    _edit_need_customer(user_client, a, c1.name)  # 같은 고객이면 그대로 묶여 있다
    db.expire_all()
    assert n.opportunity_id == o.id
    _edit_need_customer(user_client, a, "")
    db.expire_all()
    assert n.customer_id is None and n.opportunity_id is None


def test_need_row_delete_leaves_nothing_dangling(user_client, db):
    c1 = _customer(db)
    a, n = _need(db, c1)
    o = _opp(user_client, db, c1, need_ids=[str(n.id)])
    data = {"kind": a.kind, "date": a.date.isoformat(), "title": a.title, "raw_text": a.raw_text, "summary_md": a.summary_md}
    assert user_client.post(f"/activities/{a.id}", data=data, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(CustomerNeed).count() == 0
    assert user_client.get(f"/opportunities/{o.id}").status_code == 200


# ---- I3 ----

def _threads(app, n, fn):
    barrier, results = threading.Barrier(n), []

    def go(i):
        c = TestClient(app)
        c.post("/login", data={"username": "kim", "password": "pw1234"})
        barrier.wait(10)
        try:
            results.append(fn(c, i))
        except Exception as e:  # noqa: BLE001
            results.append(e)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    return results


def test_concurrent_convert_creates_exactly_one_project(app, user_client, db):
    c = _customer(db)
    o = _opp(user_client, db, c)
    user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "won"})
    res = _threads(app, 6, lambda cl, i: cl.post(f"/opportunities/{o.id}/convert", data={"mode": "create", "name": f"전환{i}"}, follow_redirects=False))
    assert all(not isinstance(r, Exception) and r.status_code == 303 for r in res), res
    db.expire_all()
    assert db.query(Project).count() == 1
    assert o.project_id == db.query(Project).one().id
    assert {r.headers["location"] for r in res} == {f"/projects/{o.project_id}"}


def test_concurrent_link_same_project_from_two_opportunities_only_one_wins(app, user_client, db):
    c = _customer(db)
    p = Project(name="기존", customer_id=c.id)
    db.add(p)
    db.commit()
    opps_ = [_opp(user_client, db, c, name=f"기회{i}") for i in range(2)]
    for o in opps_:
        user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "won"})
    res = _threads(app, 6, lambda cl, i: cl.post(f"/opportunities/{opps_[i % 2].id}/convert", data={"mode": "link", "project_id": str(p.id)}, follow_redirects=False))
    assert all(not isinstance(r, Exception) for r in res), res  # 500 이 아니다 (TestClient 는 서버 오류를 예외로 올린다)
    assert sorted(r.status_code for r in res).count(303) >= 1
    db.expire_all()
    assert sum(1 for o in opps_ if o.project_id == p.id) == 1
    assert db.query(Project).count() == 1


def test_link_unique_violation_shows_korean_message_not_500(user_client, db, monkeypatch):
    from app import opportunities as opps

    c = _customer(db)
    p = Project(name="기존", customer_id=c.id)
    db.add(p)
    db.commit()
    o1, o2 = _opp(user_client, db, c, name="a"), _opp(user_client, db, c, name="b")
    for o in (o1, o2):
        user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "won"})
    assert user_client.post(f"/opportunities/{o1.id}/convert", data={"mode": "link", "project_id": str(p.id)}, follow_redirects=False).status_code == 303
    monkeypatch.setattr(opps, "link_problem", lambda *a, **k: None)  # 사전 검사를 지나친 동시 요청을 흉내 낸다
    r = user_client.post(f"/opportunities/{o2.id}/convert", data={"mode": "link", "project_id": str(p.id)})
    assert r.status_code == 400 and opps.PROJECT_TAKEN in r.text.replace("&#39;", "'")
    db.expire_all()
    assert o2.project_id is None


# ---- I4: 시작 한 번에 DB 복사본 하나 ----

def _old_db_all_steps(settings):
    """칸 추가·JSON 복사·유래 채우기가 모두 할 일이 있는 옛 DB."""
    import json
    import sqlite3

    from sqlalchemy import text

    from app.db import Base, make_engine
    from tests.test_activity_rows import DS1, FU1, NEW_TABLES, _old_db

    _old_db(settings, [(1, FU1, DS1)])
    engine = make_engine(settings.db_path)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE activities DROP COLUMN origin_id"))  # 칸이 빠진 옛 구조 (칸 추가 단계)
        conn.execute(text("ALTER TABLE activities DROP COLUMN origin_type"))
    engine.dispose()
    con = sqlite3.connect(settings.db_path)
    con.execute("UPDATE activities SET created_at = '2026-11-01 09:02:00'")
    result = json.dumps([{"type": "activity", "id": 1, "label": "t"}])
    con.execute("INSERT INTO intakes (id, status, note, error, created_by_id, created_at, applied_at, result) VALUES (1, 'applied', '', '', 1, '2026-11-01 09:00:00', '2026-11-01 09:05:00', ?)", (result,))
    con.commit()
    con.close()


def test_one_before_migrate_copy_per_startup_shared_by_all_steps(settings):
    import sqlite3

    from tests.test_activity_rows import _start

    _old_db_all_steps(settings)
    _start(settings)
    copies = list(settings.db_path.parent.glob("app.db.before-migrate-*"))
    assert len(copies) == 1
    con = sqlite3.connect(copies[0])  # 모든 단계 전의 상태: 아직 유래 칸도, 복사된 행도 없다
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(activities)")]
        assert "origin_type" not in cols
    finally:
        con.close()
    con = sqlite3.connect(settings.db_path)
    try:
        assert con.execute("SELECT count(*) FROM follow_ups").fetchone()[0] == 4  # 세 단계가 모두 일했다
        assert con.execute("SELECT origin_type FROM activities WHERE id = 1").fetchone()[0] == "intake"
    finally:
        con.close()
    _start(settings)  # 두 번째 시작: 복사본이 늘지 않는다
    assert len(list(settings.db_path.parent.glob("app.db.before-migrate-*"))) == 1


# ---- M1: 옛 근거 경로 ----

def test_check_sources_reads_legacy_business_path_as_project_copy(tmp_path):
    from app.proposals import check_sources

    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "1.md").write_text("x", encoding="utf-8")
    found, missing = check_sources(tmp_path, ["businesses/1.md", "businesses/2.md", "projects/1.md"])
    assert found == ["businesses/1.md", "projects/1.md"]  # 저장된 글자는 그대로
    assert missing == ["businesses/2.md"]


# ---- M2: 연결 구역은 카드마다 실패해도 나머지를 쓴다 ----

def test_connections_failure_skips_section_only(db, settings, monkeypatch, caplog):
    import logging

    from app import relations
    from app.knowledge import KnowledgeMirror

    c = _customer(db)
    _need(db, c)

    def boom(*a, **k):
        raise RuntimeError("나쁜 행")

    monkeypatch.setattr(relations, "related", boom)
    mirror = KnowledgeMirror(settings.knowledge_dir)
    with caplog.at_level(logging.WARNING, logger="app.knowledge"):
        mirror.rebuild_all(db, settings.uploads_dir)  # 끝까지 돈다
    card = next((settings.knowledge_dir / "customers").glob("*.md")).read_text(encoding="utf-8")
    assert "한빛병원" in card and "## 연결" not in card
    assert any("연결 구역" in r.getMessage() for r in caplog.records)
    assert any((settings.knowledge_dir / "activities").rglob("*.md"))


# ---- M3: 어긋난 JSON 이 화면을 깨뜨리지 않는다 ----

def test_malformed_json_resolvers_do_not_break_pages(admin_client, db):
    from app import ontology
    from app.models import ResearchReport, ResearchRun, Solution

    s = Solution(name="조명")
    db.add(s)
    db.commit()
    run = ResearchRun(trigger="manual", status="done")
    db.add(run)
    db.flush()
    bad = [
        ["문자열", 3, None],
        {"solution_ids": [s.id]},  # 목록이 아니라 dict
        [{"title": ["x"], "kind": 5, "solution_ids": "1"}, {"solution_ids": [True, "2", None, s.id]}, "x", None],
        [{"title": "정상", "kind": "solution", "solution_ids": [s.id]}],
    ]
    for sug in bad:
        db.add(ResearchReport(run_id=run.id, kind="suggest", status="done", suggestions=sug))
    from app.models import Proposal
    cust = _customer(db)
    for extra in ("1", {"a": 1}, [True, "x", None, s.id], 7):
        db.add(Proposal(customer_id=cust.id, extra_solution_ids=extra))
    db.commit()
    assert admin_client.get(f"/solutions/{s.id}").status_code == 200
    assert admin_client.get(f"/customers/{cust.id}").status_code == 200
    pid = db.scalars(select(Proposal.id)).first()
    assert admin_client.get(f"/proposals/{pid}").status_code == 200
    groups = ontology.related(db, s, viewer=None)
    assert all(g.total >= 0 for g in groups)


# ---- M4: 아주 많이 연결돼도 최신 몇 개가 보인다 ----

def test_big_group_still_shows_newest(db, monkeypatch):
    from app import relations

    c = _customer(db)
    acts = []
    for i in range(9):
        a = Activity(kind="meeting", date=date(2026, 1, 1 + i), title=f"활동{i}", raw_text="r")
        a.customers = [c]
        acts.append(a)
    db.add_all(acts)
    db.commit()
    monkeypatch.setattr(relations, "CHUNK", 3)
    groups = relations.related(db, c, limit=2)
    g = next(g for g in groups if g.target_type == "activity")
    assert g.total == 9 and [i.label for i in g.items] == ["활동8", "활동7"]


# ---- M5: 묶음 합계는 있는 기록만 센다 ----

def test_json_backed_totals_count_only_existing_records(db):
    from app import relations
    from app.models import Proposal, Solution

    c = _customer(db)
    s1, s2 = Solution(name="a"), Solution(name="b")
    db.add_all([s1, s2])
    db.flush()
    p = Proposal(customer_id=c.id, extra_solution_ids=[s1.id, s2.id, 9998, 9999])
    db.add(p)
    db.commit()
    g = next(g for g in relations.related(db, p, limit=10) if g.target_type == "solution" and g.storage == "json")
    assert g.total == 2 and len(g.items) == 2


# ---- M6: 업그레이드 때 열려 있던 옛 화면 ----

def test_activity_save_without_opportunity_field_keeps_links_but_empty_field_clears(user_client, db):
    c = _customer(db)
    o = _opp(user_client, db, c)
    a = Activity(kind="meeting", date=date(2026, 11, 10), title="미팅", raw_text="r")
    a.opportunities = [o]
    db.add(a)
    db.commit()
    base = {"kind": "meeting", "date": "2026-11-10", "title": "미팅2", "raw_text": "r"}
    assert user_client.post(f"/activities/{a.id}", data=base, follow_redirects=False).status_code == 303  # 옛 화면: 칸이 없다
    db.expire_all()
    assert [x.id for x in a.opportunities] == [o.id] and a.title == "미팅2"
    assert user_client.post(f"/activities/{a.id}", data={**base, "opportunity_ids": ""}, follow_redirects=False).status_code == 303  # 새 화면에서 모두 풀었다
    db.expire_all()
    assert a.opportunities == []
    assert 'name="opportunity_ids" value=""' in user_client.get(f"/activities/{a.id}/edit").text


def test_old_businesses_post_urls_redirect_with_307_and_work(user_client, db):
    r = user_client.post("/businesses", data={"name": "옛 화면 프로젝트", "customer_id": str(_customer(db).id)}, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/projects"
    r = user_client.post("/businesses", data={"name": "옛 화면 프로젝트", "customer_id": str(db.scalars(select(Customer.id)).first())})
    assert r.status_code in (200, 303) and db.query(Project).filter_by(name="옛 화면 프로젝트").count() == 1  # 따라가면 새 경로가 처리한다
    pid = db.scalars(select(Project.id)).first()
    r = user_client.post(f"/businesses/{pid}/delete", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"/projects/{pid}/delete"


# ---- M7: 옛 JSON 복사의 완료·기한 ----

def test_copy_done_and_due_rules(settings, caplog):
    import logging
    import sqlite3

    from tests.test_activity_rows import _old_db, _snapshot, _start

    fus = [{"task": f"t{i}", "done": d, "due": due} for i, (d, due) in enumerate([
        (True, "2026-11-20"), ("true", "2026-11-21"), ("TRUE", " 2026-11-22 "), ("1", "20261123"), ("Yes", "2026-W47-1"), ("y", "2026-11-31"),
        ("완료", "2026-1-5"), ("false", ""), ("0", None), ("", 5), ("예", "2026-11-24T10:00"), (1, "2026-11-25"), (False, "2026-11-26"),
    ])]
    _old_db(settings, [(7, fus, [])])
    con = sqlite3.connect(settings.db_path)
    before = con.execute("SELECT follow_ups FROM activities WHERE id=7").fetchone()
    con.close()
    with caplog.at_level(logging.WARNING, logger="app.migrate"):
        _start(settings)
    legacy, rows, _ = _snapshot(settings)
    assert [(r[2], r[6], r[5]) for r in rows] == [
        ("t0", 1, "2026-11-20"), ("t1", 1, "2026-11-21"), ("t2", 1, "2026-11-22"), ("t3", 1, None), ("t4", 1, None), ("t5", 1, None),
        ("t6", 1, None), ("t7", 0, None), ("t8", 0, None), ("t9", 0, None), ("t10", 0, None), ("t11", 0, "2026-11-25"), ("t12", 0, "2026-11-26"),
    ]
    assert legacy[0][1] == before[0]  # 옛 JSON 은 그대로
    dropped = [r.getMessage() for r in caplog.records if "기한을 옮기지 않습니다" in r.getMessage()]
    assert len(dropped) == 6 and all("활동 기록 7" in m for m in dropped)  # 20261123, W47, 31일, 2026-1-5, 5, T10:00


# ---- M11: 프롬프트의 후보 수 상한 (검증용 집합은 그대로) ----

def test_prompt_candidate_cap_keeps_priority_first_and_validation_set_complete(db, settings, monkeypatch):
    from app.ai import proposal_tasks
    from app.models import BM, Proposal, Solution
    from app.proposals import candidate_solutions

    monkeypatch.setattr(proposal_tasks, "PROMPT_CANDIDATE_CAP", 5)
    c = _customer(db)
    bm = BM(name="에너지 BM")
    db.add(bm)
    db.flush()
    sols = [Solution(name=f"솔루션{i:02d}", stage="boxed") for i in range(12)]
    db.add_all(sols)
    db.flush()
    bm.solutions = [sols[11], sols[10]]  # 이름순으로는 맨 뒤지만 우선 묶음이다
    p = Proposal(customer_id=c.id)
    p.opportunity = Opportunity(name="기회", customer_id=c.id, bm_id=bm.id)
    db.add(p)
    db.commit()
    full = candidate_solutions(db, p)
    assert len(full) == 12  # 검증용 집합은 줄지 않는다
    prompt = proposal_tasks.directions_prompt(db, p)
    listed = [line for line in prompt.splitlines() if line.startswith("- id ") and "`solutions/" in line]
    assert len(listed) == 5
    assert listed[0].startswith(f"- id {sols[10].id}:") and listed[1].startswith(f"- id {sols[11].id}:")
    assert "외 7개는 생략" in prompt and "`solutions/`" in prompt

"""기록의 유래(origin): 코드 목록·관계 정의, 쓰는 곳, 화면 표시, 지울 때 정리, 한 번의 되짚어 채우기."""
import logging
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import ontology, origin
from app.main import create_app
from app.migrate import ORIGINS_FILLED_KEY, backfill_origins
from app.models import (
    Activity, AppSetting, Customer, Intake, Partner, ResearchReport, ResearchRun, Solution, User,
)
from tests.conftest import finished_run, form_from_page, make_user, ready_intake, nloc

NEW_COLUMNS = {"solutions": ("origin_type", "origin_id", "origin_item"), "activities": ("origin_type", "origin_id"),
               "customers": ("origin_type", "origin_id"), "partners": ("origin_type", "origin_id")}


def _other_client(app, db, username="park"):
    make_user(db, username)
    c = TestClient(app)
    assert c.post("/login", data={"username": username, "password": "pw1234"}, follow_redirects=False).status_code == 303
    return c


# ---- 정의 ----

def test_origin_code_list_and_relations_are_defined():
    cl = ontology.CODE_LISTS["origin_type"]
    assert cl.items == {"manual": "직접 입력", "intake": "올리기", "import": "파일 가져오기", "research": "리서치"} and cl.default is None
    rel = {r.key: r for r in ontology.RELATIONS}
    for key in ("R19", "R22"):
        assert rel[key].storage == "fk"
    assert "solutions.origin_id" in rel["R19"].physical.split() and "solutions.origin_type" in rel["R19"].physical.split()
    for table in ("solutions", "activities", "customers", "partners"):
        assert f"{table}.origin_type" in rel["R22"].physical.split() and f"{table}.origin_id" in rel["R22"].physical.split()
    for code in ("solution", "partner", "customer", "activity"):
        assert ontology.prop(code, "origin") == "유래"


def test_columns_exist_and_are_nullable(app):
    with app.state.engine.connect() as conn:
        for table, cols in NEW_COLUMNS.items():
            info = {r[1]: r for r in conn.execute(text(f"PRAGMA table_info({table})"))}
            for c in cols:
                assert c in info and info[c][3] == 0, (table, c)  # notnull == 0


# ---- 쓰는 곳 ----

def test_manual_create_routes_mark_manual(user_client, db):
    user_client.post("/solutions", data={"name": "직접 솔루션"})
    user_client.post("/partners", data={"name": "직접 파트너"})
    user_client.post("/customers", data={"name": "직접 고객"})
    user_client.post("/activities", data={"raw_text": "직접 쓴 메모"})
    user_client.post("/activities/raw", data={"raw_text": "원문만 저장"})
    for model in (Solution, Partner, Customer):
        obj = db.query(model).one()
        assert (obj.origin_type, obj.origin_id) == ("manual", None), model
    assert [(a.origin_type, a.origin_id) for a in db.query(Activity).order_by(Activity.id)] == [("manual", None)] * 2


def test_new_names_inside_activity_save_are_manual_and_existing_are_untouched(user_client, db):
    old = Customer(name="기존 고객")
    db.add(old)
    db.commit()
    user_client.post("/activities", data={"raw_text": "메모", "new_customers": ["기존 고객", "새 고객"],
                                          "new_solutions": ["새 솔루션"], "new_partners": ["새 파트너"]})
    db.expire_all()
    assert db.query(Customer).filter_by(name="기존 고객").one().origin_type is None  # 연결만: 유래를 바꾸지 않는다
    for model, name in ((Customer, "새 고객"), (Solution, "새 솔루션"), (Partner, "새 파트너")):
        assert db.query(model).filter_by(name=name).one().origin_type == "manual"


def test_editing_keeps_origin(user_client, db):
    s = Solution(name="올리기 솔루션", origin_type="intake", origin_id=7)
    db.add(s)
    db.commit()
    user_client.post(f"/solutions/{s.id}", data={"name": "이름 바꿈", "stage": "intake"})
    db.refresh(s)
    assert s.name == "이름 바꿈" and (s.origin_type, s.origin_id) == ("intake", 7)


def test_intake_apply_marks_only_created_records(app, user_client, db, settings):
    it, s1, c1 = ready_intake(db, settings)
    r = user_client.post(f"/intake/{it.id}/apply", data=form_from_page(user_client, it), follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    new_solution = db.query(Solution).filter_by(name="스마트 조명 제어").one()
    partner = db.query(Partner).filter_by(name="루미텍").one()
    activity = db.query(Activity).one()
    for obj in (new_solution, partner, activity):
        assert (obj.origin_type, obj.origin_id) == ("intake", it.id)
    db.refresh(s1), db.refresh(c1)
    assert s1.origin_type is None and c1.origin_type is None  # 자료만 더하거나 연결만 한 기록


def test_add_contact_changes_no_origin(app, user_client, db, settings):
    from tests.conftest import ready_bundle

    p = Partner(name="루미텍", origin_type="manual")
    c = Customer(name="한빛병원")
    db.add_all([p, c])
    db.commit()
    plan = {"contacts": [{"target_type": "partner", "target_id": p.id, "files": [1], "reason": "명함", "person": "홍길동",
                          "phone": "010-1234-5678"},
                         {"target_type": "customer", "target_id": c.id, "files": [1], "reason": "명함", "person": "김",
                          "email": "kim@example.com"}]}
    it = ready_bundle(db, settings, [("명함.txt", "홍길동 010-1234-5678".encode())], **plan)
    assert user_client.post(f"/intake/{it.id}/apply", data=form_from_page(user_client, it), follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Partner, p.id).origin_type == "manual" and db.get(Customer, c.id).origin_type is None
    person = db.get(Partner, p.id).contacts[0]  # 사람은 담당자 기록으로 (영업 D): 회사의 유래는 그대로, 담당자의 유래는 이 묶음
    assert person.phone == "010-1234-5678" and (person.origin_type, person.origin_id) == ("intake", it.id)


def _research_post(client, run, report, index, source="suggestion"):
    return client.post(f"/research/runs/{run.id}/candidate", data={"source": source, "report_id": report.id, "index": index},
                       follow_redirects=False)


def test_research_registration_sets_origin_and_blocks_duplicates(user_client, db, settings):
    run = finished_run(db, settings)
    sug = db.query(ResearchReport).filter_by(run_id=run.id, kind="suggest").one()
    r = _research_post(user_client, run, sug, 0)
    assert r.status_code == 303
    new = db.query(Solution).filter_by(origin_type="research").one()
    assert new.origin_id == sug.id and nloc(r) == f"/solutions/{new.id}/edit?notice=candidate_registered"
    again = _research_post(user_client, run, sug, 0)  # 같은 제안을 다시 등록해도 새로 만들지 않고 이미 등록된 솔루션으로 보낸다
    assert again.status_code == 303 and nloc(again) == f"/solutions/{new.id}?notice=already_registered"
    assert db.query(Solution).filter_by(origin_type="research").count() == 1
    tech = db.query(ResearchReport).filter_by(run_id=run.id, area="tech").one()
    _research_post(user_client, run, tech, 0, source="trend")  # 같은 보고서가 아니라 다른 항목이면 따로 등록된다
    assert db.query(Solution).filter_by(origin_type="research").count() == 2
    _research_post(user_client, run, tech, 1, source="trend")
    assert db.query(Solution).filter_by(origin_type="research").count() == 3


def test_deleted_registered_solution_can_be_registered_again(user_client, db, settings):
    run = finished_run(db, settings)
    sug = db.query(ResearchReport).filter_by(run_id=run.id, kind="suggest").one()
    _research_post(user_client, run, sug, 0)
    first = db.query(Solution).filter_by(origin_type="research").one()
    assert user_client.post(f"/solutions/{first.id}/delete", follow_redirects=False).status_code == 303
    assert "후보로 등록됨" not in user_client.get(f"/research/runs/{run.id}").text
    _research_post(user_client, run, sug, 0)
    assert db.query(Solution).filter_by(origin_type="research").count() == 1


def test_run_page_marks_registered_suggestions_and_trends(user_client, db, settings):
    run = finished_run(db, settings)
    sug = db.query(ResearchReport).filter_by(run_id=run.id, kind="suggest").one()
    tech = db.query(ResearchReport).filter_by(run_id=run.id, area="tech").one()
    page = user_client.get(f"/research/runs/{run.id}").text
    assert "후보로 등록됨" not in page and page.count(">후보로 등록</button>") == 3  # 새 제안 1 + 동향 2 (밀기 제안에는 버튼이 없다)
    _research_post(user_client, run, sug, 0)
    sol = db.query(Solution).filter_by(origin_id=sug.id).one()
    page = user_client.get(f"/research/runs/{run.id}").text
    assert page.count("후보로 등록됨") == 1 and f'href="/solutions/{sol.id}"' in page and page.count(">후보로 등록</button>") == 2
    _research_post(user_client, run, tech, 1, source="trend")
    page = user_client.get(f"/research/runs/{run.id}").text
    assert page.count("후보로 등록됨") == 2 and page.count(">후보로 등록</button>") == 1


# ---- 화면 ----

def _line(html: str) -> str:
    """'유래:' 줄의 글자 (없으면 빈 글자). 베타 R 의 "AI 생성" 배지는 빼고 본다 (배지는 tests/test_beta_r.py 가 본다)."""
    import re

    m = re.search(r'<p class="origin[^"]*">(.*?)</p>', html, re.S)
    if not m:
        return ""
    inner = re.sub(r'<span class="badge[^>]*>.*?</span>', "", m.group(1), flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", inner)).strip()


def _origin_pages(db):
    s = Solution(name="솔")
    p = Partner(name="파")
    c = Customer(name="고")
    a = Activity(raw_text="r", title="활")
    db.add_all([s, p, c, a])
    db.commit()
    return {f"/solutions/{s.id}": s, f"/partners/{p.id}": p, f"/customers/{c.id}": c, f"/activities/{a.id}": a}


def test_detail_pages_show_intake_origin_with_link_for_owner_and_admin_only(app, user_client, admin_client, db):
    pages = _origin_pages(db)
    owner = db.query(User).filter_by(username="kim").one()
    it = Intake(status="applied", created_by_id=owner.id)
    db.add(it)
    db.commit()
    for obj in pages.values():
        obj.origin_type, obj.origin_id = "intake", it.id
    db.commit()
    stranger = _other_client(app, db)
    for url in pages:
        for client in (user_client, admin_client):  # 올린 사람과 관리자: 링크
            html = client.get(url).text
            assert _line(html) == f"유래: 올리기 묶음 {it.id}번" and f'<a href="/intake/{it.id}">' in html, url
        html = stranger.get(url).text  # 그 밖의 사용자: 올린 사람 이름이 든 글자만
        assert _line(html) == f"유래: {owner.name or owner.username}님이 올린 묶음 {it.id}번" and f'href="/intake/{it.id}"' not in html, url


def test_detail_pages_show_research_origin_and_nothing_for_manual_or_unknown(user_client, db, settings):
    pages = _origin_pages(db)
    assert all(_line(user_client.get(u).text) == "" for u in pages)  # 모름
    run = finished_run(db, settings)
    sug = db.query(ResearchReport).filter_by(run_id=run.id, kind="suggest").one()
    day = run.started_at.strftime("%Y-%m-%d")
    for obj in pages.values():
        obj.origin_type, obj.origin_id = "research", sug.id
    db.commit()
    for url in pages:
        html = user_client.get(url).text
        assert _line(html) == f"유래: 리서치 {day} 발굴 방향 제안" and f'href="/research/runs/{run.id}"' in html, url
    for obj in pages.values():
        obj.origin_type, obj.origin_id = "manual", None
    db.commit()
    assert all(_line(user_client.get(u).text) == "" for u in pages)  # 직접 입력은 보이지 않는다


# ---- 지울 때 ----

def test_deleting_research_run_clears_origin_ids_and_shows_deleted(user_client, admin_client, db, settings):
    run = finished_run(db, settings)
    sug = db.query(ResearchReport).filter_by(run_id=run.id, kind="suggest").one()
    _research_post(user_client, run, sug, 0)
    kept = Solution(name="다른 조사에서", origin_type="research", origin_id=99999, origin_item="suggestion:0")
    db.add(kept)
    db.commit()
    sol = db.query(Solution).filter_by(origin_id=sug.id).one()
    assert admin_client.post(f"/research/runs/{run.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(ResearchRun).count() == 0
    assert (sol.origin_type, sol.origin_id) == ("research", None)  # 유형은 남기고 번호만 비운다
    assert kept.origin_id == 99999  # 다른 보고서를 가리키는 값은 건드리지 않는다
    assert _line(user_client.get(f"/solutions/{sol.id}").text) == "유래: 리서치 (조사 삭제됨)"


def test_dangling_research_origin_without_clearing_still_reads_as_deleted(user_client, db):
    s = Solution(name="x", origin_type="research", origin_id=424242)
    db.add(s)
    db.commit()
    assert _line(user_client.get(f"/solutions/{s.id}").text) == "유래: 리서치 (조사 삭제됨)"


def test_clear_dangling_for_intake_rows(user_client, db):
    pages = _origin_pages(db)
    for obj in pages.values():
        obj.origin_type, obj.origin_id = "intake", 5
    other = Solution(name="묶음 6", origin_type="intake", origin_id=6)
    db.add(other)
    db.commit()
    with db.begin_nested():
        assert origin.clear_dangling(db, "intake", [5]) == 4
    db.commit()
    db.expire_all()
    assert all((o.origin_type, o.origin_id) == ("intake", None) for o in pages.values())
    assert other.origin_id == 6
    url = next(iter(pages))
    assert _line(user_client.get(url).text) == "유래: 올리기 (묶음 삭제됨)"


def test_no_intake_delete_route_exists(app):
    """올리기 묶음을 지우는 화면은 없다 (버림은 상태 변경). 생기면 `origin.clear_dangling` 을 같은 트랜잭션에서 불러야 한다."""
    paths = [p for p in app.openapi()["paths"] if p.startswith("/intake") and "delete" in p]
    assert paths == []


# ---- 한 번의 되짚어 채우기 ----

T0 = datetime(2026, 11, 1, 9, 0, 0)


def _intake(db, owner, result, status="applied", created=T0, applied=T0 + timedelta(minutes=5)):
    it = Intake(status=status, created_by_id=owner.id, created_at=created, applied_at=applied, result=result)
    db.add(it)
    db.commit()
    return it


def _rec(db, model, created, **kw):
    base = {"raw_text": "r", "title": "t"} if model is Activity else {"name": "n"}
    obj = model(created_at=created, **{**base, **kw})
    db.add(obj)
    db.commit()
    return obj


def _fresh_flag(db):
    db.query(AppSetting).filter_by(key=ORIGINS_FILLED_KEY).delete()
    db.commit()


def _entry(obj, label=None):
    t = {Partner: "partner", Customer: "customer", Solution: "solution", Activity: "activity"}[type(obj)]
    return {"type": t, "id": obj.id, "label": label if label is not None else (obj.name if hasattr(obj, "name") else obj.title)}


def test_backfill_rules(app, db, settings, caplog):
    kim = make_user(db, "kim")
    inside, before, after = T0 + timedelta(minutes=2), T0 - timedelta(days=1), T0 + timedelta(hours=1)
    ok_partner = _rec(db, Partner, inside, name="루미텍")
    ok_customer = _rec(db, Customer, inside, name="한빛병원")
    ok_solution = _rec(db, Solution, inside, name="스마트 조명")
    renamed = _rec(db, Solution, inside, name="나중에 바꾼 이름")
    ok_activity = _rec(db, Activity, inside, title="통화")
    untitled = _rec(db, Activity, inside, title="")
    old_record = _rec(db, Partner, before, name="더 오래된 파트너")  # 묶음보다 먼저 있던 기록 (번호 재사용 따위)
    later_record = _rec(db, Customer, after, name="저장 뒤에 생긴 고객")  # 저장이 끝난 뒤 같은 번호로 생긴 기록
    appended_to = _rec(db, Solution, inside, name="자료만 더한 솔루션")  # 묶음을 올린 뒤 직접 만든 기록에 자료만 더했다
    contact_to = _rec(db, Customer, inside, name="연락처만 더한 고객")
    has_origin = _rec(db, Partner, inside, name="이미 유래가 있다", origin_type="manual")
    result = [_entry(ok_partner), _entry(ok_customer), _entry(ok_solution), _entry(renamed, "원래 이름"), _entry(ok_activity),
              {"type": "activity", "id": untitled.id, "label": "(제목 없음)"},
              _entry(old_record), _entry(later_record),
              _entry(appended_to, "자료만 더한 솔루션 (자료 2개 추가)"),
              _entry(appended_to, "자료만 더한 솔루션 (이미 등록된 솔루션에 자료 1개 추가)"),
              _entry(contact_to, "연락처만 더한 고객 (연락처 추가)"),
              _entry(has_origin),
              {"type": "partner", "id": 99999, "label": "지워진 기록"},
              {"type": "skipped", "id": None, "label": "저장하지 못한 항목", "reason": "x", "files": []},
              {"type": "unsaved", "id": None, "label": "x"},
              "깨진 줄", {"type": "solution"}]
    it = _intake(db, kim, result)
    discarded = _rec(db, Partner, inside, name="버린 묶음의 기록")
    _intake(db, kim, [_entry(discarded)], status="discarded")
    _fresh_flag(db)
    with caplog.at_level(logging.INFO, logger="app.migrate"):
        summary = backfill_origins(app.state.engine, settings.db_path)
    db.expire_all()
    done = [ok_partner, ok_customer, ok_solution, renamed, ok_activity, untitled]
    assert all((o.origin_type, o.origin_id) == ("intake", it.id) for o in done)
    for o in (old_record, later_record, appended_to, contact_to, discarded):
        assert o.origin_type is None and o.origin_id is None, o.id
    assert (has_origin.origin_type, has_origin.origin_id) == ("manual", None)  # 이미 있는 유래는 덮어쓰지 않는다
    assert summary["total"] == 6 and summary["set"] == {"partner": 1, "customer": 1, "solution": 2, "activity": 2}
    assert (summary["missing"], summary["already"], summary["outside_window"], summary["linked_only"]) == (1, 1, 2, 3)
    assert db.get(AppSetting, ORIGINS_FILLED_KEY).value == summary
    assert any("유래" in r.getMessage() and "6" in r.getMessage() for r in caplog.records)
    assert len(list(settings.db_path.parent.glob("app.db.before-migrate-*"))) == 1  # 고치기 전 복사본


def test_backfill_is_idempotent_and_never_overwrites_later_edits(app, db, settings):
    kim = make_user(db, "kim")
    p = _rec(db, Partner, T0 + timedelta(minutes=1), name="루미텍")
    it = _intake(db, kim, [_entry(p)])
    _fresh_flag(db)
    assert backfill_origins(app.state.engine, settings.db_path)["total"] == 1
    assert backfill_origins(app.state.engine, settings.db_path) is None
    p.origin_type, p.origin_id = "manual", None
    db.commit()
    assert backfill_origins(app.state.engine, settings.db_path) is None
    db.refresh(p)
    assert p.origin_type == "manual"
    # 표시를 잃어도 이미 유래가 있는 기록은 그대로다
    _fresh_flag(db)
    summary = backfill_origins(app.state.engine, settings.db_path)
    db.refresh(p)
    assert summary["total"] == 0 and summary["already"] == 1 and p.origin_type == "manual"
    assert it.id


def test_backfill_without_work_makes_no_backup(app, db, settings):
    _fresh_flag(db)
    assert backfill_origins(app.state.engine, settings.db_path)["total"] == 0
    assert list(settings.db_path.parent.glob("app.db.before-migrate-*")) == []
    assert db.get(AppSetting, ORIGINS_FILLED_KEY) is not None


def test_backfill_failure_leaves_nothing_and_retries(app, db, settings):
    from sqlalchemy import event

    kim = make_user(db, "kim")
    p = _rec(db, Partner, T0 + timedelta(minutes=1), name="루미텍")
    c = _rec(db, Customer, T0 + timedelta(minutes=1), name="한빛")
    _intake(db, kim, [_entry(p), _entry(c)])
    _fresh_flag(db)
    engine = app.state.engine
    fail = {"on": True}

    def boom(conn, cursor, statement, parameters, context, executemany):
        if fail["on"] and statement.startswith("INSERT INTO app_settings"):
            raise RuntimeError("중간에 멈춤")

    event.listen(engine, "before_cursor_execute", boom)
    try:
        with pytest.raises(Exception, match="중간에 멈춤"):
            backfill_origins(engine, settings.db_path)
        db.expire_all()
        assert db.get(Partner, p.id).origin_type is None and db.get(Customer, c.id).origin_type is None
        assert db.get(AppSetting, ORIGINS_FILLED_KEY) is None
        fail["on"] = False
        assert backfill_origins(engine, settings.db_path)["total"] == 2
    finally:
        event.remove(engine, "before_cursor_execute", boom)


def test_old_database_gets_columns_and_backfill_at_startup(settings):
    """새 열이 없는 옛 DB 로 서버를 시작하면 열이 더해지고, 되짚을 수 있는 기록에 유래가 채워진다."""
    app = create_app(settings)
    engine = app.state.engine
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ux_partners_customer_id"))
        for table, cols in NEW_COLUMNS.items():
            for c in cols:
                if table != "partners":
                    conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {c}"))
        conn.execute(text("DROP TABLE partners"))  # 외래 키가 걸린 열은 지울 수 없어 옛 구조로 다시 만든다 (비어 있다)
        conn.execute(text("CREATE TABLE partners (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR NOT NULL, contact VARCHAR NOT NULL, "
                          "description VARCHAR NOT NULL, memo VARCHAR NOT NULL, created_by_id INTEGER, created_at DATETIME NOT NULL, "
                          "updated_at DATETIME NOT NULL)"))
        conn.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": ORIGINS_FILLED_KEY})
        conn.execute(text("INSERT INTO users (id, username, name, password_hash, role, is_active) VALUES (1, 'kim', 'kim', '', 'user', 1)"))
        conn.execute(text("INSERT INTO partners (id, name, contact, description, memo, created_at, updated_at) VALUES "
                          "(1, '루미텍', '', '', '', '2026-11-01 09:02:00', '2026-11-01 09:02:00'), "
                          "(2, '예전 파트너', '', '', '', '2026-10-01 09:00:00', '2026-10-01 09:00:00')"))
        conn.execute(text("INSERT INTO intakes (id, note, status, result, error, created_at, updated_at, applied_at, created_by_id) VALUES "
                          "(3, '', 'applied', :r, '', '2026-11-01 09:00:00', '2026-11-01 09:05:00', '2026-11-01 09:05:00', 1)"),
                     {"r": '[{"type": "partner", "id": 1, "label": "루미텍"}, {"type": "partner", "id": 2, "label": "예전 파트너"}]'})
    engine.dispose()
    app = create_app(settings)
    try:
        with app.state.engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name, origin_type, origin_id, customer_id FROM partners ORDER BY id")).all()
            assert rows == [(1, "루미텍", "intake", 3, None), (2, "예전 파트너", None, None, None)]
            idx = [r[1] for r in conn.execute(text("PRAGMA index_list(partners)"))]
            assert "ux_partners_customer_id" in idx
        assert len(list(settings.db_path.parent.glob("app.db.before-migrate-*"))) >= 1
    finally:
        app.state.engine.dispose()

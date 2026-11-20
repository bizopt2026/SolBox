"""후속 조치·고객 니즈 표: 옛 JSON 복사 이관, 줄 번호 기준 저장, 완료 표시, 지우기 규칙."""
import json
import logging
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.db import Base, make_engine
from app.main import create_app
from app.migrate import ROWS_COPIED_KEY, copy_activity_json
from app.models import Activity, AppSetting, Customer, CustomerNeed, FollowUp, User
from tests.conftest import fu_rows, make_user, need_rows

NEW_TABLES = ("follow_ups", "customer_needs")


def _old_db(settings, activities):
    """옛 구조(follow_ups·customer_needs 표 없음)의 DB 에 활동 기록을 JSON 으로 넣는다."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in NEW_TABLES])
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, username, name, password_hash, role, is_active) VALUES "
                          "(1, 'a', '김과장', '', 'user', 1), (2, 'b', '박', '', 'user', 1), (3, 'c', '박', '', 'user', 1), "
                          "(4, 'd', '이퇴사', '', 'user', 0)"))
        conn.execute(text("INSERT INTO customers (id, name, industry, contact, memo) VALUES (1, '한빛병원', '', '', ''), (2, '그린-테크', '', '', ''), (3, '그린 테크', '', '', ''), (4, '남도 물산', '', '', '')"))
        for aid, fu, ds in activities:
            conn.execute(text("INSERT INTO activities (id, kind, title, raw_text, summary_md, follow_ups, demand_signals) "
                              "VALUES (:i, 'meeting', 't', 'r', '', :f, :d)"),
                         {"i": aid, "f": json.dumps(fu, ensure_ascii=False), "d": json.dumps(ds, ensure_ascii=False)})
    engine.dispose()


FU1 = [{"task": "견적 송부", "owner": "김과장", "due": "2026-11-20", "done": False},
       {"task": " 계약서  검토 ", "owner": "박", "due": "", "done": True},
       {"task": "현장 방문", "owner": "이퇴사", "due": "2026-12-01", "done": False},
       {"task": "자료 정리", "owner": "누구", "due": "", "done": False}]
DS1 = [{"customer": "한빛병원", "content": "전기요금 절감 요청"},
       {"customer": "한빛 병원", "content": "공백·기호만 다른 이름"},
       {"customer": "그린테크", "content": "이름이 두 고객과 맞음"},
       {"customer": "남도", "content": "일부만 같은 이름은 연결하지 않음"},
       {"customer": "", "content": "고객 미상"}]


def _snapshot(settings):
    con = sqlite3.connect(settings.db_path)
    try:
        return (con.execute("SELECT id, follow_ups, demand_signals FROM activities ORDER BY id").fetchall(),
                con.execute("SELECT activity_id, position, task, owner, owner_user_id, due, done FROM follow_ups ORDER BY id").fetchall(),
                con.execute("SELECT activity_id, position, customer_name, customer_id, content FROM customer_needs ORDER BY id").fetchall())
    finally:
        con.close()


def _start(settings):
    app = create_app(settings)
    app.state.engine.dispose()


def test_copy_matches_json_and_links_only_exact_matches(settings, caplog):
    _old_db(settings, [(1, FU1, DS1), (2, [], [])])
    con = sqlite3.connect(settings.db_path)
    before = con.execute("SELECT id, follow_ups, demand_signals FROM activities ORDER BY id").fetchall()
    con.close()
    with caplog.at_level(logging.INFO, logger="app.migrate"):
        _start(settings)
    legacy, fus, needs = _snapshot(settings)
    assert legacy == before  # 옛 JSON 은 그대로
    assert fus == [(1, 0, "견적 송부", "김과장", 1, "2026-11-20", 1 - 1),
                   (1, 1, " 계약서  검토 ", "박", None, None, 1),  # 같은 이름의 활성 사용자가 둘: 연결하지 않음
                   (1, 2, "현장 방문", "이퇴사", None, "2026-12-01", 0),  # 비활성 사용자: 연결하지 않음
                   (1, 3, "자료 정리", "누구", None, None, 0)]
    assert needs == [(1, 0, "한빛병원", 1, "전기요금 절감 요청"),
                     (1, 1, "한빛 병원", 1, "공백·기호만 다른 이름"),
                     (1, 2, "그린테크", None, "이름이 두 고객과 맞음"),
                     (1, 3, "남도", None, "일부만 같은 이름은 연결하지 않음"),
                     (1, 4, "", None, "고객 미상")]
    assert len(list(settings.db_path.parent.glob("app.db.before-migrate-*"))) >= 1
    summary = [r.getMessage() for r in caplog.records if "후속 조치·고객 니즈 복사:" in r.getMessage()]
    assert len(summary) == 1 and "후속 조치 4줄" in summary[0] and "고객 니즈 5줄" in summary[0]
    assert "담당 연결 1, 미연결 3" in summary[0] and "고객 연결 2, 미연결 2" in summary[0]


def test_second_startup_adds_nothing(settings):
    _old_db(settings, [(1, FU1, DS1)])
    _start(settings)
    first = _snapshot(settings)
    backups = sorted(settings.db_path.parent.glob("app.db.before-migrate-*"))
    _start(settings)
    assert _snapshot(settings) == first
    assert sorted(settings.db_path.parent.glob("app.db.before-migrate-*")) == backups


def test_missing_flag_does_not_duplicate_rows(settings):
    _old_db(settings, [(1, FU1, DS1)])
    _start(settings)
    first = _snapshot(settings)
    con = sqlite3.connect(settings.db_path)
    con.execute("DELETE FROM app_settings WHERE key = ?", (ROWS_COPIED_KEY,))
    con.commit(); con.close()
    _start(settings)
    assert _snapshot(settings) == first


def test_malformed_rows_are_skipped_rest_copied(settings, caplog):
    bad_fu = ["문자열", 3, None, {"owner": "김과장"}, {"task": "  "}, {"task": 5}, {"task": "정상", "owner": ["x"], "due": "내일", "done": "예"}]
    bad_ds = [None, {"customer": "한빛병원"}, {"customer": 7, "content": "고객이 숫자"}, "x"]
    _old_db(settings, [(1, bad_fu, bad_ds), (2, FU1[:1], [])])
    con = sqlite3.connect(settings.db_path)
    con.execute("INSERT INTO activities (id, kind, title, raw_text, summary_md, follow_ups, demand_signals) VALUES (3, 'meeting', 't', 'r', '', 'not json', '{\"a\": 1}')")
    con.execute("INSERT INTO activities (id, kind, title, raw_text, summary_md, follow_ups, demand_signals) VALUES (4, 'meeting', 't', 'r', '', 'null', 'null')")
    con.commit(); con.close()
    with caplog.at_level(logging.WARNING, logger="app.migrate"):
        _start(settings)
    _, fus, needs = _snapshot(settings)
    assert [(f[0], f[2], f[3], f[5], f[6]) for f in fus] == [(1, "정상", "", None, 0), (2, "견적 송부", "김과장", "2026-11-20", 0)]
    assert needs == [(1, 0, "", None, "고객이 숫자")]
    assert len([r for r in caplog.records if "건너뜁니다" in r.getMessage()]) >= 8


def test_failure_in_the_middle_leaves_nothing_and_retries(settings):
    _old_db(settings, [(1, FU1, DS1)])
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)
    fail = {"on": True}

    @event.listens_for(engine, "before_cursor_execute")
    def _boom(conn, cursor, statement, parameters, context, executemany):
        if fail["on"] and statement.startswith("INSERT INTO app_settings"):
            raise RuntimeError("중간에 멈춤")

    with pytest.raises(Exception, match="중간에 멈춤"):
        copy_activity_json(engine, settings.db_path)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM follow_ups")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM customer_needs")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM app_settings")).scalar() == 0
    fail["on"] = False
    summary = copy_activity_json(engine, settings.db_path)
    assert summary["follow_ups"] == 4 and summary["needs"] == 5
    assert copy_activity_json(engine, settings.db_path) is None
    engine.dispose()


def test_fresh_database_makes_no_copy(app, settings):
    assert list(settings.db_path.parent.glob("app.db.before-migrate-*")) == []


# ---- 저장·완료 표시·지우기 ----

def _post(client, activity=None, **extra):
    data = {"raw_text": "원문", **extra}
    return client.post(f"/activities/{activity.id}" if activity else "/activities", data=data, follow_redirects=False)


def test_edit_keeps_row_ids_links_and_done_flags(user_client, db):
    c = Customer(name="한빛병원"); db.add(c); db.commit()
    make_user(db, "u2", name="김과장")
    _post(user_client, fu_id=["", ""], fu_task=["견적", "계약"], fu_owner=["김과장", ""], fu_due=["2026-11-20", ""],
          ds_id=[""], ds_customer=["한빛병원"], ds_content=["절감"])
    a = db.query(Activity).one()
    f1, f2 = a.follow_ups
    n1 = a.needs[0]
    assert f1.owner_user_id is not None and n1.customer_id == c.id
    ids = (f1.id, f2.id, n1.id)
    user_client.post(f"/follow-ups/{f2.id}/toggle")
    r = _post(user_client, a, fu_id=[str(f1.id), str(f2.id)], fu_task=["견적 (수정)", "계약"], fu_owner=["김과장", ""],
              fu_due=["2026-11-21", ""], fu_done=["1"], ds_id=[str(n1.id)], ds_customer=["한빛병원"], ds_content=["절감 (수정)"])
    assert r.status_code == 303
    db.expire_all()
    a = db.get(Activity, a.id)
    assert (a.follow_ups[0].id, a.follow_ups[1].id, a.needs[0].id) == ids
    assert [(f.task, f.due, f.done) for f in a.follow_ups] == [("견적 (수정)", date(2026, 11, 21), False), ("계약", None, True)]
    assert a.follow_ups[0].owner_user_id is not None and a.needs[0].customer_id == c.id and a.needs[0].content == "절감 (수정)"


def test_edit_deletes_removed_rows_adds_new_and_reorders(user_client, db):
    _post(user_client, fu_id=["", "", ""], fu_task=["가", "나", "다"], fu_owner=["", "", ""], fu_due=["", "", ""])
    a = db.query(Activity).one()
    ga, gb, gc = [f.id for f in a.follow_ups]
    _post(user_client, a, fu_id=[str(gc), "", str(ga)], fu_task=["다", "새로", "가"], fu_owner=["", "", ""], fu_due=["", "", ""])
    db.expire_all()
    rows = db.query(FollowUp).order_by(FollowUp.position).all()
    assert [(r.task, r.position) for r in rows] == [("다", 0), ("새로", 1), ("가", 2)]
    assert [r.id for r in rows][0] == gc and [r.id for r in rows][2] == ga and gb not in [r.id for r in rows]
    _post(user_client, a, fu_id=[str(gc)], fu_task=["다"], fu_owner=[""], fu_due=[""])
    db.expire_all()
    assert [r.id for r in db.query(FollowUp)] == [gc]


def test_link_cleared_when_text_no_longer_matches(user_client, db):
    c = Customer(name="한빛병원"); db.add(c); db.commit()
    u = make_user(db, "u2", name="김과장")
    _post(user_client, fu_id=[""], fu_task=["a"], fu_owner=["김과장"], fu_due=[""], ds_id=[""], ds_customer=["한빛병원"], ds_content=["x"])
    a = db.query(Activity).one()
    f, n = a.follow_ups[0], a.needs[0]
    assert (f.owner_user_id, n.customer_id) == (u.id, c.id)
    _post(user_client, a, fu_id=[str(f.id)], fu_task=["a"], fu_owner=["김"], fu_due=[""], ds_id=[str(n.id)], ds_customer=["한빛"], ds_content=["x"])
    db.expire_all()
    f, n = db.query(FollowUp).one(), db.query(CustomerNeed).one()
    assert (f.owner, f.owner_user_id, n.customer_name, n.customer_id) == ("김", None, "한빛", None)
    _post(user_client, a, fu_id=[str(f.id)], fu_task=["a"], fu_owner=["김과장"], fu_due=[""], ds_id=[str(n.id)], ds_customer=["한빛 병원"], ds_content=["x"])
    db.expire_all()
    assert (db.query(FollowUp).one().owner_user_id, db.query(CustomerNeed).one().customer_id) == (u.id, c.id)


def test_old_form_without_row_ids_matches_rows_by_position(user_client, db):
    _post(user_client, fu_id=["", ""], fu_task=["a", "b"], fu_owner=["", ""], fu_due=["", ""])
    a = db.query(Activity).one()
    ids = [f.id for f in a.follow_ids] if hasattr(a, "follow_ids") else [f.id for f in a.follow_ups]
    _post(user_client, a, fu_task=["a2", "b2", "c"], fu_owner=["", "", ""], fu_due=["", "", ""])  # fu_id 칸이 없는 옛 화면
    db.expire_all()
    rows = db.query(FollowUp).order_by(FollowUp.position).all()
    assert [r.task for r in rows] == ["a2", "b2", "c"] and [r.id for r in rows[:2]] == ids


def test_foreign_row_ids_are_ignored(user_client, db):
    _post(user_client, fu_id=[""], fu_task=["남의 것"], fu_owner=[""], fu_due=[""])
    other = db.query(FollowUp).one()
    _post(user_client, fu_id=[str(other.id)], fu_task=["내 것"], fu_owner=[""], fu_due=[""])
    db.expire_all()
    assert sorted(f.task for f in db.query(FollowUp)) == ["남의 것", "내 것"]


def test_form_shows_ids_and_linked_chip_and_datalist(user_client, db):
    c = Customer(name="한빛병원"); db.add(c); db.commit()
    _post(user_client, fu_id=[""], fu_task=["a"], fu_owner=[""], fu_due=[""], ds_id=["", ""], ds_customer=["한빛병원", "미등록"], ds_content=["x", "y"])
    a = db.query(Activity).one()
    page = user_client.get(f"/activities/{a.id}/edit").text
    assert f'name="fu_id" value="{a.follow_ups[0].id}"' in page and f'name="ds_id" value="{a.needs[1].id}"' in page
    assert page.count("need-linked") >= 1 and f'href="/customers/{c.id}"' in page and "연결됨" in page
    assert '<datalist id="ds-customer-list">' in page and '<option value="한빛병원">' in page
    detail = user_client.get(f"/activities/{a.id}").text
    assert detail.count(">연결됨<") == 1 and f'href="/customers/{c.id}"' in detail
    assert f'action="/follow-ups/{a.follow_ups[0].id}/toggle"' in detail


def test_toggle_by_id_and_by_old_index_url(user_client, db):
    a = Activity(raw_text="x", follow_ups=fu_rows(("a", "", "", False), ("b", "", "", False)))
    db.add(a); db.commit()
    f0, f1 = a.follow_ups
    r = user_client.post(f"/follow-ups/{f1.id}/toggle", headers={"referer": "http://testserver/"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "http://testserver/"
    db.expire_all(); assert [f.done for f in db.get(Activity, a.id).follow_ups] == [False, True]
    user_client.post(f"/activities/{a.id}/followups/0/toggle")  # 옛 순번 주소
    db.expire_all(); assert [f.done for f in db.get(Activity, a.id).follow_ups] == [True, True]
    assert user_client.post(f"/activities/{a.id}/followups/2/toggle").status_code == 404
    assert user_client.post("/follow-ups/99999/toggle").status_code == 404


def test_toggle_updates_mirror(user_client, db, settings):
    _post(user_client, fu_id=[""], fu_task=["자료 송부"], fu_owner=[""], fu_due=[""])
    a = db.query(Activity).one()
    path = settings.knowledge_dir / "activities" / str(date.today().year) / f"{a.id}.md"
    assert "- [ ] 자료 송부" in path.read_text(encoding="utf-8")
    user_client.post(f"/follow-ups/{a.follow_ups[0].id}/toggle")
    assert "- [x] 자료 송부" in path.read_text(encoding="utf-8")


def test_toggle_requires_login(client, db):
    a = Activity(raw_text="x", follow_ups=fu_rows(("a", "", "", False)))
    db.add(a); db.commit()
    r = client.post(f"/follow-ups/{a.follow_ups[0].id}/toggle", follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403) and r.headers.get("location", "").startswith("/login") or r.status_code in (401, 403)
    db.expire_all(); assert db.get(Activity, a.id).follow_ups[0].done is False


def test_deleting_activity_deletes_rows_and_customer_delete_keeps_them(user_client, admin_client, db):
    c = Customer(name="한빛병원"); db.add(c); db.commit()
    _post(user_client, fu_id=[""], fu_task=["a"], fu_owner=[""], fu_due=[""], ds_id=[""], ds_customer=["한빛병원"], ds_content=["x"])
    a = db.query(Activity).one()
    assert db.query(CustomerNeed).one().customer_id == c.id
    assert admin_client.post(f"/customers/{c.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    n = db.query(CustomerNeed).one()
    assert (n.customer_id, n.customer_name, n.content) == (None, "한빛병원", "x")
    assert ">연결됨<" not in user_client.get(f"/activities/{a.id}").text
    admin_client.post(f"/activities/{a.id}/delete")
    db.expire_all()
    assert db.query(FollowUp).count() == 0 and db.query(CustomerNeed).count() == 0


def test_mirror_shows_linked_customer(user_client, db, settings):
    c = Customer(name="한빛병원"); db.add(c); db.commit()
    _post(user_client, ds_id=["", ""], ds_customer=["한빛병원", "미등록"], ds_content=["절감", "문의"])
    a = db.query(Activity).one()
    text_ = (settings.knowledge_dir / "activities" / str(date.today().year) / f"{a.id}.md").read_text(encoding="utf-8")
    assert f"- 한빛병원 (한빛병원 (customers/{c.id}.md)): 절감" in text_ and "- 미등록: 문의" in text_
    user_client.post(f"/customers/{c.id}", data={"name": "한빛의료원"})
    text_ = (settings.knowledge_dir / "activities" / str(date.today().year) / f"{a.id}.md").read_text(encoding="utf-8")
    assert "한빛의료원 (customers/" in text_


def test_home_query_uses_table_rows_only(user_client, db):
    today = date.today()
    a = Activity(raw_text="x", title="회의", follow_ups=fu_rows(
        ("곧", "", (today + timedelta(days=3)).isoformat(), False), ("끝", "", today.isoformat(), True)))
    a.legacy_follow_ups = [{"task": "옛 JSON 에만 있음", "owner": "", "due": today.isoformat(), "done": False}]
    db.add(a); db.commit()
    page = user_client.get("/").text
    assert "곧" in page and "옛 JSON 에만 있음" not in page


def test_user_deactivation_keeps_owner_link(app, db):
    u = make_user(db, "u2", name="김과장")
    a = Activity(raw_text="x", follow_ups=fu_rows(("a", "김과장", "", False)))
    a.follow_ups[0].owner_user_id = u.id
    db.add(a); db.commit()
    u.is_active = False; db.commit(); db.expire_all()
    assert db.query(FollowUp).one().owner_user_id == u.id

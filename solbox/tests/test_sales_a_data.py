"""영업 A 단계 데이터: 팀·담당·새 칸·필드 이력·사유 코드·옛 DB 이관."""
import re
import sqlite3
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app import ontology
from app.db import Base, make_engine
from app.main import create_app
from app.models import AppSetting, Customer, Opportunity, OpportunityFieldChange, Team, User
from tests.conftest import make_user


def _world(db):
    kim = make_user(db, "kim", name="김영업")
    park = make_user(db, "park", name="박하늘")
    c = Customer(name="한빛물류")
    db.add(c)
    db.commit()
    db.info["user_id"] = kim.id
    return kim, park, c


def _changes(db, opp):
    return [(ch.field, ch.old_value, ch.new_value, ch.changed_by_id, ch.source)
            for ch in db.scalars(select(OpportunityFieldChange).where(OpportunityFieldChange.opportunity_id == opp.id).order_by(OpportunityFieldChange.id))]


def test_create_records_only_filled_tracked_fields(db):
    kim, park, c = _world(db)
    opp = Opportunity(name="태양광 PPA", customer_id=c.id, amount=800_000_000, owner_user_id=kim.id, memo="메모", created_by_id=kim.id)
    db.add(opp)
    db.commit()
    assert sorted(_changes(db, opp)) == [("amount", "", "800000000", kim.id, ""), ("owner_user_id", "", str(kim.id), kim.id, "")]


def test_update_records_old_and_new_text(db):
    kim, park, c = _world(db)
    opp = Opportunity(name="태양광 PPA", customer_id=c.id, amount=800_000_000, owner_user_id=kim.id)
    db.add(opp)
    db.commit()
    db.info["user_id"] = park.id
    opp.amount = 1_200_000_000
    opp.owner_user_id = park.id
    opp.expected_close = date(2026, 12, 20)
    opp.next_step = "수정 견적 회신"
    opp.next_step_due = date(2026, 11, 23)
    db.commit()
    rows = _changes(db, opp)[2:]
    assert sorted(rows) == sorted([
        ("amount", "800000000", "1200000000", park.id, ""),
        ("owner_user_id", str(kim.id), str(park.id), park.id, ""),
        ("expected_close", "", "2026-12-20", park.id, ""),
        ("next_step", "", "수정 견적 회신", park.id, ""),
        ("next_step_due", "", "2026-11-23", park.id, ""),
    ])


def test_same_value_and_untracked_fields_leave_no_history(db):
    kim, park, c = _world(db)
    opp = Opportunity(name="태양광 PPA", customer_id=c.id, amount=800_000_000)
    db.add(opp)
    db.commit()
    before = _changes(db, opp)
    opp.amount = 800_000_000
    opp.memo = "새 메모"
    opp.name = "새 이름"
    db.commit()
    assert _changes(db, opp) == before


def test_clearing_a_field_records_empty_new_value(db):
    kim, park, c = _world(db)
    opp = Opportunity(name="태양광 PPA", customer_id=c.id, next_step="견적")
    db.add(opp)
    db.commit()
    opp.next_step = ""
    db.commit()
    assert _changes(db, opp)[-1][:3] == ("next_step", "견적", "")


def test_deleting_opportunity_deletes_history(db):
    kim, park, c = _world(db)
    opp = Opportunity(name="태양광 PPA", customer_id=c.id, amount=1)
    db.add(opp)
    db.commit()
    db.delete(opp)
    db.commit()
    assert db.scalar(select(OpportunityFieldChange)) is None


def test_team_membership_and_owner_relationship(db):
    kim, park, c = _world(db)
    team = Team(name="영업1팀")
    db.add(team)
    db.commit()
    kim.team_id = team.id
    opp = Opportunity(name="x", customer_id=c.id, owner_user_id=kim.id)
    db.add(opp)
    db.commit()
    assert opp.owner.team.name == "영업1팀" and [u.username for u in team.members] == ["kim"]
    db.delete(team)
    db.commit()
    db.refresh(kim)
    assert kim.team_id is None


def test_reason_codes_and_labels():
    assert list(ontology.OPPORTUNITY_LOST_REASONS.items()) == [
        ("price", "가격"), ("competitor", "경쟁사 선택"), ("no_budget", "예산 없음·취소"), ("delayed", "시기 연기"),
        ("mismatch", "요구 불일치"), ("no_contact", "연락 끊김"), ("other", "기타")]
    assert list(ontology.OPPORTUNITY_WON_REASONS.items()) == [
        ("price", "가격 경쟁력"), ("tech", "기술·품질"), ("trust", "관계·신뢰"), ("reference", "레퍼런스"),
        ("business_type", "사업방식(임대·투자) 조건"), ("other", "기타")]
    assert ontology.close_reasons("lost") is ontology.OPPORTUNITY_LOST_REASONS
    assert ontology.close_reasons("won") is ontology.OPPORTUNITY_WON_REASONS
    assert ontology.close_reasons("hold") == {}
    assert ontology.OPPORTUNITY_CLOSED_STAGES == ("won", "lost")
    assert (ontology.OPPORTUNITY_QUIET_DAYS, ontology.CUSTOMER_QUIET_DAYS) == (30, 90)
    assert ontology.OPPORTUNITY_TRACKED_FIELDS == ("amount", "expected_close", "owner_user_id", "next_step", "next_step_due", "close_reason")
    assert [ontology.prop("opportunity", k) for k in ("amount", "expected_close", "owner", "next_step", "next_step_due", "close_reason", "close_note")] == [
        "예상 금액", "예상 수주일", "담당", "다음 할 일", "다음 할 일 날짜", "사유", "사유 메모"]
    assert ontology.label("team") == "팀" and ontology.prop("user", "team") == "팀"


# ---- 옛 DB ----

_NEW_OPP_COLS = ("amount", "expected_close", "owner_user_id", "next_step", "next_step_due", "close_reason", "close_note")


def _old_db(settings):
    """팀 표·필드 이력 표가 없고 users.team_id·영업기회 새 칸이 없는 DB. 영업기회 둘 (하나는 만든 사람 있음)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)
    rebuilt = {"users": ("team_id",), "opportunities": _NEW_OPP_COLS}
    with engine.begin() as conn:
        sqls = {t: conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = ?", (t,)).scalar() for t in rebuilt}
    engine.dispose()
    settings.db_path.unlink()
    for ext in ("-wal", "-shm"):
        Path(str(settings.db_path) + ext).unlink(missing_ok=True)
    engine = make_engine(settings.db_path)
    keep = [t for t in Base.metadata.sorted_tables if t.name not in ("teams", "opportunity_field_changes", *rebuilt)]
    with engine.begin() as conn:
        for table, cols in rebuilt.items():
            lines = [ln for ln in sqls[table].split("\n") if not any(re.search(rf"\b{c}\b", ln) for c in cols)]
            conn.exec_driver_sql(re.sub(r",\s*\n\)", "\n)", "\n".join(lines)))
    Base.metadata.create_all(engine, tables=keep)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, username, name, password_hash, role, is_active) VALUES (7, 'kim', '김영업', '', 'user', 1)")
        conn.exec_driver_sql("INSERT INTO customers (id, name, industry, contact, memo) VALUES (1, '한빛물류', '', '', '')")
        conn.exec_driver_sql("INSERT INTO opportunities (id, name, customer_id, stage, memo, business_types, created_by_id, updated_at) VALUES (1, '옛 건', 1, 'discovery', '', '', 7, '2026-09-01 10:00:00')")
        conn.exec_driver_sql("INSERT INTO opportunities (id, name, customer_id, stage, memo, business_types) VALUES (2, '만든 사람 모름', 1, 'discovery', '', '')")
    engine.dispose()


def test_old_db_gets_columns_tables_and_owner_backfill_once(settings):
    _old_db(settings)
    con = sqlite3.connect(settings.db_path)
    assert "amount" not in [r[1] for r in con.execute("PRAGMA table_info(opportunities)")]
    con.close()
    app = create_app(settings)
    try:
        con = sqlite3.connect(settings.db_path)
        cols = [r[1] for r in con.execute("PRAGMA table_info(opportunities)")]
        assert all(c in cols for c in _NEW_OPP_COLS)
        assert "team_id" in [r[1] for r in con.execute("PRAGMA table_info(users)")]
        for t in ("teams", "opportunity_field_changes"):
            assert con.execute("SELECT name FROM sqlite_master WHERE name=?", (t,)).fetchone() is not None, t
        assert con.execute("SELECT id, owner_user_id FROM opportunities ORDER BY id").fetchall() == [(1, 7), (2, None)]
        assert con.execute("SELECT updated_at FROM opportunities WHERE id = 1").fetchone()[0].startswith("2026-09-01 10:00:00")  # 이관은 고친 시각을 건드리지 않는다
        con.execute("UPDATE opportunities SET owner_user_id = NULL WHERE id = 1")
        con.commit()
        con.close()
    finally:
        app.state.engine.dispose()
    app = create_app(settings)  # 다시 시작해도 사용자가 비운 담당을 다시 채우지 않는다
    try:
        db = app.state.session_factory()
        assert db.get(Opportunity, 1).owner_user_id is None
        assert db.get(AppSetting, "opportunity_owner_backfill") is not None
        db.close()
    finally:
        app.state.engine.dispose()

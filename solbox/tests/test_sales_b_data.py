"""영업 B 단계 데이터: 확인 항목 목록, 확인 줄·이력, AI 출처·근거, 옛 DB."""
import re
import sqlite3
from pathlib import Path

from sqlalchemy import select

from app import ontology
from app.db import Base, make_engine
from app.main import create_app
from app.models import Customer, Opportunity, OpportunityCheck, OpportunityFieldChange, OpportunityInsight
from tests.conftest import make_user

CHECKLIST = {
    "discovery": [("discovery.need", "고객 니즈·문제 확인"), ("discovery.decision", "의사결정 구조(실무 담당·결재자) 파악"),
                  ("discovery.budget", "예산 범위 또는 투자 여력 파악"), ("discovery.timing", "도입 희망 시기 파악")],
    "proposing": [("proposing.site", "현장 조사·기술 검토 완료"), ("proposing.sent", "제안서 전달"),
                  ("proposing.competitor", "경쟁사·대안 파악"), ("proposing.feedback", "고객 검토 의견 받음")],
    "negotiating": [("negotiating.approver", "결재권자 승인 경로 확인"), ("negotiating.terms", "가격·조건 합의"),
                    ("negotiating.contract", "계약 형태(구축·임대·투자) 확정"), ("negotiating.schedule", "계약·착수 일정 합의")],
}


def test_checklist_is_the_confirmed_list():
    assert {k: [tuple(x) for x in v] for k, v in ontology.OPPORTUNITY_CHECKLIST.items()} == CHECKLIST
    assert list(ontology.OPPORTUNITY_CHECKLIST) == list(ontology.OPPORTUNITY_OPEN_STAGES)
    assert ontology.check_label("proposing.sent") == "제안서 전달" and ontology.check_label("nope") == "nope"
    assert len(ontology.OPPORTUNITY_CHECK_ITEMS) == 12


def _opp(db):
    kim = make_user(db, "kim", name="김영업")
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    o = Opportunity(name="건", customer_id=c.id)
    db.add(o)
    db.commit()
    db.info["user_id"] = kim.id
    return kim, o


def _hist(db, o):
    return [(h.field, h.old_value, h.new_value, h.source, h.evidence) for h in
            db.scalars(select(OpportunityFieldChange).where(OpportunityFieldChange.opportunity_id == o.id).order_by(OpportunityFieldChange.id))]


def test_check_rows_write_history_and_touch_opportunity(db):
    kim, o = _opp(db)
    before = o.updated_at
    db.add(OpportunityCheck(opportunity_id=o.id, item="discovery.need", checked_by_id=kim.id))
    db.commit()
    db.refresh(o)
    assert o.updated_at > before
    row = db.scalar(select(OpportunityCheck))
    db.delete(row)
    db.commit()
    assert _hist(db, o)[-2:] == [("check", "", "discovery.need", "", ""), ("check", "discovery.need", "", "", "")]


def test_ai_source_and_evidence_go_into_history(db):
    kim, o = _opp(db)
    db.info["change_source"], db.info["change_evidence"] = "ai", "총 12억 정도로 보고 있습니다"
    o.amount = 1_200_000_000
    db.add(OpportunityCheck(opportunity_id=o.id, item="proposing.sent", source="ai", evidence="제안서 송부"))
    db.commit()
    rows = _hist(db, o)
    assert ("amount", "", "1200000000", "ai", "총 12억 정도로 보고 있습니다") in rows
    assert ("check", "", "proposing.sent", "ai", "제안서 송부") in rows


def test_insight_and_checks_are_deleted_with_opportunity(db):
    kim, o = _opp(db)
    db.add(OpportunityInsight(opportunity_id=o.id, briefing={"situation": "x"}, suggestions=[], basis={}))
    db.add(OpportunityCheck(opportunity_id=o.id, item="discovery.need"))
    db.commit()
    db.delete(o)
    db.commit()
    assert db.scalar(select(OpportunityInsight)) is None and db.scalar(select(OpportunityCheck)) is None


def test_old_db_gets_new_tables_and_evidence_column(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'opportunity_field_changes'").scalar()
    engine.dispose()
    settings.db_path.unlink()
    for ext in ("-wal", "-shm"):
        Path(str(settings.db_path) + ext).unlink(missing_ok=True)
    engine = make_engine(settings.db_path)
    keep = [t for t in Base.metadata.sorted_tables if t.name not in ("opportunity_checks", "opportunity_insights", "opportunity_field_changes")]
    Base.metadata.create_all(engine, tables=keep)
    with engine.begin() as conn:
        lines = [ln for ln in sql.split("\n") if not re.search(r"\bevidence\b", ln)]
        conn.exec_driver_sql(re.sub(r",\s*\n\)", "\n)", "\n".join(lines)))
    engine.dispose()
    app = create_app(settings)
    try:
        con = sqlite3.connect(settings.db_path)
        assert "evidence" in [r[1] for r in con.execute("PRAGMA table_info(opportunity_field_changes)")]
        for t in ("opportunity_checks", "opportunity_insights"):
            assert con.execute("SELECT name FROM sqlite_master WHERE name=?", (t,)).fetchone() is not None
        con.close()
    finally:
        app.state.engine.dispose()

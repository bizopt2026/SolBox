"""영업 B 단계: AI 제안 반영·무시."""
from datetime import date

from sqlalchemy import select

from app import notices, opportunity_ai as oai
from app.models import Activity, Customer, Opportunity, OpportunityCheck, OpportunityFieldChange, OpportunityInsight
from tests.conftest import nloc


def _world(db, stage="proposing"):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    o = Opportunity(name="태양광", customer_id=c.id, stage=stage, amount=800_000_000, next_step="현장 실사", next_step_due=date(2026, 11, 28))
    db.add(o)
    db.commit()
    a = Activity(kind="meeting", date=date(2026, 11, 20), title="미팅", raw_text="r")
    a.opportunities = [o]
    db.add(a)
    db.commit()
    sugs = [
        {"id": 1, "kind": "field", "field": "amount", "item": None, "value": 1_200_000_000, "due": None, "evidence": "총 12억", "activity_id": a.id, "verified": True},
        {"id": 2, "kind": "field", "field": "expected_close", "item": None, "value": "2026-12-20", "due": None, "evidence": "연내", "activity_id": a.id, "verified": False},
        {"id": 3, "kind": "field", "field": "next_step", "item": None, "value": "단가 회신", "due": None, "evidence": "단가", "activity_id": a.id, "verified": True},
        {"id": 4, "kind": "stage", "field": None, "item": None, "value": "negotiating", "due": None, "evidence": "조정", "activity_id": a.id, "verified": True},
        {"id": 5, "kind": "check", "field": None, "item": "proposing.competitor", "value": None, "due": None, "evidence": "A사", "activity_id": a.id, "verified": True},
    ]
    for s in sugs:
        s.update(state="pending", decided_by=None, decided_at=None)
    ins = oai.save_insight(db, o, None, {"situation": "x", "risks": [], "next_actions": [], "outlook": ""}, sugs, oai.basis_of(o, {a.id: "r"}))
    db.commit()
    return o, ins


def _post(client, o, ins, sid, what="apply"):
    return client.post(f"/opportunities/{o.id}/ai/{ins.id}/{sid}/{what}", follow_redirects=False)


def _state(db, ins, sid):
    db.expire_all()
    return next(s for s in db.get(OpportunityInsight, ins.id).suggestions if s["id"] == sid)


def _ai_rows(db, o):
    return [(h.field, h.new_value, h.evidence) for h in db.scalars(select(OpportunityFieldChange).where(
        OpportunityFieldChange.opportunity_id == o.id, OpportunityFieldChange.source == "ai").order_by(OpportunityFieldChange.id))]


def test_apply_fields_with_ai_history(user_client, db):
    o, ins = _world(db)
    for sid in (1, 2, 3):
        r = _post(user_client, o, ins, sid)
        assert nloc(r) == f"/opportunities/{o.id}?notice=ai_applied", nloc(r)
    db.expire_all()
    f = db.get(Opportunity, o.id)
    assert (f.amount, f.expected_close, f.next_step, f.next_step_due) == (1_200_000_000, date(2026, 12, 20), "단가 회신", None)  # 날짜 없는 제안은 옛 기한을 비운다 (리뷰 반영)
    assert _ai_rows(db, o) == [("amount", "1200000000", "총 12억"), ("expected_close", "2026-12-20", "연내"), ("next_step", "단가 회신", "단가")]
    s = _state(db, ins, 1)
    assert s["state"] == "applied" and s["decided_by"] is not None and s["decided_at"]


def test_apply_stage_reports_leftovers_and_check(user_client, db):
    o, ins = _world(db)
    r = _post(user_client, o, ins, 4)
    assert nloc(r) == f"/opportunities/{o.id}?notice=ai_stage_open.proposing.negotiating.4"
    assert notices.lookup("ai_stage_open.proposing.negotiating.4").text.startswith("AI 제안대로 단계를 '협상'으로 바꿨습니다.")
    r = _post(user_client, o, ins, 5)
    assert nloc(r) == f"/opportunities/{o.id}?notice=ai_applied"
    db.expire_all()
    assert db.get(Opportunity, o.id).stage == "negotiating"
    row = db.scalar(select(OpportunityCheck))
    assert (row.item, row.source, row.evidence) == ("proposing.competitor", "ai", "A사")


def test_dismiss_keeps_value(user_client, db):
    o, ins = _world(db)
    r = _post(user_client, o, ins, 1, "dismiss")
    assert nloc(r) == f"/opportunities/{o.id}?notice=ai_dismissed"
    db.expire_all()
    assert db.get(Opportunity, o.id).amount == 800_000_000 and _state(db, ins, 1)["state"] == "dismissed"


def test_double_apply_is_stale_and_history_once(user_client, db):
    o, ins = _world(db)
    _post(user_client, o, ins, 1)
    r = _post(user_client, o, ins, 1)
    assert nloc(r) == f"/opportunities/{o.id}?notice=ai_stale"
    assert len(_ai_rows(db, o)) == 1


def test_old_insight_is_stale(user_client, db):
    o, ins = _world(db)
    oai.save_insight(db, db.get(Opportunity, o.id), None, {"situation": "새"}, [], {})
    db.commit()
    assert nloc(_post(user_client, o, ins, 1)) == f"/opportunities/{o.id}?notice=ai_stale"
    assert user_client.post(f"/opportunities/{o.id}/ai/999/1/apply", follow_redirects=False).status_code == 303
    assert user_client.post(f"/opportunities/{o.id}/ai/{ins.id}/1/nope", follow_redirects=False).status_code in (404, 405)


def test_changed_after_analysis_is_flagged(user_client, db):
    o, ins = _world(db)
    o.amount = 900_000_000  # 분석 뒤 사람이 고쳤다
    db.commit()
    assert nloc(_post(user_client, o, ins, 1)) == f"/opportunities/{o.id}?notice=ai_applied_changed"
    assert nloc(_post(user_client, o, ins, 2)) == f"/opportunities/{o.id}?notice=ai_applied"  # 다른 칸은 따지지 않는다


def test_already_checked_item_closes_without_change(user_client, db):
    o, ins = _world(db)
    oai.set_check(db, o, "proposing.competitor", True, None)
    db.commit()
    assert nloc(_post(user_client, o, ins, 5)) == f"/opportunities/{o.id}?notice=ai_applied"
    assert _state(db, ins, 5)["state"] == "applied" and db.query(OpportunityCheck).count() == 1

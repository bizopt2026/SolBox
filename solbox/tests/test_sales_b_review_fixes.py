"""영업 B 최종 리뷰에서 고친 것."""
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import opportunity_ai as oai
from app.ai.queue import enqueue, process_next
from app.models import Activity, AIJob, Customer, Opportunity, OpportunityStageChange
from tests.conftest import nloc
from tests.test_sales_b_apply import _world as apply_world, _post


def _opp_with_activity(db, stage="proposing"):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    o = Opportunity(name="건", customer_id=c.id, stage=stage)
    db.add(o)
    db.commit()
    a = Activity(kind="meeting", date=date(2026, 11, 1), title="t", raw_text="r")
    a.opportunities = [o]
    db.add(a)
    db.commit()
    return o


def test_running_review_does_not_block_new_request(user_client, db):
    o = _opp_with_activity(db)
    job = enqueue(db, oai.REVIEW_KIND, {"opportunity_id": o.id}, None)
    job.status = "running"
    db.commit()
    made = oai.request_review(db, [o.id], None, user_client)
    assert len(made) == 1  # 도는 중인 분석은 새 메모를 못 읽으니 하나 더 기다리게 한다
    assert oai.request_review(db, [o.id], None, user_client) == []  # 대기 중이면 더 넣지 않는다


def test_enqueue_lock_failure_does_not_hide_saved_activity(user_client, db, monkeypatch):
    o = _opp_with_activity(db)

    def busy(*a, **k):
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr("app.opportunity_ai.request_review", busy)
    r = user_client.post("/activities", data={"kind": "meeting", "date": "2026-11-20", "title": "잠금", "raw_text": "r", "summary_md": "",
                                              "opportunity_ids": [str(o.id)]}, follow_redirects=False)
    assert r.status_code == 303 and "notice=created" in r.headers["location"]
    assert db.scalar(select(Activity).where(Activity.title == "잠금")) is not None


def test_ai_stage_change_is_marked_in_history(user_client, db):
    o, ins = apply_world(db)
    _post(user_client, o, ins, 4)
    db.expire_all()
    ch = db.scalars(select(OpportunityStageChange).order_by(OpportunityStageChange.id.desc())).first()
    assert (ch.to_stage, ch.source, ch.evidence) == ("negotiating", "ai", "조정")
    hist = user_client.get(f"/opportunities/{o.id}").text.split('id="h-hist"')[1].split("</section>")[0]
    assert "AI 제안" in hist and "조정" in hist


def test_closed_opportunity_hides_and_refuses_suggestions(user_client, db):
    o, ins = apply_world(db)
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": "trust"})
    assert 'id="ai-band"' not in user_client.get(f"/opportunities/{o.id}").text
    r = _post(user_client, o, ins, 4)
    assert nloc(r) == f"/opportunities/{o.id}?notice=ai_stale"
    db.expire_all()
    f = db.get(Opportunity, o.id)
    assert (f.stage, f.close_reason) == ("won", "trust")
    r = user_client.post(f"/opportunities/{o.id}/ai/review", follow_redirects=False)
    assert nloc(r) == f"/opportunities/{o.id}?notice=ai_not_open"


def test_next_step_without_due_clears_old_due(user_client, db):
    o, ins = apply_world(db)
    _post(user_client, o, ins, 3)
    db.expire_all()
    f = db.get(Opportunity, o.id)
    assert (f.next_step, f.next_step_due) == ("단가 회신", None)


def test_huge_amount_is_dropped(db):
    from app.ai.opportunity_tasks import ReviewOut

    o = _opp_with_activity(db)
    out = ReviewOut.model_validate({"fields": {"amount": {"value": 10 ** 30, "evidence": "x"}}})
    assert oai.clean_suggestions(out, o, {}, date.today()) == []


def test_empty_activity_does_not_stop_reading_older_ones(db):
    o = _opp_with_activity(db)
    empty = Activity(kind="meeting", date=date(2026, 11, 25), title="빈", raw_text="", summary_md="")
    old = Activity(kind="meeting", date=date(2026, 10, 1), title="옛", raw_text="옛 회의 내용")
    empty.opportunities = [o]
    old.opportunities = [o]
    db.add_all([empty, old])
    db.commit()
    db.refresh(o)
    prompt, texts = oai.build_prompt(db, o, date.today())
    assert old.id in texts and empty.id not in texts


def test_concurrent_same_check_does_not_500(user_client, db, monkeypatch):
    o = _opp_with_activity(db)
    from sqlalchemy.exc import IntegrityError

    def dup(*a, **k):
        raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr("app.opportunity_ai.set_check", dup)
    r = user_client.post(f"/opportunities/{o.id}/checks", data={"item": "proposing.sent", "checked": "1"}, follow_redirects=False)
    assert r.status_code == 303

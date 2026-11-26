"""영업 C 최종 리뷰에서 고친 것."""
import json
from datetime import date, datetime, timedelta

from sqlalchemy import event, select

from app import opportunities as opps, pipeline_stats as ps
from app.ai import pipeline_tasks
from app.ai.queue import enqueue
from app.models import AIJob, Customer, Opportunity, OpportunityStageChange, User


def _count(app, fn):
    n = []
    hook = lambda *a, **k: n.append(1)  # noqa: E731
    event.listen(app.state.engine, "before_cursor_execute", hook)
    try:
        fn()
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    return len(n)


def _closed(db, c, n):
    for i in range(n):
        o = Opportunity(name=f"닫힘{i}", customer_id=c.id, stage="discovery")
        db.add(o)
        db.commit()
        o.stage, o.close_reason = "lost", "other"
        db.commit()


def test_board_query_count_is_flat(app, user_client, db):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    _closed(db, c, 2)
    user_client.get("/opportunities?view=board")
    small = _count(app, lambda: user_client.get("/opportunities?view=board"))
    _closed(db, c, 15)
    assert _count(app, lambda: user_client.get("/opportunities?view=board")) == small


def test_hash_does_not_change_just_because_a_day_passes(db):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    db.add(Opportunity(name="진행", customer_id=c.id, stage="discovery", next_step=""))
    db.commit()
    today = date.today()
    assert ps.compute(db, "lq", None, today)["numbers_hash"] == ps.compute(db, "lq", None, today + timedelta(days=1))["numbers_hash"] \
        or ps.period_range("lq", today) != ps.period_range("lq", today + timedelta(days=1))


def test_board_keeps_customer_filter():
    assert opps.board_url("customer_id=3&who=mine") == "/opportunities?view=board&customer_id=3&who=mine"


def test_failed_interpretation_is_reported(user_client, db):
    job = enqueue(db, pipeline_tasks.JOB_KIND, {"period": "90d", "team": ""}, None, 0)
    job.status, job.error = "failed", "x"
    db.commit()
    card = user_client.get("/opportunities/analysis").text.split('id="pipeline-ai"')[1].split("</section>")[0]
    assert "지난 AI 해석이 실패했어요" in card


def test_find_jobs_sees_older_pending_after_newer_done(db):
    old = enqueue(db, pipeline_tasks.JOB_KIND, {"period": "90d", "team": ""}, None, 0)
    new = enqueue(db, pipeline_tasks.JOB_KIND, {"period": "90d", "team": ""}, None, 0)
    new.status, new.result = "done", json.dumps({"summary": "x"})
    db.commit()
    running, done = pipeline_tasks.find_jobs(db, "90d", "")
    assert running is not None and running.id == old.id and done["summary"] == "x"


def test_team_dwell_keys_are_stage_names_in_prompt_numbers(db):
    stats = {"tiles": {}, "stages": [], "months": [], "reasons": {}, "excluded": 0,
             "teams": [{"name": "영업1팀", "dwell": {"discovery": 3.0, "proposing": None, "negotiating": None}}]}
    text = pipeline_tasks._numbers(stats)
    assert "discovery" not in text and "발굴" in text


def test_recent_closes_window_is_thirty_days(db):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    o = Opportunity(name="닫힘", customer_id=c.id, stage="discovery")
    db.add(o)
    db.commit()
    o.stage = "lost"
    db.commit()
    ch = db.scalars(select(OpportunityStageChange).where(OpportunityStageChange.to_stage == "lost")).one()
    today = date.today()
    ch.changed_at = datetime.combine(today - timedelta(days=30), datetime.min.time()) + timedelta(hours=12)
    db.commit()
    db.expire_all()
    assert opps.recent_closes([db.get(Opportunity, o.id)], today) == []

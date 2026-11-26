"""영업 B 단계: 활동 기록을 저장하면 연결된 진행 중 영업기회의 AI 분석을 자동으로 요청, [다시 분석], 진행 상자."""
from datetime import date

import pytest
from sqlalchemy import select

from app import opportunity_ai as oai
from app.models import Activity, AIJob, Customer, Opportunity
from tests.conftest import nloc


def _world(db, stage="proposing"):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    o = Opportunity(name="태양광", customer_id=c.id, stage=stage)
    db.add(o)
    db.commit()
    return c, o


def _jobs(db):
    db.expire_all()
    return [(j.kind, j.input, j.priority) for j in db.scalars(select(AIJob).order_by(AIJob.id))]


def _save_activity(client, o, **extra):
    data = {"kind": "meeting", "date": "2026-11-20", "title": "2차 미팅", "raw_text": "총 12억 정도로 보고 있습니다", "summary_md": "",
            "opportunity_ids": [str(o.id)], **extra}
    r = client.post("/activities", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r


def test_saving_linked_activity_queues_one_review(user_client, db):
    c, o = _world(db)
    _save_activity(user_client, o)
    assert _jobs(db) == [(oai.REVIEW_KIND, {"opportunity_id": o.id}, oai.REVIEW_PRIORITY)]
    _save_activity(user_client, o)  # 대기 중이면 하나만
    assert len(_jobs(db)) == 1


def test_editing_activity_queues_review(user_client, db):
    c, o = _world(db)
    a = Activity(kind="meeting", date=date(2026, 11, 1), title="t", raw_text="r")
    a.opportunities = [o]
    db.add(a)
    db.commit()
    from app.guards import rev_of

    r = user_client.post(f"/activities/{a.id}", data={"kind": "meeting", "date": "2026-11-01", "title": "t", "raw_text": "고침", "summary_md": "",
                                                       "opportunity_ids": [str(o.id)], "_rev": rev_of(a)}, follow_redirects=False)
    assert r.status_code == 303
    assert [k for k, _, _ in _jobs(db)] == [oai.REVIEW_KIND]


def test_raw_save_with_prefilled_opportunity_queues_review(user_client, db):
    c, o = _world(db)
    r = user_client.post("/activities/raw", data={"raw_text": "회의 메모", "opportunity_id": str(o.id)}, follow_redirects=False)
    assert r.status_code == 303
    assert [k for k, _, _ in _jobs(db)] == [oai.REVIEW_KIND]


def test_closed_or_hold_opportunity_is_not_reviewed(user_client, db):
    for stage in ("won", "lost", "hold"):
        c, o = _world(db, stage)
        _save_activity(user_client, o)
    assert _jobs(db) == []


def test_activity_without_opportunity_queues_nothing(user_client, db):
    r = user_client.post("/activities", data={"kind": "meeting", "date": "2026-11-20", "title": "t", "raw_text": "r", "summary_md": ""}, follow_redirects=False)
    assert r.status_code == 303 and _jobs(db) == []


@pytest.mark.ai_gate
def test_ai_off_saves_activity_without_job(user_client, db):
    c, o = _world(db)
    _save_activity(user_client, o)
    assert db.scalar(select(Activity)) is not None and _jobs(db) == []
    r = user_client.post(f"/opportunities/{o.id}/ai/review", follow_redirects=False)
    assert "ai_off" in r.headers["location"]


def test_manual_review_button(user_client, db):
    c, o = _world(db)
    r = user_client.post(f"/opportunities/{o.id}/ai/review", follow_redirects=False)
    assert nloc(r) == f"/opportunities/{o.id}?notice=ai_no_activity"  # 읽을 활동 기록이 없다
    a = Activity(kind="meeting", date=date(2026, 11, 1), title="t", raw_text="r")
    a.opportunities = [o]
    db.add(a)
    db.commit()
    r = user_client.post(f"/opportunities/{o.id}/ai/review", follow_redirects=False)
    assert nloc(r) == f"/opportunities/{o.id}?notice=ai_review_started"
    r = user_client.post(f"/opportunities/{o.id}/ai/review", follow_redirects=False)
    assert nloc(r) == f"/opportunities/{o.id}?notice=ai_review_waiting"
    assert [(k, p) for k, _, p in _jobs(db)] == [(oai.REVIEW_KIND, 0)]  # 사람이 누른 것은 앞으로


def test_progress_fragment_and_refresh(user_client, db, app):
    app.state.settings.ai_worker_enabled = True  # 화면만 "처리기 켜짐" 으로 그린다 (작업자 스레드는 시작하지 않는다)
    c, o = _world(db)
    _save_activity(user_client, o)
    r = user_client.get(f"/opportunities/{o.id}/ai/progress")
    assert r.status_code == 200 and "차례를 기다리고 있어요" in r.text and 'hx-get="/opportunities/%d/ai/progress"' % o.id in r.text
    job = db.scalar(select(AIJob))
    job.status = "done"
    db.commit()
    r = user_client.get(f"/opportunities/{o.id}/ai/progress")
    assert r.headers.get("HX-Refresh") == "true"
    page = user_client.get(f"/opportunities/{o.id}").text
    assert 'action="/opportunities/%d/ai/review"' % o.id in page

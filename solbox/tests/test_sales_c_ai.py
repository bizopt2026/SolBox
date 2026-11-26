"""영업 C 단계: 분석 화면의 AI 해석 (작업 pipeline_insight)."""
import json
from datetime import datetime

import pytest
from sqlalchemy import select

from app.ai.queue import process_next
from app.models import AIJob, Customer, Opportunity, Team
from tests.conftest import nloc

ANSWER = {"summary": "수주율은 50% 입니다.", "highlights": ["실패 1건은 경쟁사 선택"], "causes": ["가격 경쟁"], "actions": ["협상 30일 넘은 건 점검"]}


def _world(db):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    for i, (stage, reason) in enumerate((("won", "trust"), ("lost", "competitor"))):
        o = Opportunity(name=f"건{i}", customer_id=c.id, stage="discovery", amount=100_000_000)
        db.add(o)
        db.commit()
        o.stage, o.close_reason = stage, reason
        db.commit()


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def test_start_runs_and_shows_interpretation(app, user_client, db, settings, fake_claude):
    _world(db)
    r = user_client.post("/opportunities/analysis/ai", data={"period": "q", "team": ""}, follow_redirects=False)
    assert nloc(r) == "/opportunities/analysis?period=q&notice=ai_insight_started"
    r = user_client.post("/opportunities/analysis/ai", data={"period": "q", "team": ""}, follow_redirects=False)
    assert "notice=ai_insight_waiting" in nloc(r)
    page = user_client.get("/opportunities/analysis?period=q").text
    assert 'id="pipeline-ai-job"' in page
    fake_claude.set(mode="ok", result=json.dumps(ANSWER))
    assert _run(app, settings)
    sent = fake_claude.calls()[-1]
    assert sent["argv"][sent["argv"].index("--tools") + 1] == ""
    assert '"win_rate": 0.5' in sent["stdin"] and "지어내지" in sent["stdin"]
    page = user_client.get("/opportunities/analysis?period=q").text
    card = page.split('id="pipeline-ai"')[1].split("</section>")[0]
    assert "수주율은 50% 입니다." in card and "협상 30일 넘은 건 점검" in card and "AI 초안" in card and "숫자가 바뀌었" not in card
    assert "수주율은 50% 입니다." not in user_client.get("/opportunities/analysis?period=y").text  # 다른 기간에는 보이지 않는다


def test_changed_numbers_are_flagged(app, user_client, db, settings, fake_claude):
    _world(db)
    user_client.post("/opportunities/analysis/ai", data={"period": "90d", "team": ""})
    fake_claude.set(mode="ok", result=json.dumps(ANSWER))
    _run(app, settings)
    c = db.scalar(select(Customer))
    o = Opportunity(name="새 수주", customer_id=c.id, stage="discovery")
    db.add(o)
    db.commit()
    o.stage, o.close_reason = "won", "price"
    db.commit()
    card = user_client.get("/opportunities/analysis").text.split('id="pipeline-ai"')[1].split("</section>")[0]
    assert "숫자가 바뀌었어요" in card


def test_team_param_is_kept_and_validated(user_client, db):
    t = Team(name="영업1팀")
    db.add(t)
    db.commit()
    r = user_client.post("/opportunities/analysis/ai", data={"period": "nope", "team": str(t.id)}, follow_redirects=False)
    assert nloc(r) == f"/opportunities/analysis?team={t.id}&notice=ai_insight_started"
    job = db.scalar(select(AIJob))
    assert job.kind == "pipeline_insight" and job.input["period"] == "90d" and job.input["team"] == str(t.id)
    r = user_client.post("/opportunities/analysis/ai", data={"period": "90d", "team": "999"}, follow_redirects=False)
    assert nloc(r) == "/opportunities/analysis?notice=ai_insight_started"  # 없는 팀은 전체


@pytest.mark.ai_gate
def test_ai_off(user_client, db):
    r = user_client.post("/opportunities/analysis/ai", data={"period": "90d"}, headers={"referer": "http://testserver/opportunities/analysis"}, follow_redirects=False)
    assert "notice=ai_off" in r.headers["location"]
    assert "disabled" in user_client.get("/opportunities/analysis").text.split('id="pipeline-ai"')[1].split("</section>")[0]

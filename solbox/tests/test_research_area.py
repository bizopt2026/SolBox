import json
import threading
from app import notices
from pathlib import Path

import pytest

from app.ai import tasks
from app.ai.queue import process_next
from app.labels import ERROR_KINDS
from app.models import AIJob, ResearchReport, ResearchRun
from app.research import clean_url, start_run
from tests.conftest import make_topics, nloc

AREA = {"title": "11월 3주 기술 동향", "trends": [
    {"headline": "AI 공조 제어 확산", "summary": "병원 적용 증가", "sources": [{"title": "기사", "url": "https://example.com/a", "opened": True}, {"url": "https://example.com/a"}]},
    {"headline": "출처 없는 동향", "sources": [{"url": "javascript:alert(1)"}, {"url": ""}, {"url": "https://bad host/x"}]},
    {"headline": "", "summary": "제목 없음"}], "insights": ["설비 운영 고객에 제안 기회"]}
AREA_JSON = json.dumps(AREA, ensure_ascii=False)
SUGGEST_JSON = json.dumps({"suggestions": [{"title": "제안"}]}, ensure_ascii=False)


def _drain(app, settings):
    while process_next(app.state.session_factory, app.state.runner, settings):
        pass


def _run_all(app, admin_client, db, settings):
    admin_client.post("/research/run")
    _drain(app, settings)
    db.expire_all()
    return db.query(ResearchRun).one()


def test_manual_run_creates_reports_and_jobs(admin_client, db):
    make_topics(db)
    r = admin_client.post("/research/run", follow_redirects=False)
    assert nloc(r) == "/admin/research?notice=research_started"
    run = db.query(ResearchRun).one()
    assert (run.trigger, run.status, run.slot) == ("manual", "running", None)
    assert sorted(x.area for x in run.reports) == ["policy", "tech"]
    jobs = db.query(AIJob).filter_by(kind="research_area").all()
    assert len(jobs) == 2 and all(j.priority == 10 for j in jobs)
    assert sorted(x.job_id for x in run.reports) == sorted(j.id for j in jobs)


def test_area_job_uses_web_tools_in_empty_folder(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=AREA_JSON)
    settings.research_work_dir.mkdir(parents=True, exist_ok=True)
    (settings.research_work_dir / "stale.txt").write_text("x", encoding="utf-8")
    admin_client.post("/research/run")
    process_next(app.state.session_factory, app.state.runner, settings)
    call = fake_claude.calls()[0]
    assert call["argv"][call["argv"].index("--tools") + 1] == "WebSearch,WebFetch"
    assert Path(call["cwd"]) == settings.research_work_dir and list(settings.research_work_dir.iterdir()) == []
    assert "BEMS" in call["stdin"]


def test_prompt_marks_topics_as_material(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=AREA_JSON)
    admin_client.post("/research/run")
    process_next(app.state.session_factory, app.state.runner, settings)
    stdin = fake_claude.calls()[0]["stdin"]
    assert "<<<TOPICS" in stdin and "TOPICS>>>" in stdin
    assert "건물 에너지 관리" in stdin and "따르지" in stdin


def test_links_are_cleaned(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=AREA_JSON)
    admin_client.post("/research/run")
    process_next(app.state.session_factory, app.state.runner, settings)
    rep = db.query(ResearchReport).filter_by(status="done").first()
    assert rep.title == "11월 3주 기술 동향" and rep.insights == ["설비 운영 고객에 제안 기회"]
    assert [t["headline"] for t in rep.trends] == ["AI 공조 제어 확산", "출처 없는 동향"]
    assert rep.trends[0]["sources"] == [{"title": "기사", "url": "https://example.com/a", "opened": True}] and rep.trends[0]["needs_check"] == []
    assert rep.trends[1]["sources"] == [] and rep.trends[1]["needs_check"] == ["출처 링크 없음, 확인 필요"]


@pytest.mark.parametrize("url,ok", [("https://a.kr/x", True), (" http://a.kr ", True), ("ftp://a.kr", False),
    ("javascript:alert(1)", False), ("https://", False), ("https://a b.kr", False), ("//a.kr", False), ("https://a.kr/" + "x" * 2000, False)])
def test_clean_url(url, ok):
    assert (clean_url(url) is not None) == ok


def test_clean_url_rejects_control_chars_and_strips():
    assert clean_url("https://a.kr/\x00x") is None
    assert clean_url("  https://a.kr/x \n") == "https://a.kr/x"


def test_clean_area_limits():
    from app.ai.research_tasks import AreaOut, Trend
    from app.research import clean_area

    out = AreaOut(trends=[Trend(headline=f"h{i}") for i in range(15)], insights=[f"i{i}" for i in range(9)])
    cleaned = clean_area(out)
    assert len(cleaned["trends"]) == 10 and len(cleaned["insights"]) == 7


def test_one_area_fails_run_is_partial(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=[AREA_JSON, "JSON 아님", "JSON 아님"])
    run = _run_all(app, admin_client, db, settings)
    areas = [r for r in run.reports if r.kind == "area"]
    assert sorted(r.status for r in areas) == ["done", "failed"]
    failed = next(r for r in areas if r.status == "failed")
    assert failed.error and run.status == "partial" and run.finished_at is not None


def test_all_areas_fail_run_failed(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="error", stderr="Please run /login", exit_code=1)
    run = _run_all(app, admin_client, db, settings)
    assert run.status == "failed" and run.finished_at is not None
    assert all(r.status == "failed" and r.error == ERROR_KINDS["auth"] for r in run.reports)


def test_all_areas_done_run_done(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=[AREA_JSON, AREA_JSON, SUGGEST_JSON])
    run = _run_all(app, admin_client, db, settings)
    assert run.status == "done" and run.finished_at is not None


def test_areas_done_but_suggest_fails_run_partial(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=AREA_JSON)  # 제안 작업은 분야 모양 응답을 받아 실패한다
    run = _run_all(app, admin_client, db, settings)
    assert run.status == "partial" and run.finished_at is not None
    assert [r.status for r in run.reports if r.kind == "suggest"] == ["failed"]


def test_second_run_while_running_is_refused(admin_client, db):
    make_topics(db)
    admin_client.post("/research/run")
    r = admin_client.post("/research/run", follow_redirects=False)
    assert nloc(r) == "/admin/research?notice=research_busy"
    assert db.query(ResearchRun).count() == 1 and db.query(AIJob).count() == 2
    page = admin_client.get(notices.with_notice("/admin/research", "research_busy")).text
    assert "이미 조사가 진행 중입니다." in page


def test_start_run_is_atomic_under_threads(app, db):
    make_topics(db)
    barrier = threading.Barrier(8)
    results = []

    def go():
        with app.state.session_factory() as s:
            barrier.wait()
            results.append(start_run(s, "manual", None))

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r is not None for r in results) == 1
    assert db.query(ResearchRun).count() == 1
    assert db.query(AIJob).filter_by(kind="research_area").count() == 2


def test_no_active_topics(admin_client, db):
    r = admin_client.post("/research/run", follow_redirects=False)
    assert nloc(r) == "/admin/research?notice=no_topics"
    assert db.query(ResearchRun).count() == 0
    assert "사용 중인 리서치 주제가 없습니다." in admin_client.get(notices.with_notice("/admin/research", "no_topics")).text


def test_start_run_without_topics_raises(db):
    with pytest.raises(ValueError, match="사용 중인 리서치 주제가 없습니다."):
        start_run(db, "manual", None)


def test_retry_failed_area(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=[AREA_JSON, "JSON 아님", "JSON 아님", SUGGEST_JSON])
    run = _run_all(app, admin_client, db, settings)
    failed = next(r for r in run.reports if r.status == "failed")
    old_job = failed.job_id
    fake_claude.set(mode="ok", result=AREA_JSON)
    r = admin_client.post(f"/research/reports/{failed.id}/retry", follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    failed = db.get(ResearchReport, failed.id)
    assert failed.status == "pending" and failed.error == "" and failed.job_id != old_job
    assert db.query(AIJob).filter_by(kind="research_area", status="pending").count() == 1
    assert db.get(ResearchRun, run.id).status == "running" and db.get(ResearchRun, run.id).finished_at is None
    _drain(app, settings)
    db.expire_all()
    assert db.get(ResearchRun, run.id).status == "done"


def test_retry_refuses_non_failed(admin_client, db):
    make_topics(db)
    admin_client.post("/research/run")
    rep = db.query(ResearchReport).first()
    # 대기 중(다른 요청이 먼저 다시 시도함)이면 오류 없이 관리 화면으로 돌려보낸다.
    r = admin_client.post(f"/research/reports/{rep.id}/retry", follow_redirects=False)
    assert (r.status_code, nloc(r)) == (303, "/admin/research")
    rep.status = "done"
    db.commit()
    assert admin_client.post(f"/research/reports/{rep.id}/retry").status_code == 400
    assert admin_client.post("/research/reports/999/retry").status_code == 404


def test_empty_trends_fails(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=json.dumps({"trends": []}))
    run = _run_all(app, admin_client, db, settings)
    assert run.status == "failed"
    assert all(r.status == "failed" and r.error == "조사 결과가 비어 있습니다." for r in run.reports)


def test_after_job_hook_runs_on_failure_and_swallows_errors(app, db, settings, monkeypatch, fake_claude):
    from app.ai.queue import enqueue

    seen = []

    def boom(db_, job_input):
        seen.append(job_input.get("x"))
        raise RuntimeError("hook broke")

    monkeypatch.setitem(tasks.HANDLERS, "probe", lambda *a: (_ for _ in ()).throw(ValueError("handler failed")))
    monkeypatch.setitem(tasks.AFTER_JOB, "probe", boom)
    enqueue(db, "probe", {"x": 7}, None)
    assert process_next(app.state.session_factory, app.state.runner, settings) is True
    assert seen == [7]
    assert db.query(AIJob).filter_by(kind="probe").one().status == "failed"


def test_after_job_hook_runs_on_success(app, db, settings, monkeypatch):
    from app.ai.queue import enqueue

    seen = []
    monkeypatch.setitem(tasks.HANDLERS, "probe", lambda *a: "ok")
    monkeypatch.setitem(tasks.AFTER_JOB, "probe", lambda d, i: seen.append(i["x"]))
    enqueue(db, "probe", {"x": 1}, None)
    process_next(app.state.session_factory, app.state.runner, settings)
    assert seen == [1]


def test_deleted_report_finishes_quietly(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=AREA_JSON)
    admin_client.post("/research/run")
    run = db.query(ResearchRun).one()
    db.delete(run)
    db.commit()
    _drain(app, settings)
    assert {j.status for j in db.query(AIJob).all()} == {"done"}


def test_status_fragment(app, admin_client, user_client, db, settings, fake_claude):
    make_topics(db)
    admin_client.post("/research/run")
    run = db.query(ResearchRun).one()
    app.state.settings.ai_worker_enabled = True  # 처리기가 꺼진 서버는 5초 다시 불러오기도 대기 순번도 보이지 않는다 (베타 G)
    html = user_client.get(f"/research/runs/{run.id}/status").text
    assert "대기 1번째" in html and "대기 2번째" in html and "every 5s" in html
    fake_claude.set(mode="error", stderr="Please run /login", exit_code=1)
    _drain(app, settings)
    html = admin_client.get(f"/research/runs/{run.id}/status").text
    assert ERROR_KINDS["auth"] in html and "every 5s" not in html
    assert admin_client.get("/research/runs/999/status").status_code == 404


def test_admin_runs_partial_shows_button_and_runs(admin_client, db):
    make_topics(db)
    page = admin_client.get("/admin/research").text
    assert 'action="/research/run"' in page and "지금 조사" in page
    admin_client.post("/research/run")
    page = admin_client.get("/admin/research").text
    assert "/research/runs/" in page and "진행" in page


def test_normal_user_cannot_run(user_client, db):
    make_topics(db)
    assert user_client.post("/research/run").status_code == 403
    assert user_client.post("/research/reports/1/retry").status_code == 403
    assert db.query(ResearchRun).count() == 0


@pytest.mark.parametrize("url", ["https://user:pw@a.kr/", "https://a.kr/\x85x", "https://a.kr/​x",
    "https://a.kr/‮x", "https://a.kr/⁦x", "https://a.kr/﻿x"])
def test_clean_url_rejects_credentials_and_invisible(url):
    assert clean_url(url) is None


def test_clean_url_length_boundary():
    base = "https://a.kr/"
    assert clean_url(base + "x" * (2000 - len(base))) is not None
    assert clean_url(base + "x" * (2001 - len(base))) is None


def test_source_without_url_does_not_fail_area(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    data = {"trends": [{"headline": "동향", "sources": [{"title": "주소 없음"}, {"url": "https://a.kr/x"}]}]}
    fake_claude.set(mode="ok", result=json.dumps(data, ensure_ascii=False))
    run = _run_all(app, admin_client, db, settings)
    assert run.reports[0].status == "done" and run.reports[0].trends[0]["sources"] == [{"title": "", "url": "https://a.kr/x", "opened": False}]


def test_restart_recovers_stuck_run(app, admin_client, db, settings):
    from app.ai.queue import fail_stale_running
    from app.research import recover_runs

    make_topics(db)
    admin_client.post("/research/run")
    db.query(AIJob).update({"status": "running"})
    db.commit()
    fail_stale_running(db)
    assert recover_runs(db) == 1
    db.expire_all()
    run = db.query(ResearchRun).one()
    assert run.status == "failed" and run.finished_at is not None
    assert all(r.status == "failed" and r.error for r in run.reports)
    assert admin_client.post(f"/research/reports/{run.reports[0].id}/retry", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(ResearchRun).one().status == "running"


def test_concurrent_retries_create_one_job(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="error", stderr="Please run /login", exit_code=1)
    run = _run_all(app, admin_client, db, settings)
    rid = run.reports[0].id
    before = db.query(AIJob).count()
    barrier = threading.Barrier(6)

    def go():
        with app.state.session_factory() as s:
            from app.models import User
            from app.routers.research import retry_report
            admin = s.query(User).filter_by(username="admin").one()
            barrier.wait()
            retry_report(rid, admin, s)

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert db.query(AIJob).count() == before + 1


def test_retry_refused_while_another_run_running(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="error", stderr="Please run /login", exit_code=1)
    run = _run_all(app, admin_client, db, settings)
    rid = run.reports[0].id
    admin_client.post("/research/run")
    r = admin_client.post(f"/research/reports/{rid}/retry", follow_redirects=False)
    assert nloc(r) == "/admin/research?notice=research_busy"
    db.expire_all()
    assert db.get(ResearchReport, rid).status == "failed"


def test_retry_button_only_for_admin(app, admin_client, user_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="error", stderr="Please run /login", exit_code=1)
    run = _run_all(app, admin_client, db, settings)
    assert "/retry" in admin_client.get(f"/research/runs/{run.id}/status").text
    assert "/retry" not in user_client.get(f"/research/runs/{run.id}/status").text


def test_clean_area_opened_flags_and_notes():
    from app.ai.research_tasks import AreaOut
    from app.research import clean_area

    out = AreaOut.model_validate({"trends": [
        {"headline": "열어 봄", "sources": [{"url": "https://a.kr/1", "opened": True}, {"url": "https://a.kr/2"}]},
        {"headline": "검색 결과만", "sources": [{"url": "https://a.kr/3"}, {"url": "https://a.kr/3", "opened": False}]},
        {"headline": "링크 없음", "sources": []},
        {"headline": "중복은 열어 본 쪽이 이김", "sources": [{"url": "https://a.kr/4"}, {"url": "https://a.kr/4", "opened": True}]},
    ]})
    t = clean_area(out)["trends"]
    assert [s["opened"] for s in t[0]["sources"]] == [True, False] and t[0]["needs_check"] == []
    assert t[1]["sources"] == [{"title": "", "url": "https://a.kr/3", "opened": False}]
    assert t[1]["needs_check"] == ["본문을 열어 확인하지 못함"]
    assert t[2]["needs_check"] == ["출처 링크 없음, 확인 필요"]
    assert t[3]["sources"][0]["opened"] is True and t[3]["needs_check"] == []


def test_area_prompt_asks_for_opened_flag(app, admin_client, db, settings, fake_claude):
    make_topics(db)
    fake_claude.set(mode="ok", result=AREA_JSON)
    admin_client.post("/research/run")
    process_next(app.state.session_factory, app.state.runner, settings)
    stdin = fake_claude.calls()[0]["stdin"]
    assert '"opened"' in stdin and "검색 결과" in stdin

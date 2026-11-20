import logging
from datetime import datetime, timedelta

from app import scheduler
from app.ai.queue import process_next
from app.main import create_app
from app.models import AIJob
from app.research import get_schedule


def test_system_page(admin_client, user_client, db, settings):
    r = admin_client.get("/admin/system")
    assert r.status_code == 200
    assert str(settings.data_dir) in r.text
    assert "백업 없음" in r.text
    nxt = scheduler.latest_slot(get_schedule(db), datetime.now()) + timedelta(days=7)
    assert f"{nxt.month}월 {nxt.day}일 {nxt:%H:%M}" in r.text or f"{nxt.year}년 {nxt.month}월 {nxt.day}일 {nxt:%H:%M}" in r.text  # 화면의 날짜 모양 하나
    assert "0.1.0" in r.text
    assert user_client.get("/admin/system").status_code == 403
    assert user_client.post("/admin/system/ai-check").status_code == 403
    assert user_client.post("/admin/system/backup").status_code == 403


def test_system_page_counts_jobs_and_last_error(admin_client, db):
    db.add_all([
        AIJob(kind="x", input={}, status="pending"), AIJob(kind="x", input={}, status="pending"),
        AIJob(kind="x", input={}, status="running"),
        AIJob(kind="x", input={}, status="failed", error_kind="auth", error="raw", finished_at=datetime.now()),
    ])
    db.commit()
    text = admin_client.get("/admin/system").text
    assert "대기 2건" in text and "진행 1건" in text
    assert "Claude 로그인이 필요합니다" in text


def test_ai_check_job_runs_without_tools(app, admin_client, db, settings, fake_claude):
    fake_claude.set(mode="ok", result="확인")
    admin_client.post("/admin/system/ai-check")
    assert "확인 중" in admin_client.get("/admin/system").text
    process_next(app.state.session_factory, app.state.runner, settings)
    argv = next(c["argv"] for c in fake_claude.calls() if "--tools" in c["argv"])  # 시스템 화면의 Claude CLI 점검(--version)도 호출로 남는다
    assert argv[argv.index("--tools") + 1] == ""
    assert "AI 연결 정상" in admin_client.get("/admin/system").text


def test_ai_check_failure_shows_korean_error(app, admin_client, settings, fake_claude):
    fake_claude.set(mode="error", stderr="Please login again (401)")
    admin_client.post("/admin/system/ai-check")
    process_next(app.state.session_factory, app.state.runner, settings)
    text = admin_client.get("/admin/system").text
    assert "AI 연결 정상" not in text
    assert "Claude 로그인이 필요합니다" in text


def test_ai_check_empty_answer_is_a_failure(app, admin_client, settings, fake_claude):
    fake_claude.set(mode="ok", result="  ")
    admin_client.post("/admin/system/ai-check")
    process_next(app.state.session_factory, app.state.runner, settings)
    assert "AI 연결 정상" not in admin_client.get("/admin/system").text


def test_second_ai_check_click_is_ignored_while_pending(admin_client, db):
    admin_client.post("/admin/system/ai-check")
    admin_client.post("/admin/system/ai-check")
    assert db.query(AIJob).filter_by(kind="ai_ping").count() == 1


def test_ai_check_status_partial(admin_client, user_client):
    r = admin_client.get("/admin/system/ai-check/status")
    assert r.status_code == 200
    assert user_client.get("/admin/system/ai-check/status").status_code == 403


def test_manual_backup_button(admin_client, settings):
    r = admin_client.post("/admin/system/backup", follow_redirects=False)
    assert r.status_code == 303
    assert len([p for p in settings.backup_dir.iterdir() if p.is_dir()]) == 1
    assert "백업 없음" not in admin_client.get("/admin/system").text


def test_manual_backup_database_error_shows_message(admin_client, monkeypatch):
    import sqlite3

    from app import backup

    def boom(settings):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backup, "run_backup", boom)
    r = admin_client.post("/admin/system/backup", follow_redirects=False)
    assert r.status_code != 303 and r.status_code < 500
    assert "백업하지 못했습니다" in r.text and "database is locked" in r.text


def test_log_file_written(settings):
    app = create_app(settings)
    from fastapi.testclient import TestClient

    with TestClient(app):  # 시작 시 로그 파일을 연다
        logging.getLogger("app.scheduler").warning("로그 시험 줄")
    log_file = settings.data_dir / "logs" / "app.log"
    assert "로그 시험 줄" in log_file.read_text(encoding="utf-8")
    assert not [h for h in logging.getLogger("app").handlers if getattr(h, "baseFilename", None) == str(log_file)]
    app.state.engine.dispose()


def test_job_kind_labels_cover_all_handlers():
    from app.ai import queue  # noqa: F401  (처리기 등록)
    from app.ai.tasks import HANDLERS
    from app.labels import JOB_KINDS

    assert set(HANDLERS) <= set(JOB_KINDS)


def test_system_and_jobs_pages_use_korean_job_names(admin_client, db):
    db.add(AIJob(kind="research_area", input={}, status="failed", error_kind="auth", finished_at=datetime.now()))
    db.commit()
    for url in ("/admin/system", "/admin/jobs"):
        text = admin_client.get(url).text.replace('value="research_area"', "")  # 종류 거르기 칸의 코드 값은 빼고 본다
        assert "리서치 분야 조사" in text and "research_area" not in text
    assert "다른 AI 작업이 진행 중이면" in admin_client.get("/admin/system").text

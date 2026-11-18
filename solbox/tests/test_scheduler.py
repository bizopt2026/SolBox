import threading
from datetime import datetime, timedelta

import pytest

from app import scheduler
from app.models import AppSetting, ResearchRun
from app.research import Schedule, set_schedule, start_run
from app.scheduler import latest_slot, tick
from app.models import ResearchTopic
from tests.conftest import make_topics

MON = Schedule(True, 0, 6, 0)


def old_topics(db, created=datetime(2026, 11, 1, 9, 0)):
    """슬롯보다 먼저 만든 주제 (조사 대상)."""
    make_topics(db)
    db.query(ResearchTopic).update({"created_at": created})
    db.commit()


def test_latest_slot():
    assert latest_slot(MON, datetime(2026, 11, 16, 6, 0)) == datetime(2026, 11, 16, 6, 0)  # 월요일 정각
    assert latest_slot(MON, datetime(2026, 11, 16, 5, 59)) == datetime(2026, 11, 9, 6, 0)
    assert latest_slot(MON, datetime(2026, 11, 18, 10, 0)) == datetime(2026, 11, 16, 6, 0)


def test_catches_up_once_after_downtime(app, db, settings):
    old_topics(db)
    tue = datetime(2026, 11, 17, 9, 0)
    assert tick(app, tue) == ["research"]
    run = db.query(ResearchRun).one()
    assert run.slot == datetime(2026, 11, 16, 6, 0) and run.trigger == "schedule"
    assert tick(app, tue + timedelta(minutes=1)) == []  # 같은 주에 두 번 돌지 않는다
    db.expire_all()
    assert db.query(ResearchRun).count() == 1


def test_too_old_slot_is_skipped(app, db):
    old_topics(db)
    assert tick(app, datetime(2026, 11, 22, 23, 0)) == []  # 월요일로부터 6일 17시간
    assert db.query(ResearchRun).count() == 0
    assert tick(app, datetime(2026, 11, 22, 6, 0)) == ["research"]  # 정확히 6일


def test_disabled_schedule(app, db):
    old_topics(db)
    set_schedule(db, Schedule(False, 0, 6, 0))
    assert tick(app, datetime(2026, 11, 17, 9, 0)) == []
    assert db.query(ResearchRun).count() == 0


def test_manual_run_does_not_consume_slot(app, db):
    old_topics(db)
    manual = start_run(db, "manual", None)
    manual.status = "done"
    db.commit()
    assert tick(app, datetime(2026, 11, 16, 6, 0)) == ["research"]
    db.expire_all()
    runs = db.query(ResearchRun).order_by(ResearchRun.id).all()
    assert [r.trigger for r in runs] == ["manual", "schedule"]
    assert runs[1].slot == datetime(2026, 11, 16, 6, 0)


def test_busy_run_retries_next_tick(app, db):
    old_topics(db)
    busy = start_run(db, "manual", None)
    now = datetime(2026, 11, 17, 9, 0)
    assert tick(app, now) == []
    busy.status = "done"
    db.commit()
    assert tick(app, now + timedelta(minutes=1)) == ["research"]


def test_no_topics_skips_slot_silently_and_catches_up_later(app, db, caplog, monkeypatch):
    from app import scheduler

    monkeypatch.setattr(scheduler, "_WARNED_SLOTS", set())
    now = datetime(2026, 11, 17, 9, 0)
    with caplog.at_level("WARNING", logger="app.scheduler"):
        assert tick(app, now) == []
        assert tick(app, now + timedelta(minutes=1)) == []
    assert len([r for r in caplog.records if "주제가 없습니다" in r.getMessage()]) == 1  # 예정 시각당 한 번
    assert db.query(ResearchRun).count() == 0  # 빈 실패 실행을 만들지 않는다
    old_topics(db, created=datetime(2026, 11, 15, 9, 0))  # 슬롯 전에 있던 주제가 나중에 보이게 된 경우만 따라잡는다
    assert tick(app, now + timedelta(minutes=2)) == ["research"]
    run = db.query(ResearchRun).one()
    assert run.slot == datetime(2026, 11, 16, 6, 0) and len(run.reports) >= 1


def test_topics_created_after_slot_do_not_trigger_catch_up(app, db):
    make_topics(db)
    db.query(ResearchTopic).update({"created_at": datetime(2026, 11, 16, 6, 0, 1)})  # 슬롯 1초 뒤
    db.commit()
    now = datetime(2026, 11, 17, 9, 0)
    assert tick(app, now) == []
    assert db.query(ResearchRun).count() == 0
    assert tick(app, datetime(2026, 11, 23, 6, 0)) == ["research"]  # 다음 슬롯에는 돈다


def test_topic_created_exactly_at_slot_counts(app, db):
    old_topics(db, created=datetime(2026, 11, 16, 6, 0))
    assert tick(app, datetime(2026, 11, 17, 9, 0)) == ["research"]


def test_inactive_topics_do_not_count_for_catch_up(app, db):
    old_topics(db)
    db.query(ResearchTopic).update({"is_active": False})
    db.add(ResearchTopic(name="새 주제", keywords="", area="tech", created_at=datetime(2026, 11, 17, 8, 0)))
    db.commit()
    assert tick(app, datetime(2026, 11, 17, 9, 0)) == []


def test_deleted_scheduled_run_is_not_rerun(app, db):
    old_topics(db)
    tue = datetime(2026, 11, 17, 9, 0)
    assert tick(app, tue) == ["research"]
    run = db.query(ResearchRun).one()
    run.status = "done"
    db.commit()
    db.delete(run)  # 관리자가 이번 주 예약 실행을 지웠다
    db.commit()
    assert tick(app, tue + timedelta(minutes=1)) == []
    assert db.query(ResearchRun).count() == 0
    assert db.get(AppSetting, "research_last_slot").value == "2026-11-16T06:00:00"
    assert tick(app, datetime(2026, 11, 23, 6, 0)) == ["research"]  # 다음 주 슬롯은 돈다


def test_manual_run_does_not_store_last_slot(app, db):
    old_topics(db)
    start_run(db, "manual", None)
    assert db.get(AppSetting, "research_last_slot") is None


def test_run_without_reports_still_renders(app, db, admin_client):
    run = ResearchRun(trigger="schedule", status="failed", slot=datetime(2026, 11, 16, 6, 0), finished_at=datetime(2026, 11, 17, 9, 0))
    db.add(run)  # 옛 DB 에 남아 있을 수 있는 보고서 없는 실행
    db.commit()
    assert admin_client.get("/research").status_code == 200
    assert admin_client.get(f"/research/runs/{run.id}").status_code == 200
    assert admin_client.get("/admin/research").status_code == 200


def test_concurrent_ticks_create_one_run(app, db):
    old_topics(db)
    now = datetime(2026, 11, 17, 9, 0)
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        results.append(tick(app, now))

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [[], ["research"]]
    db.expire_all()
    assert db.query(ResearchRun).count() == 1


def test_start_run_refuses_taken_slot(app, db):
    old_topics(db)
    slot = datetime(2026, 11, 16, 6, 0)
    first = start_run(db, "schedule", None, slot=slot)
    first.status = "done"
    db.commit()
    assert start_run(db, "schedule", None, slot=slot) is None
    assert db.query(ResearchRun).count() == 1


def test_daily_jobs_once_per_day_after_two(app, db, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "DAILY_JOBS", [lambda app: calls.append(1), lambda app: calls.append(2)])
    set_schedule(db, Schedule(False, 0, 6, 0))
    assert tick(app, datetime(2026, 11, 18, 1, 59)) == []
    assert tick(app, datetime(2026, 11, 18, 2, 0)) == ["daily"] and calls == [1, 2]
    assert tick(app, datetime(2026, 11, 18, 15, 0)) == []
    assert tick(app, datetime(2026, 11, 19, 2, 5)) == ["daily"]
    db.expire_all()
    assert db.get(AppSetting, "daily_last_run").value == "2026-11-19"


def test_failed_daily_job_is_retried_after_an_hour(app, db, monkeypatch):
    calls = []
    state = {"fail": True}

    def flaky(app):
        calls.append("flaky")
        if state["fail"]:
            raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "DAILY_JOBS", [lambda app: calls.append("ok"), flaky])
    set_schedule(db, Schedule(False, 0, 6, 0))
    t0 = datetime(2026, 11, 18, 2, 0)
    assert tick(app, t0) == ["daily"] and calls == ["ok", "flaky"]
    db.expire_all()
    assert db.get(AppSetting, "daily_last_run") is None  # 실패하면 오늘 한 것으로 치지 않는다
    assert db.get(AppSetting, "daily_last_attempt").value == t0.isoformat()
    assert tick(app, t0 + timedelta(minutes=59)) == [] and len(calls) == 2  # 한 시간이 지나기 전엔 다시 하지 않는다
    state["fail"] = False
    assert tick(app, t0 + timedelta(hours=1)) == ["daily"] and len(calls) == 4
    db.expire_all()
    assert db.get(AppSetting, "daily_last_run").value == "2026-11-18"
    assert tick(app, t0 + timedelta(hours=3)) == [] and len(calls) == 4  # 성공했으니 하루 한 번


def test_failed_daily_job_keeps_retrying_hourly(app, db, monkeypatch):
    calls = []

    def always(app):
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "DAILY_JOBS", [always])
    set_schedule(db, Schedule(False, 0, 6, 0))
    t0 = datetime(2026, 11, 18, 2, 0)
    for i, expected in ((0, ["daily"]), (30, []), (61, ["daily"]), (100, []), (125, ["daily"])):
        assert tick(app, t0 + timedelta(minutes=i)) == expected
    assert len(calls) == 3


def test_default_jobs_registered_once_by_start(app, settings, monkeypatch):
    settings.scheduler_enabled = True
    monkeypatch.setattr(scheduler, "_loop", lambda app, stop: None)
    scheduler.start(app)
    scheduler.start(app)
    assert len(scheduler.DAILY_JOBS) == 1
    ran = []
    from app import backup

    monkeypatch.setattr(backup, "run_backup", lambda s: ran.append(s))
    scheduler.DAILY_JOBS[0](app)
    assert ran == [settings]
    scheduler.stop(app)


def test_tick_survives_exceptions(app, monkeypatch):
    def boom(db, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "research_due", boom)
    assert tick(app, datetime(2026, 11, 17, 9, 0)) == []


def test_start_stop_thread(app, settings, monkeypatch):
    settings.scheduler_enabled = False
    scheduler.start(app)
    assert app.state.scheduler_thread is None
    settings.scheduler_enabled = True
    monkeypatch.setattr(scheduler, "tick", lambda app, now: [])  # 실제 점검(백업 포함)은 하지 않는다
    scheduler.start(app)
    assert app.state.scheduler_thread.is_alive()
    started = datetime.now()
    scheduler.stop(app)
    assert not app.state.scheduler_thread.is_alive()
    assert (datetime.now() - started).total_seconds() < 3

import threading

import pytest

from app import labels
from app.ai.queue import (
    _claim_next, enqueue, fail_stale_running, process_next, queue_position, start_workers, stop_workers,
)
from app.ai.tasks import HANDLERS
from app.models import AIJob, User



def _echo(monkeypatch):
    monkeypatch.setitem(HANDLERS, "echo", lambda db, inp, runner, s: runner.run(inp["text"], timeout_s=s.ai_timeout_s))


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def test_jobs_run_in_priority_then_id_order(app, db, settings, fake_claude, monkeypatch):
    _echo(monkeypatch)
    fake_claude.set(result="ok")
    batch = enqueue(db, "echo", {"text": "batch"}, None, priority=10)
    i1 = enqueue(db, "echo", {"text": "i1"}, None)
    i2 = enqueue(db, "echo", {"text": "i2"}, None)
    assert [_run(app, settings) for _ in range(3)] == [True, True, True]
    assert [c["stdin"] for c in fake_claude.calls()] == ["i1", "i2", "batch"]
    for j in (batch, i1, i2):
        db.refresh(j)
        assert j.status == "done" and j.result == "ok" and j.started_at and j.finished_at


def test_queue_position(db):
    jobs = [enqueue(db, "echo", {}, None) for _ in range(3)]
    assert [queue_position(db, j) for j in jobs] == [1, 2, 3]
    jobs[0].status = "running"
    jobs[1].status = "done"
    db.commit()
    assert [queue_position(db, j) for j in jobs] == [0, 0, 1]


def test_queue_position_respects_priority(db):
    low = enqueue(db, "echo", {}, None, priority=10)
    high = enqueue(db, "echo", {}, None)
    assert queue_position(db, high) == 1
    assert queue_position(db, low) == 2


def test_claude_error_marks_failed_with_kind(app, db, settings, fake_claude, monkeypatch):
    _echo(monkeypatch)
    fake_claude.set(mode="error", stderr="usage limit reached")
    job = enqueue(db, "echo", {"text": "x"}, None)
    assert _run(app, settings) is True
    db.refresh(job)
    assert job.status == "failed" and job.error_kind == "limit"
    assert "usage limit" in job.error and job.finished_at


def test_handler_exception_and_unknown_kind_fail_as_unknown(app, db, settings, monkeypatch):
    def boom(db, inp, runner, s):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(HANDLERS, "boom", boom)
    a = enqueue(db, "boom", {}, None)
    b = enqueue(db, "nope", {}, None)
    assert _run(app, settings) and _run(app, settings)
    for j in (a, b):
        db.refresh(j)
        assert j.status == "failed" and j.error_kind == "unknown" and j.finished_at
    assert "kaboom" in a.error


def test_process_next_returns_false_when_empty(app, settings):
    assert _run(app, settings) is False


def test_job_is_claimed_once(app, db, settings, fake_claude, monkeypatch):
    _echo(monkeypatch)
    fake_claude.set(result="ok")
    enqueue(db, "echo", {"text": "x"}, None)
    assert _run(app, settings) is True
    assert _run(app, settings) is False
    assert len(fake_claude.calls()) == 1


def test_fail_stale_running(db):
    j = AIJob(kind="echo", status="running", input={})
    db.add(j)
    db.commit()
    assert fail_stale_running(db) == 1
    db.refresh(j)
    assert j.status == "failed" and "재시작" in j.error and j.error_kind == "restart"  # 재시작으로 중단된 작업은 자기 오류 종류를 가진다


def test_workers_process_jobs_and_stop(app, db, settings, fake_claude, monkeypatch):
    _echo(monkeypatch)
    fake_claude.set(result="ok")
    settings.ai_worker_enabled = True
    job = enqueue(db, "echo", {"text": "x"}, None)
    start_workers(app)
    try:
        done = threading.Event()
        for _ in range(100):
            db.expire_all()
            if db.get(AIJob, job.id).status == "done":
                done.set()
                break
            threading.Event().wait(0.1)
        assert done.is_set()
    finally:
        stop_workers(app)
    assert not any(t.is_alive() for t in app.state.worker_threads)


def test_workers_not_started_when_disabled(app):
    start_workers(app)
    assert not getattr(app.state, "worker_threads", [])
    stop_workers(app)


def _job(db, owner, status="pending", **kw):
    j = enqueue(db, "echo", {"a": 1}, owner.id)
    j.status = status
    for k, v in kw.items():
        setattr(j, k, v)
    db.commit()
    return j


def _kim(db):
    return db.query(User).filter_by(username="kim").one()


@pytest.mark.usefixtures("worker_on")  # 처리기가 켜진 서버의 대기 화면
def test_status_fragment(user_client, db):
    j = _job(db, _kim(db))
    r = user_client.get(f"/jobs/{j.id}/status")
    assert r.status_code == 200 and "AI 대기 1번째" in r.text and "hx-get" in r.text
    j.status = "running"
    db.commit()
    r = user_client.get(f"/jobs/{j.id}/status")
    assert "AI가 작성 중입니다" in r.text and "hx-get" in r.text
    j.status = "done"
    db.commit()
    r = user_client.get(f"/jobs/{j.id}/status")
    assert "완료" in r.text and "hx-get" not in r.text and "HX-Redirect" not in r.headers
    r = user_client.get(f"/jobs/{j.id}/status?next=/activities/draft/3")
    assert r.headers["HX-Redirect"] == "/activities/draft/3"
    for bad in ("https://evil.example", "//evil.example", "/\\evil.example"):
        r = user_client.get(f"/jobs/{j.id}/status", params={"next": bad})
        assert "HX-Redirect" not in r.headers


def test_status_failed_shows_message_and_retry(user_client, db):
    j = _job(db, _kim(db), "failed", error_kind="limit")
    r = user_client.get(f"/jobs/{j.id}/status")
    assert labels.ERROR_KINDS["limit"] in r.text and "hx-get" not in r.text


def test_status_forbidden_for_other_user(user_client, admin_client, db):
    admin = db.query(User).filter_by(username="admin").one()
    j = _job(db, admin)
    assert user_client.get(f"/jobs/{j.id}/status").status_code == 403
    assert admin_client.get(f"/jobs/{j.id}/status").status_code == 200
    assert user_client.get("/jobs/9999/status").status_code == 404


def test_retry_creates_new_job(user_client, db):
    j = _job(db, _kim(db), "failed", error_kind="limit")
    r = user_client.post(f"/jobs/{j.id}/retry")
    assert r.status_code == 200
    new = db.get(AIJob, r.json()["job_id"])
    assert new.id != j.id and new.status == "pending"
    assert new.kind == j.kind and new.input == j.input and new.requester_id == j.requester_id


def test_retry_rules(user_client, admin_client, db):
    kim = _kim(db)
    running = _job(db, kim, "running")
    assert user_client.post(f"/jobs/{running.id}/retry").status_code == 400
    failed = _job(db, kim, "failed")
    admin = db.query(User).filter_by(username="admin").one()
    other = _job(db, admin, "failed")
    assert user_client.post(f"/jobs/{other.id}/retry").status_code == 403
    assert admin_client.post(f"/jobs/{failed.id}/retry").status_code == 200


def test_admin_jobs_page(user_client, admin_client, db):
    _job(db, _kim(db), "failed", error_kind="auth")
    assert user_client.get("/admin/jobs").status_code == 403
    r = admin_client.get("/admin/jobs")
    assert r.status_code == 200 and "echo" in r.text and labels.ERROR_KINDS["auth"] in r.text


def test_concurrent_claims_never_duplicate(app, db):
    ids = {enqueue(db, "echo", {}, None).id for _ in range(6)}
    n = 8
    barrier = threading.Barrier(n)
    claimed, errors = [], []

    def worker():
        barrier.wait()
        try:
            while (c := _claim_next(app.state.session_factory)) is not None:
                claimed.append(c[0])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(claimed) == sorted(ids)


def test_finish_is_retried_when_db_fails(app, db, settings, monkeypatch):
    import app.ai.queue as q

    monkeypatch.setattr(q, "FINISH_RETRY_SLEEP_S", 0)
    monkeypatch.setitem(HANDLERS, "ok", lambda db, inp, runner, s: "r")
    job = enqueue(db, "ok", {}, None)
    real, calls = q._finish_once, []

    def flaky(sf, job_id, **values):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("database is locked")
        real(sf, job_id, **values)

    monkeypatch.setattr(q, "_finish_once", flaky)
    assert _run(app, settings) is True
    db.refresh(job)
    assert job.status == "done" and len(calls) == 3


def test_finish_failure_does_not_raise(app, db, settings, monkeypatch):
    import app.ai.queue as q

    monkeypatch.setattr(q, "FINISH_RETRY_SLEEP_S", 0)
    monkeypatch.setitem(HANDLERS, "ok", lambda db, inp, runner, s: "r")
    enqueue(db, "ok", {}, None)
    monkeypatch.setattr(q, "_finish_once", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("locked")))
    assert _run(app, settings) is True


def test_status_redirects_when_failed_and_encodes_non_ascii(user_client, db):
    j = _job(db, _kim(db), "failed", error_kind="limit")
    r = user_client.get(f"/jobs/{j.id}/status?next=/activities/draft/3")
    assert r.headers["HX-Redirect"] == "/activities/draft/3"
    j.status = "done"
    db.commit()
    r = user_client.get(f"/jobs/{j.id}/status", params={"next": "/a?q=한글&x=1"})
    assert r.status_code == 200
    assert r.headers["HX-Redirect"] == "/a?q=%ED%95%9C%EA%B8%80&x=1"


def test_handler_receives_job_id(app, db, settings, monkeypatch):
    got = {}
    monkeypatch.setitem(HANDLERS, "probe", lambda db, inp, r, s: got.update(inp) or "ok")
    job = enqueue(db, "probe", {"a": 1}, None)
    process_next(app.state.session_factory, app.state.runner, settings)
    assert got == {"a": 1, "_job_id": job.id}
    db.refresh(job)
    assert job.input == {"a": 1}

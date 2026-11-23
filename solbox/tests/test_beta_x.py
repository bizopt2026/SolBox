"""베타 X: 시스템 업데이트 (확인·적용). 임시 bare 저장소 + 복제본으로 실제 git 을 돌린다. 실제 os._exit·SIGINT·pip 은 부르지 않는다."""
import subprocess
import time
from pathlib import Path

import pytest

from app import backup, update
from app.models import AIJob, AppSetting


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def commit(work, name, text, msg):
    f = work / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-m", msg)
    git(work, "push", "origin", "main")
    return git(work, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    bare, work, srv = tmp_path / "origin.git", tmp_path / "work", tmp_path / "srv"
    git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    git(tmp_path, "clone", str(bare), str(work))
    git(work, "checkout", "-b", "main")
    commit(work, "solbox/pyproject.toml", "[project]\nname='x'\n", "first")
    git(tmp_path, "clone", str(bare), str(srv))
    git(srv, "checkout", "main")
    return type("R", (), {"bare": bare, "work": work, "srv": srv, "head": staticmethod(lambda: git(srv, "rev-parse", "HEAD"))})


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(update, "_LAST_CHECK", None)
    monkeypatch.setattr(update, "_STATE", {"state": "idle", "steps": [], "error": "", "to": "", "from": "", "pip": False})
    monkeypatch.setenv(update.SUPERVISED_ENV, "1")
    yield
    deadline = time.time() + 10
    while update._APPLY_LOCK.locked() and time.time() < deadline:
        time.sleep(0.02)


@pytest.fixture
def at_srv(repos, monkeypatch):
    monkeypatch.setattr(update, "SOLBOX_DIR", repos.srv / "solbox")
    return repos


@pytest.fixture
def exits(monkeypatch):
    calls = []
    monkeypatch.setattr(update, "schedule_restart", lambda *a, **k: calls.append(1))
    return calls


@pytest.fixture
def backups(monkeypatch):
    calls = []
    real = backup.run_backup

    def spy(settings, *a, **k):
        calls.append(1)
        return real(settings, *a, **k)

    monkeypatch.setattr(backup, "run_backup", spy)
    return calls


def wait_done(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if update.state()["state"] != "running":
            return update.state()
        time.sleep(0.05)
    raise AssertionError("업데이트가 끝나지 않았다")


def apply_via_page(admin_client, token="t1"):
    r = admin_client.post("/admin/system/update/apply", data={"_tok": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/system/update/progress"
    return wait_done()


# ---------- 확인 ----------

def test_not_a_git_folder(app, tmp_path, monkeypatch):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "pyproject.toml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(update, "SOLBOX_DIR", d)
    res = update.check(app.state.session_factory)
    assert res["status"] == "no_git" and "운영가이드 9번의 zip 방법" in res["message"] and not res["can_apply"]


def test_wrong_branch_blocks(at_srv, app):
    git(at_srv.srv, "checkout", "-b", "feature")
    res = update.check(app.state.session_factory)
    assert not res["can_apply"] and any("'feature'" in b for b in res["blockers"])


def test_local_edits_block_and_list_files(at_srv, app):
    (at_srv.srv / "solbox" / "pyproject.toml").write_text("changed", encoding="utf-8")
    res = update.check(app.state.session_factory)
    assert not res["can_apply"] and res["dirty"] == ["solbox/pyproject.toml"] and any("직접 고친" in b for b in res["blockers"])
    (at_srv.srv / "solbox" / "untracked.txt").write_text("x", encoding="utf-8")  # 추적하지 않는 새 파일은 막지 않는다
    assert update.check(app.state.session_factory)["dirty"] == ["solbox/pyproject.toml"]


def test_many_local_edits_are_capped_at_ten(at_srv, app):
    for i in range(12):
        (at_srv.work / "solbox" / f"f{i}.txt").write_text("x", encoding="utf-8")
    git(at_srv.work, "add", "-A")
    git(at_srv.work, "commit", "-m", "files")
    git(at_srv.work, "push", "origin", "main")
    git(at_srv.srv, "pull", "origin", "main")
    for i in range(12):
        (at_srv.srv / "solbox" / f"f{i}.txt").write_text("y", encoding="utf-8")
    res = update.check(app.state.session_factory)
    assert len(res["dirty"]) == 10 and res["dirty_more"] == 2


def test_local_only_commit_blocks(at_srv, app):
    (at_srv.srv / "solbox" / "mine.txt").write_text("x", encoding="utf-8")
    git(at_srv.srv, "add", "-A")
    git(at_srv.srv, "commit", "-m", "local")
    commit(at_srv.work, "solbox/a.txt", "1", "remote")
    res = update.check(app.state.session_factory)
    assert not res["can_apply"] and any("이 폴더에만 있는 커밋" in b for b in res["blockers"])


def test_no_updates(at_srv, app):
    res = update.check(app.state.session_factory)
    assert res["status"] == "up_to_date" and res["behind"] == 0 and not res["can_apply"] and res["blockers"] == []


def test_updates_available_lists_commits(at_srv, app):
    commit(at_srv.work, "solbox/a.txt", "1", "첫째 변경")
    commit(at_srv.work, "solbox/b.txt", "2", "둘째 변경")
    res = update.check(app.state.session_factory)
    assert res["status"] == "available" and res["can_apply"] and res["behind"] == 2
    assert [s for _, s in res["commits"]] == ["둘째 변경", "첫째 변경"] and res["pyproject"] is False
    commit(at_srv.work, "solbox/pyproject.toml", "[project]\nname='y'\n", "deps")
    assert update.check(app.state.session_factory)["pyproject"] is True


def test_fetch_failure(at_srv, app):
    git(at_srv.srv, "remote", "set-url", "origin", str(at_srv.bare.parent / "nope.git"))
    res = update.check(app.state.session_factory)
    assert res["status"] == "fetch_failed" and res["message"].startswith("GitHub 에 연결할 수 없어요") and not res["can_apply"]


def test_fetch_error_text_is_redacted(at_srv, app):
    git(at_srv.srv, "remote", "set-url", "origin", "https://user:s3cretpass@127.0.0.1:1/x.git")
    res = update.check(app.state.session_factory)
    assert res["status"] == "fetch_failed" and "s3cretpass" not in res["message"]


def test_not_supervised_blocks(at_srv, app, monkeypatch):
    commit(at_srv.work, "solbox/a.txt", "1", "c")
    monkeypatch.delenv(update.SUPERVISED_ENV)
    res = update.check(app.state.session_factory)
    assert not res["can_apply"] and any("run_server.ps1" in b for b in res["blockers"])


def test_ai_job_blocks(at_srv, app, db):
    commit(at_srv.work, "solbox/a.txt", "1", "c")
    db.add_all([AIJob(kind="x", input={}, status="running"), AIJob(kind="x", input={}, status="pending")])
    db.commit()
    res = update.check(app.state.session_factory)
    assert not res["can_apply"] and res["ai_active"] == 2 and any("2건" in b for b in res["blockers"])


def test_check_button_shows_result_and_apply_button(at_srv, admin_client):
    commit(at_srv.work, "solbox/a.txt", "1", "화면에 보일 변경")
    assert "지금 업데이트" not in admin_client.get("/admin/system").text
    r = admin_client.post("/admin/system/update/check", follow_redirects=True)
    assert r.status_code == 200 and "화면에 보일 변경" in r.text and "새 커밋 1개" in r.text and 'action="/admin/system/update/apply"' in r.text


def test_healthz_is_public_and_reports_boot_commit(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True and "commit" in r.json()


# ---------- 적용 ----------

def test_apply_success(at_srv, admin_client, app, exits, backups):
    old = at_srv.head()
    new = commit(at_srv.work, "solbox/a.txt", "1", "c1")
    admin_client.post("/admin/system/update/check")
    st = apply_via_page(admin_client)
    assert st["state"] == "done" and at_srv.head() == new != old
    assert backups == [1] and exits == [1]
    rec = update.read_record(app.state.session_factory)
    assert rec["ok"] and rec["from"] == old and rec["to"] == new and rec["pip"] is False and rec["by"] == "admin" and rec["notice_pending"]
    assert admin_client.get("/admin/system/update/status").json()["state"] == "done"


def test_apply_runs_pip_when_pyproject_changed(at_srv, admin_client, app, exits, backups, monkeypatch):
    pip = []
    monkeypatch.setattr(update, "run_pip", lambda: pip.append(1))
    commit(at_srv.work, "solbox/pyproject.toml", "[project]\nname='z'\n", "deps")
    st = apply_via_page(admin_client)
    assert st["state"] == "done" and pip == [1] and exits == [1]
    assert update.read_record(app.state.session_factory)["pip"] is True


def test_apply_pip_failure_resets_and_does_not_restart(at_srv, admin_client, app, exits, backups, monkeypatch):
    def boom():
        raise update.GitError("pip 설치가 실패했어요: no matching distribution")

    monkeypatch.setattr(update, "run_pip", boom)
    old = at_srv.head()
    commit(at_srv.work, "solbox/pyproject.toml", "[project]\nname='z'\n", "deps")
    st = apply_via_page(admin_client)
    assert st["state"] == "failed" and "pip 설치가 실패" in st["error"]
    assert at_srv.head() == old and exits == []
    rec = update.read_record(app.state.session_factory)
    assert rec["ok"] is False and rec["reset"] is True and rec["pip"] is True
    page = admin_client.get("/admin/system").text
    assert "업데이트하지 못했어요" in page and "되돌렸어요" in page
    assert "업데이트하지 못했어요" not in admin_client.get("/admin/system").text  # 알림은 한 번만


def test_apply_backup_failure_stops_before_pull(at_srv, admin_client, app, exits, monkeypatch):
    def bad(settings, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "run_backup", bad)
    old = at_srv.head()
    commit(at_srv.work, "solbox/a.txt", "1", "c")
    st = apply_via_page(admin_client)
    assert st["state"] == "failed" and "백업하지 못해서" in st["error"] and at_srv.head() == old and exits == []
    assert update.read_record(app.state.session_factory)["reset"] is False


def test_apply_blocked_when_not_supervised(at_srv, admin_client, exits, backups, monkeypatch):
    commit(at_srv.work, "solbox/a.txt", "1", "c")
    old = at_srv.head()
    monkeypatch.delenv(update.SUPERVISED_ENV)
    st = apply_via_page(admin_client)
    assert st["state"] == "failed" and "run_server.ps1" in st["error"] and at_srv.head() == old and backups == [] and exits == []


def test_apply_blocked_when_ai_job_running(at_srv, admin_client, db, exits, backups):
    commit(at_srv.work, "solbox/a.txt", "1", "c")
    old = at_srv.head()
    db.add(AIJob(kind="x", input={}, status="running"))
    db.commit()
    st = apply_via_page(admin_client)
    assert st["state"] == "failed" and "AI 작업이 1건" in st["error"] and at_srv.head() == old and backups == [] and exits == []


def test_apply_rechecks_instead_of_trusting_the_page(at_srv, admin_client, exits, backups):
    commit(at_srv.work, "solbox/a.txt", "1", "c")
    admin_client.post("/admin/system/update/check")
    (at_srv.srv / "solbox" / "pyproject.toml").write_text("edited after the check", encoding="utf-8")  # 확인 뒤에 직접 고침
    st = apply_via_page(admin_client)
    assert st["state"] == "failed" and "직접 고친" in st["error"] and backups == [] and exits == []


def test_non_admin_is_forbidden(user_client):
    assert user_client.post("/admin/system/update/check").status_code == 403
    assert user_client.post("/admin/system/update/apply", data={"_tok": "x"}).status_code == 403
    assert user_client.get("/admin/system/update/status").status_code == 403
    assert user_client.get("/admin/system/update/progress").status_code == 403


def test_double_submit_runs_once(at_srv, admin_client, exits, backups):
    commit(at_srv.work, "solbox/a.txt", "1", "c")
    r1 = admin_client.post("/admin/system/update/apply", data={"_tok": "same"}, follow_redirects=False)
    r2 = admin_client.post("/admin/system/update/apply", data={"_tok": "same"}, follow_redirects=False)
    assert r1.status_code == r2.status_code == 303 and r2.headers["location"].startswith("/admin/system/update/progress")
    wait_done()
    assert backups == [1] and exits == [1]


def test_lock_blocks_second_start(app, monkeypatch):
    assert update._APPLY_LOCK.acquire(blocking=False)
    try:
        assert update.start_apply(app, "x") is False
        assert "적용하는 중" in update.run_check(app.state.session_factory)["busy"]
    finally:
        update._APPLY_LOCK.release()


def test_progress_page_redirects_when_idle(admin_client):
    assert admin_client.get("/admin/system/update/progress", follow_redirects=False).headers["location"] == "/admin/system"


def test_success_notice_waits_for_new_code(app, monkeypatch):
    sf = app.state.session_factory
    with sf() as db:
        db.add(AppSetting(key=update.RECORD_KEY, value={"ok": True, "from": "a" * 40, "to": "b" * 40, "notice_pending": True}))
        db.commit()
    monkeypatch.setattr(update, "_BOOT", {"sha": "a" * 40, "subject": ""})
    assert update.take_notice(sf) is None  # 아직 옛 코드가 돈다
    monkeypatch.setattr(update, "_BOOT", {"sha": "b" * 40, "subject": ""})
    assert update.take_notice(sf)["ok"] is True and update.take_notice(sf) is None


def test_restart_worker_asks_for_graceful_shutdown_then_hard_exit(monkeypatch):
    seen = []
    monkeypatch.setattr(update, "request_shutdown", lambda: seen.append("sigint"))
    monkeypatch.setattr(update, "hard_exit", lambda: seen.append("hard"))
    update._restart_worker(0, 0)
    assert seen == ["sigint", "hard"]


def test_run_server_script_sets_supervised_flag():
    text = (Path(__file__).resolve().parent.parent / "scripts" / "run_server.ps1").read_text(encoding="utf-8-sig")
    assert '$env:SOLBOX_SUPERVISED = "1"' in text and text.index("SOLBOX_SUPERVISED = ") < text.index("while ($true)")

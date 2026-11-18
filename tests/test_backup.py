import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pytest

from app import backup, cli
from app.backup import latest, prune, run_backup, verify
from app.config import load_settings
from app.models import Customer, Solution

NOW = datetime(2026, 11, 18, 2, 0, 0)


def _query(db_file, sql):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_backup_contents(app, user_client, db, settings):
    s = Solution(name="A")
    db.add(s)
    db.commit()
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("a.txt", b"hello", "text/plain"))])
    (settings.knowledge_dir / "x.md").write_text("k", encoding="utf-8")
    path = run_backup(settings, now=NOW)
    assert path == settings.backup_dir / "20261118-020000"
    assert _query(path / "app.db", "select name from solutions") == [("A",)]
    assert len(list((path / "uploads").rglob("*.txt"))) == 1 and not (path / "knowledge").exists()
    assert (path / "decks").is_dir()
    assert verify(path) == []
    meta = json.loads((path / "backup.json").read_text(encoding="utf-8"))
    assert meta["files"] == 1 and meta["db_bytes"] == (path / "app.db").stat().st_size and meta["created_at"]
    assert not list(settings.backup_dir.glob("*.partial"))
    assert sorted(p.name for p in path.iterdir()) == ["app.db", "backup.json", "decks", "uploads"]


def test_backup_with_missing_folders(app, settings):
    shutil.rmtree(settings.uploads_dir)
    shutil.rmtree(settings.decks_dir)
    path = run_backup(settings, now=NOW)
    assert (path / "uploads").is_dir() and (path / "decks").is_dir()
    assert verify(path) == []


def test_backup_copies_symlinks_as_links(app, settings, tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink 없음")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    try:
        os.symlink(outside, settings.uploads_dir / "link", target_is_directory=True)
    except OSError:
        pytest.skip("symlink 권한 없음")
    path = run_backup(settings, now=NOW)
    assert (path / "uploads" / "link").is_symlink()
    assert verify(path) == []


def test_backup_while_writing(app, db, settings):
    stop = threading.Event()
    errors = []

    def writer():
        session = app.state.session_factory()
        try:
            while not stop.is_set():
                session.add(Customer(name="c"))
                session.commit()
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            session.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        time.sleep(0.05)
        path = run_backup(settings, now=NOW)
        time.sleep(0.15)
    finally:
        stop.set()
        t.join()
    assert not errors
    assert verify(path) == []
    assert _query(path / "app.db", "select count(*) from customers")[0][0] >= 0


def test_failed_backup_leaves_no_partial(settings, app, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(backup.shutil, "copytree", boom)
    with pytest.raises(OSError):
        run_backup(settings, now=NOW)
    assert list(settings.backup_dir.iterdir()) == []


def test_copytree_never_merges_into_existing(settings, app, monkeypatch):
    seen = []
    real = shutil.copytree

    def spy(*a, **k):
        seen.append(k.get("dirs_exist_ok"))
        assert k.get("symlinks") is True
        return real(*a, **k)

    monkeypatch.setattr(backup.shutil, "copytree", spy)
    run_backup(settings, now=NOW)
    assert seen == [False, False]


def test_missing_database_is_an_error(settings):
    with pytest.raises(FileNotFoundError):
        run_backup(settings, now=NOW)
    assert not settings.db_path.exists()


def test_stale_partial_removed_recent_kept(app, settings):
    settings.backup_dir.mkdir(parents=True)
    old = settings.backup_dir / "20261110-020000.partial"
    recent = settings.backup_dir / "20261118-000000.partial"
    other = settings.backup_dir / "mine.partial"
    for p in (old, recent, other):
        p.mkdir()
    run_backup(settings, now=NOW)
    assert not old.exists() and recent.exists() and other.exists()


def test_prune_keeps_recent_and_latest(tmp_path):
    for n in ("20261101-020000", "20261110-020000", "20261117-020000", "notes", "20261117"):
        (tmp_path / n).mkdir()
    (tmp_path / "20261001-010101").write_text("file", encoding="utf-8")
    removed = prune(tmp_path, 14, datetime(2026, 11, 18, 3, 0))
    assert [p.name for p in removed] == ["20261101-020000"]
    assert (tmp_path / "notes").exists() and (tmp_path / "20261117").exists()
    assert (tmp_path / "20261001-010101").exists() and not (tmp_path / "20261101-020000").exists()


def test_prune_never_removes_the_only_backup(tmp_path):
    (tmp_path / "20260918-020000").mkdir()
    assert prune(tmp_path, 14, datetime(2026, 11, 18, 3, 0)) == []
    assert (tmp_path / "20260918-020000").exists()
    (tmp_path / "20260919-020000").mkdir()  # 더 최근 것이 생기면 오래된 쪽은 지운다
    assert [p.name for p in prune(tmp_path, 14, datetime(2026, 11, 18, 3, 0))] == ["20260918-020000"]


def test_prune_ignores_pattern_lookalikes_and_symlinks(tmp_path):
    root = tmp_path / "b"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    for n in ("20260101-020000x", "x20260101-020000", "20260101-020000.partial", "2026-01-01"):
        (root / n).mkdir()
    try:
        os.symlink(outside, root / "20260102-020000", target_is_directory=True)
    except OSError:
        pytest.skip("symlink 권한 없음")
    (root / "20261117-020000").mkdir()
    removed = prune(root, 14, datetime(2026, 11, 18, 3, 0))
    assert removed == []
    assert (outside / "keep.txt").exists() and (root / "20260102-020000").is_symlink()
    assert len(list(root.iterdir())) == 6


def test_prune_missing_dir(tmp_path):
    assert prune(tmp_path / "nope", 14, NOW) == []


def test_run_backup_prunes(app, settings):
    old = settings.backup_dir / "20261001-020000"
    old.mkdir(parents=True)
    run_backup(settings, now=NOW)
    assert not old.exists()


def test_verify_detects_corruption(app, settings):
    path = run_backup(settings, now=NOW)
    (path / "app.db").write_bytes(b"garbage" * 1000)
    assert len(verify(path)) >= 1


def test_verify_missing_pieces(app, settings, tmp_path):
    path = run_backup(settings, now=NOW)
    (path / "backup.json").unlink()
    assert any("backup.json" in p for p in verify(path))
    (path / "app.db").unlink()
    assert any("app.db" in p for p in verify(path))
    assert verify(tmp_path / "nothing") != []


def test_verify_counts_files(app, settings):
    path = run_backup(settings, now=NOW)
    (path / "uploads" / "extra.txt").write_text("x", encoding="utf-8")
    problems = verify(path)
    assert len(problems) == 1 and "파일" in problems[0]


def test_latest(app, settings):
    assert latest(settings.backup_dir) is None
    run_backup(settings, now=NOW)
    run_backup(settings, now=NOW + timedelta(days=1))
    (settings.backup_dir / "20271231-000000").mkdir()  # backup.json 이 없는 폴더는 건너뛴다
    got = latest(settings.backup_dir)
    assert got["path"] == settings.backup_dir / "20261119-020000"
    assert got["created_at"] == datetime(2026, 11, 19, 2, 0, 0) and got["db_bytes"] > 0


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('data_dir = "data"\nsecret_key = "k"\n', encoding="utf-8")
    monkeypatch.setenv("SOLBOX_CONFIG", str(path))
    return path


def test_cli_backup_and_verify(cfg, capsys):
    cli.main(["create-admin", "--username", "boss", "--name", "관리자", "--password", "pw1234"])
    capsys.readouterr()
    cli.main(["backup"])
    out = capsys.readouterr().out
    folders = list((cfg.parent / "backups").iterdir())
    assert len(folders) == 1 and str(folders[0]) in out and "백업" in out and "KB" in out or "MB" in out or " B" in out
    cli.main(["backup", "--verify", str(folders[0])])
    assert "이상 없음" in capsys.readouterr().out
    (folders[0] / "app.db").write_bytes(b"junk" * 500)
    with pytest.raises(SystemExit) as e:
        cli.main(["backup", "--verify", str(folders[0])])
    assert e.value.code == 1
    assert "app.db" in capsys.readouterr().out


def test_cli_backup_without_database(cfg):
    with pytest.raises(SystemExit) as e:
        cli.main(["backup"])
    assert "데이터베이스" in str(e.value)
    assert not (cfg.parent / "data" / "app.db").exists()


def _config(tmp_path, extra=""):
    path = tmp_path / "conf" / "config.toml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(f'data_dir = "../d"\nsecret_key = "k"\n{extra}', encoding="utf-8")
    return path


def test_backup_dir_default_and_relative(tmp_path):
    s = load_settings(_config(tmp_path))
    assert s.backup_dir == (tmp_path / "conf" / ".." / "d").parent / "backups" and s.backup_keep_days == 14
    s = load_settings(_config(tmp_path, 'backup_dir = "bk"\nbackup_keep_days = 3\n'))
    assert s.backup_dir == tmp_path / "conf" / "bk" and s.backup_keep_days == 3
    abs_dir = tmp_path / "abs"
    assert load_settings(_config(tmp_path, f"backup_dir = '{abs_dir.as_posix()}'\n")).backup_dir == abs_dir


def test_backup_dir_inside_data_dir_rules(tmp_path):
    ok = load_settings(_config(tmp_path, 'backup_dir = "../d/backups"\n'))
    assert ok.backup_dir.name == "backups"
    for sub in ("uploads", "decks", "uploads/x"):
        with pytest.raises(SystemExit) as e:
            load_settings(_config(tmp_path, f'backup_dir = "../d/{sub}"\n'))
        assert "백업" in str(e.value)

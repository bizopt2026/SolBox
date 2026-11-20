import pytest

from app import cli
from app.models import User


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('data_dir = "data"\nsecret_key = "k"\n', encoding="utf-8")
    monkeypatch.setenv("SOLBOX_CONFIG", str(path))
    return path


def _users(settings_path):
    from app.config import load_settings
    from app.db import make_engine, make_session_factory
    engine = make_engine(load_settings(settings_path).db_path)
    try:
        with make_session_factory(engine)() as db:
            return [u.username for u in db.query(User)]
    finally:
        engine.dispose()


def test_create_admin_prompts_for_password_when_omitted(cfg, monkeypatch):
    answers = iter(["비밀번호1", "비밀번호1"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    cli.main(["create-admin", "--username", "boss", "--name", "관리자"])
    assert _users(cfg) == ["boss"]


def test_create_admin_prompt_mismatch_exits(cfg, monkeypatch):
    answers = iter(["a1", "b2"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    with pytest.raises(SystemExit) as e:
        cli.main(["create-admin", "--username", "boss", "--name", "관리자"])
    assert "일치" in str(e.value)
    assert not (cfg.parent / "data" / "app.db").exists()  # 계정도 DB도 만들지 않았다


def test_cli_adds_missing_columns_to_an_older_database(cfg, monkeypatch):
    import sqlite3

    data = cfg.parent / "data"
    data.mkdir()
    conn = sqlite3.connect(data / "app.db")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, name TEXT, password_hash TEXT, role TEXT)")
    conn.commit()
    conn.close()
    seen = []
    from app import migrate

    real = migrate.add_missing_columns
    monkeypatch.setattr(migrate, "add_missing_columns", lambda *a, **k: seen.append(1) or real(*a, **k))
    cli.main(["create-admin", "--username", "boss", "--name", "관리자", "--password", "pw1234"])
    cli.main(["rebuild-knowledge"])
    assert len(seen) == 2
    assert _users(cfg) == ["boss"]

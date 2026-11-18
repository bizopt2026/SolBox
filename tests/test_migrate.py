import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Integer, String, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import Settings
from app.db import make_engine
from app.main import create_app
from app.migrate import add_missing_columns, missing_columns


def _old_proposals_db(db_path):
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE proposals (id INTEGER PRIMARY KEY, title VARCHAR, customer_id INTEGER, status VARCHAR)"
    )
    con.execute("INSERT INTO proposals (id, title, customer_id, status) VALUES (1, '옛 제안', 1, 'new')")
    con.commit()
    con.close()


def test_adds_missing_column_and_keeps_data(tmp_path):
    db_path = tmp_path / "app.db"
    _old_proposals_db(db_path)
    engine = make_engine(db_path)
    added = add_missing_columns(engine, db_path)
    engine.dispose()
    assert "proposals.chat_from_id" in added and "proposals.request_memo" in added
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT title, chat_from_id, request_memo, directions FROM proposals").fetchone()
    con.close()
    assert row == ("옛 제안", None, "", "[]")
    assert len(list(tmp_path.glob("app.db.before-migrate-*"))) == 1


def test_backup_contains_old_data(tmp_path):
    db_path = tmp_path / "app.db"
    _old_proposals_db(db_path)
    engine = make_engine(db_path)
    add_missing_columns(engine, db_path)
    engine.dispose()
    backup = next(tmp_path.glob("app.db.before-migrate-*"))
    con = sqlite3.connect(backup)
    cols = [r[1] for r in con.execute("PRAGMA table_info(proposals)")]
    rows = con.execute("SELECT title FROM proposals").fetchall()
    con.close()
    assert "request_memo" not in cols and rows == [("옛 제안",)]


def test_no_change_no_backup(app, settings):
    assert missing_columns(app.state.engine) == []
    assert add_missing_columns(app.state.engine, settings.db_path) == []
    assert list(settings.db_path.parent.glob("app.db.before-migrate-*")) == []


class OtherBase(DeclarativeBase):
    pass


class Widget(OtherBase):
    __tablename__ = "widgets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)


def test_not_null_without_default_raises(tmp_path):
    db_path = tmp_path / "app.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    engine = make_engine(db_path)
    with pytest.raises(RuntimeError, match="widgets.label"):
        add_missing_columns(engine, db_path, metadata=OtherBase.metadata)
    engine.dispose()
    assert list(tmp_path.glob("app.db.before-migrate-*")) == []


def test_missing_table_is_left_to_create_all(tmp_path):
    db_path = tmp_path / "app.db"
    engine = make_engine(db_path)
    assert missing_columns(engine) == []
    assert add_missing_columns(engine, db_path) == []
    assert inspect(engine).get_table_names() == []
    engine.dispose()
    assert list(tmp_path.glob("app.db.before-migrate-*")) == []


def test_create_app_migrates_old_db(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = Settings(data_dir=data_dir, secret_key="test", ai_worker_enabled=False)
    _old_proposals_db(settings.db_path)
    app = create_app(settings)
    try:
        from tests.conftest import make_user

        with app.state.session_factory() as db:
            make_user(db, "kim")
        client = TestClient(app)
        resp = client.post("/login", data={"username": "kim", "password": "pw1234"}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert client.get("/proposals").status_code == 200
    finally:
        app.state.engine.dispose()

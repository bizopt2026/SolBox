import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

FAKE_CLAUDE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        secret_key="test",
        ai_worker_enabled=False,
        claude_cmd=[sys.executable, str(FAKE_CLAUDE)],
    )


@pytest.fixture
def app(settings):
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def db(app):
    session = app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(app):
    return TestClient(app)


def make_user(db, username, role="user", password="pw1234", name=None):
    from app.models import User
    from app.security import hash_password

    user = User(
        username=username,
        name=name or username,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    return user


def _logged_in_client(app, db, username, role):
    make_user(db, username, role=role)
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": "pw1234"}, follow_redirects=False)
    assert r.status_code == 303
    return c


@pytest.fixture
def user_client(app, db):
    return _logged_in_client(app, db, "kim", "user")


@pytest.fixture
def admin_client(app, db):
    return _logged_in_client(app, db, "admin", "admin")

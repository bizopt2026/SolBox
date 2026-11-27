"""한 PC 에 운영·베타 솔박스를 함께 띄우기 (2026-11-27): 로그인 쿠키 이름·구분 표시·자동 시작 작업 이름."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from tests.conftest import make_user

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path, extra=""):
    p = tmp_path / "config.toml"
    p.write_text(f'data_dir = "data"\nsecret_key = "{"x" * 40}"\n{extra}', encoding="utf-8")
    return p


def test_defaults_keep_the_running_server_as_it_is(tmp_path):
    s = load_settings(_config(tmp_path))
    assert s.session_cookie == "session" and s.instance_label == ""


def test_beta_settings_are_read_and_checked(tmp_path):
    s = load_settings(_config(tmp_path, 'session_cookie = "solbox_beta"\ninstance_label = " 베타 "\n'))
    assert s.session_cookie == "solbox_beta" and s.instance_label == "베타"
    for bad in ('session_cookie = "세션"', 'session_cookie = "a b"', 'session_cookie = ""', 'instance_label = "아주아주아주아주긴표시글자"'):
        with pytest.raises(SystemExit):
            load_settings(_config(tmp_path, bad + "\n"))


def _login(client):
    r = client.post("/login", data={"username": "beta", "password": "pw1234"}, follow_redirects=False)
    assert r.status_code == 303
    return r


def test_beta_uses_its_own_cookie_and_shows_its_label(settings, tmp_path):
    settings.session_cookie = "solbox_beta"
    settings.instance_label = "베타"
    application = create_app(settings)
    try:
        with application.state.session_factory() as db:
            make_user(db, "beta")
        c = TestClient(application)
        login = c.get("/login").text
        assert "솔박스 베타</title>" in login and '<span class="instance-tag">베타</span>' in login, "로그인 화면부터 베타임이 보인다"
        r = _login(c)
        assert "solbox_beta=" in r.headers.get("set-cookie", "") and "session=" not in r.headers.get("set-cookie", "").split(";")[0]
        home = c.get("/").text
        assert 'data-instance="베타"' in home and '<span class="instance-tag">베타</span>' in home and "솔박스 베타</title>" in home
    finally:
        application.state.engine.dispose()


def test_running_server_has_no_label(user_client):
    home = user_client.get("/").text
    assert "data-instance" not in home and "instance-tag" not in home


def test_autostart_task_name_can_differ_for_the_beta_folder():
    text = (ROOT / "scripts" / "install_autostart.ps1").read_text(encoding="utf-8-sig")
    assert '[string]$TaskName = "SolutionBox"' in text and "$taskName = $TaskName" in text

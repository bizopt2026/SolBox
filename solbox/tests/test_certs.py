import ipaddress
import socket
import ssl
import sys
import threading
import time

import httpx
import pytest
import uvicorn
from cryptography import x509

from app import cli
from app.certs import make_self_signed
from app.config import Settings
from app.main import create_app


def test_make_self_signed(tmp_path):
    crt, key = make_self_signed(tmp_path, ["10.231.1.254", "solbox.local"])
    cert = x509.load_pem_x509_certificate(crt.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("10.231.1.254")]
    assert san.get_values_for_type(x509.DNSName) == ["solbox.local"]
    assert cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value == "10.231.1.254"
    # 휴대폰 접속 준비 (2026-11-27): 애플 기기가 믿는 서버 인증서 (825일 이하, 서버 용도), 같은 폴더의 자체 발급자가 서명
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days <= 825
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    ca = x509.load_pem_x509_certificate((tmp_path / "ca.crt").read_bytes())
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    assert cert.issuer == ca.subject
    cert.verify_directly_issued_by(ca)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)  # 실제로 쓸 수 있는 쌍인지
    with pytest.raises(FileExistsError):
        make_self_signed(tmp_path, ["10.231.1.254"])


def test_make_self_signed_overwrite_and_validation(tmp_path):
    crt, _ = make_self_signed(tmp_path, ["127.0.0.1"])
    before = crt.read_bytes()
    make_self_signed(tmp_path, ["127.0.0.1"], overwrite=True)
    assert crt.read_bytes() != before
    with pytest.raises(ValueError):
        make_self_signed(tmp_path / "other", [])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_https_server_starts_with_generated_cert(tmp_path):
    crt, key = make_self_signed(tmp_path / "certs", ["127.0.0.1"])
    settings = Settings(
        data_dir=tmp_path / "data", secret_key="t", ai_worker_enabled=False, scheduler_enabled=False,
        ssl_certfile=str(crt), ssl_keyfile=str(key),
    )
    app = create_app(settings)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, ssl_certfile=str(crt), ssl_keyfile=str(key), log_level="warning",
    ))
    thread = threading.Thread(target=server.run)
    thread.start()
    try:
        deadline = time.time() + 20
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started
        ctx = ssl.create_default_context(cafile=str(crt))
        with httpx.Client(verify=ctx) as c:
            r = c.get(f"https://127.0.0.1:{port}/login")
        assert r.status_code == 200
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        app.state.engine.dispose()


def test_cli_make_cert_prints_config_lines(tmp_path, capsys):
    out = tmp_path / "certs"
    cli.main(["make-cert", "--host", "10.231.0.5", "--host", "solbox.local", "--out", str(out)])
    text = capsys.readouterr().out
    assert (out / "server.crt").is_file() and (out / "server.key").is_file()
    assert f'ssl_certfile = "{(out / "server.crt").resolve().as_posix()}"' in text
    assert f'ssl_keyfile = "{(out / "server.key").resolve().as_posix()}"' in text
    with pytest.raises(SystemExit) as e:  # 이미 있으면 덮어쓰지 않는다
        cli.main(["make-cert", "--host", "10.231.0.5", "--out", str(out)])
    assert "--overwrite" in str(e.value)
    cli.main(["make-cert", "--host", "10.231.0.5", "--out", str(out), "--overwrite"])


def _write_config(tmp_path, monkeypatch, extra=""):
    path = tmp_path / "config.toml"
    path.write_text(f'data_dir = "data"\nsecret_key = "k"\n{extra}', encoding="utf-8")
    monkeypatch.setenv("SOLBOX_CONFIG", str(path))
    return path


def test_cli_check_ok(tmp_path, monkeypatch, capsys):
    port = _free_port()
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    crt, key = make_self_signed(tmp_path / "certs", ["127.0.0.1"])
    _write_config(
        tmp_path, monkeypatch,
        f'port = {port}\nclaude_cmd = ["{sys.executable.replace(chr(92), "/")}"]\n'
        f'ssl_certfile = "{crt.as_posix()}"\nssl_keyfile = "{key.as_posix()}"\n',
    )
    cli.main(["check"])  # 문제가 없으면 종료 코드 없이 돌아온다
    out = capsys.readouterr().out
    assert "문제" not in out
    assert out.count("정상") >= 7
    assert not (tmp_path / "data" / "app.db").exists()  # 데이터를 바꾸지 않는다


def test_cli_check_reports_missing_cli_and_busy_port(tmp_path, monkeypatch, capsys):
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = busy.getsockname()[1]
    try:
        _write_config(tmp_path, monkeypatch, f'port = {port}\nclaude_cmd = ["no-such-claude-binary"]\n')
        with pytest.raises(SystemExit) as e:
            cli.main(["check"])
    finally:
        busy.close()
    assert e.value.code == 1
    lines = capsys.readouterr().out.splitlines()
    cli_line = next(line for line in lines if "Claude CLI" in line)
    assert "문제" in cli_line
    port_line = next(line for line in lines if "포트" in line)
    assert f"포트 {port}을 다른 프로그램이 쓰고 있습니다" in port_line
    assert any("Python" in line and "정상" in line for line in lines)
    assert any("지식" in line for line in lines)


def test_cli_check_missing_config_and_certificate(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SOLBOX_CONFIG", str(tmp_path / "none.toml"))
    with pytest.raises(SystemExit) as e:
        cli.main(["check"])
    assert e.value.code == 1
    assert "문제" in capsys.readouterr().out
    _write_config(tmp_path, monkeypatch, f'ssl_certfile = "{tmp_path / "x.crt"}"\nssl_keyfile = "{tmp_path / "x.key"}"\n')
    with pytest.raises(SystemExit):
        cli.main(["check"])
    assert any("인증서" in line and "문제" in line for line in capsys.readouterr().out.splitlines())


@pytest.mark.parametrize("bad", ["http://10.0.0.1", "10.0.0.1:8443", "a b", "host/x", "한글.local", "-x.local"])
def test_make_self_signed_rejects_bad_hosts(tmp_path, bad):
    with pytest.raises(ValueError) as e:
        make_self_signed(tmp_path, [bad])
    assert "접속 주소" in str(e.value)
    assert not list(tmp_path.iterdir())


def test_cli_make_cert_bad_host_is_korean_error(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["make-cert", "--host", "10.0.0.1:8443", "--out", str(tmp_path / "c")])
    assert "접속 주소" in str(e.value)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 권한")
def test_key_file_is_private(tmp_path):
    _, key = make_self_signed(tmp_path, ["127.0.0.1"])
    assert key.stat().st_mode & 0o777 == 0o600
    _, key = make_self_signed(tmp_path, ["127.0.0.1"], overwrite=True)
    assert key.stat().st_mode & 0o777 == 0o600


def test_failed_write_leaves_nothing(tmp_path, monkeypatch):
    import os as _os

    real = _os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("디스크 오류")
        return real(src, dst)

    monkeypatch.setattr("app.certs.os.replace", flaky)
    with pytest.raises(OSError):
        make_self_signed(tmp_path, ["127.0.0.1"])
    assert list(tmp_path.iterdir()) == []


def test_overwrite_failure_after_key_replaced_restores_old_pair(tmp_path, monkeypatch):
    crt, key = make_self_signed(tmp_path, ["127.0.0.1"])
    old_crt, old_key = crt.read_bytes(), key.read_bytes()
    import os as _os

    real = _os.replace
    calls = []

    def flaky(src, dst):
        calls.append(str(dst))
        if str(dst).endswith("server.crt"):
            raise OSError("디스크 오류")
        return real(src, dst)

    monkeypatch.setattr("app.certs.os.replace", flaky)
    with pytest.raises(OSError):
        make_self_signed(tmp_path, ["127.0.0.1"], overwrite=True)
    assert crt.read_bytes() == old_crt and key.read_bytes() == old_key  # 짝이 맞는 옛 파일 그대로
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ca.crt", "ca.key", "server.crt", "server.key"]


def test_pyproject_requires_recent_cryptography():
    from pathlib import Path

    assert '"cryptography>=42"' in (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")


def test_cli_check_fresh_install_is_ok(tmp_path, monkeypatch, capsys):
    _write_config(tmp_path, monkeypatch, f"port = {_free_port()}\n" f'claude_cmd = ["{sys.executable.replace(chr(92), "/")}"]\n')
    cli.main(["check"])  # data_dir 이 아직 없다
    out = capsys.readouterr().out
    assert "문제" not in out and "지식 폴더: 정상 (아직 없음" in out


def test_cli_check_flags_missing_knowledge_when_data_exists(tmp_path, monkeypatch, capsys):
    import sqlite3

    (tmp_path / "data").mkdir()
    sqlite3.connect(tmp_path / "data" / "app.db").close()
    _write_config(tmp_path, monkeypatch, f"port = {_free_port()}\n" f'claude_cmd = ["{sys.executable.replace(chr(92), "/")}"]\n')
    with pytest.raises(SystemExit):
        cli.main(["check"])
    assert any("지식 폴더" in line and "문제" in line for line in capsys.readouterr().out.splitlines())


def test_cli_check_port_permission_error(tmp_path, monkeypatch, capsys):
    from app import system

    class Boom:
        def __init__(self, *a): pass
        def setsockopt(self, *a): pass
        def bind(self, addr): raise PermissionError("denied")
        def close(self): pass

    monkeypatch.setattr(system.socket, "socket", Boom)
    _write_config(tmp_path, monkeypatch, 'port = 443\n')
    with pytest.raises(SystemExit):
        cli.main(["check"])
    out = capsys.readouterr().out
    assert "포트 443을 열 수 없습니다 (다른 프로그램이 쓰고 있거나 권한이 없습니다)" in out


def test_reissue_keeps_the_ca_unless_asked(tmp_path):
    """서버 인증서를 다시 만들어도 발급자(ca.crt)는 그대로라 기기에 설치한 신뢰가 이어진다. --new-ca 면 새 발급자."""
    make_self_signed(tmp_path, ["10.0.0.5"])
    ca1 = (tmp_path / "ca.crt").read_bytes()
    crt, _ = make_self_signed(tmp_path, ["10.0.0.6"], overwrite=True)
    assert (tmp_path / "ca.crt").read_bytes() == ca1
    x509.load_pem_x509_certificate(crt.read_bytes()).verify_directly_issued_by(x509.load_pem_x509_certificate(ca1))
    make_self_signed(tmp_path, ["10.0.0.6"], overwrite=True, new_ca=True)
    assert (tmp_path / "ca.crt").read_bytes() != ca1
    assert b"PRIVATE KEY" not in (tmp_path / "ca.crt").read_bytes() and b"PRIVATE KEY" in (tmp_path / "ca.key").read_bytes()


def test_ca_download_route(tmp_path):
    """/ca.crt: 로그인 없이 발급자 인증서(키 없음)를 내려받는다. 인증서를 쓰지 않는 서버는 404."""
    from fastapi.testclient import TestClient

    crt, key = make_self_signed(tmp_path / "certs", ["127.0.0.1"])
    settings = Settings(data_dir=tmp_path / "data", secret_key="t", ai_worker_enabled=False, scheduler_enabled=False,
                        ssl_certfile=str(crt), ssl_keyfile=str(key))
    app = create_app(settings)
    try:
        r = TestClient(app).get("/ca.crt")
        assert r.status_code == 200 and r.content == (tmp_path / "certs" / "ca.crt").read_bytes()
        assert r.headers["content-type"].startswith("application/x-x509-ca-cert") and b"PRIVATE KEY" not in r.content
    finally:
        app.state.engine.dispose()
    plain = create_app(Settings(data_dir=tmp_path / "data2", secret_key="t", ai_worker_enabled=False, scheduler_enabled=False))
    try:
        assert TestClient(plain).get("/ca.crt").status_code == 404
    finally:
        plain.state.engine.dispose()


def test_check_warns_before_the_server_cert_expires(tmp_path):
    from app import system

    crt, key = make_self_signed(tmp_path, ["127.0.0.1"], days=30)
    s = Settings(data_dir=tmp_path / "data", secret_key="t", ssl_certfile=str(crt), ssl_keyfile=str(key))
    name, problem, _ = system._check_cert(s)
    assert problem and "만료" in problem and "make-cert --overwrite" in problem
    crt2, key2 = make_self_signed(tmp_path / "b", ["127.0.0.1"])
    assert system._check_cert(Settings(data_dir=tmp_path / "d", secret_key="t", ssl_certfile=str(crt2), ssl_keyfile=str(key2)))[1] is None


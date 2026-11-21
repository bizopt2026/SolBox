"""시스템 점검: 관리자 화면과 `python -m app.cli check` 가 함께 쓰는 도우미."""
from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

from app import transcribe
from app.korean import josa
from app.config import Settings

MIN_PYTHON = (3, 11)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"  # pragma: no cover


def folder_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def app_version() -> str:
    try:
        return metadata.version("solbox")
    except metadata.PackageNotFoundError:
        return "알 수 없음"


def sqlite_target(path: Path) -> str:
    """SQLite 에 넘길 경로. file: URI 로 바꾸지 않는다 (UNC 경로는 URI 로 만들면 SQLite 가 거부한다)."""
    return str(path)


def open_readonly(path: Path, timeout: float = 5) -> sqlite3.Connection:
    """쓰지 못하는 연결로 연다. 파일이 있는지는 부른 쪽이 먼저 확인해야 한다."""
    conn = sqlite3.connect(sqlite_target(path), timeout=timeout)
    try:
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


Check = tuple[str, str | None, str]  # (항목, 문제 내용 또는 None, 덧붙일 설명)


def _check_python() -> Check:
    v = sys.version_info
    text = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < MIN_PYTHON:
        return ("Python 버전", f"{text} 입니다. 3.11 이상이 필요합니다.", "")
    return ("Python 버전", None, text)


def _check_data_dir(settings: Settings) -> Check:
    d = settings.data_dir
    if not d.exists():
        parent = d.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if os.access(parent, os.W_OK):
            return ("데이터 폴더", None, f"{d} (아직 없음. 처음 실행할 때 만들어집니다)")
        return ("데이터 폴더", f"{d} 를 만들 수 없습니다 (쓰기 권한이 없습니다).", "")
    try:
        with tempfile.TemporaryFile(dir=d):
            pass
    except OSError as e:
        return ("데이터 폴더", f"{d} 에 쓸 수 없습니다 ({e}).", "")
    return ("데이터 폴더", None, f"{d} (쓰기 가능)")


def _check_db(settings: Settings) -> Check:
    path = settings.db_path
    if not path.is_file():
        return ("데이터베이스", None, "아직 없음. 처음 실행할 때 만들어집니다")
    try:
        conn = open_readonly(path)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return ("데이터베이스", f"{path} 를 열 수 없습니다 ({e}).", "")
    return ("데이터베이스", None, str(path))


def _check_knowledge(settings: Settings) -> Check:
    if settings.knowledge_dir.is_dir():
        return ("지식 폴더", None, str(settings.knowledge_dir))
    if not settings.data_dir.exists() or not settings.db_path.exists():  # 처음 설치: 처음 실행할 때 만들어진다
        return ("지식 폴더", None, "아직 없음. 처음 실행할 때 만들어집니다")
    return ("지식 폴더", f"{settings.knowledge_dir} 가 없습니다. python -m app.cli rebuild-knowledge 로 만드세요.", "")


def _check_claude(settings: Settings, timeout: float = 30) -> Check:
    name = settings.claude_cmd[0] if settings.claude_cmd else ""
    exe = shutil.which(name) if name else None
    if exe is None:
        return ("Claude CLI", josa("'" + name + "'", "을/를") + f" 찾을 수 없습니다. 설치하고 PATH 를 확인하세요.", "")
    try:
        done = subprocess.run(
            [exe, *settings.claude_cmd[1:], "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
            stdin=subprocess.DEVNULL,  # 서버의 표준 입력을 물려받아 입력을 기다리며 멈추는 일이 없게 한다
        )
    except subprocess.TimeoutExpired:
        return ("Claude CLI", f"확인 못 함: --version 이 {timeout:g}초 안에 끝나지 않았습니다.", "")
    except (OSError, subprocess.SubprocessError) as e:
        return ("Claude CLI", f"--version 을 실행하지 못했습니다 ({e}).", "")
    if done.returncode != 0:
        return ("Claude CLI", f"--version 이 실패했습니다 (종료 코드 {done.returncode}).", "")
    return ("Claude CLI", None, (done.stdout or "").strip().splitlines()[0] if (done.stdout or "").strip() else exe)


def _check_transcriber(settings: Settings) -> Check:
    t = transcribe.get(settings)
    if settings.transcriber == "none":
        return ("음성 받아쓰기", None, "사용 안 함")
    if not t.available():
        return ("음성 받아쓰기", t.label(), "")
    return ("음성 받아쓰기", None, t.label())


def _check_cert(settings: Settings) -> Check | None:
    crt, key = settings.ssl_certfile, settings.ssl_keyfile
    if not crt and not key:
        return None
    if not crt or not key:
        return ("인증서", "ssl_certfile 과 ssl_keyfile 을 둘 다 적어야 합니다.", "")
    for p in (crt, key):
        if not Path(p).is_file():
            return ("인증서", f"파일이 없습니다: {p}", "")
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(crt, key)
    except (ssl.SSLError, OSError) as e:
        return ("인증서", f"인증서와 키를 읽을 수 없습니다 ({e}).", "")
    return ("인증서", None, str(crt))


def _check_port(settings: Settings) -> Check:
    s = socket.socket(socket.AF_INET6 if ":" in settings.host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":  # Windows 에서는 이 옵션이 이중 사용을 허용해 버린다
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((settings.host, settings.port))
    except PermissionError:
        return ("포트", f"포트 {settings.port}을 열 수 없습니다 (다른 프로그램이 쓰고 있거나 권한이 없습니다).", "")
    except OSError as e:
        if isinstance(e, socket.gaierror):
            return ("포트", f"주소 {settings.host} 를 쓸 수 없습니다 ({e}).", "")
        return ("포트", f"포트 {settings.port}을 다른 프로그램이 쓰고 있습니다 (서버가 이미 실행 중이면 정상입니다).", "")
    finally:
        s.close()
    return ("포트", None, f"{settings.host}:{settings.port} 사용 가능")


# 점검 항목마다 "무슨 점검인지"와 "문제가 있을 때 할 일" (관리자 시스템 화면과 `python -m app.cli check` 가 같은 항목·같은 말을 쓴다)
CHECK_INFO: dict[str, tuple[str, str]] = {
    "설정 파일 읽기": ("config.toml 을 읽어 서버 설정을 불러옵니다.", "config.toml 의 문법과 항목 이름을 config.example.toml 과 견주어 고치세요."),
    "Python 버전": (f"솔박스는 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 이상에서 돌아갑니다.", f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 이상을 설치하고 가상환경을 다시 만드세요."),
    "데이터 폴더": ("데이터베이스·올린 파일·지식 사본이 저장되는 폴더이고, 서버가 쓸 수 있어야 합니다.", "config.toml 의 data_dir 경로와 그 폴더의 쓰기 권한(서버를 돌리는 계정)을 확인하세요."),
    "데이터베이스": ("모든 기록이 든 app.db 파일을 열 수 있는지 봅니다.", "파일이 손상됐으면 서버를 멈추고 최근 백업으로 복원하세요 (운영 가이드의 복원 항목)."),
    "지식 폴더": ("AI 가 읽는 지식 사본 폴더입니다 (원본은 데이터베이스입니다).", "python -m app.cli rebuild-knowledge 로 데이터베이스에서 다시 만드세요."),
    "Claude CLI": ("AI 작업이 부르는 Claude 명령을 실행할 수 있는지 봅니다.", "Claude CLI 를 설치하고 PATH 를 확인한 뒤, 서버 계정으로 claude 를 한 번 실행해 로그인하세요."),
    "음성 받아쓰기": ("음성 파일을 글자로 바꾸는 선택 기능입니다. 꺼 두어도 다른 기능에는 영향이 없습니다.",
                 "쓰려면 config.toml 의 transcriber 를 켜고 python -m app.cli stt-setup 으로 모델을 받은 뒤 서버를 다시 시작하세요."),
    "인증서": ("HTTPS 접속에 쓰는 인증서와 키 파일을 읽을 수 있는지 봅니다.", "python -m app.cli make-cert 로 다시 만들거나 config.toml 의 ssl_certfile·ssl_keyfile 경로를 고치세요."),
    "포트": ("서버가 접속을 받을 주소와 포트를 쓸 수 있는지 봅니다.", "다른 프로그램이 같은 포트를 쓰고 있으면 config.toml 의 port 를 바꾸거나 그 프로그램을 끄세요."),
}


def describe(name: str) -> tuple[str, str]:
    """(점검 뜻, 문제일 때 할 일). 모르는 항목은 빈 글."""
    return CHECK_INFO.get(name, ("", ""))


def format_lines(results: list[Check]) -> list[str]:
    """명령줄 `check` 의 출력 줄들. 첫 줄은 항목마다 "이름: 정상 (…)" / "이름: 문제: …", 그 밑에 뜻과 (문제일 때) 할 일."""
    lines = []
    for name, problem, detail in results:
        meaning, todo = describe(name)
        if problem is None:
            lines.append(f"{name}: 정상" + (f" ({detail})" if detail else ""))
        else:
            lines.append(f"{name}: 문제: {problem}")
        if meaning:
            lines.append(f"  뜻: {meaning}")
        if problem is not None and todo:
            lines.append(f"  할 일: {todo}")
    return lines


def run_checks(load: Callable[[], Settings], skip_port: bool = False, claude_timeout: float = 30) -> list[Check]:
    """점검 결과 목록. 설정을 읽지 못하면 그 한 줄만 돌려준다. 서버를 띄우거나 데이터를 바꾸지 않는다."""
    try:
        settings = load()
    except SystemExit as e:
        return [("설정 파일 읽기", str(e.code).strip() or "읽을 수 없습니다.", "")]
    results: list[Check] = [("설정 파일 읽기", None, "")]
    results.append(_check_python())
    results.append(_check_data_dir(settings))
    results.append(_check_db(settings))
    results.append(_check_knowledge(settings))
    results.append(_check_claude(settings, claude_timeout))
    results.append(_check_transcriber(settings))
    cert = _check_cert(settings)
    if cert is not None:
        results.append(cert)
    if skip_port:  # 서버가 이미 그 포트로 이 화면을 보내고 있다: 새로 열어 보지 않고 쓰는 중이라고 알린다
        results.append(("포트", None, f"{settings.host}:{settings.port} 에서 이 서버가 응답하는 중입니다"))
    else:
        results.append(_check_port(settings))
    return results

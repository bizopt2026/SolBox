"""베타 J 묶음: 베타 I 검토(review7)에서 나온 고침의 시험.

휴리스틱의 기대값은 `tests/test_beta_i.py` 의 회귀표가 정본이다 (이 묶음이 더한 줄도 거기 있다). 여기에는 표로 표현할 수 없는 것만 둔다:
오류 분류의 선형 시간(1MB)과 길이 한도, 비밀 가리기의 길이 한도, `get_db` 닫기의 자동 쓰기, 점검 명령의 출력 한도.
선형 시험은 시계 한도가 아니라 100KB 대비 1MB 의 시간 비율로 본다 (부하가 걸린 기계에서도 흔들리지 않고, 제곱 시간이면 수백 배라 잡힌다).
비밀처럼 보이는 글자는 소스에 그대로 두지 않고 런타임에 이어 붙인다 (공개 저장소의 비밀 검사기가 시험 값을 비밀로 오인하지 않게).
"""
from __future__ import annotations

import io
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app import system
from app.ai import runner
from app.ai.runner import MAX_TEXT, _classify, _redact_all, classify_error, redact
from app.deck import has_internal_mark
from app.proposals import is_author_instruction

SK = "sk-" + "ant-"
GH = "gh" + "p_"
MARK = "Zq7pX2mK9"


def _ratio_ok(fn, make, factor=20.0, slack=3.0):
    """`make(n)` 이 만든 약 n 글자 글로 100KB 와 1MB 의 시간을 재서, 1MB 가 100KB 의 `factor` 배 + 여유 안에 끝나는지 본다."""
    small, big = make(100_000), make(1_000_000)
    t0 = time.perf_counter()
    fn(small)
    t_small = time.perf_counter() - t0
    t0 = time.perf_counter()
    fn(big)
    t_big = time.perf_counter() - t0
    assert t_big <= factor * t_small + slack, (t_small, t_big)


# ================= J1: 오류 분류 =================

CLASSIFY_SHAPES = {
    "env + spaces": lambda n: "ANTHROPIC_API_KEY" + " " * n + "x",
    "env + newlines": lambda n: "ANTHROPIC_API_KEY" + "\n" * n + "x",
    "env + tabs and colon": lambda n: "ANTHROPIC_API_KEY:" + "\t" * n + "x",
    "many env + spaces": lambda n: ("ANTHROPIC_API_KEY" + " " * 300 + "x ") * (n // 320),
    "repeated 401": lambda n: "401 " * (n // 4),
    "repeated status 401": lambda n: "status 401 " * (n // 11),
    "repeated proxy auth": lambda n: "proxy auth " * (n // 11),
    "repeated proxy authentication": lambda n: "proxy authentication " * (n // 21),
    "error + spaces": lambda n: "error" + " " * n + "x",
    "code=": lambda n: "code" + "= " * (n // 2),
    "api key": lambda n: "the api key " * (n // 12),
    "exit code": lambda n: "exit code: " * (n // 11),
    "invalid token": lambda n: "invalid token " * (n // 14),
}


@pytest.mark.parametrize("name", list(CLASSIFY_SHAPES))
def test_j1_classify_is_linear(name):
    """`ANTHROPIC_API_KEY` 뒤 `\\s*[:\\s]\\s*` 는 빈칸·줄바꿈이 이어지면 제곱 시간이었다 (16KB 에 16초)."""
    _ratio_ok(_classify, CLASSIFY_SHAPES[name])


def test_j1_classify_of_a_huge_text_is_capped_and_keeps_the_status_line():
    filler = "x" * 5_000_000
    assert classify_error(filler + "\nAPI Error: 401 invalid x-api-key") == "auth"  # 끝의 상태 줄
    assert classify_error("API Error (429)\n" + filler) == "limit"  # 처음의 상태 줄
    assert classify_error("ANTHROPIC_API_KEY" + " " * 5_000_000 + "x") == "unknown"
    t0 = time.perf_counter()
    classify_error("ANTHROPIC_API_KEY" + "\n" * 5_000_000)
    assert time.perf_counter() - t0 < 20


def test_j1_redact_caps_a_huge_text_keeping_head_and_tail():
    head, tail = f"first {SK}api03-{MARK}head", f"{GH}{MARK}abcdefghijklmnopqrstuvwx last"
    out = redact(head + "\n" + "a" * 3_000_000 + "\n" + tail)
    assert len(out) <= MAX_TEXT + 10 and out.startswith("first ") and out.endswith(" last")
    assert MARK not in out and out.count("***") >= 2
    assert redact("짧은 글") == "짧은 글" and redact(None) == ""


# ================= J6: 분류 (회귀표 밖의 짝) =================

@pytest.mark.parametrize("text,kind", [
    ("Provided OpenAI API key is invalid", "!auth"),
    ("Tavily API key is invalid", "!auth"),
    ("the weather api key is not set", "!auth"),
    ("API key is invalid for MCP server x", "!auth"),
    ("Invalid API key · Please run /login", "auth"),
    ("API Error: 407 Proxy Authentication Required", "network"),
    ("HTTP/1.1 407 Proxy Authentication Required", "network"),
    ("tunneling socket could not be established, statusCode=407", "network"),
    ("line 407: proxy_pass", "!network"),
    ("Quota exceeded for proxy auth tokens", "limit"),
    ("proxy auth plugin loaded", "!network"),
    ("proxy-authorization header set. API Error: 401", "auth"),
])
def test_j6_classification_pairs(text, kind):
    got = classify_error(text)
    assert (got != kind[1:]) if kind.startswith("!") else (got == kind), (text, got)


# ================= J4: sk-ant 키는 어디에 붙어 있어도 =================

@pytest.mark.parametrize("prefix", ["", "x", "_", "0", "KEY_", "한글", "\\n", "\\t", "=", ":", "'", '"', "(", "/", "-", "bad key:"])
def test_j4_anthropic_keys_are_masked_wherever_they_sit(prefix):
    key = f"{SK}api03-{MARK}abcDEF_ghi-jklMNOpqr"
    out = redact(prefix + key)
    assert MARK not in out and "***" in out, out
    assert redact(out) == out


@pytest.mark.parametrize("sep", [".", "/", "+", "="])
def test_j4_tail_after_punctuation_is_masked_too(sep):
    out = redact(f"{SK}api03-{MARK}{sep}secretpart{sep}more")
    assert "secretpart" not in out and "more" not in out and out == "***", out


def test_j4_other_distinctive_prefixes_without_a_left_boundary():
    for pre in ("KEY_", "x", "0", "한글"):
        assert MARK not in redact(f"{pre}{GH}{MARK}abcdefghijklmnopqrstuvwx"), pre
        assert MARK not in redact(f"{pre}sk-" + f"proj-{MARK}abcdefghijklmnop"), pre
    # 흔한 낱말은 그대로
    for text in ("ri" "sk-" "ant-colony", "task-management", "desk-antique shop", "ask-answer-1"):
        assert redact(text) == text, text


# ================= J7: 가리기가 더 가리는 곳과 건드리지 않는 곳 =================

@pytest.mark.parametrize("text", [
    f'{{"Authorization":["Bearer {MARK}xxxxxx"]}}', f"https://user:p@ss{MARK}@proxy:8080", f"http://{MARK}@proxy:8080", f'\\"api_key\\": \\"{MARK}\\"',
    f"SECRET_KEY_BASE={MARK}", f"API_KEY_V2={MARK}", f"ANTHROPIC_KEY={MARK}", f"ACCESS_KEY={MARK}", f"비밀번호: {MARK}", f"-u user:{MARK}",
    "-----BEG" + f"IN PRIVATE KEY-----\\nMIIE{MARK}\\n-----END PRIVATE KEY-----",
])
def test_j7_review_masks(text):
    out = redact(text)
    assert MARK not in out and redact(out) == out, out


def test_j7_pem_with_literal_newlines_masks_up_to_the_end_marker():
    out = redact("before -----BEG" + f"IN RSA PRIVATE KEY-----\\nMIIE{MARK}\\nabc\\n-----END RSA PRIVATE KEY----- after")
    assert out == "before *** after", out


def test_j7_userinfo_masks_up_to_the_last_at_sign_before_the_host():
    assert redact("https://user:p@ssX@proxy:8080/path") == "https://***@proxy:8080/path"
    assert redact("ssh://git@github.com/org/repo.git") == "ssh://git@github.com/org/repo.git"


def test_j7_short_names_match_only_as_whole_names():
    for text in ("compass: north", "bypass: true", "OLDPWD=/home/user", "passage: 12345678", "author: Kim1234567", "OAuth: https://claude.ai/x"):
        assert redact(text) == text, text
    for text in ("pass=hunter2x", "DB_PASS=hunter2x", "x-auth: hunter2x", "pwd=Hunter2Hunter2", "PGPASSWORD=hunter2x"):
        assert "***" in redact(text), text


def test_j7_every_pass_is_idempotent_on_pathological_shapes():
    for text in ('{"api_key": "abcdSECRET1234"}', "headers: {'x-api-key': 'abcdSECRET1234'}", '{"apiKey":{"value":"s3cr3tVALUE9xyzQ"}}', '"token": "***"'):
        once = redact(text)
        assert redact(once) == once, (text, once)


@pytest.mark.parametrize("name", ["pem literal", "userinfo", "userinfo at signs", "sk-ant tail", "curl -u", "max_token", "pass names", "cookie", "authorization list", "short name suffix"])
def test_j7_new_patterns_are_linear(name):
    shapes = {
        "pem literal": lambda n: ("-----BEG" + "IN PRIVATE KEY-----\\n") * (n // 28),
        "userinfo": lambda n: "a://" + "b:@" * (n // 3),
        "userinfo at signs": lambda n: ("a://" + "b" * 90 + ":" + "@" * 100) * (n // 200),
        "sk-ant tail": lambda n: (SK + "a./") * (n // 10),
        "curl -u": lambda n: "-u a:bbb " * (n // 9),
        "max_token": lambda n: "max_" * (n // 4) + "token",
        "pass names": lambda n: "pass: " * (n // 6),
        "cookie": lambda n: "cookie: " * (n // 8),
        "authorization list": lambda n: "Authorization:[" * (n // 15),
        "short name suffix": lambda n: "a_" * (n // 2) + "pass",
    }
    _ratio_ok(_redact_all, shapes[name])


# ================= J2·J3: 작성자 말투 (표 밖의 선형 시간) =================

AUTHOR_SHAPES = {
    "later fill": lambda n: "추후 " * (n // 3) + "업데이트",
    "real value": lambda n: "실제 " * (n // 3) + "교체",
    "deictic": lambda n: "이 " * (n // 2) + "금액 확인 필요",
    "doc words": lambda n: "절감률 " * (n // 4) + "근거 확인 필요",
    "numbers": lambda n: "30% " * (n // 4) + "근거 확인 필요",
    "visual": lambda n: "도면 " * (n // 3) + "확인 필요",
    "site": lambda n: "현장 " * (n // 3) + "사진 첨부 필요",
    "source need": lambda n: "법적 " * (n // 3) + "근거 필요",
}


@pytest.mark.parametrize("name", list(AUTHOR_SHAPES))
def test_j2_author_rules_are_linear(name):
    _ratio_ok(is_author_instruction, AUTHOR_SHAPES[name])


# ================= J8: 사본 경로 =================

DECK_SHAPES = {
    "unit then parens": lambda n: ("customers/100명(2026.11 기준) ") * (n // 24),
    "thousands": lambda n: "customers/1,200" * (n // 15),
    "lone units": lambda n: "solutions/3안 " * (n // 12),
    "knowledge units": lambda n: "knowledge/solutions/3일 " * (n // 22),
    "entities": lambda n: "solutions/&#51;" * (n // 15),
    "percent digits": lambda n: "solutions%2F%33" * (n // 15),
    "mirror files": lambda n: "data/knowledge/index.md " * (n // 24),
}


@pytest.mark.parametrize("name", list(DECK_SHAPES))
def test_j8_deck_rules_are_linear(name):
    _ratio_ok(has_internal_mark, DECK_SHAPES[name])


def test_j8_extension_is_looked_for_only_right_after_the_number_or_unit():
    assert not has_internal_mark("partners/2개사(kt.com)")
    assert has_internal_mark("partners/2개사.md")
    assert has_internal_mark("partners/2개사/card.md")
    assert not has_internal_mark("https://x.com/knowledge/a.md")  # 주소 속 낱말은 사본 파일이 아니다
    assert not has_internal_mark("solbox.db 파일")  # 경로 없이 이름만 있는 것은 둔다


# ================= J5: get_db 닫기는 자동 쓰기를 하지 않는다 =================

def test_j5_get_db_teardown_does_not_autoflush_a_pending_row(app, db):
    from app.db import get_db
    from app.models import Solution, User
    from tests.conftest import make_user

    make_user(db, "kim", "user")
    engine = app.state.engine
    engine.dispose()

    @event.listens_for(engine, "connect")
    def _short(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA busy_timeout=300")

    request = SimpleNamespace(app=app, state=SimpleNamespace(user=None))
    gen = get_db(request)
    session = next(gen)
    request.state.user = session.query(User).filter_by(username="kim").one()
    session.rollback()  # 로그인 사용자 줄의 값이 낡은 것으로 표시된다 → 닫기 전에 다시 읽는다
    session.add(Solution(name="롤백 뒤에 추가한 줄"))  # 아직 flush 하지 않은 새 줄
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda conn, cur, st, params, ctx, many: statements.append(st))
    locker = sqlite3.connect(str(app.state.settings.db_path), isolation_level=None)
    locker.execute("BEGIN IMMEDIATE")  # 진짜 쓰기 잠금: 쓰려 하면 막힌다
    try:
        t0 = time.perf_counter()
        gen.close()
        elapsed = time.perf_counter() - t0
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    assert elapsed < 2, elapsed
    assert not any(st.lstrip().upper().startswith("INSERT") for st in statements), statements
    assert any("FROM users" in st for st in statements)  # 사용자 줄은 다시 읽었다
    with app.state.session_factory() as check:
        assert check.query(Solution).count() == 0


# ================= J9: 점검 명령의 출력 한도 =================

FLOOD = "import sys\nchunk = 'v' * 65536\nfor _ in range(320):\n    sys.stdout.write(chunk)\nsys.stdout.flush()\n"  # 20MB


def test_j9_run_bounded_caps_stdout_and_keeps_draining():
    t0 = time.perf_counter()
    code, out = system.run_bounded([sys.executable, "-c", FLOOD], 60)
    assert code == 0 and len(out) == system.OUTPUT_CAP, len(out)  # 길이로 본다 (메모리 사용량이 아니라)
    assert time.perf_counter() - t0 < 50


def test_j9_run_bounded_caps_stderr_and_still_returns_stdout():
    script = "import sys\nsys.stderr.write('e' * 20_000_000)\nsys.stderr.flush()\nprint('1.2.3')\n"
    code, out = system.run_bounded([sys.executable, "-c", script], 60)
    assert code == 0 and out.strip() == "1.2.3"


def test_j9_run_bounded_returns_when_the_child_exits_even_if_a_grandchild_keeps_the_pipe(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX 프로세스 그룹 시험")
    script = "import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\nprint('9.9.9')\n"
    t0 = time.perf_counter()
    code, out = system.run_bounded([sys.executable, "-c", script], 3)
    assert code == 0 and out.strip() == "9.9.9"
    assert time.perf_counter() - t0 < 15


def test_j9_version_line_drops_control_characters(settings, monkeypatch):
    monkeypatch.setattr(system, "run_bounded", lambda cmd, timeout: (0, "\x00\x1b[31m1.2.3 (Claude Code)\x07\x00\x08\n\rsecond"))
    name, problem, detail = system._check_claude(settings)
    assert problem is None and detail == "[31m1.2.3 (Claude Code)", detail
    monkeypatch.setattr(system, "run_bounded", lambda cmd, timeout: (0, "\x00\x00\n\x1b\n"))
    name, problem, detail = system._check_claude(settings)
    assert problem is None and detail  # 읽을 글이 없으면 실행 파일 경로


def test_j9_windows_reap_does_not_close_pipes_a_reader_may_hold(monkeypatch):
    closed: list[str] = []

    class Pipe:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    class Proc:
        stdin, stdout, stderr = Pipe("in"), Pipe("out"), Pipe("err")

        def communicate(self, timeout=None):
            raise ValueError("pipes closed")

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(runner.sys, "platform", "win32")
    runner._reap(Proc())
    assert closed == []  # Windows: 읽기 스레드가 쥔 파이프의 close() 는 끝없이 멈출 수 있다 → 버린다
    monkeypatch.setattr(runner.sys, "platform", "linux")
    runner._reap(Proc())
    assert closed == ["in", "out", "err"]  # POSIX: 그대로 닫는다


def test_j9_reap_never_waits_forever_for_a_process_that_will_not_die(monkeypatch):
    import subprocess

    class Proc:
        stdin = stdout = stderr = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired("x", 5)

        def wait(self, timeout=None):
            if timeout is None:
                raise AssertionError("한정 없는 wait")
            raise subprocess.TimeoutExpired("x", timeout)

    runner._reap(Proc())  # 끝없이 기다리지 않고 돌아온다


def test_j9_fake_popen_shape_still_works(monkeypatch):
    class FakePopen:
        returncode = 0

        def __init__(self, cmd, **kw):
            self.stdout = io.BufferedReader(io.BytesIO(b"1.0\n"))
            self.stderr = io.BufferedReader(io.BytesIO(b""))

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(system.subprocess, "Popen", FakePopen)
    assert system.run_bounded(["x"], 1) == (0, "1.0\n")

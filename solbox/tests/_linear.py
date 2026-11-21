"""선형 시간 시험의 도구와 모양 모음 (베타 K).

정규식이 제곱 시간이면 한 번 부르는 데 몇 분이 걸리고, `re` 는 일하는 동안 GIL 을 놓지 않아서 같은 프로세스 안의 스레드·`signal.alarm` 으로는 멈출 수 없다.
그래서 모양마다 **자식 프로세스**가 10KB·100KB·1MB 글로 세 번 재고(줄마다 바로 내보낸다), 부모는 한 번에 `LEG_LIMIT_S` 초(기본 30초)를 넘기면 자식을 죽이고
"어느 모양의 몇 KB 에서 멈췄다" 로 바로 실패한다. 이 파일을 직접 실행하면(`python tests/_linear.py <묶음>`) 자식이 된다.
시험(`tests/test_beta_k.py`)은 부모만 부르고, 시간이 걸리는 규칙(분류·가리기·고객용 경로·작성자 말투)마다 한 묶음씩 돌린다.

비밀처럼 보이는 글자(`sk-ant-`·`ghp_`·PEM 시작 표지)는 소스에 그대로 두지 않고 이어 붙인다.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEG_LIMIT_S = 30.0  # 한 번(한 모양의 한 크기)에 허용하는 시간. 넘으면 자식을 죽이고 실패한다
START_LIMIT_S = 90.0  # 자식이 앱을 불러오는 시간
SIZES = (10_000, 100_000, 1_000_000)
SK = "sk-" + "ant-"
PEM = "-----BEG" + "IN PRIVATE KEY-----"


def _rep(s: str, n: int) -> str:
    return (s * (n // len(s) + 1))[:n]


# ---- 모양: 이름 → n 글자쯤의 글을 만드는 함수 ----
CLASSIFY = {
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
    # 베타 K
    "vendor lines": lambda n: "Tavily: invalid API key\n" * (n // 24),
    "vendor one line": lambda n: "Tavily: invalid API key " * (n // 24),
    "api key is missing, one line": lambda n: "api key is missing " * (n // 19),
    "invalid api key for": lambda n: "invalid api key for Tavily " * (n // 27),
    "env then newlines then words": lambda n: ("ANTHROPIC_API_KEY\n" + "\n" * 50 + "missing dependency foo\n") * (n // 90),
    "middle status": lambda n: "x" * (n // 2) + "\nAPI Error: 401\n" + "x" * (n // 2),
    # 베타 L
    "no vendor, one line": lambda n: "node: Invalid API key " * (n // 22),
    "for proper noun": lambda n: "Invalid API key for Foo " * (n // 24),
    "middle 407 files": lambda n: "x" * (n // 2) + "\n" + "error: 407 files could not be read\n" * (n // 34) + "x" * (n // 2),
    "middle 407 one line": lambda n: "x" * (n // 2) + " error: 407 files " * (n // 18) + "x" * (n // 2),
}

REDACT = {
    "API_KEY": lambda n: "API_KEY" * (n // 7),
    "long name": lambda n: "a" * n + "API_KEY",
    "Authorization": lambda n: "Authorization: " * (n // 15),
    "Bearer": lambda n: "Bearer " * (n // 7),
    "sk-": lambda n: "sk-" * (n // 3),
    "x-api-key": lambda n: "x-api-key " * (n // 10),
    "token:": lambda n: "token: " * (n // 7),
    "token=": lambda n: "token=a " * (n // 8),
    "quote": lambda n: 'token="' * (n // 7),
    "yaml": lambda n: "api_key:\n  " * (n // 11),
    "is": lambda n: "token is " * (n // 9),
    "cookie": lambda n: "cookie: " * (n // 8),
    "pem": lambda n: PEM * (n // 27),
    "query": lambda n: "?key=a&" * (n // 7),
    "brace": lambda n: "token={" * (n // 7),
    "userinfo": lambda n: "a://" + "b:" * (n // 2) + "x",
    "long value": lambda n: "token=" + "a" * n,
    "sentence": lambda n: "token: " + "a " * (n // 2),
    "pem literal": lambda n: (PEM + "\\n") * (n // 28),
    "userinfo b:@": lambda n: "a://" + "b:@" * (n // 3),
    "userinfo at signs": lambda n: ("a://" + "b" * 90 + ":" + "@" * 100) * (n // 200),
    "sk-ant tail": lambda n: (SK + "a./") * (n // 10),
    "curl -u": lambda n: "-u a:bbb " * (n // 9),
    "max_token": lambda n: "max_" * (n // 4) + "token",
    "pass names": lambda n: "pass: " * (n // 6),
    "authorization list": lambda n: "Authorization:[" * (n // 15),
    "short name suffix": lambda n: "a_" * (n // 2) + "pass",
    # 베타 K
    "compact json": lambda n: '{"api_key":"abcd1234","model":"x"}' * (n // 34),
    "compact json one value": lambda n: '{"api_key":"' + "a" * n + '","model":"x"}',
    "authorization quoted": lambda n: '{"Authorization":"Bearer abcdef123456","m":1}' * (n // 50),
    "argv flags": lambda n: "['--api-key', 'abcdefgh'] " * (n // 25),
    "curl -U": lambda n: "-U a:bbbbbb " * (n // 12),
    "proxy-user": lambda n: "--proxy-user a:bbbbbb " * (n // 22),
    "private key words": lambda n: "private key " * (n // 12),
    "host:port list": lambda n: "http://a.com:80,k@b " * (n // 20),
    "kebab userinfo": lambda n: "vertex://claude-sonnet-4-5@2025 " * (n // 32),
    "sk-ant glued letters": lambda n: ("desk-ant-" + "1" * 30 + " ") * (n // 40),
    "sk-ant no boundary": lambda n: SK * (n // 7),
    "bearer token name": lambda n: "AWS_BEARER_TOKEN_BEDROCK " * (n // 25),
    "korean token": lambda n: "토큰: 210000개 " * (n // 10),
    "path values": lambda n: "credentials=./a/b " * (n // 18),
    "huge capped middle": lambda n: ("x " * (n // 2)) + SK + "api03-abcDEF" + ("y " * (n // 2)),
    # 베타 L
    "escaped quotes": lambda n: 'password: "a \\"b\\" c" ' * (n // 22),
    "escaped quote run": lambda n: 'password: "' + '\\"' * (n // 2) + '"',
    "unterminated escaped": lambda n: 'password: "' + "a\\" * (n // 2),
}

DECK = {
    "sol/3": lambda n: _rep("solutions/3", n),
    "x.com chain": lambda n: "x.com/" + _rep("solutions/3/", n),
    "units": lambda n: "intranet/" + _rep("solutions/3개/", n),
    "many ext": lambda n: "q/" + _rep("solutions/3.a", n),
    "dots": lambda n: "intranet/solutions/3" + ".1" * (n // 2),
    "junk": lambda n: "=" * n + "/solutions/3",
    "seps": lambda n: "solutions" + "/" * n + "x",
    "dotseps": lambda n: "solutions/" + "./" * (n // 2) + "3",
    "general": lambda n: "q/" + _rep("solutions/3-", n),
    "percent": lambda n: "%25" * (n // 3) + "x",
    "entities": lambda n: _rep("&#47", n),
    "amp": lambda n: "&amp;" * (n // 5),
    "colons": lambda n: "a:" * (n // 2) + "/solutions/3",
    "hosts": lambda n: _rep("a-" * 140 + "/solutions/3q ", n),
    "labels": lambda n: _rep(":" * 280 + "/solutions/3q ", n),
    "combining": lambda n: "e" + "\u0301" * n,
    "invisible": lambda n: "\u200b" * n + "solutions/3",
    "dense junk": lambda n: _rep("=a:/solutions/3x", n),
    "long head": lambda n: "/" + "a/" * (n // 2) + "solutions/3",
    "unit then parens": lambda n: "customers/100명(2026.11 기준) " * (n // 24),
    "thousands": lambda n: "customers/1,200" * (n // 15),
    "lone units": lambda n: "solutions/3안 " * (n // 12),
    "knowledge units": lambda n: "knowledge/solutions/3일 " * (n // 22),
    "digit entities": lambda n: "solutions/&#51;" * (n // 15),
    "percent digits": lambda n: "solutions%2F%33" * (n // 15),
    "mirror files": lambda n: "data/knowledge/index.md " * (n // 24),
    # 베타 K
    "mirror file + particle, spaced": lambda n: "knowledge/index.md의 " * (n // 19),
    "mirror file glued": lambda n: "knowledge/index.md," * (n // 18),
    "mirror file kept in urls": lambda n: "https://kt.com/knowledge/index.md," * (n // 34),
    "db file kept": lambda n: "R&D/data/app.db," * (n // 16),
    "db file glued, anchored": lambda n: "/srv/solbox/data/app.db," * (n // 24) + "x",
    "url spans": lambda n: "https://a.com/," * (n // 15) + "knowledge/x.md",
    "undated": lambda n: "activities/undated/12" * (n // 21),
    "data shapes spaced": lambda n: "intake_work/5/1.md " * (n // 19),
    "data shapes glued, kept": lambda n: "decks/x," * (n // 8),
    "data subdir": lambda n: "R&D/data/models/x " * (n // 18),
    "data subdir glued": lambda n: "R&D/data/models/x," * (n // 18),
    # 베타 L
    "scheme url spans": lambda n: "s3://a/data/logs/x," * (n // 18),
    "scheme run": lambda n: "a" * n + "://data/logs/x",
    "data models names": lambda n: "data/models/small," * (n // 18),
    "data models kept": lambda n: "data/models/API," * (n // 16),
    "before-migrate": lambda n: "app.db.before-migrate-20261121-101500 " * (n // 39),
    "solbox head": lambda n: "solbox/data/logs/x," * (n // 19),
}

AUTHOR = {
    "docs": lambda n: "금액 " * (n // 3),
    "docs+": lambda n: "금액 " * (n // 3) + "확인 필요",
    "nospace": lambda n: "금액" * (n // 2) + " 확인 필요",
    "topic": lambda n: "금액은 " + "기재 " * (n // 3),
    "topic2": lambda n: "금액은" * (n // 3) + " 기재 예정x",
    "together": lambda n: "함께 " * (n // 3),
    "slide": lambda n: "이 슬라이드는 " + "삭제 " * (n // 3),
    "plain": lambda n: "가" * (n // 2) + " " + "나" * (n // 2),
    "docdot": lambda n: "금액 가 " * (n // 5),
    "verbs": lambda n: "확인 " * (n // 3),
    "todo": lambda n: " TODO" * (n // 5) + "x",
    "spaces": lambda n: "확인" + " " * n + "x",
    "tail spaces": lambda n: "금액 확인 필요" + " " * n + "x",
    "real": lambda n: "실제 " * (n // 3) + "교체",
    "plans": lambda n: "확인 예정 " * (n // 6),
    "doc spaces": lambda n: ("금액 확인" + " " * 100) * (n // 110),
    "later fill": lambda n: "추후 " * (n // 3) + "업데이트",
    "real value": lambda n: "실제 " * (n // 3) + "교체",
    "deictic": lambda n: "이 " * (n // 2) + "금액 확인 필요",
    "doc words": lambda n: "절감률 " * (n // 4) + "근거 확인 필요",
    "numbers": lambda n: "30% " * (n // 4) + "근거 확인 필요",
    "visual": lambda n: "도면 " * (n // 3) + "확인 필요",
    "site": lambda n: "현장 " * (n // 3) + "사진 첨부 필요",
    "source need": lambda n: "법적 " * (n // 3) + "근거 필요",
    # 베타 K
    "source need, 출처": lambda n: "인용 " * (n // 3) + "출처 필요",
    "source need one long word": lambda n: "가" * n + " 출처 필요",
    "deictic stay words": lambda n: "위 금액은 " + "계약 " * (n // 3) + "재확인 필요",
    "deictic plan": lambda n: "이 " * (n // 2) + "금액 수정 예정",
    "real extra": lambda n: "실제 " + "데이터 " * (n // 4) + "로 교체 필요",
    "visual plan": lambda n: "현장 " * (n // 3) + "사진 삽입 예정",
    # 베타 L
    "fixed placeholders": lambda n: "○○ " * (n // 3),
    "fixed OO run": lambda n: "O" * n,
    "fixed 오타": lambda n: "오타 " * (n // 3),
    "fixed brackets": lambda n: "(" * n + "입력",
    "fixed brackets doc": lambda n: "(금액 " * (n // 4) + "입력",
    "fixed 초안 dash": lambda n: "초안 " * (n // 3),
    # 베타 O: 공백을 줄이지 않고 돌리던 `이 …` 머리와 `오타` 낱말마다 줄 전체를 훑던 모양
    "fixed 이 x + spaces": lambda n: "이 x" + " " * n + "y",
    "fixed 오타 commas": lambda n: "오타," * (n // 3) + "x",
    "fixed 오타 slashes + paren": lambda n: "오타/" * (n // 3) + "x (a",
}

GROUPS = {"classify": CLASSIFY, "redact": REDACT, "deck": DECK, "author": AUTHOR}


def _function(group: str):
    sys.path.insert(0, str(ROOT))
    if group == "classify":
        from app.ai.runner import _classify

        return _classify
    if group == "redact":
        from app.ai.runner import _redact_all

        return _redact_all
    if group == "deck":
        from app.deck import has_internal_mark

        return has_internal_mark
    from app.proposals import is_author_instruction

    return is_author_instruction


def _exit_when_parent_dies() -> None:
    """부모(시험)가 죽으면 자식도 스스로 끝낸다 (베타 L): 제곱 시간 규칙에 갇힌 자식이 시험이 끝난 뒤에도 CPU 를 쓰며 남지 않게. `re` 가 GIL 을 놓지 않으면 이 스레드도 못 돌 수 있어,
    부모가 죽여 주는 쪽(`run_group` 의 `finally`)이 첫째 안전장치고 이것은 부모가 아예 사라진 경우의 둘째 장치다."""
    parent = os.getppid()  # 자식이 뜬 순간의 부모가 기준이다 (부모가 알려 준 번호와 견주지 않는다: 낡은 번호·재사용된 번호로 시작하자마자 끝나지 않게). 이 번호가 바뀔 때만 끝낸다

    def watch() -> None:
        while True:
            time.sleep(1.0)
            now = os.getppid()
            if now != parent:
                sys.stderr.write(f"_linear child: parent changed from {parent} to {now}; exiting\n")
                sys.stderr.flush()
                os._exit(3)

    threading.Thread(target=watch, daemon=True).start()


def _child(group: str) -> None:
    _exit_when_parent_dies()
    fn = _function(group)
    for name, make in GROUPS[group].items():
        for n in SIZES:
            text = make(n)
            print(f"start\t{name}\t{n}", flush=True)
            t0 = time.perf_counter()
            fn(text)
            print(f"done\t{name}\t{n}\t{time.perf_counter() - t0:.4f}", flush=True)


def run_group(group: str, *, leg_limit: float = LEG_LIMIT_S, root: Path | None = None) -> dict[str, dict[int, float]]:
    """한 묶음의 모든 모양을 자식 프로세스에서 재서 `{모양: {크기: 초}}` 를 돌려준다. 한 번이 `leg_limit` 초를 넘으면 자식을 죽이고 AssertionError."""
    root = Path(root) if root is not None else ROOT
    script = root / "tests" / "_linear.py"
    proc = subprocess.Popen([sys.executable, str(script), group], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(root))
    lines: queue.Queue = queue.Queue()
    errors: list[str] = []
    def pump() -> None:
        for ln in proc.stdout:
            lines.put(ln)
        lines.put(None)

    pump_thread = threading.Thread(target=pump, daemon=True)
    err_thread = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    pump_thread.start()
    err_thread.start()
    times: dict[str, dict[int, float]] = {}
    current, limit = "(앱을 불러오는 중)", START_LIMIT_S
    try:
        while True:
            try:
                line = lines.get(timeout=limit)
            except queue.Empty:
                raise AssertionError(f"{group}: '{current}' 가 {limit:g}초 안에 끝나지 않았다 (제곱 시간이거나 멈췄다)") from None
            if line is None:
                break
            kind, name, size, *rest = line.rstrip("\n").split("\t")
            if kind == "start":
                current, limit = f"{name} @ {int(size):,}자", leg_limit
            else:
                times.setdefault(name, {})[int(size)] = float(rest[0])
        if proc.wait(timeout=10) != 0:
            raise AssertionError(f"{group}: 자식이 실패했다\n{''.join(errors)[-2000:]}")
        expected = set(GROUPS[group])
        got = {name for name, t in times.items() if set(t) == set(SIZES)}
        assert got == expected and set(times) == expected, f"{group}: 돌려받은 모양이 기대와 다르다 (빠진 것 {sorted(expected - got)}, 더 온 것 {sorted(set(times) - expected)})"  # 자식이 일찍 끝나 모양이 빠져도 초록이 되지 않게
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        pump_thread.join(5)
        err_thread.join(5)
        for pipe in (proc.stdout, proc.stderr):
            pipe.close()
    return times


def check_ratios(group: str, times: dict[str, dict[int, float]], *, factor: float = 25.0) -> None:
    """10KB→100KB→1MB 가 크기에 비례해 늘어났는지 본다 (선형이면 10배, 제곱이면 100배). 몇 밀리초 규모의 흔들림은 여유로 받는다."""
    bad = []
    for name, t in times.items():
        small, mid, big = (t[n] for n in SIZES)
        if mid > factor * small + 1.0 or big > factor * mid + 3.0 or big > LEG_LIMIT_S:
            bad.append(f"{name}: {small:.3f}s → {mid:.3f}s → {big:.3f}s")
    assert not bad, f"{group}: 크기에 비례하지 않게 늘어났다\n" + "\n".join(bad)


if __name__ == "__main__":
    _child(sys.argv[1])

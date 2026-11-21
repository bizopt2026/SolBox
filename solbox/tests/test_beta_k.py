"""베타 K 묶음: 베타 J 검토(review8)에서 나온 고침의 시험.

휴리스틱의 기대값은 `tests/test_beta_i.py` 의 회귀표가 정본이다 (이 묶음이 더한 줄도 거기 있다). 여기에는 표로 표현할 수 없는 것만 둔다:
데이터 폴더 경로 모양 전부(아래 표), 가리기의 두 번 가려도 같음(속성 시험), 실제 실행기를 거친 오류 글, 긴 글 자르기, 점검 명령, 선형 시간(자식 프로세스, `tests/_linear.py`).
비밀처럼 보이는 글자(`sk-ant-`·`ghp_`·PEM 시작 표지·JWT·Basic 값)는 소스에 그대로 두지 않고 이어 붙인다.

앱이 `data_dir` 아래에 쓰는 경로 모양 (코드에서 읽은 것: ontology.py 의 mirror_path, intake.py, records.py, config.py, backup.py, main.py, routers/proposals.py):
  app.db (+ -wal, -shm)                                     DB
  knowledge/solutions/<id>/card.md                          사본
  knowledge/solutions/<sid>/files/<id>-<이름>.md            사본 (자료 글)
  knowledge/{customers,partners,projects,opportunities,bms}/<id>.md, knowledge/businesses/<id>.md (옛)
  knowledge/activities/<연도|undated>/<id>.md               사본 (날짜 없는 활동은 undated)
  knowledge/research/<날짜>-<market|tech|policy|suggest>-<id>.md
  uploads/solutions/<sid>/<fid><확장자>                      올린 자료
  uploads/intake/<iid>/<fid><확장자>[.transcript.txt]         올리기 파일과 받아쓴 글
  intake_work/<iid>/<번호>-<이름>.md|.jpg|.png, 파일목록.md   올리기 작업 폴더
  research_work/                                            웹 조사 작업 폴더 (비어 있음)
  decks/<pid>/deck-<날짜>-<시각>-<6자>[-internal].pptx        제안서 파일
  logs/app.log[.N]                                          로그
  models/…                                                  받아쓰기 모델
  (data 밖) backups/<날짜>-<시각>[.partial]/{app.db,uploads/…,decks/…,backup.json}   백업
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app import system
from app.ai import runner
from app.ai.runner import ClaudeError, ClaudeRunner, MAX_TEXT, _cap, classify_error, redact
from app.deck import has_internal_mark
from tests import _linear
from tests.test_beta_i import LEAVE, MASK

SK = "sk-" + "ant-"
GH = "gh" + "p_"
MARK = "Zq7pX2mK9"

# ================= K4: 데이터 폴더의 모든 경로 모양 (맨 것, 조사, 괄호, `근거=`) =================
SHAPES = {
    "app.db": "data/app.db", "app.db-wal": "data/app.db-wal", "app.db-shm": "data/app.db-shm",
    "mirror solution card": "knowledge/solutions/3/card.md", "mirror solution file": "knowledge/solutions/7/files/3-사양서.md",
    "mirror customer": "knowledge/customers/12.md", "mirror partner": "knowledge/partners/5.md", "mirror project": "knowledge/projects/8.md",
    "mirror legacy business": "knowledge/businesses/4.md", "mirror opportunity": "knowledge/opportunities/2.md", "mirror bm": "knowledge/bms/6.md",
    "mirror activity year": "knowledge/activities/2026/15.md", "mirror activity undated": "knowledge/activities/undated/12.md",
    "mirror research area": "knowledge/research/2026-11-20-market-9.md", "mirror research suggest": "knowledge/research/2026-11-20-suggest-10.md",
    "cited solution card": "solutions/3/card.md", "cited solution file": "solutions/7/files/3-사양서.md", "cited customer": "customers/12.md",
    "cited activity undated": "activities/undated/12.md", "cited activity year": "activities/2026/15.md", "cited research": "research/2026-11-20-tech-9.md",
    "cited solution dir": "solutions/3/", "cited files dir": "solutions/3/files/",
    "upload solution": "data/uploads/solutions/3/12.pdf", "upload intake": "data/uploads/intake/5/9.m4a", "upload transcript": "uploads/intake/5/9.m4a.transcript.txt",
    "intake work doc": "data/intake_work/5/1-회의록.md", "intake work photo": "intake_work/5/2-현장사진.jpg", "intake work list": "intake_work/5/파일목록.md",
    "research work": "data/research_work/", "deck customer": "data/decks/4/deck-20261121-101500-ab12cd.pptx",
    "deck internal": "decks/4/deck-20261121-101500-ab12cd-internal.pptx", "log": "data/logs/app.log", "log rotated": "logs/app.log.1",
    "model": "data/models/small/model.bin", "backup db": "backups/20261121-101500/app.db", "backup uploads": "backups/20261121-101500/uploads/solutions/3/4.pdf",
    "backup meta": "backups/20261121-101500/backup.json", "backup partial": "backups/20261121-101500.partial",
}
FORMS = {"bare": "{}", "particle": "{}에서", "paren": "({})", "eq": "근거={}"}
# 모양이 아주 일반적인 낱말이라 지우지 않는다 (알려진 한계): `data/` 없는 `models/…` 와 `data/` 없는 단독 `logs/app.log` 가 아닌 글
KNOWN_LEAKS: set[tuple[str, str]] = set()


@pytest.mark.parametrize("form", list(FORMS))
@pytest.mark.parametrize("name", list(SHAPES))
def test_k4_every_data_path_shape_is_removed(name, form):
    text = FORMS[form].format(SHAPES[name])
    if (name, form) in KNOWN_LEAKS:
        assert not has_internal_mark(text)
    else:
        assert has_internal_mark(text), text


def test_k4_mirror_file_in_a_url_span_is_kept_but_next_to_one_is_removed():
    assert not has_internal_mark("https://kt.com/knowledge/index.md")
    assert has_internal_mark("https://kt.com,knowledge/index.md")
    assert has_internal_mark("출처:https://kt.com/a;data/app.db")


# ================= K5: 두 번 가려도 같다 =================
def test_k5_compact_json_is_idempotent_and_keeps_the_rest():
    text = '{"api_key":"abcd1234","model":"x"}'
    once = redact(text)
    assert once == '{"api_key":"***","model":"x"}' and redact(once) == once


def test_k5_stderr_example_keeps_status_and_message_through_the_runner(settings, fake_claude):
    stderr = ('Error: request failed {"config":{"apiKey":"abcd1234","baseURL":"https://api.anthropic.com"},"status":400,'
              '"message":"prompt is too long: 210000 tokens > 200000 maximum"}')
    fake_claude.set(mode="error", stderr=stderr, exit_code=1)
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x")
    msg = str(e.value)
    assert "abcd1234" not in msg and '"status":400' in msg and "prompt is too long: 210000 tokens > 200000 maximum" in msg and '"apiKey":"***"' in msg
    assert redact(msg) == msg


def _renderings(t: str):
    yield t
    yield json.dumps({"message": t, "model": "x"}, separators=(",", ":"), ensure_ascii=False)
    yield json.dumps({"message": t, "model": "x"}, ensure_ascii=False)
    yield "FOO=1\n" + t + "\nBAR=2"
    yield "> " + t + "\n< HTTP/1.1 400"
    yield "Error: request failed " + t + " end"
    yield "[" + json.dumps(t) + ', "x"]'


@pytest.mark.parametrize("rows", [[t for t, _ in MASK], list(LEAVE)], ids=["MASK", "LEAVE"])
def test_k5_redact_twice_equals_redact_once_for_every_row_and_rendering(rows):
    bad = []
    for t in rows:
        for r in _renderings(t):
            once = redact(r)
            if redact(once) != once:
                bad.append((r, once))
    assert not bad, bad[:5]


NAMES = ["api_key", "apiKey", "token", "password", "secret", "x-api-key", "ANTHROPIC_API_KEY", "access_token", "Authorization", "auth_token", "client_secret", "passphrase", "credentials", "private_key"]
VALUES = ["abcd1234", MARK, "p@ss word1", "short1", "Bearer abcdef123456"]
PAIR_FORMS = ['{{"{n}":"{v}","model":"x","status":400}}', '{{"{n}": "{v}", "model": "x"}}', '{{"a":{{"{n}":"{v}","b":1}},"status":400}}', "{{'{n}': '{v}', 'model': 'x'}}",
              "{n}={v}\nmodel=x", "{n}: {v}\nmodel: x", "> {n}: {v}\n< ok", "{n}={v} model=x"]


@pytest.mark.parametrize("fmt", PAIR_FORMS)
def test_k5_name_value_pairs_are_idempotent_and_keep_neighbours(fmt):
    for n in NAMES:
        for v in VALUES:
            t = fmt.format(n=n, v=v)
            once = redact(t)
            assert redact(once) == once, (t, once)
            assert ("model" not in t or "model" in once) and ("status" not in t or "status" in once), (t, once)


# ================= K6/K7/K8 =================
def test_k6_userinfo_masks_only_the_userinfo():
    assert redact("https://user:Zq7pX2mK9@host?email=a@b.com") == "https://***@host?email=a@b.com"


@pytest.mark.parametrize("text", ["desk-" "ant-" + "1" * 30, SK + "api03-" + MARK + "abcdef", "x" + SK + "api03-" + MARK + "abcdef", "KEY_" + SK + MARK + "abcdef"])
def test_k6_sk_ant_boundary(text):
    out = redact(text)
    assert ("***" in out) == (not text.startswith("desk-"))


def test_k7_cap_never_shows_half_a_key_at_the_cut():
    key = SK + "api03-" + MARK + "abcdefghijklmnopqrstuvwxyz0123456789"
    for shift in range(0, 60):
        filler = "a " * ((MAX_TEXT // 2 - shift) // 2)
        text = filler + key + " " + "b " * MAX_TEXT
        assert MARK not in redact(text), shift
        text2 = "c " * MAX_TEXT + filler + key + "x" * 5000
        out2 = redact(text2)
        assert "abcdefghij" not in out2 and MARK not in out2, shift
    glued = "z" * (MAX_TEXT // 2 - 10) + key + "z" * (MAX_TEXT)  # 공백이 없는 글: 이어진 낱말
    assert MARK not in redact(glued)
    assert len(_cap("x " * MAX_TEXT)) <= MAX_TEXT + 10


def test_k7_classify_sees_a_status_line_in_the_cut_middle():
    filler = "x " * (MAX_TEXT)
    assert classify_error(filler + "\nAPI Error: 401\n" + filler) == "auth"
    assert classify_error(filler + "\nAPI Error (429)\n" + filler) == "limit"
    assert classify_error(filler + "\nplain\n" + filler) == "unknown"


# ================= K9: 선형 시간은 자식 프로세스에서 (제곱이면 30초 안에 실패한다) =================
@pytest.mark.parametrize("group", list(_linear.GROUPS))
def test_k9_linear(group):
    _linear.check_ratios(group, _linear.run_group(group))


def test_k9_harness_kills_a_quadratic_child_quickly(tmp_path):
    root = tmp_path / "scratch"
    (root / "tests").mkdir(parents=True)
    src = Path(_linear.__file__).read_text(encoding="utf-8")
    src = src.replace("GROUPS = {", "def _slow(text):\n    if len(text) >= 100_000:\n        time.sleep(300)  # 제곱 시간인 규칙을 흉내 낸다\n\n\nGROUPS = {", 1)
    src = src.replace("def _function(group: str):\n", "def _function(group: str):\n    return _slow\n", 1)
    (root / "tests" / "_linear.py").write_text(src, encoding="utf-8")
    t0 = time.perf_counter()
    with pytest.raises(AssertionError, match="끝나지 않았다"):
        _linear.run_group("classify", leg_limit=3, root=root)
    assert time.perf_counter() - t0 < 60


# ================= L11: 점검 명령 =================
def test_l11_kill_tree_skips_killpg_after_the_child_was_reaped(monkeypatch):
    calls = []
    monkeypatch.setattr(runner.os, "killpg", lambda *a: calls.append(a), raising=False)

    class Proc:
        pid = 4321
        returncode = 0

        def kill(self):
            calls.append("kill")

    runner._kill_tree(Proc())
    assert calls == ["kill"]
    Proc.returncode = None
    runner._kill_tree(Proc())
    assert calls[1][0] == 4321


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX")
def test_l11_run_bounded_kills_a_grandchild_while_the_child_is_still_a_zombie(tmp_path):
    pidfile = tmp_path / "g.pid"
    script = ("import subprocess, sys\np = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              f"open({str(pidfile)!r}, 'w').write(str(p.pid))\nprint('9.9.9')\n")
    code, out = system.run_bounded([sys.executable, "-c", script], 2)
    assert code == 0 and out.strip() == "9.9.9"
    import os
    pid = int(pidfile.read_text())
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail("손자가 남았다")


@pytest.mark.parametrize("raw,expect", [
    ("\x00\x1b[31m1.2.3 (Claude Code)\x07\x00\x08\n\rsecond", "1.2.3 (Claude Code)"), ("\x1b]0;title\x07v1\x1b[0m", "v1"), ("\x1b[1;32m2.0\x1b[0m\nx", "2.0"),
])
def test_l11_version_line_strips_full_ansi_sequences(settings, monkeypatch, raw, expect):
    monkeypatch.setattr(system, "run_bounded", lambda cmd, timeout: (0, raw))
    assert system._check_claude(settings)[2] == expect


# ================= L12: 비밀 모양 글자 =================
def test_l12_assembled_literals_do_not_sit_in_the_test_sources():
    import re
    pat = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.|Basic [A-Za-z0-9+/=]{12,}|sk-ant-[A-Za-z0-9]|gh[pousr]_[A-Za-z0-9]{20}|-----BEGIN")
    here = Path(__file__).parent
    for f in sorted(here.glob("test_beta_[h-k].py")) + [here / "_linear.py"]:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "pat = re.compile" in line:
                continue
            assert not pat.search(line), (f.name, i)

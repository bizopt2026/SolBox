"""베타 L 묶음: 베타 K 검토(review9)에서 나온 작은 고침의 시험.

휴리스틱의 기대값은 `tests/test_beta_i.py` 의 회귀표가 정본이다 (이 묶음이 더한 줄도 거기 있다: `DECK_*`·`AUTHOR_*`·`MASK`·`CLASSIFY`). 여기에는 표로 표현할 수 없는 것과
표에 넣지 않은 한계(알려진 한계를 일부러 못 박은 줄)만 둔다. 비밀처럼 보이는 글자는 소스에 그대로 두지 않는다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import system
from app.ai.runner import ClaudeError, ClaudeRunner, MAX_TEXT, classify_error, redact
from app.deck import has_internal_mark
from app.proposals import is_author_instruction
from tests import _linear


# ================= L1: 고객 글의 `data/<폴더>/` 는 남기고 앱의 모양만 지운다 =================
@pytest.mark.parametrize("text", [
    "data/logs/metrics 통합 수집", "big data/logs/분석", "sensor data/logs/alerts 연동", "open data/models/API 제공", "MLOps: data/models/serving 3계층",
    "데이터 레이크(data/models/logs) 구성", "S3 버킷 data/uploads/ 에 저장", "고객사 서버 /data/models/ 에 모델 배포", "D:\\data\\logs\\ 보관 90일",
    "HDFS /user/data/models/v1", "s3://kt-lake/data/logs/2026/", "gs://bucket/data/models/v3",
    # 같은 모양의 다른 꼴: 낱말 끝·조사·괄호
    "data/logs/metrics를 수집", "(data/models/serving)", "근거=data/logs/analytics",
    "hdfs://nn:8020/data/models/v1", "ftp://files.kt.com/data/app.db", "abfss://c@acct.dfs.core.windows.net/data/uploads/solutions/3/x.pdf", "wasbs://c/data/decks/4/x",
])
def test_l1_customer_data_paths_are_kept(text):
    assert not has_internal_mark(text), text


@pytest.mark.parametrize("text", [
    "data/app.db.before-migrate-20261121-101500", "data/app.db.before-migrate-20261121-101500-12", "app.db.before-migrate-20261121-101500에서", "(app.db.before-migrate-20261121-101500-3)",
    "data/uploads/intake/5/9.m4a.transcript.txt.tmp", "data/decks/4/deck-20261121-101500-ab12cd.pptx.part", "decks/4/deck-20261121-101500-ab12cd-internal.pptx.part",
    "data/models/.hf_home", "근거=data/models/.hf_home", "data/models/small", "data/models/large-v3-turbo/model.bin", "data/models/models--Systran--faster-whisper-small",
    "solbox/data/logs/x", "/srv/solbox/data/models/foo", "C:\\solbox\\data\\uploads\\x",
    "file:///srv/data/app.db", "file:///C:/solbox/data/app.db",
])
def test_l1_the_apps_own_shapes_are_removed(text):
    assert has_internal_mark(text), text


def test_l1_known_limits():
    # `data/` 없는 `models/.hf_home` 는 일반 글과 구별하지 못해 남는다 (모양이 아주 일반적이다)
    assert not has_internal_mark("models/.hf_home")
    # 앱의 모델 이름이 그대로 고객의 폴더 이름이면 지운다 (안전한 쪽)
    assert has_internal_mark("data/models/base/")
    # 주소 속의 앱 이름은 주소로 본다
    assert not has_internal_mark("s3://bucket/data/app.db")
    assert not has_internal_mark("hdfs://nn/data/models/small/x")


# ================= L2: 알려진 다른 업체 표지가 있을 때만 로그인이 아니다 =================
AUTH = [
    "node: Invalid API key", "anthropic.AuthenticationError: invalid api key", "Error [AuthenticationError]: Invalid API key", "Unhandled rejection: Invalid API key",
    "Execution error: Invalid API key", "C:\\Users\\kim\\AppData\\Roaming\\npm\\claude.cmd: Invalid API key", "Process exited: API key is missing", "Invalid API key. Check for typos",
    "Invalid API key for this organization", "Invalid API key for api.anthropic.com", "Request failed: Invalid API key for this organization",
]
NOT_AUTH = [
    "Tavily: invalid API key", "Brave Search error: the API key is missing", "Invalid API key for Tavily", "No API key provided for OpenAI", "Invalid API key for MCP server foo",
    "OPENAI api key is missing", "Invalid API key for Foo", "Serper: Invalid API key", "mcp__tavily__search failed: Invalid API key", "GitHub: Invalid API key", "Google Gemini: Invalid API key",
    "AWS Bedrock: Invalid API key", "Slack: invalid API key", "Notion API: Invalid API key",
]


@pytest.mark.parametrize("text", AUTH)
def test_l2_claude_auth_errors_without_a_vendor_mark_are_auth(text):
    assert classify_error(text) == "auth", text


@pytest.mark.parametrize("text", NOT_AUTH)
def test_l2_other_vendor_key_problems_are_not_auth(text):
    assert classify_error(text) != "auth", text


def test_l2_the_mark_must_be_on_the_same_line():
    assert classify_error("Tavily search finished\nInvalid API key") == "auth"
    assert classify_error("Invalid API key\nTavily search finished") == "auth"
    assert classify_error("Invalid API key  \nInvalid API key for Tavily") == "auth"  # 한 줄이라도 Claude 쪽이 있으면 로그인


def test_l2_a_vendor_name_inside_a_path_is_not_a_mark():
    assert classify_error("C:\\Users\\kim\\github\\tools\\claude.cmd: Invalid API key") == "auth"
    assert classify_error("/home/kim/google/bin/claude: Invalid API key") == "auth"


def test_l2_through_the_real_runner(settings, fake_claude):
    fake_claude.set(mode="error", stderr="node: Invalid API key", exit_code=1)
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x")
    assert e.value.kind == "auth"
    fake_claude.set(mode="error", stderr="Tavily: Invalid API key", exit_code=1)
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x")
    assert e.value.kind != "auth"


# ================= L3: 작성자 메모의 고정 낱말 [베타 Q: 낱말 규칙은 없앴다. 자리표시 모양만 남았다] =================
# 베타 Q: 자리표시 모양(`XX%`·`OO`·`○○`·`XX`)만 남았다. 낱말 규칙이 옮기던 줄은 아래 `FIXED_RETIRED` 로 옮겼다.
FIXED_MOVE = [
    'XX% 절감', '절감률 XX%', 'xx% 개선', 'OO', 'OO 절감', 'OO원 절감', 'OO개사 도입', '○○', '약 ○○억 규모', '○○ 개사', 'XX', 'XX억 규모', 'XX원',
]
# 사용자 결정 2026-11-22: 내부 단어 규칙을 뺐다. 이 줄들은 이제 슬라이드에 남는다 (글은 그대로, 옮기던 표에서 남기는 표로 옮겼다).
FIXED_RETIRED = [
    '작성자: 김대리', '문서 작성자 확인', '팀장님 확인 후 수정', '팀장님께 보고 필요', '법무팀 검토 필요', '법무팀 검토 후 삭제', '내부 검토용', '이 슬라이드는 내부 검토용입니다', '슬라이드 순서 변경', '슬라이드 순서 확인',
    '고객에게 보여주지 말 것', '보여주지 말 것', '로고 넣기', '고객 로고 넣기', '제목 수정 필요', '내부용', '이 슬라이드는 내부용', '이 슬라이드는 내부용입니다', '[내부용] 단가표', '(내부용)', '내부용: 원가 구조', '초안',
    '(초안)', '[초안]', '초안 - 2차', '초안: 영업팀', '제안 개요 (초안)', '제안 개요 [초안]', '오타', '오타 수정', '제목 오타', '제목 오타 확인', '오타 있음', '폰트 통일', '폰트 변경', '폰트 수정 필요',
    '폰트 확인',
]
FIXED_STAY = [
    "초안 검토 후 최종안 제출", "설계 초안 제출 2주", "초안 작성 후 협의", "AI가 회의록 초안을 작성", "내부망 연동", "내부망 보안 점검", "내부용 서버 구축", "고객사 내부용 포털 개발", "폰트 라이선스 포함",
    "폰트 크기 조절 기능", "오타 없는 OCR 인식률 99%", "오타 교정 기능 제공", "팀장급 PM 투입", "팀장 회의 참석", "작성 일정 협의", "제안서 작성 일정", "슬라이드 형태로 제공", "로고 삽입 위치 협의",
    "로그인 화면 (ID 입력)", "고객 데이터를 입력", "○○시청 도입 사례", "OOO 시스템 도입", "담당: OOO 팀", "OO건설 사례", "XX전자 협력", "OOXX", "XXL 사이즈", "서비스명: TBD Cloud",
    "시범 데이터는 실제 운영 데이터로 교체 예정",
]


@pytest.mark.parametrize("line", FIXED_MOVE)
def test_l3_placeholder_shapes_move_to_author_notes(line):
    assert is_author_instruction(line), line


@pytest.mark.parametrize("line", FIXED_RETIRED)
def test_l3_retired_word_rule_lines_stay(line):
    assert not is_author_instruction(line), line


@pytest.mark.parametrize("line", FIXED_STAY)
def test_l3_customer_bullets_sharing_a_word_stay(line):
    assert not is_author_instruction(line), line


def test_l3_known_limits():
    # 낱말 하나인 `OO` 는 줄 어디에 있어도 자리표시로 본다
    assert is_author_instruction("AB 3개 중 OO 선택")
    # 베타 Q: `초안` 같은 낱말은 어디에 있든 슬라이드에 둔다 (작성자의 "초안 확인 후 삭제" 도 둔다)
    assert not is_author_instruction("초안 확인 후 삭제")
    # `내부용` 도 낱말 규칙이 없다
    assert not is_author_instruction("내부용 자료는 별도 제공합니다")


def test_l3_real_value_topic_with_a_plan_stays_but_the_k2_rows_still_move():
    assert not is_author_instruction("시범 데이터는 실제 운영 데이터로 교체 예정")
    assert not is_author_instruction("시범 장비는 실제 양산 장비로 교체 예정")
    for line in ("실제 데이터로 교체 필요", "실제 사진으로 교체", "실제 고객사 로고로 교체", "실제 화면 캡처로 교체", "샘플 이미지 → 실제 이미지로 교체", "금액은 실제 값으로 교체 예정", "실제 데이터로 교체 예정"):
        assert is_author_instruction(line), line


def test_l3_placeholder_shapes_apply_to_every_text_slot_of_a_slide():
    from app.proposals import _move_instructions

    s = {"bullets": ["운영 지원", "XX% 절감"], "left": ["OO 절감"], "right": [], "rows": [["항목", "○○"]], "steps": [{"name": "1", "detail": "XX원"}],
         "key_message": "절감률 XX%", "needs_check": []}
    moved = _move_instructions(s)
    assert moved == 5 and s["bullets"] == ["운영 지원"] and s["left"] == [] and s["rows"] == [["항목", ""]] and s["steps"][0]["detail"] == "" and s["key_message"] == ""
    assert "XX% 절감" in s["needs_check"] and "○○" in s["needs_check"]


def test_l3_word_lines_stay_in_every_text_slot_of_a_slide():
    # 베타 Q: 내부 낱말(초안·작성자·팀장님·법무팀 검토 …)이 든 줄은 어느 칸에 있어도 슬라이드에 남는다
    from app.proposals import _move_instructions

    s = {"bullets": ["운영 지원", "초안"], "left": ["작성자: 김대리"], "right": [], "rows": [["항목", "내부 검토용"]], "steps": [{"name": "1", "detail": "법무팀 검토 필요"}],
         "key_message": "팀장님 확인 후 수정", "needs_check": []}
    assert _move_instructions(s) == 0
    assert s["bullets"] == ["운영 지원", "초안"] and s["left"] == ["작성자: 김대리"] and s["rows"] == [["항목", "내부 검토용"]] and s["steps"][0]["detail"] == "법무팀 검토 필요"
    assert s["key_message"] == "팀장님 확인 후 수정" and s["needs_check"] == []


# ================= L4: 끝나지 않은 창 제목 =================
@pytest.mark.parametrize("raw,expect", [
    ("\x1b]0;title 2.0.1\nnext", "title 2.0.1"),
    ("\x1b]0;2.0.1 (Claude Code)", "2.0.1 (Claude Code)"),
    ("\x1b]0;title\x07 2.0.1 (Claude Code)", "2.0.1 (Claude Code)"),
    ("\x1b]0;title\x1b\\2.0.1", "2.0.1"),
    ("\x1b]8;;http://x\x1b\\2.0.1\x1b]8;;\x1b\\", "2.0.1"),
    ("\x1b]", "(exe)"),
    ("\x1b]0;\nv9", "v9"),
])
def test_l4_unterminated_osc_does_not_cross_a_newline_or_blank_the_line(settings, monkeypatch, raw, expect):
    monkeypatch.setattr(system, "run_bounded", lambda cmd, timeout: (0, raw))
    monkeypatch.setattr(system.shutil, "which", lambda name: "(exe)")
    detail = system._check_claude(settings)[2]
    assert detail == expect, (raw, detail)


def test_l4_unterminated_osc_is_capped(settings, monkeypatch):
    monkeypatch.setattr(system, "run_bounded", lambda cmd, timeout: (0, "\x1b]0;" + "a" * 5000 + "\nv 1"))
    monkeypatch.setattr(system.shutil, "which", lambda name: "(exe)")
    detail = system._check_claude(settings)[2]
    assert len(detail) <= system.VERSION_LINE_MAX and "\x1b" not in detail


def test_l4_a_long_terminated_title_is_not_swallowed_whole():
    # 종료 표지가 200자 밖에 있으면 통째로 먹지 않는다 (제목 머리만 뺀다)
    out = system._clean_version_text("\x1b]0;" + "t" * 300 + "\x07 2.0.1")
    assert "2.0.1" in out and "\x1b" not in out


# ================= L5: 가운데 훑기는 짧은 글과 같은 제외를 쓴다, 이스케이프한 따옴표 =================
_PAD = ("x" * 99 + "\n") * 1500  # 약 15만 자


@pytest.mark.parametrize("mid,kind", [
    ("error: 407 files could not be read", "unknown"), ("error: 407 lines skipped", "unknown"), ("HTTP 407 files", "unknown"),
    ("API Error: 407 Proxy Authentication Required", "network"), ("HTTP 407 Proxy Authentication Required", "network"),
    ("API Error: 401", "auth"), ("HTTP 401 Unauthorized", "auth"), ("API Error: 429", "limit"), ('"status": 429', "limit"),
])
def test_l5_middle_scan_uses_the_short_path_rules(mid, kind):
    text = _PAD + mid + "\n" + _PAD + _PAD
    assert len(text) > MAX_TEXT * 2 and mid not in text[:MAX_TEXT // 2] and mid not in text[-MAX_TEXT // 2:]
    assert classify_error(text) == kind, mid


def test_l5_many_false_middle_lines_before_the_real_one_are_bounded_and_found_or_skipped_quickly():
    text = _PAD + "error: 407 files could not be read\n" * 3000 + _PAD + _PAD
    t0 = time.perf_counter()
    assert classify_error(text) == "unknown"
    assert time.perf_counter() - t0 < 10


@pytest.mark.parametrize("text,gone", [
    ('password: "a b \\"S3cr3t\\" c"', "S3cr3t"),
    ('Error: password: "a \\"S3cr3t\\" b" end', "S3cr3t"),
    ('{"password":"a\\"S3cr3t\\"b c","model":"x"}', "S3cr3t"),
    ("password: 'it\\'s S3cr3t'", "S3cr3t"),
    ('api_key = "x\\"y S3cr3t z"', "S3cr3t"),
])
def test_l5_quoted_values_with_escaped_quotes_are_fully_masked(text, gone):
    once = redact(text)
    assert gone not in once and "***" in once, once
    assert redact(once) == once


def test_l5_escaped_quotes_keep_the_neighbours():
    once = redact('{"password":"a\\"S3cr3t\\"b c","model":"x","status":400}')
    assert once == '{"password":"***","model":"x","status":400}'


# ================= L6: 선형 시험 도구 =================
def test_l6_run_group_requires_exactly_the_expected_shapes(tmp_path):
    root = tmp_path / "scratch"
    (root / "tests").mkdir(parents=True)
    src = Path(_linear.__file__).read_text(encoding="utf-8")
    src = src.replace("def _function(group: str):\n", "def _function(group: str):\n    return lambda text: None\n", 1)
    src = src.replace("for name, make in GROUPS[group].items():", "for name, make in list(GROUPS[group].items())[:2]:", 1)  # 자식이 일찍 끝나 모양이 빠진다
    (root / "tests" / "_linear.py").write_text(src, encoding="utf-8")
    with pytest.raises(AssertionError, match="기대와 다르다"):
        _linear.run_group("classify", leg_limit=10, root=root)


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        pass
    else:
        return stat.rsplit(")", 1)[1].split()[0] != "Z"  # 좀비는 끝난 것이다
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX")
def test_l6_child_exits_when_its_parent_dies(tmp_path):
    root = str(Path(_linear.__file__).resolve().parent.parent)
    child = f"import sys, time\nsys.path.insert(0, {root!r})\nfrom tests import _linear\n_linear._exit_when_parent_dies()\nopen({str(tmp_path / 'ready')!r}, 'w').close()\ntime.sleep(120)\n"
    # 베타 M: 자식은 뜰 때의 부모를 기준으로 잡으므로, 부모는 자식이 기준을 잡은 뒤(표지 파일)에 끝난다
    parent = ("import subprocess, sys, os, time\n"
              f"p = subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
              f"for _ in range(200):\n    if os.path.exists({str(tmp_path / 'ready')!r}):\n        break\n    time.sleep(0.1)\n"
              "print(p.pid, flush=True)\nos._exit(0)\n")
    out = subprocess.run([sys.executable, "-c", parent], capture_output=True, text=True, timeout=60).stdout.split()
    pid = int(out[0])
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.2)
        assert not _alive(pid), "부모가 죽었는데 자식이 남았다"
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass

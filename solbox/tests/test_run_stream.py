import threading
import time

import pytest

from app.ai.runner import ClaudeError, ClaudeRunner


def _runner(settings):
    return ClaudeRunner(settings.claude_cmd, settings.knowledge_dir)


def test_stream_accumulates_and_returns_result(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["안녕", "하세요", "!"])
    seen = []
    out = _runner(settings).run_stream("질문", on_text=seen.append)
    assert out == "안녕하세요!" and seen == ["안녕", "안녕하세요", "안녕하세요!"]
    argv = fake_claude.calls()[0]["argv"]
    assert "stream-json" in argv and "--verbose" in argv and "--include-partial-messages" in argv
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep" and "--strict-mcp-config" in argv
    assert fake_claude.calls()[0]["stdin"] == "질문"


def test_stream_result_overrides_accumulated_text(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["초안"], result="최종")
    assert _runner(settings).run_stream("x", on_text=lambda t: None) == "최종"


def test_stream_failure_midway_is_classified(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["가", "나"], stream_fail_after=1, stderr="usage limit reached")
    seen = []
    with pytest.raises(ClaudeError) as e:
        _runner(settings).run_stream("x", on_text=seen.append)
    assert e.value.kind == "limit" and seen == ["가"]


def test_stream_timeout_kills_process(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["가", "나"], chunk_delay_s=3)
    before = threading.active_count()
    with pytest.raises(ClaudeError) as e:
        _runner(settings).run_stream("x", on_text=lambda t: None, timeout_s=1)
    assert e.value.kind == "timeout"
    assert threading.active_count() == before


def test_stream_on_text_exception_does_not_abort(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["안녕", "하세요", "!"])
    calls = []

    def boom(text):
        calls.append(text)
        raise RuntimeError("boom")

    assert _runner(settings).run_stream("x", on_text=boom) == "안녕하세요!"
    assert len(calls) == 3


def test_stream_ignores_non_json_and_unknown_lines(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["가", "나"], noise_lines=True)
    seen = []
    assert _runner(settings).run_stream("x", on_text=seen.append) == "가나"
    assert seen == ["가", "가나"]


def test_stream_is_error_result_raises(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["가"], result="Invalid API key · Please login", result_is_error=True)
    with pytest.raises(ClaudeError) as e:
        _runner(settings).run_stream("x", on_text=lambda t: None)
    assert e.value.kind == "auth"


def test_stream_without_result_line_raises(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["가"], no_result=True)
    with pytest.raises(ClaudeError) as e:
        _runner(settings).run_stream("x", on_text=lambda t: None)
    assert e.value.kind == "unknown"


def test_stream_large_prompt_does_not_deadlock(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["ok"])
    prompt = "가" * 2_000_000
    assert _runner(settings).run_stream(prompt, on_text=lambda t: None) == "ok"
    assert fake_claude.calls()[0]["stdin"] == prompt


def test_stream_missing_binary(settings):
    r = ClaudeRunner(["no-such-claude-binary-xyz"], settings.knowledge_dir)
    with pytest.raises(ClaudeError) as e:
        r.run_stream("x", on_text=lambda t: None)
    assert e.value.kind == "not_installed"


def test_stream_deadline_holds_even_if_grandchild_keeps_stdout_open(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=["가"], chunk_delay_s=30, orphan_s=12)
    start = time.monotonic()
    with pytest.raises(ClaudeError) as e:
        _runner(settings).run_stream("x", on_text=lambda t: None, timeout_s=1)
    assert e.value.kind == "timeout"
    assert time.monotonic() - start < 6

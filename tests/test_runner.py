from pathlib import Path

import pytest

from app.ai.runner import ClaudeError, ClaudeRunner


def test_run_returns_result_and_sends_prompt_via_stdin(settings, fake_claude):
    fake_claude.set(mode="ok", result="안녕하세요")
    r = ClaudeRunner(settings.claude_cmd, settings.knowledge_dir)
    assert r.run("프롬프트 본문") == "안녕하세요"
    call = fake_claude.calls()[0]
    assert call["stdin"] == "프롬프트 본문" and "프롬프트 본문" not in call["argv"]
    assert call["argv"][-5:] == ["-p", "--output-format", "json", "--allowedTools", "Read,Glob,Grep"]
    assert Path(call["cwd"]).resolve() == settings.knowledge_dir.resolve()


@pytest.mark.parametrize("stderr,kind", [
    ("Please run /login to authenticate", "auth"),
    ("Claude usage limit reached", "limit"),
    ("getaddrinfo ENOTFOUND api.anthropic.com", "network"),
    ("something odd", "unknown"),
])
def test_error_classification(settings, fake_claude, stderr, kind):
    fake_claude.set(mode="error", stderr=stderr, exit_code=1)
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x")
    assert e.value.kind == kind
    assert stderr in str(e.value)


def test_is_error_json_with_zero_exit_is_classified(settings, fake_claude):
    fake_claude.set(mode="is_error", result="Claude usage limit reached")
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x")
    assert e.value.kind == "limit"


def test_garbage_stdout_is_unknown(settings, fake_claude):
    fake_claude.set(mode="garbage")
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x")
    assert e.value.kind == "unknown"


def test_message_keeps_last_500_chars(settings, fake_claude):
    fake_claude.set(mode="error", stderr="a" * 2000 + "END", exit_code=1)
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x")
    assert len(e.value.message) <= 500 and e.value.message.endswith("END")


def test_result_list_advances_per_call(settings, fake_claude):
    fake_claude.set(mode="ok", result=["하나", "둘"])
    r = ClaudeRunner(settings.claude_cmd, settings.knowledge_dir)
    assert [r.run("x"), r.run("x")] == ["하나", "둘"]


def test_missing_binary_is_not_installed(settings):
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(["no-such-claude-binary"], settings.knowledge_dir).run("x")
    assert e.value.kind == "not_installed"


def test_timeout(settings, fake_claude):
    fake_claude.set(mode="sleep", sleep_s=5)
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x", timeout_s=1)
    assert e.value.kind == "timeout"


def test_large_prompt_200kb(settings, fake_claude):   # Review Focus 4
    fake_claude.set(mode="ok", result="ok")
    big = "가" * 70_000
    ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run(big)
    assert fake_claude.calls()[0]["stdin"] == big

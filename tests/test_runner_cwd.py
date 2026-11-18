from pathlib import Path

import pytest

from app.ai import runner as runner_mod
from app.ai import tasks
from app.ai.runner import DEFAULT_TOOLS, WEB_TOOLS, ClaudeRunner, assert_tool_separation
from app.config import Settings
from pydantic import BaseModel


def test_run_uses_given_cwd(settings, fake_claude, tmp_path):
    fake_claude.set(mode="ok", result="ok")
    work = tmp_path / "work"
    ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x", allowed_tools=WEB_TOOLS, cwd=work)
    call = fake_claude.calls()[0]
    assert work.is_dir()
    assert Path(call["cwd"]).resolve() == work.resolve()
    assert call["argv"][call["argv"].index("--tools") + 1] == "WebSearch,WebFetch"
    assert call["argv"][call["argv"].index("--allowedTools") + 1] == "WebSearch,WebFetch"


def test_run_stream_uses_given_cwd(settings, fake_claude, tmp_path):
    fake_claude.set(mode="stream", chunks=["a"], result="a")
    work = tmp_path / "work2"
    ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run_stream(
        "x", on_text=lambda t: None, allowed_tools=WEB_TOOLS, cwd=work)
    assert Path(fake_claude.calls()[0]["cwd"]).resolve() == work.resolve()


def test_default_cwd_is_runner_cwd(settings, fake_claude):
    fake_claude.set(mode="ok", result="ok")
    ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x")
    assert Path(fake_claude.calls()[0]["cwd"]).resolve() == settings.knowledge_dir.resolve()


def test_empty_tools_argv(settings, fake_claude, tmp_path):
    fake_claude.set(mode="ok", result="ok")
    ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x", allowed_tools=(), cwd=tmp_path / "w")
    argv = fake_claude.calls()[0]["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--allowedTools") + 1] == ""
    assert_tool_separation(())


def test_mixed_tools_rejected(settings):
    r = ClaudeRunner(settings.claude_cmd, settings.knowledge_dir)
    with pytest.raises(ValueError, match="웹 도구와 파일 도구를 함께 쓸 수 없습니다."):
        r.run("x", allowed_tools=("Read", "WebFetch"))
    with pytest.raises(ValueError):
        r.run_stream("x", on_text=lambda t: None, allowed_tools=("Grep", "WebSearch"))
    assert_tool_separation(DEFAULT_TOOLS)
    assert_tool_separation(WEB_TOOLS)


class _Out(BaseModel):
    a: int = 0


def test_run_json_passes_tools_timeout_cwd(app, db, settings, monkeypatch, tmp_path):
    seen = []

    def spy(self, prompt, **kw):
        seen.append(kw)
        return '{"a": 1}'

    monkeypatch.setattr(ClaudeRunner, "run", spy)
    runner = app.state.runner
    work = tmp_path / "w"
    tasks.run_json(db, runner, "p", _Out, settings, allowed_tools=WEB_TOOLS, timeout_s=1800, cwd=work)
    assert seen[0] == {"allowed_tools": WEB_TOOLS, "timeout_s": 1800, "cwd": work}
    tasks.run_json(db, runner, "p", _Out, settings)
    assert seen[1] == {"allowed_tools": DEFAULT_TOOLS, "timeout_s": settings.ai_timeout_s, "cwd": None}


def test_settings_defaults(tmp_path):
    s = Settings(data_dir=tmp_path / "d", secret_key="k")
    assert s.research_timeout_s == 1800
    assert s.research_work_dir == s.data_dir / "research_work"
    assert runner_mod.WEB_TOOLS == ("WebSearch", "WebFetch")


@pytest.mark.parametrize("tools", [
    ("Read WebFetch",), (" Read", "WebFetch"), ("read", "WebFetch"), ("Read(*)", "WebFetch"),
    ("WebFetch", "Bash"), ("WebFetch", "Edit"), ("Bash",), ("WebFetch ",), ("Read,WebFetch",),
])
def test_guard_rejects_unknown_or_malformed_tools(settings, tools):
    with pytest.raises(ValueError):
        assert_tool_separation(tools)
    with pytest.raises(ValueError, match="허용되지 않은 도구입니다|함께"):
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x", allowed_tools=tools)


def test_guard_message_names_tool():
    with pytest.raises(ValueError, match="허용되지 않은 도구입니다: Bash"):
        assert_tool_separation(("Bash",))


@pytest.mark.parametrize("tools", ["Read,WebFetch", "Read", None, ("Read", 1), {"Read"}])
def test_guard_type_errors(settings, tools):
    with pytest.raises(TypeError):
        assert_tool_separation(tools)
    with pytest.raises(TypeError):
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x", allowed_tools=tools)

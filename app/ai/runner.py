"""Claude CLI 실행기. 프롬프트는 표준 입력으로 넘기고, 모든 실패는 ClaudeError 로 돌려준다."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

DEFAULT_TOOLS = ["Read", "Glob", "Grep"]

# (종류, 키워드) — 위에서부터 먼저 맞는 것이 이긴다.
_CLASSIFY = [
    ("auth", ("login", "authenticat", "unauthorized", "401")),
    ("limit", ("usage limit", "rate limit", "quota", "429")),
    ("network", ("enotfound", "econnrefused", "network", "getaddrinfo", "timed out")),
]


class ClaudeError(Exception):
    def __init__(self, kind: str, message: str = ""):
        super().__init__(message)
        self.kind = kind
        self.message = message

    def __str__(self) -> str:
        return self.message


def classify_error(text: str) -> str:
    lowered = text.lower()
    for kind, words in _CLASSIFY:
        if any(w in lowered for w in words):
            return kind
    return "unknown"


def _kill_tree(proc: subprocess.Popen) -> None:
    """프로세스와 가능한 한 그 자식까지 죽인다 (best effort)."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, check=False, timeout=10,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _reap(proc: subprocess.Popen) -> None:
    """죽인 프로세스를 회수한다. 손자 프로세스가 파이프를 쥐고 있어도 멈추지 않는다."""
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass
        proc.wait()


class ClaudeRunner:
    def __init__(self, cmd: list[str], cwd: Path):
        self.cmd = list(cmd)
        self.cwd = Path(cwd)

    def run(
        self,
        prompt: str,
        *,
        allowed_tools: list[str] = DEFAULT_TOOLS,
        timeout_s: int = 600,
    ) -> str:
        exe = shutil.which(self.cmd[0])
        if exe is None:
            raise ClaudeError("not_installed", f"Claude CLI를 찾을 수 없습니다: {self.cmd[0]}")
        argv = [exe, *self.cmd[1:], "-p", "--output-format", "json",
                "--allowedTools", ",".join(allowed_tools)]
        self.cwd.mkdir(parents=True, exist_ok=True)
        popen_kwargs: dict = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                argv, cwd=self.cwd, text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **popen_kwargs,
            )
        except FileNotFoundError as e:
            raise ClaudeError("not_installed", str(e)) from e
        except OSError as e:
            raise ClaudeError("unknown", str(e)[-500:]) from e

        try:
            stdout, stderr = proc.communicate(prompt, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            _reap(proc)
            raise ClaudeError("timeout", f"{timeout_s}초 안에 끝나지 않았습니다.") from None
        except BaseException:
            _kill_tree(proc)
            _reap(proc)
            raise

        stdout, stderr = stdout or "", stderr or ""
        data = None
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            pass

        if proc.returncode == 0 and data is not None and not data.get("is_error"):
            result = data.get("result")
            if isinstance(result, str):
                return result

        raw = stdout + "\n" + stderr
        if data is not None and isinstance(data.get("result"), str):
            raw = data["result"] + "\n" + stderr
        raise ClaudeError(classify_error(raw), raw.strip()[-500:])

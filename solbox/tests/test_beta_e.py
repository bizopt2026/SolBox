"""베타 E 묶음: 3.11 에서도 돌아야 하는 문법, AI 문, 문구 걸러내기 등의 회귀 시험."""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def _py_files():
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def fstring_problems(source: str) -> list[str]:
    """3.12 에서만 되는 f-string 쓰기(같은 따옴표 겹쓰기·치환 칸의 역슬래시·주석)를 찾는다.

    3.12 이상에서는 f-string 이 FSTRING_START/MIDDLE/END 토큰으로 나뉘므로 그것으로 센다.
    3.11 이하에서는 컴파일 자체가 같은 문법을 거절하므로 이 검사는 건너뛴다.
    """
    if sys.version_info < (3, 12):
        return []
    out: list[str] = []
    stack: list[dict] = []  # 열린 f-string: {"q": 따옴표, "depth": 치환 칸 깊이}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        t = tok.type
        if t == tokenize.FSTRING_START:
            q = tok.string[-1]
            if len(tok.string) >= 4 and tok.string[-3:] in ('"""', "'''"):
                q = tok.string[-3:]
            if stack and stack[-1]["depth"] > 0 and stack[-1]["q"] == q:
                out.append(f"{tok.start[0]}: 같은 따옴표 {q} 를 치환 칸 안에서 다시 씀")
            stack.append({"q": q, "depth": 0})
        elif t == tokenize.FSTRING_END:
            stack.pop()
        elif stack:
            top = stack[-1]
            if t == tokenize.OP and tok.string == "{":
                top["depth"] += 1
            elif t == tokenize.OP and tok.string == "}":
                top["depth"] = max(0, top["depth"] - 1)
            if top["depth"] > 0:
                if t == tokenize.COMMENT:
                    out.append(f"{tok.start[0]}: 치환 칸 안의 주석")
                if t == tokenize.STRING:
                    body = tok.string.lstrip("rbuRBU")
                    if body[:1] == top["q"][:1] and not (top["q"] == '"""' and body[:3] != '"""'):
                        out.append(f"{tok.start[0]}: 같은 따옴표 {top['q']} 문자열을 치환 칸 안에서 씀")
                    if "\\" in tok.string:
                        out.append(f"{tok.start[0]}: 치환 칸 안 문자열의 역슬래시")
    return out


def test_scanner_catches_nested_same_quotes():
    if sys.version_info < (3, 12):
        pytest.skip("3.12 이상에서만 토큰으로 검사한다 (3.11 은 컴파일이 거절)")
    bad = 'x = f"a {f("b")} c"\n'
    assert fstring_problems(bad)
    assert not fstring_problems("y = f\"a {f('b')} c\"\n")
    assert fstring_problems('z = f"{\'\\n\'.join(a)}"\n')


def test_no_py312_fstring_syntax_in_app():
    problems = []
    for p in _py_files():
        for msg in fstring_problems(p.read_text(encoding="utf-8")):
            problems.append(f"{p.relative_to(ROOT)}:{msg}")
    assert not problems, "\n".join(problems)


def test_app_compiles_on_py311_when_available():
    exe = shutil.which("python3.11")
    if exe is None:
        pytest.skip("python3.11 이 없다")
    r = subprocess.run([exe, "-m", "compileall", "-q", str(APP), str(ROOT / "tests")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_newer_stdlib_features():
    import re
    pat = re.compile(r"itertools\.batched|\bPath\([^)]*\)\.walk\(|\.walk\(top|copy\.replace\(|"
                     r"from typing import[^\n]*\boverride\b|^type \w+ *=|^\s*def \w+\[|^\s*class \w+\[", re.M)
    hits = []
    for p in _py_files():
        for m in pat.finditer(p.read_text(encoding="utf-8")):
            hits.append(f"{p.relative_to(ROOT)}: {m.group(0)}")
    assert not hits, hits

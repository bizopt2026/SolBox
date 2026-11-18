from pathlib import Path

import pytest

SCRIPTS = sorted((Path(__file__).parent.parent / "scripts").glob("*.ps1"))


def test_scripts_exist():
    assert {p.name for p in SCRIPTS} == {"run_server.ps1", "install_autostart.ps1", "open_firewall.ps1"}


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_ps1_is_utf8_with_bom_and_clean(path):
    data = path.read_bytes()
    # Windows PowerShell 5.1 은 BOM 이 없으면 한글을 시스템 코드 페이지로 잘못 읽는다.
    assert data.startswith(b"\xef\xbb\xbf")
    text = data[3:].decode("utf-8")
    assert "﻿" not in text
    bad = [c for c in text if (ord(c) < 32 and c not in "\r\n") or 127 <= ord(c) <= 159]
    assert not bad, f"탭·제어 문자가 있습니다: {bad[:5]!r}"
    assert "Linux에서 작성되어 실행 검증이 되지 않았습니다" in text
    assert "$ErrorActionPreference = \"Stop\"" in text


def test_gitattributes_forces_crlf_for_ps1():
    attrs = (Path(__file__).parent.parent / ".gitattributes").read_text(encoding="utf-8")
    assert "*.ps1 text eol=crlf" in attrs


def test_remove_paths_do_not_throw():
    root = Path(__file__).parent.parent / "scripts"
    auto = (root / "install_autostart.ps1").read_text(encoding="utf-8-sig")
    assert "Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue" in auto
    fw = (root / "open_firewall.ps1").read_text(encoding="utf-8-sig")
    assert "ValidateRange(1, 65535)" in fw

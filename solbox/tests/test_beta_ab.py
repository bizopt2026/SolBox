"""베타 AB: 로컬 PC 에서 "PPT 파일을 만들지 못했습니다." 만 뜨고 원인이 남지 않던 것.

- 실패 원인을 app.log 에 남긴다 (`log.exception`). 예전에는 로그에 아무것도 남지 않았다.
- 화면 문구가 원인에 따라 할 일을 알려 준다: 파일이 잡혀 있음(PermissionError) / 쓸 수 없음(OSError) / 그 밖(종류 이름).
- Windows 에서 백신·인덱서가 방금 쓴 파일을 잠깐 잡아 바꿔치기가 PermissionError 로 실패하는 것을 몇 번 다시 해 본다 (`deck.replace_retry`).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app import deck as deck_mod
from app.deck import replace_retry
from app.models import Proposal
from app.routers import proposals as proposals_router
from tests.conftest import outlined_proposal


def test_ab_replace_retry_survives_short_lock(tmp_path, monkeypatch):
    src, dst = tmp_path / "a.tmp", tmp_path / "a.pptx"
    src.write_text("x", encoding="utf-8")
    real, calls = Path.replace, {"n": 0}

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(32, "being used by another process")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    replace_retry(src, dst, wait=0)
    assert dst.read_text(encoding="utf-8") == "x" and calls["n"] == 3


def test_ab_replace_retry_gives_up(tmp_path, monkeypatch):
    src = tmp_path / "a.tmp"
    src.write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(PermissionError(32, "locked")))
    with pytest.raises(PermissionError):
        replace_retry(src, tmp_path / "a.pptx", tries=3, wait=0)


def test_ab_other_errors_are_not_retried(tmp_path, monkeypatch):
    calls = {"n": 0}

    def broken(self, target):
        calls["n"] += 1
        raise FileNotFoundError(2, "gone")

    monkeypatch.setattr(Path, "replace", broken)
    with pytest.raises(FileNotFoundError):
        replace_retry(tmp_path / "a", tmp_path / "b", wait=0)
    assert calls["n"] == 1


def _deck_files(settings, p):
    folder = settings.decks_dir / str(p.id)
    return sorted(x.name for x in folder.iterdir()) if folder.exists() else []


def test_ab_build_succeeds_through_a_short_windows_lock(user_client, db, settings, monkeypatch):
    p = outlined_proposal(db, settings)
    real, calls = Path.replace, {"n": 0}

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(32, "being used by another process")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(deck_mod.time if hasattr(deck_mod, "time") else __import__("time"), "sleep", lambda s: None)
    r = user_client.post(f"/proposals/{p.id}/deck", follow_redirects=False)
    assert r.status_code == 303 and "deck_built" in r.headers["location"]
    db.refresh(p)
    assert p.deck_name and not any(n.endswith((".tmp", ".part")) for n in _deck_files(settings, p))


@pytest.mark.parametrize("exc, expected", [
    (PermissionError(32, "locked"), "PowerPoint, 백신 검사 등"),
    (OSError(28, "No space left on device"), "디스크 공간이나 폴더 권한"),
    (ValueError("bad slide"), "원인: ValueError"),
])
def test_ab_failure_message_and_log(user_client, db, settings, monkeypatch, caplog, exc, expected):
    p = outlined_proposal(db, settings)

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(proposals_router, "_write_deck_pair", boom)
    with caplog.at_level(logging.ERROR):
        r = user_client.post(f"/proposals/{p.id}/deck")
    assert r.status_code == 400
    assert "PPT 파일을 만들지 못했습니다." in r.text and expected in r.text
    assert any("파일을 만들지 못했습니다" in rec.getMessage() and rec.exc_info for rec in caplog.records)
    db.refresh(p)
    assert not p.deck_name and _deck_files(settings, p) == []


def test_ab_unknown_error_text_does_not_leak_details(user_client, db, settings, monkeypatch):
    p = outlined_proposal(db, settings)
    monkeypatch.setattr(proposals_router, "_write_deck_pair", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("C:/secret/path <b>x</b>")))
    r = user_client.post(f"/proposals/{p.id}/deck")
    assert "RuntimeError" in r.text and "C:/secret/path" not in r.text and "<b>x</b>" not in r.text

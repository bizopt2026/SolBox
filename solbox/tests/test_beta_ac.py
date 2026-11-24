"""베타 AC: 로컬 PC 에서 PPT 만들기가 `PackageNotFoundError: Package not found at '…\\pptx\\templates\\default.pptx'` 로 실패했다.

설치 폴더의 빈 서식 파일을 python-pptx 가 열지 못한 것이다 (회사 PC 의 문서 보안·백신이 .pptx 를 바꾸거나 막는 경우가 흔하다).
- 빈 서식을 파이썬 파일 안의 글로 담아 메모리에서 연다 (`app.pptx_template`). 설치 폴더의 파일은 읽지 않는다.
- 점검(`python -m app.cli check`, 관리 › 시스템)에 "PPT 만들기" 를 더했다: 데이터 폴더에 써 보고 다시 읽는다.
"""
from __future__ import annotations

import hashlib
import zipfile
from datetime import date

import pptx.api

from app import system
from app.deck import build_deck
from app.pptx_template import SHA256, blank_presentation, template_bytes
from tests.conftest import outlined_proposal


def test_ac_embedded_template_is_the_original():
    data = template_bytes()
    assert hashlib.sha256(data).hexdigest() == SHA256
    assert data[:2] == b"PK" and len(blank_presentation().slide_layouts) == 11


def test_ac_deck_builds_when_installed_template_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pptx.api, "_default_pptx_path", lambda: str(tmp_path / "gone" / "default.pptx"))
    outline = {"title": "T", "slides": [{"kind": "cover", "title": "표지"}, {"kind": "bullets", "title": "본문", "bullets": ["가", "나"]}]}
    out = tmp_path / "x.pptx"
    assert build_deck(outline, out, audience="customer", customer_name="고객", today=date(2026, 11, 24)) >= 2
    assert zipfile.is_zipfile(out)


def test_ac_deck_route_builds_with_broken_installed_template(user_client, db, settings, tmp_path, monkeypatch):
    bad = tmp_path / "default.pptx"
    bad.write_bytes(b"encrypted by document security")
    monkeypatch.setattr(pptx.api, "_default_pptx_path", lambda: str(bad))
    p = outlined_proposal(db, settings)
    r = user_client.post(f"/proposals/{p.id}/deck", follow_redirects=False)
    assert r.status_code == 303 and "deck_built" in r.headers["location"]


def _by_name(results, name):
    return next(r for r in results if r[0] == name)


def test_ac_check_reports_ok_and_leaves_no_file(settings):
    name, problem, detail = _by_name(system.run_checks(lambda: settings, skip_port=True, claude_timeout=1), "PPT 만들기")
    assert problem is None and detail
    assert not (settings.decks_dir / ".check-pptx.pptx").exists()


def test_ac_check_reports_document_security(settings, monkeypatch):
    monkeypatch.setattr(zipfile, "is_zipfile", lambda p: False)
    problem = system._check_pptx(settings)[1]
    assert problem and "문서 보안" in problem
    assert not (settings.decks_dir / ".check-pptx.pptx").exists()


def test_ac_check_has_help_text():
    what, todo = system.describe("PPT 만들기")
    assert what and "DRM" in todo

"""베타 U: 제안 화면 — 구역 안 진행 상자(구성안·방향), PPT 만들기 성공 표시, 옛 파일 지우기 실패 허용, 확인 항목이 남은 장 바로가기."""
from datetime import datetime, timedelta
from pathlib import Path

from app.models import AIJob, Proposal
from tests.conftest import nloc, outlined_proposal, refining_proposal


def _worker_on(app):
    """처리기가 켜진 것처럼 그린다 (시험 서버는 꺼진 채 만들어진다: 켜져 있어야 대기 줄이 '꺼짐' 이 아니라 진행 상자로 보인다)."""
    app.state.settings.ai_worker_enabled = True


def _outline_job(user_client, db, p, status="pending"):
    user_client.post(f"/proposals/{p.id}/outline")
    db.refresh(p)
    job = db.get(AIJob, p.active_job_id)
    if status == "running":
        job.status, job.started_at = "running", datetime.now() - timedelta(seconds=35)
        db.commit()
    return job


def _section(html: str, key: str) -> str:
    start = html.index(f'id="{key}"')
    end = html.find('<details class="card section"', start + 10)
    return html[start:end if end > 0 else len(html)]


def test_u1_pending_outline_shows_box_inside_section(app, user_client, db, settings):
    _worker_on(app)
    p = refining_proposal(db, settings)
    _outline_job(user_client, db, p)
    sec = _section(user_client.get(f"/proposals/{p.id}").text, "outline")
    assert 'id="job-box-outline"' in sec and 'role="status"' in sec
    assert "AI 대기 1번째" in sec and "보통 2분 안에" in sec  # 구성안은 AI 검토까지 (베타 AI)
    assert f'hx-get="/proposals/{p.id}/progress?section=outline"' in sec and 'hx-trigger="every 2s"' in sec
    assert "대화를 마치고" not in sec  # 빈 상태 글은 진행 중에는 보이지 않는다


def test_u1_running_outline_shows_elapsed_and_hint(app, user_client, db, settings):
    _worker_on(app)
    p = refining_proposal(db, settings)
    _outline_job(user_client, db, p, "running")
    sec = _section(user_client.get(f"/proposals/{p.id}").text, "outline")
    assert "AI가 슬라이드 구성안을 만드는 중" in sec and "35초 경과" in sec and "보통 2분 안에 끝납니다" in sec


def test_u1_regenerate_hides_form_and_shows_box(app, user_client, db, settings):
    _worker_on(app)
    p = outlined_proposal(db, settings)
    _outline_job(user_client, db, p, "running")
    sec = _section(user_client.get(f"/proposals/{p.id}").text, "outline")
    assert 'id="job-box-outline"' in sec and 'class="regen"' not in sec and "8장" in sec


def test_u1_progress_fragment_and_refresh_on_finish(app, user_client, db, settings):
    _worker_on(app)
    p = refining_proposal(db, settings)
    job = _outline_job(user_client, db, p, "running")
    r = user_client.get(f"/proposals/{p.id}/progress?section=outline")
    assert r.status_code == 200 and 'id="job-box-outline"' in r.text and "HX-Refresh" not in r.headers
    # 다른 구역의 조각 요청은 이 작업의 것이 아니다 -> 새로 고침
    assert user_client.get(f"/proposals/{p.id}/progress?section=directions").headers["HX-Refresh"] == "true"
    job.status = "done"
    db.commit()
    r = user_client.get(f"/proposals/{p.id}/progress?section=outline")
    assert r.headers["HX-Refresh"] == "true"
    assert user_client.get("/proposals/99999/progress").headers["HX-Redirect"] == "/proposals"


def test_u1_failed_outline_shows_reason_and_retry_in_section(app, user_client, db, settings):
    p = refining_proposal(db, settings)
    job = _outline_job(user_client, db, p)
    job.status, job.error_kind = "failed", "timeout"
    db.commit()
    sec = _section(user_client.get(f"/proposals/{p.id}").text, "outline")
    assert 'class="error job-failed"' in sec and "슬라이드 구성안을 만들지 못했어요" in sec
    assert f'action="/proposals/{p.id}/outline"' in sec and "다시 시도" in sec


def test_u1_directions_section_has_same_box(app, user_client, db, settings):
    """방향 만들기도 같은 문제(쪽 맨 위 줄만 진행을 알림)였다: 구역 안에 같은 상자."""
    _worker_on(app)
    p = refining_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/directions")
    sec = _section(user_client.get(f"/proposals/{p.id}").text, "directions")
    assert 'id="job-box-directions"' in sec and "제안 방향을 만들" in sec or "AI 대기" in sec
    assert f"/proposals/{p.id}/progress?section=directions" in sec
    r = user_client.post(f"/proposals/{p.id}/directions", follow_redirects=False)
    assert r.status_code == 303  # 이미 진행 중: 알림으로 돌아온다


def test_u1_submit_buttons_have_busy_label(user_client, db, settings):
    p = refining_proposal(db, settings)
    html = user_client.get(f"/proposals/{p.id}").text
    assert 'data-busy-label="만드는 중…"' in html  # 제목 줄 "구성안 만들기" 와 대화 구역 버튼
    p2 = outlined_proposal(db, settings)
    html = user_client.get(f"/proposals/{p2.id}").text
    assert html.count('data-busy-label="만드는 중…"') >= 2  # 다시 만들기 · PPT 만들기
    js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "data-busy-label" in js and "data-orig-label" in js  # JS 없이도 폼은 그대로 제출된다 (속성만 추가)


def test_u2_success_line_inside_deck_box(user_client, db, settings):
    p = outlined_proposal(db, settings)
    r = user_client.post(f"/proposals/{p.id}/deck", follow_redirects=False)
    page = user_client.get(r.headers["location"])
    sec = _section(page.text, "deck")
    assert 'class="deck-done"' in sec and "PPT 파일을 만들었습니다" in sec and "고객용 PPT 내려받기" in sec
    # 알림 없이 다시 열면 성공 줄은 없다
    assert "PPT 파일을 만들었습니다" not in _section(user_client.get(f"/proposals/{p.id}").text, "deck")


def test_u2_success_line_on_outline_page(user_client, db, settings):
    p = outlined_proposal(db, settings)
    r = user_client.post(f"/proposals/{p.id}/deck", data={"back": "outline"}, follow_redirects=False)
    page = user_client.get(r.headers["location"]).text
    assert 'class="deck-done"' in page


def test_u2_old_file_unlink_failure_is_best_effort(user_client, db, settings, monkeypatch, caplog):
    p = outlined_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/deck")
    db.refresh(p)
    first = p.deck_name
    real = Path.unlink

    def locked(self, *a, **k):  # Windows: 파워포인트가 연 파일
        if self.name.startswith(first.removesuffix(".pptx")):
            raise PermissionError(13, "in use", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked)
    with caplog.at_level("WARNING"):
        r = user_client.post(f"/proposals/{p.id}/deck", follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == f"/proposals/{p.id}?notice=deck_built#deck"
    db.refresh(p)
    assert p.deck_name != first and (settings.decks_dir / str(p.id) / p.deck_name).is_file()
    assert "이전 제안서 파일을 지우지 못했습니다" in caplog.text
    monkeypatch.undo()
    assert user_client.get(f"/proposals/{p.id}/deck").content[:2] == b"PK"


def test_u2_warning_links_to_slides_with_open_checks(user_client, db, settings):
    p = outlined_proposal(db, settings)  # 2번 장(색인 2)에만 확인할 점 하나
    html = user_client.get(f"/proposals/{p.id}").text
    assert "확인하지 않은 항목이 1개 남아 있습니다" in html
    assert f'href="/proposals/{p.id}/outline?open=2#slide-2"' in html and "3장(1개)" in html
    page = user_client.get(f"/proposals/{p.id}/outline?open=2")
    assert 'id="slide-2"' in page.text


def test_u2_warning_has_no_links_when_all_checked(user_client, db, settings):
    p = outlined_proposal(db, settings)
    slides = [dict(s) for s in p.outline["slides"]]
    slides[2]["checked"] = list(slides[2]["needs_check"])
    p.outline = {**p.outline, "slides": slides}
    db.commit()
    html = user_client.get(f"/proposals/{p.id}").text
    assert "확인할 장 바로가기" not in html

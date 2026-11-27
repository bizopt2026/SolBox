"""화면 재디자인 4차: 제안 작업 화면의 제목 줄 동작·단계 막대·맥락 카드, 올리기 진행 띠·받아쓰기 진행, 리서치 실행 화면, 로그인·오류 화면."""
import io
import re
from datetime import datetime

import pytest
from fastapi import UploadFile

from app import intake as intake_lib
from app.models import AIJob, Intake, User
from tests.conftest import finished_run, make_research_world, outlined_proposal, refining_proposal, ready_intake


def _primary(html):
    """제목 줄의 다음 동작 덩어리."""
    return re.search(r'<span id="primary-action".*?</span>', html, re.S).group(0)


# ---- 제안: 제목 줄의 다음 동작 ----

def test_header_primary_new_status_makes_directions(user_client, db, settings):
    p = refining_proposal(db, settings)
    p.directions, p.selected_index, p.status = None, None, "new"
    db.commit()
    block = _primary(user_client.get(f"/proposals/{p.id}").text)
    assert f'action="/proposals/{p.id}/directions"' in block and "방향 만들기" in block
    assert user_client.post(f"/proposals/{p.id}/directions").status_code in (200, 303)


def test_header_primary_directions_ready_points_to_choice(user_client, db, settings):
    p = refining_proposal(db, settings)
    p.selected_index, p.status = None, "directions"
    db.commit()
    block = _primary(user_client.get(f"/proposals/{p.id}").text)
    assert 'href="#directions"' in block and "방향 고르기" in block


def test_header_primary_refining_makes_outline(user_client, db, settings):
    p = refining_proposal(db, settings)
    block = _primary(user_client.get(f"/proposals/{p.id}").text)
    assert f'action="/proposals/{p.id}/outline"' in block and "구성안 만들기" in block


def test_header_primary_outlined_builds_deck(user_client, db, settings):
    p = outlined_proposal(db, settings)
    block = _primary(user_client.get(f"/proposals/{p.id}").text)
    assert f'action="/proposals/{p.id}/deck"' in block and "PPT 만들기" in block
    assert user_client.post(f"/proposals/{p.id}/deck").status_code in (200, 303)


def test_header_primary_deck_downloads_then_rebuilds_when_stale(user_client, db, settings):
    p = outlined_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/deck")
    db.refresh(p)
    block = _primary(user_client.get(f"/proposals/{p.id}").text)
    assert f'href="/proposals/{p.id}/deck"' in block and "PPT 내려받기" in block
    assert user_client.get(f"/proposals/{p.id}/deck").status_code == 200
    p.outline = {**p.outline, "title": "바뀐 제목"}
    p.outline_updated_at = datetime(2099, 1, 1)
    db.commit()
    html = user_client.get(f"/proposals/{p.id}").text
    assert "PPT 내려받기" not in html and "PPT 다시 만들기" in _primary(html)


def test_header_primary_disabled_while_ai_works(user_client, db, settings):
    p = refining_proposal(db, settings)
    job = AIJob(kind="proposal_chat", input={"proposal_id": p.id}, status="running", priority=0)
    db.add(job)
    db.flush()
    p.active_job_id = job.id
    db.commit()
    assert "disabled" in _primary(user_client.get(f"/proposals/{p.id}").text)


# ---- 제안: 단계 막대·맥락 카드 ----

def test_stepper_steps_are_in_page_anchors_with_state(user_client, db, settings):
    p = refining_proposal(db, settings)
    nav = re.search(r'<nav class="stepbar".*?</nav>', user_client.get(f"/proposals/{p.id}").text, re.S).group(0)
    for key in ("target", "directions", "chat"):
        assert f'href="#{key}"' in nav
    assert nav.count("step done") == 2 and nav.count('aria-current="step"') == 1 and "step current" in nav
    assert 'href="#outline"' not in nav  # 구성안이 없으면 아직 못 가는 단계는 링크가 없다


def test_context_cards_when_a_direction_is_selected(user_client, db, settings):
    p = refining_proposal(db, settings)
    html = user_client.get(f"/proposals/{p.id}").text
    for label in ("선택한 방향", "대상 요약", "연결된 것"):
        assert f'aria-label="{label}"' in html
    side = html.split('<aside class="daside"', 1)[1]
    assert "전력비 절감 패키지" in side and 'href="#directions"' in side and "바꾸기" in side and "근거 0 · 확인할 점 0" in side


def test_context_cards_without_selection_have_no_direction_card(user_client, db, settings):
    p = refining_proposal(db, settings)
    p.selected_index, p.status = None, "directions"
    db.commit()
    html = user_client.get(f"/proposals/{p.id}").text
    assert 'aria-label="선택한 방향"' not in html and 'aria-label="대상 요약"' in html


def test_chat_bubbles_carry_time_meta(user_client, db, settings):
    from app.models import ProposalMessage
    p = refining_proposal(db, settings)
    db.add(ProposalMessage(proposal_id=p.id, role="user", content="공조만 하자", created_at=datetime(2026, 11, 20, 14, 14)))
    db.commit()
    html = user_client.get(f"/proposals/{p.id}").text
    assert re.search(r"나 · (2026년 )?11월 20일 14:14", html) and "data-chat-scroll" in html


# ---- 올리기 ----

def _bundle(db, settings, files, status, **kw):
    user = db.query(User).filter_by(username="kim").one()
    it, _ = intake_lib.create_intake(db, settings, user, "", [UploadFile(io.BytesIO(d), filename=n) for n, d in files])
    it.status = status
    for k, v in kw.items():
        setattr(it, k, v)
    db.commit()
    return it


@pytest.mark.parametrize("status,expected", [
    ("uploaded", "current"), ("ready", "current"), ("applied", "done"), ("failed", "failed"),
])
def test_intake_timeline_state_per_status(user_client, db, settings, status, expected):
    it = _bundle(db, settings, [("a.txt", b"x")], status)
    html = user_client.get(f"/intake/{it.id}").text
    strip = re.search(r'<ol class="timeline".*?</ol>', html, re.S).group(0)
    states = re.findall(r'<li class="tl (\w+)"', strip)
    assert len(states) == 4 and f'data-status="{status}"' in strip
    at = {"uploaded": 0, "ready": 2, "applied": 3, "failed": 1}[status]
    assert states[at] == expected
    assert states[:at].count("done") == at if status != "applied" else states == ["done"] * 4


def test_intake_timeline_discarded_is_all_todo_with_note(user_client, db, settings):
    it = _bundle(db, settings, [("a.txt", b"x")], "discarded")
    strip = re.search(r'<ol class="timeline".*?</ol>', user_client.get(f"/intake/{it.id}").text, re.S).group(0)
    assert re.findall(r'<li class="tl (\w+)"', strip) == ["todo"] * 4 + ["note"]


def test_header_actions_per_intake_state(user_client, db, settings):
    up = _bundle(db, settings, [("a.txt", b"x")], "uploaded")
    html = user_client.get(f"/intake/{up.id}").text
    assert f'action="/intake/{up.id}/analyze"' in html and "정리 시작" in html
    done = _bundle(db, settings, [("a.txt", b"x")], "applied")
    assert 'href="/intake/new"' in user_client.get(f"/intake/{done.id}").text


def test_transcription_progress_renders_bar(user_client, db, settings):
    it = _bundle(db, settings, [("회의.m4a", b"\x00\x01")], "analyzing")
    job = AIJob(kind="intake_analyze", input={"intake_id": it.id}, status="running", priority=0)
    db.add(job)
    db.flush()
    it.job_id = job.id
    f = it.files[0]
    f.read_status, f.progress = "pending_transcript", 42
    db.commit()
    html = user_client.get(f"/intake/{it.id}").text
    assert "받아쓰는 중 42%" in html and 'role="progressbar"' in html and 'aria-valuenow="42"' in html and "width:42%" in html
    frag = user_client.get(f"/intake/{it.id}/status").text
    assert 'aria-valuenow="42"' in frag


def test_no_progress_bar_without_percentage(user_client, db, settings):
    it = _bundle(db, settings, [("회의.m4a", b"\x00\x01")], "uploaded")
    assert 'role="progressbar"' not in user_client.get(f"/intake/{it.id}").text


def test_intake_new_lists_my_recent_bundles(user_client, db, settings):
    it = _bundle(db, settings, [("a.txt", b"x")], "uploaded")
    html = user_client.get("/intake/new").text
    assert "최근 올린 묶음" in html and f'href="/intake/{it.id}"' in html


def test_ready_bundle_review_cards_use_switch_and_chips(user_client, db, settings):
    it, s1, c1 = ready_intake(db, settings, owner="kim")
    html = user_client.get(f"/intake/{it.id}").text
    assert 'plan-skip' in html and 'class="chips plan-attached"' in html and "plan-reason" in html


# ---- 리서치 ----

def test_research_run_page_states(user_client, db, settings):
    world = make_research_world(db, settings)
    run = finished_run(db, settings)
    html = user_client.get(f"/research/runs/{run.id}").text
    assert 'class="tabs"' in html and "trend-card" in html and ">후보로 등록</button>" in html and "모두 펼치기" in html
    assert 'class="trend-title"' in html


def test_research_run_page_failed_area_has_alert(user_client, db, settings):
    make_research_world(db, settings)
    run = finished_run(db, settings)
    html = user_client.get(f"/research/runs/{run.id}").text
    assert 'role="alert">조사하지 못했습니다' in html


# ---- 로그인·오류 ----

def test_login_and_error_pages(client, user_client):
    login = client.get("/login").text
    assert "솔박스에 로그인" in login and "사내 워크스페이스" in login and login.count('class="primary"') == 1
    bad = client.post("/login", data={"username": "x", "password": "y"})
    assert 'role="alert"' in bad.text
    out = client.get("/nope-nothing")
    assert out.status_code in (404, 200)
    inn = user_client.get("/solutions/99999")
    assert inn.status_code == 404 and "404" in inn.text and 'href="/"' in inn.text and "목록에서 다시 찾아보세요" in inn.text


def test_system_page_check_rows(admin_client):
    html = admin_client.get("/admin/system").text
    assert 'class="check-rows"' in html and "확인 필요" in html and "아직 백업이 없습니다" in html

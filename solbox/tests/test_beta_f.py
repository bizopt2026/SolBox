"""베타 F 묶음: 사본 경로 거르기, 쓰기 잠금 503, 로그인 문제 분류, 작성자 말투, 시작 때 리서치 복구, AI 꺼짐 문 …"""
from __future__ import annotations

import pytest

from app.deck import has_internal_mark


# ---- F1: 고객용 PPT 사본 경로 (머리말이 무엇이든 knowledge/ 마디가 있으면 내부) ----

@pytest.mark.parametrize("text", [
    "근거=customers/12.md", "출처=activities/2026/1.md", "→customers/12.md", "참고·solutions/3/card.md", "※solutions/3",
    "*solutions/3/card.md*", "**solutions/3**", "#solutions/3", "‘solutions/3’", "“solutions/3.md”", "「solutions/3」", "출처:solutions/3.md",
    "~/data/knowledge/solutions/3/card.md", "$DATA/knowledge/solutions/3.md", "%APPDATA%\\solbox\\knowledge\\solutions\\3.md",
    "\\\\server\\share\\knowledge\\solutions\\3.md", "file:///C:/solbox/knowledge/solutions/3.md", "/home/user/my.app/knowledge/solutions/3.md",
    "/srv/solbox.kr/data/knowledge/solutions/3.md", "data.bak/knowledge/solutions/3.md", "SOLUTIONS/3.md", "knowledge/Solutions/3.md",
    "C:\\Users\\kim\\solbox\\data\\knowledge\\solutions\\3\\card.md", "D:\\솔박스 자료\\knowledge\\solutions\\3.md", "출처: opportunities/2.md, customers/12.md",
    "근거는solutions/3.md", "solutions/3.md참고", "solutions\\3", "x:solutions/3", "[[solutions/3]]", "research/2026-11/5.md", "activities/2026/1.md",
    "1.solutions/3.md", "-solutions/3.md", "- solutions/3.md", "solutions／3",
    # 번호 뒤가 띄어쓰기인 낱말은 맨 경로와 구별할 수 없어 지운다 (안전한 쪽): 일부러 지우는 것이다.
    "solutions/3 단계로 구성", "research/2026 보고서 인용", "activities/2026 계획", "the customers/1000 ratio",
])
def test_f1_internal_paths_removed(text):
    assert has_internal_mark(text), text


@pytest.mark.parametrize("text", [
    "https://x.com/solutions/3", "http://localhost:8443/customers/12", "https://acme.co.kr/solutions/3", "www.acme.co.kr/customers/12", "x.com/solutions/3",
    "192.168.0.10/customers/12", "intranet/solutions/3", "고객사/파트너사 3곳", "2026/11/20", "24/7 지원", "TCP/IP", "연 2/3 수준",
    "partners/2개사", "projects/2건 수주", "opportunities/3건", "customers/1인당 비용", "solutions/5G 라우터", "IoT solutions/3rd party 연동",
    "e-solutions/3", "a-solutions/3.md", "Smart-Solutions/3D", "bit.ly/solutions/3", "localhost:8443/solutions/3", "docs.acme.io/customers/1",
    "B2B customers/partners 모두 지원", "research/개발 조직과 협업", "AWS/Azure solutions/3종", "(https://x.com/solutions/3)",
])
def test_f1_ordinary_text_kept(text):
    assert not has_internal_mark(text), text


# ---- F3/F7: 로그인 문제 분류 ----

from app.ai.runner import ClaudeError, ClaudeRunner, classify_error, error_text


@pytest.mark.parametrize("text", [
    "Request timed out after 401 seconds", "Error reading /home/u/data/knowledge/customers/401.md", "Error: file activities/2026/401.md not found",
    "error at line 401", "timeout: 600s exceeded; cost $0.401", "API Error: 529 overloaded (request id 401)", "MCP server oauth-proxy failed to start",
    "The session expired file", "wrote 401 lines", "port 8401 in use",
])
def test_f3_not_auth(text):
    assert classify_error(text) != "auth", text


@pytest.mark.parametrize("text", [
    '{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}',
    "Could not resolve authentication method. Expected either apiKey or authToken to be set.", "You must be logged in to use Claude",
    "Unauthenticated", "API key invalid", "run `claude login`", "Invalid API key · Please run /login", "OAuth token has expired",
    "401 Unauthorized", "API Error: 401", "HTTP 401", "status 401", "OAuth error", "Error: 401 Unauthorized",
    'API Error: 401 {"type":"error","error":{"type":"authentication_error"}}',
])
def test_f3_auth(text):
    assert classify_error(text) == "auth", text


def test_f3_timeout_and_limit_not_shadowed():
    assert classify_error("Request timed out after 401 seconds") == "network"
    assert classify_error("usage limit reached · resets 4:01pm") == "limit"
    assert classify_error("API Error: 429 rate limit") == "limit"


def test_f7_error_text_includes_plain_stdout_with_stderr_noise():
    warn = "(node:1) [DEP0040] DeprecationWarning: The `punycode` module is deprecated."
    shown = error_text(None, warn, result_is_error=False, stdout="Invalid API key · Please run /login", returncode=1)
    assert classify_error(shown) == "auth"
    # 종료 코드 0 이면 정상 글일 수 있으므로 쓰지 않는다
    assert "Please run /login" not in error_text(None, warn, result_is_error=False, stdout="Invalid API key · Please run /login", returncode=0)


def test_f7_run_classifies_plain_stdout_with_stderr_noise(settings, fake_claude):
    fake_claude.set(mode="garbage", garbage="Invalid API key · Please run /login", stderr="(node:1) DeprecationWarning: x", exit_code=1)
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run("x")
    assert e.value.kind == "auth"


def test_f7_stream_classifies_plain_stdout_line(settings, fake_claude):
    fake_claude.set(mode="garbage", garbage="Invalid API key · Please run /login", stderr="(node:1) DeprecationWarning: x", exit_code=1)
    with pytest.raises(ClaudeError) as e:
        ClaudeRunner(settings.claude_cmd, settings.knowledge_dir).run_stream("x", on_text=lambda t: None)
    assert e.value.kind == "auth"


# ---- F4: 작성자 말투 (대상이 문서 값이거나 이름표·괄호가 붙을 때만) ----

from app.proposals import is_author_instruction

F4_MOVE = [
    "근거를 제시할 것", "가격표를 포함할 것", "레퍼런스를 포함하세요", "실제 수치로 교체할 것", "근거 자료 보강할 것", "확인할 것", "고객 사례 추가 필요",
    "내용 보강 필요", "수치 업데이트 필요", "확인 필요: 절감률", "[확인 필요] 절감률 15%", "금액 확인 필요 - 견적팀", "절감률 수치 확인 필요(영업팀)",
    "작성 예정", "금액 미정", "여기에 도입 사례를 넣으세요", "확인 바람", "[TBD] 비용", "가격 TBD",
]
F4_KEEP = [
    "제안 내용을 검토해 주시기 바랍니다", "귀사의 검토 바랍니다", "점검 결과를 앱에서 확인하세요", "QR 코드로 설치 현황을 확인해 주세요",
    "네트워크 구성 확인 필요", "사전 현장 확인 필요", "기존 설비 보완 필요", "현장 여건 검토 요망", "ROI 분석 결과를 함께 제시",
    "TODO 앱 연동", "서비스명: TBD Cloud", "담당: OOO 팀", "많은 관심 바랍니다", "설치·교육 함께 포함", "운영 인력 2명 추가 필요",
    "운영 매뉴얼과 교육 자료를 함께 제시", "서버실 온도 관리 필요",
]


@pytest.mark.parametrize("line", F4_MOVE)
def test_f4_author_notes_move(line):
    assert is_author_instruction(line), line


@pytest.mark.parametrize("line", F4_KEEP)
def test_f4_customer_sentences_stay(line):
    assert not is_author_instruction(line), line

# 충돌(보고): "법적 기준을 반드시 명시" 는 앞 묶음 시험이 옮기라고 한 "보안 정책을 반드시 명시" 와 규칙으로 가를 수 없어 옮긴다.


# ---- F5: 처리기가 꺼진 서버의 시작 복구는 돌지 않는 제안 작업을 만들지 않는다 ----

from fastapi.testclient import TestClient

from app.models import AIJob, ResearchReport, ResearchRun
from tests.conftest import make_topics


def _run_with_finished_areas(db):
    make_topics(db)
    run = ResearchRun(trigger="manual", status="running")
    db.add(run)
    db.flush()
    db.add(ResearchReport(run_id=run.id, kind="area", area="tech", status="done", title="t"))
    db.add(ResearchReport(run_id=run.id, kind="area", area="policy", status="failed", error="x"))
    db.commit()
    return run.id


def test_f5_startup_with_worker_off_makes_no_dead_job(app, db, admin_client):
    run_id = _run_with_finished_areas(db)
    with TestClient(app):  # 시작 처리(복구)가 돈다
        pass
    db.expire_all()
    assert db.query(AIJob).filter_by(kind="research_suggest").count() == 0
    assert db.query(ResearchReport).filter_by(run_id=run_id, kind="suggest").count() == 0
    assert db.get(ResearchRun, run_id).status == "running"
    page = admin_client.get(f"/research/runs/{run_id}/status").text
    assert "처리기가 꺼져 있어" in page and "이어서 진행해요" in page and "계속 진행됩니다" not in page


def test_f5_run_resumes_when_server_starts_with_worker_on(app, db):
    from app.research import recover_runs
    run_id = _run_with_finished_areas(db)
    assert recover_runs(db, worker_on=False) == 1
    assert db.query(AIJob).filter_by(kind="research_suggest").count() == 0
    assert recover_runs(db, worker_on=True) == 1
    assert db.query(AIJob).filter_by(kind="research_suggest", status="pending").count() == 1
    assert db.get(ResearchRun, run_id).status == "running"


# ---- F2: 쓰기 잠금 대기 초과는 어느 쓰기 단계에서든 503 ----

import re
import sqlite3

from sqlalchemy import event as sa_event

from app.models import Activity, Customer, FormToken, Intake
from tests.conftest import form_from_page, ready_intake


def _hold_write_lock(app):
    """다른 연결이 쓰기 잠금을 쥔다 (이 앱의 연결은 대기 50ms 로 줄인다)."""
    engine = app.state.engine
    sa_event.listen(engine, "connect", lambda c, r: c.execute("PRAGMA busy_timeout=50"))
    engine.dispose()
    raw = sqlite3.connect(str(app.state.settings.db_path), timeout=0, isolation_level=None)
    raw.execute("BEGIN IMMEDIATE")
    return raw


def _release(raw):
    raw.execute("ROLLBACK")
    raw.close()


def _tok(client, url):
    return re.search(r'name="_tok" value="([^"]+)"', client.get(url).text).group(1)


def _busy_page(r):
    assert r.status_code == 503 and "잠시 후 다시 시도" in r.text and "Retry-After" in r.headers, (r.status_code, r.text[:200])
    assert "data-history-back" in r.text and "저장하지 않았" in r.text


def test_f2_create_with_token_under_lock_is_503_and_retry_works(app, user_client, db):
    tok = _tok(user_client, "/customers/new")
    raw = _hold_write_lock(app)
    try:
        r = user_client.post("/customers", data={"name": "새 고객", "_tok": tok}, follow_redirects=False)
    finally:
        _release(raw)
    _busy_page(r)
    db.expire_all()
    assert db.query(Customer).count() == 0 and db.query(FormToken).count() == 0
    ok = user_client.post("/customers", data={"name": "새 고객", "_tok": tok}, follow_redirects=False)  # 같은 표로 다시
    assert ok.status_code == 303
    db.expire_all()
    assert [c.name for c in db.query(Customer)] == ["새 고객"]


def test_f2_raw_save_under_lock(app, user_client, db):
    tok = _tok(user_client, "/activities/new")
    raw = _hold_write_lock(app)
    try:
        r = user_client.post("/activities/raw", data={"raw_text": "회의 메모", "_tok": tok}, follow_redirects=False)
    finally:
        _release(raw)
    _busy_page(r)
    db.expire_all()
    assert db.query(Activity).count() == 0
    assert user_client.post("/activities/raw", data={"raw_text": "회의 메모", "_tok": tok}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(Activity).count() == 1


def test_f2_intake_apply_under_lock(app, user_client, db, settings):
    it, *_ = ready_intake(db, settings, owner="kim")
    form = form_from_page(user_client, it)
    raw = _hold_write_lock(app)
    try:
        r = user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    finally:
        _release(raw)
    _busy_page(r)
    db.expire_all()
    assert db.get(Intake, it.id).status == "ready"  # 반쯤 저장되지 않았다
    assert db.query(Activity).count() == 0
    ok = user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    assert ok.status_code == 303
    db.expire_all()
    assert db.get(Intake, it.id).status == "applied"


def test_f2_delete_under_lock(app, user_client, db):
    from app.models import User
    c = Customer(name="한빛병원", created_by_id=db.query(User).filter_by(username="kim").one().id)
    db.add(c)
    db.commit()
    cid = c.id
    raw = _hold_write_lock(app)
    try:
        r = user_client.post(f"/customers/{cid}/delete", follow_redirects=False)
    finally:
        _release(raw)
    _busy_page(r)
    db.expire_all()
    assert db.get(Customer, cid) is not None
    assert user_client.post(f"/customers/{cid}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Customer, cid) is None


def test_f2_other_operational_errors_stay_500(app, user_client, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.routers import customers

    def boom(*a, **k):
        raise OperationalError("select", {}, Exception("no such table: x"))

    monkeypatch.setattr(customers, "get_deletable_or_404", boom)
    r = user_client.post("/customers/1/delete", follow_redirects=False)
    assert r.status_code == 500 and "잠시 후 다시" not in r.text


def test_f2_idmarks_lock_failure_is_always_write_busy(app, db, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app import guards, idmarks

    class Snapshot(Exception):
        sqlite_errorcode = 517

    def boom(*a, **k):
        raise OperationalError("s", {}, Snapshot("database is locked"))

    monkeypatch.setattr(idmarks, "_lock_and_mark", boom)
    db.add(Customer(name="새 고객"))
    with pytest.raises(guards.WriteBusy):
        db.flush()
    db.rollback()


def test_f2_release_failure_does_not_leave_a_stuck_token(app, user_client, db):
    """표를 차지한 뒤 잠금 때문에 풀지 못해도 같은 표로 다시 낼 수 있다."""
    from sqlalchemy.exc import OperationalError
    from app import guards

    tok = _tok(user_client, "/customers/new")
    factory = app.state.session_factory
    once = guards.Once(factory, 1, f"/customers|{tok}", "d", "/customers")
    once.claim()
    raw = _hold_write_lock(app)
    try:
        once.release()  # 잠겨 있어 지우지 못한다 (예외로 새지 않는다)
    finally:
        _release(raw)
    assert db.query(FormToken).count() == 1
    again = guards.Once(factory, 1, f"/customers|{tok}", "d", "/customers")
    again.claim()  # 이어받는다 (30초를 기다리지 않는다)
    assert again.claimed
    guards._ABANDONED.clear()


# ---- F8: 로그인·권한 검사가 AI 꺼짐 문보다 먼저 ----

from tests.test_beta_e import AI_POSTS

F8_URLS = {
    "/opportunities/{opportunity_id}/ai/review": "/opportunities/1/ai/review",
    "/solutions/{solution_id}/draft": "/solutions/1/draft", "/activities/draft": "/activities/draft",
    "/activities/draft/{job_id}/retry": "/activities/draft/1/retry", "/admin/system/ai-check": "/admin/system/ai-check", "/admin/research/keywords": "/admin/research/keywords",
    "/jobs/{job_id}/retry": "/jobs/1/retry", "/proposals/{proposal_id}/directions": "/proposals/1/directions",
    "/proposals/{proposal_id}/chat": "/proposals/1/chat", "/proposals/{proposal_id}/chat/retry": "/proposals/1/chat/retry",
    "/proposals/{proposal_id}/outline": "/proposals/1/outline", "/research/run": "/research/run",
    "/research/reports/{report_id}/retry": "/research/reports/1/retry", "/intake/{intake_id}/analyze": "/intake/1/analyze", "/admin/duplicates/ai/{kind}": "/admin/duplicates/ai/partner",
    "/reports/{report_id}/ai/summary": "/reports/1/ai/summary", "/reports/{report_id}/ai/plan": "/reports/1/ai/plan", "/reports/{report_id}/chat": "/reports/1/chat",
    "/reports/{report_id}/chat/retry": "/reports/1/chat/retry", "/reports/{report_id}/ai/notes": "/reports/1/ai/notes",
    "/proposals/{proposal_id}/outline/slides/{index}/ai": "/proposals/1/outline/slides/1/ai",
}
ADMIN_ONLY = {"/admin/duplicates/ai/{kind}", "/admin/system/ai-check", "/research/run", "/research/reports/{report_id}/retry"}


@pytest.mark.ai_gate
def test_f8_anonymous_posts_go_to_login_not_ai_off(app):
    assert set(F8_URLS) == AI_POSTS
    anon = TestClient(app)
    for route, url in F8_URLS.items():
        r = anon.post(url, data={"message": "x"}, headers={"referer": "http://testserver/proposals/1"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", (route, r.status_code, r.headers.get("location"))


@pytest.mark.ai_gate
def test_f8_non_admin_gets_403_on_admin_only_routes(app, user_client):
    for route in ADMIN_ONLY:
        r = user_client.post(F8_URLS[route], headers={"referer": "http://testserver/admin/jobs"}, follow_redirects=False)
        assert r.status_code == 403, (route, r.status_code)
    r = user_client.post("/jobs/1/retry", headers={"referer": "http://testserver/admin/jobs"}, follow_redirects=False)  # 로그인한 사용자는 꺼짐 알림
    assert r.status_code == 303 and "notice=ai_off" in r.headers["location"]


# ---- F6: AI 꺼짐일 때 쓴 글을 잃지 않는다 ----

from html import unescape

from tests.conftest import outlined_proposal, refining_proposal


@pytest.mark.ai_gate
def test_f6_activity_draft_while_off_rerenders_form_with_memo(app, user_client, db):
    c = Customer(name="한빛병원")
    db.add(c)
    db.commit()
    memo = "긴 메모 첫 줄\n두 번째 줄 " * 20
    tok = _tok(user_client, f"/activities/new?customer_id={c.id}")
    r = user_client.post("/activities/draft", data={"raw_text": memo, "_tok": tok, "customer_id": str(c.id)}, follow_redirects=False)
    assert r.status_code == 409 and "AI 작업 처리기가 꺼져 있어 시작하지 못했습니다" in r.text
    assert memo.strip() in unescape(r.text) and 'name="customer_id" value="%d"' % c.id in r.text  # 메모와 연결 칩이 그대로
    db.expire_all()
    assert db.query(AIJob).count() == 0 and db.query(FormToken).count() == 0  # 폼 표를 쓰지 않았다
    ok = user_client.post("/activities/raw", data={"raw_text": memo, "_tok": tok}, follow_redirects=False)  # 같은 표로 원문 저장
    assert ok.status_code == 303


@pytest.mark.ai_gate
def test_f6_proposal_chat_and_outline_while_off_keep_text(app, user_client, db, settings):
    p = outlined_proposal(db, settings)
    r = user_client.post(f"/proposals/{p.id}/chat", data={"message": "가격 슬라이드를 넣어 줘"}, follow_redirects=False)
    assert r.status_code == 409 and "가격 슬라이드를 넣어 줘" in unescape(r.text) and "AI 작업 처리기가 꺼져 있어" in r.text
    r = user_client.post(f"/proposals/{p.id}/outline", data={"instruction": "가격 슬라이드를 앞으로"}, follow_redirects=False)
    assert r.status_code == 409 and 'value="가격 슬라이드를 앞으로"' in unescape(r.text)
    db.expire_all()
    assert db.query(AIJob).count() == 0
    # 글 없는 AI 라우트는 그대로 돌려보낸다
    r = user_client.post(f"/proposals/{p.id}/directions", headers={"referer": f"http://testserver/proposals/{p.id}"}, follow_redirects=False)
    assert r.status_code == 303 and "notice=ai_off" in r.headers["location"]


@pytest.mark.ai_gate
def test_f6_intake_analyze_while_off_saves_edited_note(app, user_client, db, settings):
    it, *_ = ready_intake(db, settings, owner="kim")
    r = user_client.post(f"/intake/{it.id}/analyze", data={"note": "고친 메모"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/intake/{it.id}?notice=ai_off")
    db.expire_all()
    assert db.get(Intake, it.id).note == "고친 메모" and db.get(Intake, it.id).status == "ready"
    assert db.query(AIJob).count() == 0


# ---- F9: 오류 화면의 뒤로 가기는 JS 없이도 길이 있다 ----

def test_f9_back_button_is_hidden_until_js_and_noscript_text_exists(app, user_client):
    assert "data-history-back" not in user_client.get("/nothing-here-at-all").text  # 404 는 단추 없음
    raw = _hold_write_lock(app)
    try:
        r = user_client.post("/customers", data={"name": "x"}, follow_redirects=False)
    finally:
        _release(raw)
    assert r.status_code == 503
    assert re.search(r"<button[^>]*data-history-back[^>]*\shidden", r.text)
    assert "<noscript>" in r.text and "브라우저의 뒤로 가기" in r.text and "다시 골라야" in r.text
    js = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "[data-history-back][hidden]" in js


# ---- F10: 막힌 AI 버튼 곁에는 이유 한 줄 ----

from datetime import datetime
from pathlib import Path


def _failed_job(db, kind="solution_draft", **inp):
    j = AIJob(kind=kind, input=inp, status="failed", error_kind="timeout", finished_at=datetime.now(), requester_id=1)
    db.add(j)
    db.commit()
    return j


@pytest.mark.ai_gate
def test_f10_disabled_ai_buttons_have_one_reason_line(app, admin_client, db, settings):
    from tests.conftest import finished_run
    from app.models import Solution
    sol = Solution(name="AI 공조")
    db.add(sol)
    db.commit()
    j = _failed_job(db, solution_id=sol.id)
    run = finished_run(db, settings)
    pages = {
        "/admin/jobs": 'aria-describedby="ai-note"', "/admin/system": 'aria-describedby="ai-note"',
        f"/solutions/{sol.id}/edit?draft_job={j.id}": 'aria-describedby="ai-note"', f"/research/runs/{run.id}/status": 'aria-describedby="ai-note-run"',
        f"/jobs/{j.id}/status": f'aria-describedby="ai-note-job-{j.id}"',
    }
    for url, describedby in pages.items():
        page = admin_client.get(url).text
        assert page.count("data-ai-blocked") == 1, url
        assert describedby in page and "disabled" in page, url
    run_page = admin_client.get(f"/research/runs/{run.id}").text  # 끝난 조사의 실패한 분야 "다시 조사"
    assert run_page.count("data-ai-blocked") == 1 and 'aria-describedby="ai-note"' in run_page


# ---- F12: 상단 줄 AI 상태의 "마지막으로 끝난 작업" 조회는 한 줄만, 색인 있음 ----

def test_f12_last_finished_job_query_has_limit_and_index(app, db):
    from sqlalchemy import inspect as sa_inspect, text
    from app.main import _ai_status_data
    app.state.settings.ai_worker_enabled = True
    statements = []
    sa_event.listen(app.state.engine, "before_cursor_execute", lambda c, cur, stmt, p, ctx, many: statements.append(stmt))
    _ai_status_data(app.state.session_factory, app.state.settings)
    last = [s for s in statements if "finished_at IS NOT NULL" in s]
    assert last and all("LIMIT" in s for s in last)
    assert "ix_ai_jobs_finished_at" in {i["name"] for i in sa_inspect(app.state.engine).get_indexes("ai_jobs")}


def test_f12_index_is_added_to_an_old_database_without_touching_rows(app, db):
    from sqlalchemy import inspect as sa_inspect, text
    from app.migrate import add_unique_indexes
    db.add(AIJob(kind="ai_ping", input={}, status="done", finished_at=datetime.now()))
    db.commit()
    db.execute(text("DROP INDEX ix_ai_jobs_finished_at"))
    db.commit()
    assert "ix_ai_jobs_finished_at" not in {i["name"] for i in sa_inspect(app.state.engine).get_indexes("ai_jobs")}
    assert "ix_ai_jobs_finished_at" in add_unique_indexes(app.state.engine)
    assert db.query(AIJob).count() == 1


def test_notices_inline_matches_from_request_shape():
    from app import notices
    n = notices.inline("ai_off", "/x")
    assert n["tone"] == "error" and "꺼져 있어" in n["text"] and n["dismiss"] == "/x"
    assert notices.inline("no-such-code", "/x") is None

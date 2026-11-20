"""제안 구체화 대화: 대기·진행·완료·실패 상태가 대화 구역 안에서 보인다."""
import re
import subprocess
from datetime import datetime, timedelta

import pytest

from app import labels
from app.ai import queue as ai_queue
from app.ai.queue import process_next
from app.ai.runner import ClaudeError, ClaudeRunner
from app.models import AIJob, Proposal, ProposalMessage, User
from tests.conftest import refining_proposal

WAIT_REASON = "답변이 끝나면 이어서 물을 수 있어요"


@pytest.fixture(autouse=True)
def _worker_on(app):
    """작업 처리기가 꺼진 서버는 대기 작업이 시작되지 않아 화면이 다시 불러오지 않는다 (아래 따로 시험). 여기서는 켜 둔 서버를 본다."""
    app.state.settings.ai_worker_enabled = True


def _live(html: str) -> str:
    """대화 구역의 살아 있는 부분(`#chat-live`)만 잘라 낸다."""
    m = re.search(r'<div id="chat-live".*?<!--/chat-live-->', html, re.S)
    assert m, "chat-live 구역이 없습니다"
    return m.group(0)


def _chat_job(db, p, status, *, result="", started_ago=None, error_kind="", error="", kind="proposal_chat", ask=True):
    kim = db.query(User).filter_by(username="kim").one()
    if ask:
        db.add(ProposalMessage(proposal_id=p.id, role="user", content="공조만 하자"))
    now = datetime.now()
    job = AIJob(
        kind=kind, status=status, result=result, error_kind=error_kind, error=error, requester_id=kim.id,
        input={"proposal_id": p.id}, created_at=now - timedelta(seconds=(started_ago or 0) + 1),
        started_at=now - timedelta(seconds=started_ago) if started_ago is not None else None,
    )
    db.add(job)
    db.flush()
    p.active_job_id = job.id
    db.commit()
    return job


def _other_job(db, status, kind="research_area", started_ago=None, **kw):
    now = datetime.now()
    kw.setdefault("input", {})
    job = AIJob(kind=kind, status=status, created_at=now - timedelta(minutes=5),
                started_at=now - timedelta(seconds=started_ago) if started_ago is not None else None, **kw)
    db.add(job)
    db.commit()
    return job


def _page(user_client, p):
    return user_client.get(f"/proposals/{p.id}").text


# ---- 대기 ----

def test_pending_behind_running_job_names_it_inside_the_chat(user_client, db, settings):
    p = refining_proposal(db, settings)
    _other_job(db, "running", started_ago=185)
    _chat_job(db, p, "pending")
    html = _page(user_client, p)
    live = _live(html)
    assert "공조만 하자" in live  # 사용자 메시지는 그대로
    assert "대기 중" in live and "앞에 AI 작업 1개" in live
    assert re.search(r"리서치 분야 조사 진행 중 \(3분 \d+초 경과\)", live)
    assert "자동으로 시작" in live
    assert 'class="chat-msg assistant live pending"' in live
    # 입력은 사라지지 않고 이유와 함께 꺼져 있다
    assert re.search(r"<textarea[^>]*disabled", live) and WAIT_REASON in live
    # 대화 구역이 처음부터 펼쳐져 있다 (제안서 단계여도)
    assert re.search(r'<details class="card section" id="chat" open', html)


def test_pending_counts_every_job_ahead_and_hides_nothing_private(user_client, db, settings):
    p = refining_proposal(db, settings)
    other = db.query(User).filter_by(username="kim").one()
    _other_job(db, "running", kind="intake_analyze", started_ago=20, requester_id=other.id, input={"private": "비밀 메모"})
    _other_job(db, "pending", kind="proposal_directions")
    _chat_job(db, p, "pending")
    live = _live(_page(user_client, p))
    assert "앞에 AI 작업 2개" in live
    assert "올린 파일 정리 진행 중" in live and "제안 방향 만들기 대기 중" in live
    assert "비밀 메모" not in live


def test_pending_with_nothing_ahead_still_says_it_starts_automatically(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "pending")
    live = _live(_page(user_client, p))
    assert "대기 중" in live and "자동으로 시작" in live and "앞에 AI 작업" not in live


# ---- 진행 ----

def test_running_without_text_shows_typing_and_elapsed(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "running", started_ago=7)
    live = _live(_page(user_client, p))
    assert "답변을 쓰는 중…" in live and 'class="typing"' in live and "초 경과" in live
    assert re.search(r"<textarea[^>]*disabled", live) and WAIT_REASON in live


def test_running_shows_partial_answer_rendered_as_markdown(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "running", result="범위를 **공조**로 <b>좁힙니다</b>", started_ago=12)
    live = _live(_page(user_client, p))
    assert "<strong>공조</strong>" in live and "&lt;b&gt;좁힙니다&lt;/b&gt;" in live and "<b>좁힙니다</b>" not in live
    assert 'class="typing"' in live and "답변을 쓰는 중…" not in live


def test_live_fragment_polls_faster_while_running(user_client, db, settings):
    p = refining_proposal(db, settings)
    job = _chat_job(db, p, "running", started_ago=3)
    frag = user_client.get(f"/proposals/{p.id}/chat/live").text
    assert f'hx-get="/proposals/{p.id}/chat/live"' in frag and "every 1s" in frag
    job.status, job.started_at = "pending", None
    db.commit()
    assert "every 2s" in user_client.get(f"/proposals/{p.id}/chat/live").text


def test_top_status_is_compact_and_does_not_poll_for_chat(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "running", result="부분 답변", started_ago=3)
    html = _page(user_client, p)
    top = html.split('<div id="ai-status">', 1)[1].split('<div class="dgrid pgrid">', 1)[0]
    assert "부분 답변" not in top and "hx-get" not in top and 'href="#chat"' in top


# ---- 완료 ----

def test_done_fragment_has_final_message_enabled_input_no_polling_and_no_redirect(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="stream", chunks=["범위를 ", "좁힙니다."])
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "공조만 하자"})
    r = user_client.get(f"/proposals/{p.id}/chat/live")
    assert "hx-get" in r.text and "대기 중" in r.text  # 기다리는 동안의 조각은 맨 끝 말풍선뿐 (앞선 메시지는 다시 그리지 않는다)
    assert "공조만 하자" not in r.text and 'id="chat-live"' not in r.text
    process_next(app.state.session_factory, app.state.runner, settings)
    r = user_client.get(f"/proposals/{p.id}/chat/live")
    live = _live(r.text)
    assert "범위를 좁힙니다." in live and 'data-state="idle"' in live
    assert "hx-get" not in live and "hx-redirect" not in {k.lower() for k in r.headers}
    assert not re.search(r"<textarea[^>]*disabled", live) and "autofocus" not in live
    # 위쪽 상태 줄은 같은 응답에서 비운다 (OOB)
    assert 'id="ai-status"' in r.text and "hx-swap-oob" in r.text


def test_live_fragment_refreshes_step_bar_and_summary_out_of_band(user_client, db, settings):
    p = refining_proposal(db, settings)
    r = user_client.get(f"/proposals/{p.id}/chat/live")
    assert 'id="stepbar"' in r.text and 'id="summary-chat"' in r.text


# ---- 실패 ----

@pytest.mark.parametrize("kind", ["auth", "network", "timeout", "bad_output", "unknown"])
def test_failed_shows_reason_and_retry_inside_the_chat(user_client, db, settings, kind):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "failed", error_kind=kind, error="원인 글")
    html = _page(user_client, p)
    live = _live(html)
    assert labels.ERROR_KINDS[kind] in live
    assert "공조만 하자" in live  # 사용자 메시지는 남는다
    assert f'action="/proposals/{p.id}/chat/retry"' in live and "다시 시도" in live
    assert not re.search(r"<textarea[^>]*disabled", live)  # 다른 말로 다시 물을 수 있다
    assert "hx-get" not in live
    assert re.search(r'<details class="card section" id="chat" open', html)


def test_failed_chat_page_has_no_second_retry_form_on_top(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "failed", error_kind="auth")
    html = _page(user_client, p)
    assert html.count(f'action="/proposals/{p.id}/chat/retry"') == 1


def test_failed_other_job_does_not_show_chat_failure(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "failed", error_kind="auth", kind="proposal_outline", ask=False)
    live = _live(_page(user_client, p))
    assert labels.ERROR_KINDS["auth"] not in live and "chat/retry" not in live


def test_failed_chat_after_assistant_answer_is_not_shown(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "failed", error_kind="auth")
    db.add(ProposalMessage(proposal_id=p.id, role="assistant", content="이미 답했습니다"))
    db.commit()
    live = _live(_page(user_client, p))
    assert "chat/retry" not in live


def test_failed_job_shows_the_partial_text_marked_as_interrupted(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="stream", chunks=["반쯤 ", "온 답"], stream_fail_after=1, stderr="Invalid API key · Please run /login")
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"})
    process_next(app.state.session_factory, app.state.runner, settings)
    live = _live(_page(user_client, p))
    assert labels.ERROR_KINDS["auth"] in live and "반쯤" in live and "중단된 답변" in live


# ---- 자바스크립트 없이 ----

def test_no_js_page_shows_state_and_a_refresh_link(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "running", started_ago=3)
    live = _live(_page(user_client, p))
    # 새로 고침은 조각 이동이 아니라 실제로 다시 불러오는 주소 (매번 다른 쿼리)
    assert re.search(rf'<a href="/proposals/{p.id}\?refresh=\d+#chat">새로 고침</a>', live)
    assert "<noscript>" in live  # 스크립트가 없을 때만 보이는 안내


# ---- 안 깨뜨릴 것 ----

def test_messages_from_previous_direction_stay_hidden_behind_divider(user_client, db, settings):
    p = refining_proposal(db, settings)
    db.add(ProposalMessage(proposal_id=p.id, role="user", content="옛 방향 질문"))
    db.commit()
    p.chat_from_id = p.messages[-1].id
    db.commit()
    _chat_job(db, p, "running", started_ago=2)
    live = _live(_page(user_client, p))
    assert "이전 방향에서 나눈 대화" in live and "chat-msg user old" in live


def test_other_job_running_disables_input_with_reason(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "running", kind="proposal_outline", ask=False, started_ago=2)
    live = _live(_page(user_client, p))
    assert re.search(r"<textarea[^>]*disabled", live) and "hx-get" not in live  # 위쪽 상태가 쪽을 새로 불러온다
    assert "답변을 쓰는 중" not in live


def test_chat_is_refused_while_a_job_runs(user_client, db, settings):
    p = refining_proposal(db, settings)
    _chat_job(db, p, "running", started_ago=2)
    r = user_client.post(f"/proposals/{p.id}/chat", data={"message": "또"}, follow_redirects=False)
    assert r.status_code == 409 and "또" in re.search(r"<textarea id=\"chat-message\"[^>]*>(.*?)<\/textarea>", r.text, re.S).group(1)
    db.expire_all()
    assert db.query(ProposalMessage).filter_by(proposal_id=p.id).count() == 1


# ---- 대기열 안내 (모든 AI 대기) ----

def test_jobs_ahead_orders_running_first_and_excludes_itself(db):
    run = _other_job(db, "running", started_ago=10)
    pend = _other_job(db, "pending", kind="intake_analyze")
    mine = _other_job(db, "pending", kind="activity_structure")
    ahead = ai_queue.jobs_ahead(db, mine)
    assert [j.id for j in ahead] == [run.id, pend.id]
    assert ai_queue.jobs_ahead(db, run) == []  # 진행 중인 작업 앞에는 아무것도 없다


def test_ahead_lines_use_job_kind_labels_and_cap_the_list():
    now = datetime(2026, 11, 20, 12, 0, 0)
    jobs = [AIJob(kind="research_area", status="running", started_at=now - timedelta(minutes=3))]
    jobs += [AIJob(kind="intake_analyze", status="pending") for _ in range(3)]
    lines = labels.ahead_lines(jobs, now)
    assert lines[0] == "리서치 분야 조사 진행 중 (3분 경과)"
    assert lines[1] == "올린 파일 정리 대기 중" and lines[-1] == "외 1개" and len(lines) == 4
    assert labels.ahead_lines([AIJob(kind="no_such", status="pending")], now) == ["AI 작업 대기 중"]


def test_generic_wait_text_names_what_is_ahead_after_a_few_seconds(user_client, db):
    kim = db.query(User).filter_by(username="kim").one()
    _other_job(db, "running", started_ago=65)
    j = ai_queue.enqueue(db, "solution_draft", {}, kim.id)
    j.created_at = datetime.now() - timedelta(seconds=30)
    db.commit()
    html = user_client.get(f"/jobs/{j.id}/status").text
    assert "AI 대기 1번째 (앞 작업이 끝나면 시작합니다)" in html
    assert re.search(r"리서치 분야 조사 진행 중 \(1분 \d+초 경과\)", html)
    j.created_at = datetime.now()  # 방금 넣은 작업에는 아직 덧붙이지 않는다
    db.commit()
    assert "리서치 분야 조사" not in user_client.get(f"/jobs/{j.id}/status").text


# ---- 스트리밍이 안 될 때: 분명한 실패와 한 번의 대체 실행 ----

def _runner(settings):
    return ClaudeRunner(settings.claude_cmd, settings.knowledge_dir)


def test_stream_without_any_event_is_a_clear_failure(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=[], no_result=True)
    with pytest.raises(ClaudeError) as e:
        _runner(settings).run_stream("x", on_text=lambda t: None)
    assert e.value.kind == "bad_output" and "결과" in e.value.message


def test_stream_with_only_unparseable_output_is_a_clear_failure(settings, fake_claude):
    fake_claude.set(mode="garbage")
    with pytest.raises(ClaudeError) as e:
        _runner(settings).run_stream("x", on_text=lambda t: None)
    assert e.value.kind == "bad_output" and "읽지 못했습니다" in e.value.message


def test_nonzero_exit_without_output_names_the_exit_code(settings, fake_claude):
    fake_claude.set(mode="stream", chunks=[], stream_fail_after=0, exit_code=3)
    with pytest.raises(ClaudeError) as e:
        _runner(settings).run_stream("x", on_text=lambda t: None)
    assert e.value.kind == "unknown" and "3" in e.value.message


def test_run_stream_never_calls_communicate_after_readers_closed_the_pipes(settings, fake_claude, monkeypatch):
    """Windows 의 communicate 는 이미 닫힌 파이프에서 IndexError 를 낼 수 있다. 정상 종료 뒤에는 부르지 않는다."""
    fake_claude.set(mode="stream", chunks=["가", "나"])

    def boom(self, *a, **k):
        raise IndexError("list index out of range")

    monkeypatch.setattr(subprocess.Popen, "communicate", boom)
    assert _runner(settings).run_stream("x", on_text=lambda t: None) == "가나"


def _chat_proposal(user_client, db, settings, message="질문"):
    p = refining_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/chat", data={"message": message})
    return p


def test_chat_falls_back_to_plain_run_when_stream_fails_before_any_text(app, user_client, db, settings, fake_claude, caplog):
    p = _chat_proposal(user_client, db, settings)
    fake_claude.set(mode="stream", chunks=["가"], stream_fail_after=0, stderr="error: unknown option '--include-partial-messages'",
                    json_result="대체 경로 답변")
    with caplog.at_level("WARNING"):
        process_next(app.state.session_factory, app.state.runner, settings)
    db.expire_all()
    assert [(m.role, m.content) for m in p.messages] == [("user", "질문"), ("assistant", "대체 경로 답변")]
    job = db.query(AIJob).one()
    assert job.status == "done" and p.active_job_id is None
    argvs = [" ".join(c["argv"]) for c in fake_claude.calls()]
    assert len(argvs) == 2 and "stream-json" in argvs[0] and "--output-format json" in argvs[1]
    assert any("스트리밍" in r.getMessage() and "대체" in r.getMessage() for r in caplog.records)


def test_chat_falls_back_when_stream_ends_without_result_event(app, user_client, db, settings, fake_claude):
    p = _chat_proposal(user_client, db, settings)
    fake_claude.set(mode="stream", chunks=[], no_result=True, json_result="두 번째 길 답변")
    process_next(app.state.session_factory, app.state.runner, settings)
    db.expire_all()
    assert p.messages[-1].content == "두 번째 길 답변"


def test_chat_does_not_fall_back_once_text_has_streamed(app, user_client, db, settings, fake_claude):
    p = _chat_proposal(user_client, db, settings)
    fake_claude.set(mode="stream", chunks=["반쯤 ", "온"], stream_fail_after=1, stderr="boom", json_result="안 쓰임")
    process_next(app.state.session_factory, app.state.runner, settings)
    db.expire_all()
    assert db.query(AIJob).one().status == "failed" and len(fake_claude.calls()) == 1
    assert [m.role for m in p.messages] == ["user"]


@pytest.mark.parametrize("stderr, kind", [("Invalid API key · Please run /login", "auth"), ("usage limit reached", "limit"),
                                          ("getaddrinfo ENOTFOUND", "network")])
def test_chat_does_not_fall_back_for_errors_a_second_run_cannot_fix(app, user_client, db, settings, fake_claude, stderr, kind):
    _chat_proposal(user_client, db, settings)
    fake_claude.set(mode="stream", chunks=[], stream_fail_after=0, stderr=stderr, json_result="안 쓰임")
    process_next(app.state.session_factory, app.state.runner, settings)
    job = db.query(AIJob).one()
    db.refresh(job)
    assert job.status == "failed" and job.error_kind == kind and len(fake_claude.calls()) == 1


def test_chat_reports_the_fallback_failure_not_the_stream_failure(app, user_client, db, settings, fake_claude):
    _chat_proposal(user_client, db, settings)
    fake_claude.set(mode="stream", chunks=[], no_result=True)  # json_result 가 없으면 대체 실행도 읽을 수 없는 출력을 받는다
    process_next(app.state.session_factory, app.state.runner, settings)
    job = db.query(AIJob).one()
    db.refresh(job)
    assert job.status == "failed" and len(fake_claude.calls()) == 2


# ---- 처리기가 꺼진 서버: 계속 다시 불러오지 않고 안내한다 ----

def test_pending_chat_with_the_worker_off_shows_a_notice_and_does_not_poll(app, user_client, db, settings):
    app.state.settings.ai_worker_enabled = False
    p = refining_proposal(db, settings)
    _chat_job(db, p, "pending")
    live = _live(_page(user_client, p))
    assert labels.AI_OFF_NOTICE in live and "hx-get" not in live and "hx-trigger" not in live
    frag = user_client.get(f"/proposals/{p.id}/chat/live").text
    assert labels.AI_OFF_NOTICE in frag and "hx-trigger" not in frag

from app.ai.queue import process_next
from app.ai.runner import ClaudeRunner
from app.labels import ERROR_KINDS
from app.models import AIJob, Proposal, ProposalMessage
from app.proposals import start_job
from tests.conftest import make_proposal_world, make_user, refining_proposal


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def _roles(p):
    return [(m.role, m.content) for m in p.messages]


def test_chat_round_trip_with_partial_text(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="stream", chunks=["범위를 ", "공조로 ", "좁히겠습니다."])
    r = user_client.post(f"/proposals/{p.id}/chat", data={"message": "공조만 하자"}, follow_redirects=False)
    assert r.headers["location"] == f"/proposals/{p.id}#chat"
    db.refresh(p)
    assert _roles(p) == [("user", "공조만 하자")]
    _run(app, settings)
    db.refresh(p)
    assert _roles(p) == [("user", "공조만 하자"), ("assistant", "범위를 공조로 좁히겠습니다.")]
    assert p.active_job_id is None
    prompt = fake_claude.calls()[0]["stdin"]
    assert "공조만 하자" in prompt and "전력비 절감 패키지" in prompt
    assert f"customers/{p.customer_id}.md" in prompt and f"solutions/{p.solutions[0].id}/" in prompt
    job = db.query(AIJob).one()
    assert job.status == "done" and job.result == "범위를 공조로 좁히겠습니다."


def test_partial_text_is_written_to_job_result(app, user_client, db, settings, monkeypatch):
    p = refining_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"})
    seen = {}

    def fake_stream(self, prompt, *, on_text, **kw):
        on_text("부분")
        with app.state.session_factory() as other:
            seen["result"] = other.query(AIJob).one().result
        return "최종"

    monkeypatch.setattr(ClaudeRunner, "run_stream", fake_stream)
    _run(app, settings)
    assert seen["result"] == "부분"
    db.expire_all()
    assert db.query(AIJob).one().result == "최종"
    assert _roles(p)[-1] == ("assistant", "최종")


def test_partial_text_writes_are_throttled(app, user_client, db, settings, monkeypatch):
    p = refining_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"})
    seen = []

    def fake_stream(self, prompt, *, on_text, **kw):
        for t in ("가", "가나", "가나다"):
            on_text(t)  # 0.5초 안의 연속 호출은 첫 번째만 기록한다
            with app.state.session_factory() as other:
                seen.append(other.query(AIJob).one().result)
        return "가나다라"

    monkeypatch.setattr(ClaudeRunner, "run_stream", fake_stream)
    _run(app, settings)
    assert seen == ["가", "가", "가"]


def test_partial_text_write_failure_does_not_fail_job(app, user_client, db, settings, monkeypatch):
    p = refining_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"})

    def fake_stream(self, prompt, *, on_text, **kw):
        def boom(*a, **k):
            raise RuntimeError("db")
        monkeypatch.setattr("app.ai.proposal_tasks.Session", boom)
        on_text("부분")
        monkeypatch.undo()
        return "끝"

    monkeypatch.setattr(ClaudeRunner, "run_stream", fake_stream)
    _run(app, settings)
    db.expire_all()
    assert db.query(AIJob).one().status == "done"
    assert _roles(p)[-1] == ("assistant", "끝")


def test_status_fragment_shows_partial_answer(user_client, db, settings):
    p = refining_proposal(db, settings)
    job = AIJob(kind="proposal_chat", status="running", result="지금까지 <b>답변</b>", input={"proposal_id": p.id})
    db.add(job)
    db.flush()
    p.active_job_id = job.id
    db.commit()
    r = user_client.get(f"/proposals/{p.id}/status")
    assert "지금까지 &lt;b&gt;답변&lt;/b&gt;" in r.text and "<b>답변</b>" not in r.text
    assert "every 1s" in r.text
    job.kind = "proposal_directions"
    db.commit()
    r = user_client.get(f"/proposals/{p.id}/status")
    assert "지금까지" not in r.text and "every 2s" in r.text


def test_chat_failure_keeps_user_message_only(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="stream", chunks=["반쯤 ", "온 답"], stream_fail_after=1, stderr="getaddrinfo ENOTFOUND")
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"})
    _run(app, settings)
    db.refresh(p)
    assert _roles(p) == [("user", "질문")]
    page = user_client.get(f"/proposals/{p.id}").text
    assert ERROR_KINDS["network"] in page and "다시 시도" in page and "반쯤" not in page
    assert f"/proposals/{p.id}/chat/retry" in page


def test_chat_retry_reuses_last_user_message(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="stream", chunks=["반쯤"], stream_fail_after=1, stderr="getaddrinfo ENOTFOUND")
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"})
    _run(app, settings)
    fake_claude.set(mode="stream", chunks=["성공 ", "답변"])
    r = user_client.post(f"/proposals/{p.id}/chat/retry", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/proposals/{p.id}#chat"
    _run(app, settings)
    db.refresh(p)
    assert _roles(p) == [("user", "질문"), ("assistant", "성공 답변")]
    assert db.query(AIJob).count() == 2


def test_chat_retry_rejected_when_not_failed(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    assert user_client.post(f"/proposals/{p.id}/chat/retry").status_code == 400  # 작업도 메시지도 없음
    fake_claude.set(mode="stream", chunks=["답"])
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"})
    assert user_client.post(f"/proposals/{p.id}/chat/retry").status_code == 400  # 진행 중
    _run(app, settings)
    assert user_client.post(f"/proposals/{p.id}/chat/retry").status_code == 400  # 성공 후
    assert db.query(AIJob).count() == 1


def test_chat_requires_selected_direction(user_client, db, settings):
    p, *_ = make_proposal_world(db, settings)
    p.directions = [{"title": "x", "solutions": []}]
    p.status = "directions"
    db.commit()
    assert user_client.post(f"/proposals/{p.id}/chat", data={"message": "안녕"}).status_code == 400
    assert db.query(ProposalMessage).count() == 0 and db.query(AIJob).count() == 0
    assert user_client.post(f"/proposals/{p.id}/chat/retry").status_code == 400
    assert user_client.post("/proposals/9999/chat", data={"message": "x"}).status_code == 404


def test_empty_message_rejected(user_client, db, settings):
    p = refining_proposal(db, settings)
    for bad in ("", "   \n"):
        r = user_client.post(f"/proposals/{p.id}/chat", data={"message": bad})
        assert r.status_code == 400 and "메시지를 입력하세요." in r.text
    assert db.query(ProposalMessage).count() == 0


def test_busy_does_not_store_message(user_client, db, settings):
    p = refining_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "첫째"})
    r = user_client.post(f"/proposals/{p.id}/chat", data={"message": "둘째"}, follow_redirects=False)
    assert r.headers["location"] == f"/proposals/{p.id}?notice=busy"
    db.expire_all()
    assert [m.content for m in p.messages] == ["첫째"] and db.query(AIJob).count() == 1


def test_messages_are_escaped(user_client, db, settings):
    p = refining_proposal(db, settings)
    db.add(ProposalMessage(proposal_id=p.id, role="assistant", content="<script>alert(1)</script>\n둘째 줄"))
    db.commit()
    page = user_client.get(f"/proposals/{p.id}").text
    assert "&lt;script&gt;" in page and "<script>alert(1)" not in page


def test_chat_form_hidden_before_selection(user_client, db, settings):
    p, *_ = make_proposal_world(db, settings)
    assert 'name="message"' not in user_client.get(f"/proposals/{p.id}").text
    q = refining_proposal(db, settings)
    assert 'name="message"' in user_client.get(f"/proposals/{q.id}").text


def test_history_limited_to_30(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    for i in range(39):
        db.add(ProposalMessage(proposal_id=p.id, role="user" if i % 2 == 0 else "assistant", content=f"기록{i:02d}번"))
    db.commit()
    fake_claude.set(mode="stream", chunks=["답"])
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "마지막 질문"})  # 총 40개
    _run(app, settings)
    prompt = fake_claude.calls()[0]["stdin"]
    assert all(f"기록{i:02d}번" not in prompt for i in range(10))
    assert "기록10번" in prompt and "기록38번" in prompt and "마지막 질문" in prompt


def test_stale_job_is_superseded(app, user_client, db, settings, fake_claude):
    p = refining_proposal(db, settings)
    fake_claude.set(mode="stream", chunks=["답"])
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"})
    p.active_job_id = None  # 다른 흐름이 자리를 가져갔다
    db.commit()
    _run(app, settings)
    db.refresh(p)
    assert _roles(p) == [("user", "질문")]
    assert db.query(AIJob).one().result == "superseded"


def test_deleted_proposal_finishes_quietly(app, user_client, db, settings, monkeypatch):
    p = refining_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"})

    def fake_stream(self, prompt, *, on_text, **kw):
        with app.state.session_factory() as other:
            other.delete(other.get(Proposal, p.id))
            other.commit()
        return "답"

    monkeypatch.setattr(ClaudeRunner, "run_stream", fake_stream)
    _run(app, settings)
    db.expire_all()
    job = db.query(AIJob).one()
    assert job.status == "done" and job.result == "deleted"


def test_no_selected_direction_fails_job(app, db, settings, fake_claude):
    p, *_ = make_proposal_world(db, settings)
    user = make_user(db, "u1")
    job = start_job(db, p, "proposal_chat", user)
    _run(app, settings)
    db.refresh(job)
    assert job.status == "failed" and job.error == "먼저 제안 방향을 선택하세요."

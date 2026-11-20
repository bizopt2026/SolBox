import re
from urllib.parse import quote

from app.ai.queue import enqueue, process_next
from app.knowledge import safe_name
from app.models import Proposal, ProposalMessage
from app.proposals import display_title
from tests.conftest import make_proposal_world, outlined_proposal, refining_proposal


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def test_table_rows_without_columns_is_rejected(user_client, db, settings):
    p = outlined_proposal(db, settings)
    before = [dict(s) for s in p.outline["slides"]]
    r = user_client.post(f"/proposals/{p.id}/outline/slides/1", data={"kind": "table", "title": "표", "columns": "", "rows": "a | b\nc | d"})
    assert r.status_code == 400 and "표의 열 이름을 입력하세요." in r.text
    db.refresh(p)
    assert p.outline["slides"] == before
    # 행도 열도 없으면 빈 표로 저장된다
    r = user_client.post(f"/proposals/{p.id}/outline/slides/1", data={"kind": "table", "title": "표"})
    assert r.status_code == 200 and p.outline is not None


def test_chat_lost_race_removes_message(user_client, db, settings, monkeypatch):
    p = refining_proposal(db, settings)
    monkeypatch.setattr("app.routers.proposals.start_job", lambda *a, **k: None)
    r = user_client.post(f"/proposals/{p.id}/chat", data={"message": "질문"}, follow_redirects=False)
    assert r.status_code == 409 and "질문" in re.search(r"<textarea id=\"chat-message\"[^>]*>(.*?)<\/textarea>", r.text, re.S).group(1)
    assert db.query(ProposalMessage).count() == 0


def test_chat_lost_race_survives_vanished_proposal(user_client, db, settings, monkeypatch):
    p = refining_proposal(db, settings)
    pid = p.id

    def vanish(db_, proposal, *a, **k):
        db_.query(ProposalMessage).delete()
        db_.delete(db_.get(Proposal, pid))
        db_.commit()
        return None

    monkeypatch.setattr("app.routers.proposals.start_job", vanish)
    r = user_client.post(f"/proposals/{pid}/chat", data={"message": "질문"}, follow_redirects=False)
    assert r.status_code == 303


def test_input_length_caps(user_client, db, settings):
    p = refining_proposal(db, settings)
    r = user_client.post(f"/proposals/{p.id}/chat", data={"message": "가" * 4001})
    assert r.status_code == 400 and "메시지는 4000자까지 보낼 수 있습니다." in r.text
    assert db.query(ProposalMessage).count() == 0
    assert user_client.post(f"/proposals/{p.id}/chat", data={"message": "가" * 4000}).status_code == 200
    r = user_client.post(f"/proposals/{p.id}/outline", data={"instruction": "가" * 501})
    assert r.status_code == 400 and "추가 지시는 500자까지 쓸 수 있습니다." in r.text
    r = user_client.post(f"/proposals/{p.id}/target", data={"request_memo": "가" * 2001})
    assert r.status_code == 400 and "요청 메모는 2000자까지 쓸 수 있습니다." in r.text
    c = p.customer_id
    r = user_client.post("/proposals", data={"customer_id": str(c), "request_memo": "가" * 2001})
    assert r.status_code == 400 and "요청 메모는 2000자까지 쓸 수 있습니다." in r.text
    assert db.query(Proposal).count() == 1
    assert user_client.post("/proposals", data={"customer_id": str(c), "request_memo": "가" * 2000}).status_code == 200
    assert db.query(Proposal).count() == 2


def test_download_header_has_ascii_fallback(user_client, db, settings):
    p = outlined_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/deck")
    d = user_client.get(f"/proposals/{p.id}/deck")
    assert d.headers["content-disposition"] == (
        f'attachment; filename="proposal-{p.id}-customer.pptx"; filename*=UTF-8\'\'' + quote(safe_name(display_title(p)) + "_고객용.pptx"))


def test_bad_output_twice_shows_korean_message(app, db, settings, fake_claude):
    fake_claude.set(mode="ok", result='{"title": 1, ')
    job = enqueue(db, "activity_structure", {"raw_text": "x"}, None)
    _run(app, settings)
    db.refresh(job)
    assert job.error_kind == "bad_output" and job.error == "AI 응답 형식이 올바르지 않습니다. 다시 시도하세요."
    assert len(fake_claude.calls()) == 2


def test_status_for_deleted_proposal_redirects(user_client):
    r = user_client.get("/proposals/9999/status")
    assert r.status_code == 200 and r.headers["HX-Redirect"] == "/proposals"


def test_editing_after_deck_reopens_status(user_client, db, settings):
    p = outlined_proposal(db, settings)
    user_client.post(f"/proposals/{p.id}/deck")
    db.refresh(p)
    assert p.status == "deck"
    user_client.post(f"/proposals/{p.id}/outline/slides/1", data={"kind": "bullets", "title": "고침"})
    db.refresh(p)
    assert p.status == "outlined" and p.deck_name

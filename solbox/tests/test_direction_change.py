import json

from app.ai.queue import process_next
from app.models import ProposalMessage
from tests.conftest import outlined_proposal
from tests.test_proposal_directions import _set, good_directions


def _run(app, settings):
    return process_next(app.state.session_factory, app.state.runner, settings)


def _with_deck_and_chat(user_client, db, settings):
    p = outlined_proposal(db, settings)
    db.add_all([ProposalMessage(proposal_id=p.id, role="user", content="옛날 질문"),
                ProposalMessage(proposal_id=p.id, role="assistant", content="옛날 답")])
    db.commit()
    assert user_client.post(f"/proposals/{p.id}/deck", follow_redirects=False).status_code == 303
    db.refresh(p)
    path = settings.decks_dir / str(p.id) / p.deck_name
    assert path.exists() and p.outline
    return p, path


def _assert_cleared(db, p, path):
    db.refresh(p)
    assert p.outline is None and p.outline_updated_at is None
    assert p.deck_name == "" and p.deck_built_at is None and not path.exists()
    assert p.chat_from_id == p.messages[-1].id


def test_switching_direction_clears_outline_and_deck(app, user_client, db, settings, fake_claude):
    p, path = _with_deck_and_chat(user_client, db, settings)
    assert user_client.post(f"/proposals/{p.id}/select", data={"index": "1"}).status_code == 200
    _assert_cleared(db, p, path)
    assert p.selected_index == 1 and p.status == "refining"
    # 새 방향의 대화 프롬프트에는 이전 대화가 들어가지 않는다
    fake_claude.set(mode="stream", chunks=["답"])
    user_client.post(f"/proposals/{p.id}/chat", data={"message": "새 질문"})
    _run(app, settings)
    prompt = fake_claude.calls()[0]["stdin"]
    assert "새 질문" in prompt and "옛날 질문" not in prompt and "옛날 답" not in prompt
    # 구성안 프롬프트도 마찬가지
    fake_claude.set(mode="ok", result=json.dumps({"title": "t", "slides": []}))
    user_client.post(f"/proposals/{p.id}/outline", data={"instruction": ""})
    _run(app, settings)
    outline_prompt = fake_claude.calls()[-1]["stdin"]
    assert "새 질문" in outline_prompt and "옛날 질문" not in outline_prompt
    # 화면에는 이전 대화가 구분선과 함께 남는다
    html = user_client.get(f"/proposals/{p.id}").text
    assert "이전 방향에서 나눈 대화 (새 방향에는 반영되지 않습니다)" in html and "옛날 질문" in html


def test_reselecting_same_direction_keeps_everything(user_client, db, settings):
    p, path = _with_deck_and_chat(user_client, db, settings)
    user_client.post(f"/proposals/{p.id}/select", data={"index": "0"})
    db.refresh(p)
    assert p.outline and p.deck_name and path.exists() and p.chat_from_id is None and p.deck_built_at is not None


def test_regeneration_clears_outline_and_deck(app, user_client, db, settings, fake_claude):
    p, path = _with_deck_and_chat(user_client, db, settings)
    s1, s2 = p.solutions
    _set(fake_claude, good_directions(s1, s2))
    user_client.post(f"/proposals/{p.id}/directions")
    _run(app, settings)
    _assert_cleared(db, p, path)
    assert p.selected_index is None and p.status == "directions"


def test_confirm_only_when_outline_exists(user_client, db, settings):
    p = outlined_proposal(db, settings)
    html = user_client.get(f"/proposals/{p.id}").text
    assert html.count("방향을 바꾸면 만들어 둔 구성안과 제안서가 지워집니다. 계속할까요?") == 2  # 다른 방향 카드 + 다시 만들기
    p.outline = None
    db.commit()
    assert "방향을 바꾸면" not in user_client.get(f"/proposals/{p.id}").text

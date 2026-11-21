"""베타 G 묶음: 사본 경로 조사·단위 판정, 표 머리글 걸러내기, 버려진 폼 표 인수, 제안 대화 한 트랜잭션, 작성자 말투, 오류 분류·비밀 가리기 …"""
from __future__ import annotations

import pytest

from app.deck import has_internal_mark


# ---- G1: 번호 뒤에 글자가 붙으면 단위일 때만 남긴다 ----

@pytest.mark.parametrize("text", [
    "자세한 내용은 solutions/3에서 확인", "customers/12의 니즈를 반영", "solutions/3과 solutions/4를 비교", "solutions/3번 문서",
    "solutions/3를", "solutions/3을", "solutions/3에", "solutions/3은", "solutions/3는", "solutions/3와", "solutions/3과", "solutions/3도",
    "solutions/3로", "solutions/3호", "solutions/3_v2", "solutions/3a",
    "출처:/solutions/3.md", "data/solutions/3.md", "data/solutions/3/card.md", "file:///C:/data/solutions/3.md", "~/backup.old/solutions/3.md",
    "C:/Users/kim.cs/solbox/data/solutions/3.md", "solutions%2F3.md", "solutions%5C3.md", "ｓｏｌｕｔｉｏｎｓ/3.md", "solu\u200btions/3.md",
    "solutions//3.md", "solutions/./3.md", "solutions\\\\3.md",
])
def test_g1_leaks_detected(text):
    assert has_internal_mark(text), text


@pytest.mark.parametrize("text", [
    "solutions/5G/LTE", "customers/100명/월", "projects/3건/년", "partners/2.5억", "customers/12%", "solutions/3~5종", "solutions/3-5개",
    "partners/2개사", "customers/1인당 비용", "solutions/5G 라우터", "IoT solutions/3rd party 연동", "solutions/3월", "customers/1.5만명",
    "intranet/solutions/3", "www.acme.co.kr/solutions/3.md", "localhost:8443/solutions/3", "a-solutions/3.md",
])
def test_g1_units_and_addresses_kept(text):
    assert not has_internal_mark(text), text


# ---- G2: 표 머리글도 걸러낸다 ----

def test_g2_table_header_scrubbed():
    from app.deck import _scrub_customer
    stats = {}
    out = _scrub_customer({"kind": "table", "title": "t", "columns": ["항목", "knowledge/solutions/3/card.md", "고객 customers/12.md"],
                           "rows": [["a", "b", "c"]]}, stats)
    assert out["columns"] == ["항목", "", ""]
    assert out["rows"] == [["a", "b", "c"]]
    assert stats["removed"] == 2


# ---- G3: 버려진 폼 표 이어받기는 한 제출만 ----

def test_g3_abandoned_takeover_has_one_winner(app):
    import threading
    from app import guards
    from app.models import FormToken

    factory = app.state.session_factory
    old_wait = guards.TOKEN_WAIT_S
    guards.TOKEN_WAIT_S = 0.06
    try:
        for i in range(200):
            token = f"/x|race{i}"
            with factory() as d:
                d.add(FormToken(user_id=2, token=token, body_hash="h"))
                d.commit()
            guards._ABANDONED.add((2, token))
            res, bar = [], threading.Barrier(2)

            def go():
                once = guards.Once(factory, 2, token, "h")
                bar.wait()
                try:
                    once.claim()
                    res.append(once.claimed)
                except Exception as e:  # noqa: BLE001
                    res.append(type(e).__name__)

            ts = [threading.Thread(target=go) for _ in range(2)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            assert res.count(True) == 1, (i, res)
            assert (2, token) not in guards._ABANDONED
    finally:
        guards.TOKEN_WAIT_S = old_wait
        guards._ABANDONED.clear()


def test_g3_purge_drops_stale_abandoned(app):
    from app import guards
    guards._ABANDONED.add((9, "/gone|nothing"))
    with app.state.session_factory() as d:
        guards.purge_tokens(d)
    assert (9, "/gone|nothing") not in guards._ABANDONED


# ---- G4: 제안 대화는 메시지와 작업이 한 트랜잭션 ----

def _busy_on_job_insert(monkeypatch_or_none=None):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session
    from app.models import AIJob

    class Busy(Exception):
        sqlite_errorcode = 5

    state = {"on": True}

    def before_flush(session, ctx, instances):
        if state["on"] and any(isinstance(o, AIJob) for o in session.new):
            raise OperationalError("insert", {}, Busy("database is locked"))

    event.listen(Session, "before_flush", before_flush)
    return state, lambda: event.remove(Session, "before_flush", before_flush)


def test_g4_chat_busy_leaves_no_orphan_message(app, user_client, db, settings):
    from app.models import AIJob, ProposalMessage
    from tests.conftest import refining_proposal

    p = refining_proposal(db, settings)
    state, remove = _busy_on_job_insert()
    try:
        r = user_client.post(f"/proposals/{p.id}/chat", data={"message": "가격 장을 앞으로"}, follow_redirects=False)
        assert r.status_code == 503
        db.expire_all()
        assert db.query(ProposalMessage).filter_by(proposal_id=p.id).count() == 0
        assert db.query(AIJob).count() == 0
        state["on"] = False
        r = user_client.post(f"/proposals/{p.id}/chat", data={"message": "가격 장을 앞으로"}, follow_redirects=False)
        assert r.status_code == 303
    finally:
        remove()
    db.expire_all()
    assert [m.content for m in db.query(ProposalMessage).filter_by(proposal_id=p.id)] == ["가격 장을 앞으로"]
    assert db.query(AIJob).count() == 1


def test_g4_intake_analyze_note_not_saved_when_busy(app, user_client, db, settings):
    from app.models import AIJob, Intake
    from tests.conftest import ready_intake

    it, *_ = ready_intake(db, settings)
    iid = it.id
    it.status, it.note = "failed", "원래 메모"
    db.commit()
    state, remove = _busy_on_job_insert()
    try:
        r = user_client.post(f"/intake/{iid}/analyze", data={"note": "고친 메모"}, follow_redirects=False)
    finally:
        remove()
    assert r.status_code == 503
    db.expire_all()
    assert db.get(Intake, iid).note == "원래 메모"


# ---- G5: 작성자 말투 ----

@pytest.mark.parametrize("text", [
    "제안 내용을 검토해 주세요", "유지보수 기간 중 기능 업데이트 예정", "2차 사업 기간에 센서 300대 추가 예정", "월간 실적 보고서 자동 작성 예정",
    "유지보수 기간 연장 시 별도 계약 추가 필요", "예산 수립 시 운영비 포함 필요", "보안 점검 내용 연 1회 업데이트 필요",
])
def test_g5_customer_lines_stay(text):
    from app.proposals import is_author_instruction
    assert not is_author_instruction(text), text


@pytest.mark.parametrize("text", [
    "출처 확인 필요", "고객사명 확인 필요", "담당자 확인 필요", "도입 시기 확인 필요", "TODO: 수치 확인", "TBD: 일정", "이 슬라이드는 삭제할 것",
    "규모 확인 요망", "작성 예정", "금액 미정",
])
def test_g5_author_notes_move(text):
    from app.proposals import is_author_instruction
    assert is_author_instruction(text), text


# ---- G6/G7: 오류 분류와 비밀 가리기 ----

from app.ai.runner import ClaudeError, classify_error, error_text, redact


@pytest.mark.parametrize("text,kind", [
    ("ANTHROPIC_API_KEY is not set", "auth"), ("Request failed with status code 429", "limit"), ("API Error (401) invalid", "auth"),
    ('{"status":401,"error":"x"}', "auth"), ("API Error (429)", "limit"), ("Credit balance is too low", "limit"),
    ("407 Proxy Authentication Required", "network"), ("Error: unauthorized tool use", "unknown"),
])
def test_g6_classify(text, kind):
    assert classify_error(text) == kind


@pytest.mark.parametrize("text,gone", [
    ("실패 sk-ant-api03-abcDEF_123-xyz", "abcDEF_123"), ("ANTHROPIC_API_KEY=hunter2value", "hunter2value"),
    ("Authorization: Bearer abcdef123456", "abcdef123456"), ("x-api-key: abcdef12", "abcdef12"),
    ("export OPENAI_API_KEY='zzzsecret'", "zzzsecret"),
])
def test_g7_redact(text, gone):
    assert gone not in redact(text)
    assert gone not in ClaudeError("unknown", text).message


def test_g7_error_text_has_no_blank_gaps():
    shown = error_text(None, "", result_is_error=False, stdout="Invalid API key", returncode=1)
    assert shown == "Invalid API key"
    assert "\n\n" not in error_text("x", "\n warn \n", result_is_error=True)

"""베타 H 묶음: 사본 경로 단위·확장자·슬래시 닮은꼴·성능, 표 머리글, 제안 한 트랜잭션, 작성자 말투, aria-describedby, 비밀 가리기, 오류 분류 …"""
from __future__ import annotations

import time

import pytest

from app.deck import has_internal_mark


# ---- H1: 번호 뒤 단위는 낱말 끝일 때만 단위다 ----

@pytest.mark.parametrize("text", [
    "근거는 solutions/3만 참고", "customers/12인 문서 참고", "solutions/3대로 진행", "solutions/3등을 참고", "solutions/3인지 확인",
    "customers/12일자 기록", "solutions/3md", "solutions/3Draft", "opportunities/2main", "solutions/3the", "solutions/3Docs", "solutions/3stage",
])
def test_h1_particle_and_word_tails_detected(text):
    assert has_internal_mark(text), text


@pytest.mark.parametrize("text", [
    "projects/3단계 로드맵", "projects/3분기 착수", "partners/3사 협업", "solutions/24시간 지원", "solutions/2세대", "customers/30여곳",
    "projects/1조 규모", "solutions/3x 성능", "customers/12일", "solutions/3위", "customers/12일 기록", "solutions/3대 규모",
    "partners/2개사", "solutions/5G", "solutions/5G/LTE", "customers/100명/월", "projects/3건/년", "partners/2.5억", "customers/12%", "solutions/3~5종",
])
def test_h1_units_kept(text):
    assert not has_internal_mark(text), text


# ---- H2: 확장자 뒤에 조사가 와도 파일 경로다 ----

@pytest.mark.parametrize("text", [
    "출처: data/solutions/3.md의 요약", "backup/customers/12.md에서 인용", "data/solutions/3/card.md의", "data/solutions/3.md,", "(data/solutions/3.md)",
])
def test_h2_file_tail_with_particle(text):
    assert has_internal_mark(text), text


# ---- H3: 슬래시 닮은꼴 ----

@pytest.mark.parametrize("text", [
    "solutions%252F3", "solutions%252f3.md", "solutions∕3", "solutions⁄3", "solutions&#47;3", "solutions&#x2F;3", "solutions&sol;3",
])
def test_h3_slash_lookalikes(text):
    assert has_internal_mark(text), text


# ---- H4: 길고 띄어쓰기 없는 줄도 빨리 끝난다 ----

@pytest.mark.parametrize("text", [
    "q/" + "solutions/3개solutions/3개" * 4000,
    "intranet/" + "solutions/3x" * 8000,
    "a" * 50000 + "/" + "solutions/3개" * 4000,
    "q/" + "solutions/3-" * 9000,
])
def test_h4_pathological_lines_are_fast(text):
    t0 = time.perf_counter()
    has_internal_mark(text)
    assert time.perf_counter() - t0 < 1.0


# ---- H5: 표 머리글은 표 장에서만 걸러 센다 ----

def test_h5_columns_scrubbed_only_for_tables():
    from app.deck import _scrub_customer
    stats = {}
    out = _scrub_customer({"kind": "bullets", "title": "t", "columns": ["solutions/3.md"], "bullets": ["a"]}, stats)
    assert stats["removed"] == 0
    assert out["columns"] == ["solutions/3.md"]
    stats = {}
    out = _scrub_customer({"kind": "table", "title": "t", "columns": ["항목", "solutions/3.md"], "rows": [["a", "b"]]}, stats)
    assert out["columns"] == ["항목", ""] and stats["removed"] == 1


# ---- H6: 제안 건 만들기는 제안 건과 방향 작업이 한 트랜잭션 ----

def _busy_on_job_insert():
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


def _form_token(client, url):
    import re
    return re.search(r'name="_tok" value="([^"]+)"', client.get(url).text).group(1)


def test_h6_create_proposal_busy_leaves_nothing_and_retry_makes_one(app, user_client, db):
    from app.models import AIJob, Customer, Proposal, User

    kim = db.query(User).filter_by(username="kim").one()
    c = Customer(name="한빛병원", created_by_id=kim.id)
    db.add(c)
    db.commit()
    tok = _form_token(user_client, "/proposals/new")
    data = {"customer_id": str(c.id), "request_memo": "두 커밋", "_tok": tok}
    state, remove = _busy_on_job_insert()
    try:
        r = user_client.post("/proposals", data=data, follow_redirects=False)
        assert r.status_code == 503 and "저장하지 않았" in r.text
        db.expire_all()
        assert db.query(Proposal).count() == 0 and db.query(AIJob).count() == 0
        state["on"] = False
        r = user_client.post("/proposals", data=data, follow_redirects=False)
        assert r.status_code == 303, r.status_code
    finally:
        remove()
    db.expire_all()
    props = db.query(Proposal).all()
    assert len(props) == 1 and props[0].active_job_id is not None
    assert db.query(AIJob).count() == 1


def test_h6_create_proposal_ai_off_saves_without_job(app, user_client, db, monkeypatch):
    from app.ai import gate
    from app.models import AIJob, Customer, Proposal, User

    monkeypatch.setattr(gate, "ai_is_off", lambda req: True)
    kim = db.query(User).filter_by(username="kim").one()
    c = Customer(name="한빛병원", created_by_id=kim.id)
    db.add(c)
    db.commit()
    r = user_client.post("/proposals", data={"customer_id": str(c.id), "request_memo": "꺼짐"}, follow_redirects=False)
    assert r.status_code == 303 and "created_ai_off" in r.headers["location"]
    db.expire_all()
    p = db.query(Proposal).one()
    assert p.active_job_id is None and db.query(AIJob).count() == 0


def test_h6_multi_file_upload_busy_midway_says_what_was_saved(user_client, db):
    """파일마다 따로 커밋하므로, 둘째 파일에서 잠금에 막혀도 첫 파일은 저장돼 있다. 503 "아무것도 저장되지 않았다" 가 아니라 어디까지인지 알린다."""
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session
    from app.models import Solution, SolutionFile

    class Busy(Exception):
        sqlite_errorcode = 5

    s = Solution(name="A")
    db.add(s)
    db.commit()
    seen = {"n": 0}

    def before_flush(session, ctx, instances):
        if any(isinstance(o, SolutionFile) for o in session.new):
            seen["n"] += 1
            if seen["n"] == 2:
                raise OperationalError("insert", {}, Busy("database is locked"))

    event.listen(Session, "before_flush", before_flush)
    try:
        files = [("files", ("a.txt", b"one", "text/plain")), ("files", ("b.txt", b"two", "text/plain"))]
        r = user_client.post(f"/solutions/{s.id}/files", files=files, follow_redirects=False)
    finally:
        event.remove(Session, "before_flush", before_flush)
    assert r.status_code == 200 and "저장하지 못한 파일: b.txt" in r.text and "앞의 파일은 저장했습니다" in r.text
    db.expire_all()
    assert [f.original_name for f in db.query(SolutionFile)] == ["a.txt"]


# ---- H7: 작성자 말투 ----

@pytest.mark.parametrize("text", [
    "해당 장비는 철거 후 제거 바랍니다", "이 장치는 설치 후 보호 필름을 제거하세요", "현장 담당자 확인 필요", "귀사 담당자 확인 필요",
    "도입 규모 검토 필요", "규모 확대 검토 필요",
])
def test_h7_customer_lines_stay(text):
    from app.proposals import is_author_instruction
    assert not is_author_instruction(text), text


@pytest.mark.parametrize("text", [
    "실제 고객사 수치로 교체 필요", "최신 통계 수치로 업데이트 필요", "절감률 산출 근거 자료 추가 필요", "도입 사례 사진 추가 예정",
    "금액은 견적 확정 후 기입 예정", "상세 일정은 추후 기재 예정", "담당자 확인 필요", "규모 확인 요망", "이 슬라이드는 삭제할 것",
    "이 장은 삭제하세요", "이 페이지 삭제 바랍니다",
])
def test_h7_author_notes_move(text):
    from app.proposals import is_author_instruction
    assert is_author_instruction(text), text

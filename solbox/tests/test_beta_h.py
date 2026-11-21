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

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

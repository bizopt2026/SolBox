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

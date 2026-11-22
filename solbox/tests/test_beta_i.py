"""베타 I 묶음: 휴리스틱 회귀표(사본 경로·작성자 말투·비밀 가리기·오류 분류)와 쓰기 잠금 업로드·점검 명령 …

지난 세 묶음이 휴리스틱(사본 경로, 작성자 말투, 비밀 가리기)을 고칠 때마다 앞 묶음이 맞히던 줄을 깨뜨렸다. 그래서 이 파일은 휴리스틱마다 하나의 표를 가진다.
표는 tests/test_beta_b~h 와 검토자의 재현 글(review3~6)에서 모은 경우를 겹치지 않게 모은 것이고, 기대값은 CLAUDE.md "베타 I 묶음" 의 정책을 따른다.
앞 자료끼리 어긋난 줄은 정책으로 정했다 (아래 `CONFLICTS`). 휴리스틱을 고치면 이 표가 모두 초록이어야 한다.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
import unicodedata

import pytest
from fastapi.testclient import TestClient

from app.ai.runner import _classify, _redact_all, classify_error, redact
from app.deck import _normalize, has_internal_mark
from app.proposals import is_author_instruction

# 앞 자료끼리 어긋난 줄과 정한 쪽 (표에는 정한 쪽만 있다)
CONFLICTS = {
    "solutions/3G망": "베타 G 시험은 남김, 베타 H 문서는 경로(영문 단위에 한글 낱말이 붙음) → 경로로 지운다 (H 를 따름)",
    "customers/3만 · customers/3만 명 · customers/2등 · customers/5인 · projects/3인 투입": "검토자 h1 은 남기라 했지만 정책은 `solutions/3만` 을 경로로 지운다 → 한 글자 조사 단위가 낱말 끝에 홀로 오면 지운다",
    "solutions/1위를": "위 + 조사: 약한 단위 + 조사는 수량인지 가를 수 없다 → 지운다",
    "customers/50%를 · customers/50%이상": "정책의 `solutions/3%a`·`3%의` 는 경로 → `%` 뒤에는 낱말 글자가 오면 지운다",
    "solutions/24h · solutions/5G망": "모호한 영문 한 글자 단위·한글 낱말 → 지운다",
    "customers/12일 · solutions/3위 · solutions/2세대": "g1 은 모호(None), 베타 H 시험은 남김 → 남긴다",
    "계약 기간 확인 필요 · 추가 예산 검토 필요 · 장비 수량 확인 필요 · 회선 수량 추가 필요 · 센서 수량 추가 예정": "두 낱말 앞말 + 문서 값 낱말: `도입 시기 확인 필요`(베타 G 시험)와 규칙으로 가를 수 없다 → 표에 넣지 않고 지금 동작(옮김)을 둔다",
    "XX% 절감 · 이 슬라이드는 내부용 (AUTHOR_UNDECIDED 에 있던 줄)": "베타 L 의 고정 낱말이 옮겼다 (표의 줄이 아니므로 뒤집은 것은 없다). [베타 Q: `XX% 절감` 은 자리표시 모양이라 지금도 옮기고, `이 슬라이드는 내부용` 은 낱말 규칙을 없애서 슬라이드에 남는다]",
    "착수 전 귀사 보안 정책 확인 필요": "베타 E 의 `도입 전 … 확인 필요` 는 옮김, 검토자는 고객 문장 → `귀사` 가 든 줄만 고객 쪽 조건으로 둔다",
}


def _flat(table: dict) -> list[str]:
    seen: dict[str, None] = {}
    for items in table.values():
        for t in items:
            seen[t] = None
    return list(seen)


# ---- 사본 경로 회귀표: True = 고객용 PPT 에서 지운다(경로), False = 남긴다 (정책은 CLAUDE.md 베타 I 묶음) ----
DECK_REMOVE = {
    'tests c': [
        'customers/12', '자세한 건 solutions/3/card.md 참고', 'research/2026-11-01-x-3.md', '(projects/5)', '출처: solutions/7/files/3-사양서.md',
        'businesses/4.md',
    ],
    'tests e': [
        'knowledge/solutions/3/card.md 참고', 'C:/solbox/data/knowledge/customers/4.md', 'solutions\\3.md', '../solutions/3.md',
        'data\\knowledge\\solutions\\3\\card.md', '/srv/solbox/data/knowledge/customers/4.md', '"solutions/3.md"',
    ],
    'tests f': [
        '근거=customers/12.md', '출처=activities/2026/1.md', '→customers/12.md', '참고·solutions/3/card.md', '※solutions/3', '*solutions/3/card.md*',
        '**solutions/3**', '#solutions/3', '‘solutions/3’', '“solutions/3.md”', '「solutions/3」', '출처:solutions/3.md',
        '~/data/knowledge/solutions/3/card.md', '$DATA/knowledge/solutions/3.md', '%APPDATA%\\solbox\\knowledge\\solutions\\3.md',
        '\\\\server\\share\\knowledge\\solutions\\3.md', 'file:///C:/solbox/knowledge/solutions/3.md', '/home/user/my.app/knowledge/solutions/3.md',
        '/srv/solbox.kr/data/knowledge/solutions/3.md', 'data.bak/knowledge/solutions/3.md', 'SOLUTIONS/3.md', 'knowledge/Solutions/3.md',
        'C:\\Users\\kim\\solbox\\data\\knowledge\\solutions\\3\\card.md', 'D:\\솔박스 자료\\knowledge\\solutions\\3.md',
        '출처: opportunities/2.md, customers/12.md', '근거는solutions/3.md', 'solutions/3.md참고', 'solutions\\3', 'x:solutions/3', '[[solutions/3]]',
        'research/2026-11/5.md', 'activities/2026/1.md', '1.solutions/3.md', '-solutions/3.md', '- solutions/3.md', 'solutions／3',
        'solutions/3 단계로 구성', 'research/2026 보고서 인용', 'activities/2026 계획', 'the customers/1000 ratio',
    ],
    'tests g': [
        '자세한 내용은 solutions/3에서 확인', 'customers/12의 니즈를 반영', 'solutions/3과 solutions/4를 비교', 'solutions/3번 문서', '출처:/solutions/3.md',
        'file:///C:/data/solutions/3.md', '~/backup.old/solutions/3.md', 'C:/Users/kim.cs/solbox/data/solutions/3.md', 'solutions%2F3.md',
        'solutions%5C3.md', 'ｓｏｌｕｔｉｏｎｓ/3.md', 'solu\u200btions/3.md', 'solutions//3.md', 'solutions/./3.md', 'solutions\\\\3.md',
    ],
    'general folder + file tail': [
        'data/solutions/3.md', 'data/solutions/3/card.md', 'data/solutions/3.md,', 'data/solutions/3.md의', 'data/solutions/3.markdown',
        'data/solutions/3.json', 'data/solutions/3.bak', 'data/solutions/3/files/1.pdf',
    ],
    'tests h': [
        '근거는 solutions/3만 참고', 'customers/12인 문서 참고', 'solutions/3대로 진행', 'solutions/3등을 참고', 'solutions/3인지 확인', 'customers/12일자 기록',
        'solutions/3Docs', '출처: data/solutions/3.md의 요약', 'backup/customers/12.md에서 인용', 'data/solutions/3/card.md의', '(data/solutions/3.md)',
        'solutions%252F3', 'solutions%252f3.md', 'solutions&#x2F;3',
    ],
    'L-2': [
        'solutions∕3', 'solutions⁄3', 'solutions&#47;3', 'solutions&sol;3', 'solutions⧸3', 'solutions%25252F3.md', 'solutions%2525252F3',
        'solutions%25255C3', 'solutions&#047;3', 'solutions&#0047;3', 'solutions&#x02F;3', 'solutions&#47/3', 'solutions&#92;3', 'solutions&#x5c;3',
        'solutions&bsol;3', 'solutions&amp;#47;3', 'solutions&amp;amp;#47;3', 'solutions&#X2F;3', 'solutions⧵3', 'solutions∖3', 'solutions╱3',
        'solutions%252F3.md',
    ],
    'policy path+particle': [
        'solutions/3만',
    ],
    'review5 g1': [
        'solutions/3등', 'solutions/3만 참고', '근거: solutions/3만 봐도', 'solutions/3인 경우', 'solutions/3만이', 'solutions/3일까', 'solutions/3인지',
        'solutions/3이다', 'solutions/3원본', 'solutions/3주소', 'solutions/3회의록', 'solutions/3대상', 'solutions/3명시', 'solutions/3인용', 'solutions/3개요',
        'solutions/3점검', 'solutions/3Markdown', 'solutions/3data', 'solutions/3detail', 'solutions/3-1', 'solutions/3.5', 'solutions/3개.md',
        'solutions/3#장', 'solutions/3?x=1', 'solutions/3%20card.md', 'solutions/3%2Fcard.md', 'solutions%2F3', 'ｓｏｌｕｔｉｏｎｓ/３', 'solu\u200btions/3',
        'solutions//3', 'solutions/./3', 'solutions/03', 'solutions/٣', 'solutions/３', 'solutions/③', 'Solutions/3', 'SOLUTIONS/3.MD',
        'solutions/3\xa0에서', 'solutions/5G.md', 'solutions/5G/card.md', 'solutions/3개/card.md', 'solutions/3명/월.md', 'backup/knowledge/solutions/3',
        '출처:/solutions/3', '출처: solutions/3', '~/backup.old/solutions/3', 'C:/Users/kim.cs/solutions/3', 'file://server.local/solutions/3',
        'D:\\data\\solutions\\3', 'data\\solutions\\3.md', 'ftp://nas/knowledge/solutions/3', '\\\\nas\\share\\solutions\\3',
        'srv/data/solutions/3.md', 'research/2026 보고서', 'customers/50%이상', 'solutions/3way', 'solutions/3 개',
    ],
    'L-1': [
        'solutions/../solutions/3', 'solutions/3%a', 'solutions/3%의', 'data/solutions/3.html', 'data/solutions/3/card.markdown',
    ],
    'h-doc(conflict with g1)': [
        'solutions/3G망',
    ],
    'beta J: knowledge anchor, entities, mirror files': [
        'knowledge/solutions/3일', 'C:\\data\\knowledge\\solutions\\3개', 'solutions/&#51;', 'solutions%2F%33', 'data/knowledge/index.md', 'knowledge/solutions/card.md', 'data/solbox.db',
    ],
    'beta K (review8 k3/k4/l8: mirror files followed by a Korean particle, mirror files next to a URL, activities/undated, data folders)': [
        'knowledge/index.md의 목록', 'knowledge/index.md에서', 'knowledge/index.md를 참고', 'knowledge/solutions/card.md의', 'data/app.db를 백업', 'C:\\solbox\\data\\app.db에',
        'https://kt.com,knowledge/index.md', '출처:https://kt.com/a;data/app.db', 'activities/undated/12.md', '근거=activities/undated/12.md', 'knowledge/activities/undated/12.md',
        'activities/undated/12.md에서', '(activities/undated/12.md)', '/srv/solbox/data/app.db', 'solbox/data/app.db',
        'data/uploads/intake/5/9.m4a', 'uploads/intake/5/9.m4a.transcript.txt', 'data/intake_work/5/1-회의록.md', 'intake_work/5/파일목록.md', 'research_work/x.md',
        'data/decks/4/deck-20261121-101500-ab12cd.pptx', 'backups/20261121-101500/app.db', 'data/logs/app.log', 'data/models/small/model.bin',
    ],
    'beta L (review9 l1: shapes the app writes that the K table missed; `data/models/` only with the app model dir names or a solbox head)': [
        'data/app.db.before-migrate-20261121-101500', 'data/app.db.before-migrate-20261121-101500-2', 'app.db.before-migrate-20261121-101500-2',
        'data/uploads/intake/5/9.m4a.transcript.txt.tmp', 'uploads/intake/5/9.m4a.transcript.txt.tmp', 'data/decks/4/deck-20261121-101500-ab12cd.pptx.part',
        'decks/4/deck-20261121-101500-ab12cd.pptx.part', 'data/models/.hf_home', 'data/models/models--Systran--faster-whisper-small/snapshots/x/model.bin',
        'solbox/data/logs/x', 'C:\\solbox\\data\\models\\foo',
    ],
    'beta M (review10 d1: only real remote schemes are addresses; `data/decks/7`-like ends)': [
        'C://solbox/data/app.db', '경로:C://solbox/data/uploads/solutions/3/a.pdf', 'C://Users/kim/solbox/data/decks/7/deck-20261121-101500-ab12cd.pptx', 'c://solbox/data/logs/app.log',
        'sqlite:///C:/solbox/data/app.db', 'sqlite:///data/app.db', 'x://data/uploads/solutions/3/a.pdf', 'path://data/app.db', 'note://decks/4/deck-20261121-101500-ab12cd.pptx',
        '(x://intake_work/5/1-회의록.md)', 'data/decks/7', 'data\\decks\\7', 'data/uploads/solutions', 'data/uploads/intake',
    ],
    'review6 h1 keep list (conflict: policy/H)': [
        'customers/3만 명', 'customers/3만', 'customers/2등', 'customers/5인', 'customers/5인 이하', 'solutions/1위를', 'customers/50%를', 'solutions/24h',
        'solutions/5G망', 'projects/3인 투입',
    ],
}

DECK_KEEP = {
    'tests c': [
        '자세한 소개: https://acme.co.kr/solutions/ai-vision', 'B2B customers/partners 모두 지원', 'README.md 참고', 'research/개발 조직과 협업', '연 2/3 수준',
        'notes.md 파일', 'a-solutions/3.md', 'https://x.kr/solutions/3.md',
    ],
    'review6 h1 keep list': [
        'www.acme.co.kr/customers/12', 'intranet/solutions/3', '24/7 지원', 'localhost:8443/solutions/3', 'customers/100명/월', 'customers/30여곳',
        'solutions/3위', 'solutions/3D', 'solutions/3가지', 'solutions/4K', 'solutions/30분', 'solutions/1st', '3/4분기 실적', '1/3 수준으로 절감', '속도 100km/h',
        '10/100M 이더넷', 'A/S 3건', '2024/25 시즌', '1/2', 'TCP/IP 4계층', '입/출력 3종', 'partners/2개사 확보', 'solutions/5G 기반', 'customers/12% 증가',
        'solutions/3종', 'solutions/3단계', 'partners/3분기', 'customers/24시간', 'solutions/3개 이상 도입', '고객사 customers/12일 기준', 'customers/3대',
        'customers/2주', 'partners/5개사와 협력', 'solutions/3개를 묶음', 'customers/10명이 사용', 'solutions/3종의 솔루션', 'customers/100명의 사용자', 'customers/3년간',
        'customers/3년 계약', 'solutions/2건을 수주', 'customers/3곳에서 운영', 'solutions/3개월간', 'projects/3개월', 'projects/2단계로', 'projects/6개월의 일정',
        'projects/3명 투입', 'opportunities/5억 규모', 'opportunities/5억원의', 'opportunities/3건이', 'activities/주 3회', 'activities/3회씩', 'customers/1만명',
        'partners/3사', 'partners/3사와', 'partners/3社', 'solutions/3조', 'solutions/30분 이내', 'solutions/2배로', 'solutions/2배', 'solutions/3.5배 향상',
        'customers/50% 이상', 'solutions/3가지를', 'solutions/3일만에', 'solutions/3일 만에', 'solutions/2차년도', 'solutions/2x', 'solutions/4k', 'solutions/8K급',
        'solutions/3D 모델', 'solutions/100M', 'https://a.com/solutions/3.md', 'a-solutions/3', '제휴 파트너사/고객사 3곳', '솔루션/서비스 3종', '보안/네트워크 2개 영역',
        'solutions / 3개', 'Solutions/3rd party', 'Research/2026년 전망', 'research/2026년도 계획', 'bm/3종', 'bm/3개의 모델', 'needs/3가지', 'contacts/2명',
        'contacts/2명과', 'files/3개', 'files/3개를 첨부',
    ],
    'tests e': [
        'https://x.com/solutions/3', 'x.com/solutions/3', 'http://localhost:8443/customers/12',
    ],
    'beta J (review7 d1/d2: a later dot in the same word is not an extension)': [
        'partners/2개사(kt.com)', 'solutions/3종(v2.0)', 'customers/100명(2026.11 기준)', 'projects/3개월(2026.1~2026.3)', 'customers/1,200명', 'projects/3MM', 'activities/1주차',
        'opportunities/10억대', 'opportunities/100억원대', 'solutions/3안', 'solutions/2중화', 'partners/3자 협력', 'research/3편', 'solutions/24x7',
    ],
    'tests f': [
        'https://acme.co.kr/solutions/3', '192.168.0.10/customers/12', '고객사/파트너사 3곳', '2026/11/20', 'TCP/IP', 'partners/2개사', 'projects/2건 수주',
        'opportunities/3건', 'customers/1인당 비용', 'solutions/5G 라우터', 'IoT solutions/3rd party 연동', 'e-solutions/3', 'Smart-Solutions/3D',
        'bit.ly/solutions/3', 'docs.acme.io/customers/1', 'AWS/Azure solutions/3종', '(https://x.com/solutions/3)',
    ],
    'tests g': [
        'solutions/5G/LTE', 'projects/3건/년', 'partners/2.5억', 'customers/12%', 'solutions/3~5종', 'solutions/3-5개', 'solutions/3월', 'customers/1.5만명',
        'www.acme.co.kr/solutions/3.md',
    ],
    'tests h': [
        'projects/3단계 로드맵', 'projects/3분기 착수', 'partners/3사 협업', 'solutions/24시간 지원', 'solutions/2세대', 'projects/1조 규모', 'solutions/3x 성능',
        'customers/12일', 'customers/12일 기록', 'solutions/3대 규모', 'solutions/5G',
    ],
    'review5 g1': [
        'solutions/3M', 'customers/12월', 'solutions/3차', 'solutions/3건의', 'solutions/3Kb', 'solutions/3GB', 'solutions/3th', 'solutions/3st',
        'solutions/3-5종', 'solutions/3%', 'customers/12개사', 'projects/2건', 'customers/1인당', 'solutions/2.5억', 'solutions/3개사의', 'solutions/3건을',
        'x.com/solutions/3.md', '고객 12곳/파트너 3곳', 'solutions 3종', 'research/2026년', 'activities/2026년도', 'research/2026년도', 'projects/3차년도',
        'projects/1단계', 'projects/3단계', 'projects/3분기', 'projects/1분기', 'customers/3사', 'customers/10여곳', 'partners/20여개사', 'customers/5개소',
        'solutions/3개씩', 'projects/10여건', 'customers/3社', 'solutions/8K', 'solutions/3세대', 'solutions/3시간', 'solutions/24시간', 'projects/6개월',
        'projects/3년차', 'solutions/3rd', 'solutions/3x', 'solutions/2배속', 'projects/3억원', 'projects/1조', 'projects/3천만원', 'solutions/3개 솔루션',
    ],
    'beta K (review8 l9: general words that look like mirror or data files)': [
        'docs/knowledge/guide.md 제공', 'Obsidian vault(knowledge/notes.md)', 'R&D/data/app.db 미사용', 'kt.com/data/app.db', '/var/lib/app.db', 'node_modules/app.db',
        'https://kt.com/knowledge/index.md', 'https://x.com/data/app.db', 'models/ 폴더 안의 GPU 목록', 'logs/ 와 uploads/ 설명',
    ],
    'beta L (review9 l1: ordinary customer bullets with a `data/<folder>/` path or a scheme URL)': [
        'data/logs/metrics 통합 수집', 'big data/logs/분석', 'sensor data/logs/alerts 연동', 'open data/models/API 제공', 'MLOps: data/models/serving 3계층',
        '데이터 레이크(data/models/logs) 구성', 'S3 버킷 data/uploads/ 에 저장', '고객사 서버 /data/models/ 에 모델 배포', 'D:\\data\\logs\\ 보관 90일',
        'HDFS /user/data/models/v1', 's3://kt-lake/data/logs/2026/', 'gs://bucket/data/models/v3',
    ],
    'beta M (review10 d1: remote schemes on the allowlist stay addresses)': [
        's3://kt-lake/data/decks/7', 'hdfs://nn:8020/data/uploads/solutions', 'sftp://files.kt.com/data/uploads/intake', 'jdbc:postgresql://db/data/app.db', 'gs://bucket/data/uploads/solutions/3/x.pdf',
    ],
    'policy M-4': [
        'solutions/3Mbps', 'partners/2개사가 참여', 'solutions/10Gbps', 'solutions/100MB', 'solutions/1GB', 'research/2026년에는', 'partners/2개사가',
        'customers/100명의', 'solutions/3개를', 'projects/6개월의',
    ],
}

# ---- 작성자 말투 회귀표: MOVE = 확인할 점으로 옮긴다, STAY = 슬라이드에 둔다 ----
AUTHOR_MOVE = {
    'test_beta_b': [
        '총액과 견적 유효기간을 함께 제시', '담당자 이름을 기재해 주세요.', '금액 확인 필요', '연락처를 추가할 것', '일정을 작성할 것', '[TBD] 비용', '수치를 입력하세요', '절감률 보완 필요',
    ],
    'test_beta_c': [
        '담당자 이름을 함께 기재', '보안 정책을 반드시 명시', '도입 전 네트워크 환경 확인 필요', '세부 일정 수정 필요', '자료를 확인하시기 바랍니다', '검토 요망', '○○', '(○○)', 'OOO', '담당자: ○○○', '비용: ○○원',
        '수치를 기재해 주세요.',
    ],
    'test_beta_e': [
        '담당자 이름을 기재해 주세요', '수치를 기입 바랍니다', '근거 자료를 삽입할 것',
    ],
    'test_beta_f': [
        '근거를 제시할 것', '가격표를 포함할 것', '레퍼런스를 포함하세요', '실제 수치로 교체할 것', '근거 자료 보강할 것', '확인할 것', '고객 사례 추가 필요', '내용 보강 필요', '수치 업데이트 필요', '확인 필요: 절감률',
        '[확인 필요] 절감률 15%', '금액 확인 필요 - 견적팀', '절감률 수치 확인 필요(영업팀)', '작성 예정', '금액 미정', '여기에 도입 사례를 넣으세요', '확인 바람', '가격 TBD',
    ],
    'test_beta_g': [
        '출처 확인 필요', '고객사명 확인 필요', '담당자 확인 필요', '도입 시기 확인 필요', 'TODO: 수치 확인', 'TBD: 일정', '이 슬라이드는 삭제할 것', '규모 확인 요망',
    ],
    'test_beta_h': [
        '실제 고객사 수치로 교체 필요', '최신 통계 수치로 업데이트 필요', '절감률 산출 근거 자료 추가 필요', '도입 사례 사진 추가 예정', '금액은 견적 확정 후 기입 예정', '상세 일정은 추후 기재 예정', '이 장은 삭제하세요',
        '이 페이지 삭제 바랍니다',
    ],
    'h7.memo': [
        '사례 사진 삽입 예정', '일정은 견적 확정 후 기입 예정', '금액은 영업팀 확인 후 기재 예정', '해당 장은 삭제하세요', '이 슬라이드 삭제', '이 장 삭제 예정', '수치 확인 필요함', '수치 확인이 필요', '수치 확인 필요합니다',
        '금액 재확인 필요', '금액 확인 요', '수치 검증 필요', '수치 출처 필요', '(금액 확인 필요)', '※ 금액 확인 필요', '- 금액 확인 필요', '금액 확인 필요!', '금액 확인 필요?', '금액 확인 필요...',
        '금액 확인 필요 (영업)', '담당자 이름 확인 필요', '규모 수치 확인 필요', '담당자명 기입 예정', '고객사 담당자 확인 필요', '이 슬라이드를 삭제할 것', '이 슬라이드 삭제할 것', '이 슬라이드는 발표 전 삭제할 것',
        '해당 페이지 삭제 요망', '본 슬라이드는 삭제할 것', '아래 표는 예시이며 실제 값으로 교체', '로고 삽입 예정', '그림 삽입 예정', '도표 추가 예정', '스크린샷 추가 필요', '이미지 교체 필요', '추후 작성', '추후 보완 예정',
        '내용 추후 보완', '수치는 추후 업데이트',
    ],
    'h7c.extra': [
        '이 장은 삭제할 것', '이 쪽을 제거해 주세요',
    ],
    'beta J (review7 a1: lead is all document values, or starts with a deictic)': [
        '절감률 수치 근거 확인 필요', 'ROI 수치 출처 확인 필요', '이 금액은 다시 확인 필요', '절감률 30%는 근거 확인 필요', '이 부분 사례 추가 필요', '여기에 고객 사례 추가 필요', '레퍼런스 사례 2건 추가 필요',
        '고객사 맞춤 내용으로 수정 필요', '위 수치는 영업팀과 다시 확인 필요',
    ],
    'beta K (review8 k2: picture/data memos the J site-word rule kept)': [
        '현장 사진 삽입 예정', '장비 사진 삽입 필요', '설치 사진 삽입 예정', '센서 이미지 삽입 필요', 'CCTV 화면 스크린샷 추가 필요', '건물 외관 사진 추가 예정', '기존 로고 교체 필요',
        '실제 데이터로 교체 필요', '실제 사진으로 교체', '실제 고객사 로고로 교체', '실제 화면 캡처로 교체', '샘플 이미지 → 실제 이미지로 교체',
        '데이터 출처 필요', '그래프 출처 필요', '자료 출처 필요', '인용 출처 필요', '근거 자료 필요',
    ],
    'beta L (review9 l3: fixed vocabulary of internal words, any other rule aside)': [
        'XX% 절감', '절감률 XX%', 'OO 절감', 'OO원 절감', '○○', '약 ○○억 규모', 'OO 개사',
    ],
    'beta M (review10 a1: memo-shaped lines of the fixed vocabulary still move)': [
        'XX% 절감 (수치 입력)', '[초안] 금액 수정 필요',
    ],
}

# 사용자 결정 2026-11-22: 내부 단어 규칙을 뺐다. 이 줄들은 이제 슬라이드에 남는다 (베타 L~O 의 고정 낱말 규칙이 옮기던 줄. 글은 그대로, MOVE 에서 STAY 로 옮겼다).
AUTHOR_WORD_RULE_RETIRED = {
    'beta N': [
        '고객에게 보여주지 말 것 (원가 포함)',
        '고객에게 보여주지 말 것 - 원가 포함',
        '이 표는 고객에게 보여주지 말 것, 내부 원가임',
        '이 장표는 최종본에서 고객에게 보여주지 말 것',
        '고객에게 보여주지 말고 구두로만 설명',
        '내부 검토용 (배포 금지)',
        '내부 검토용 - 배포 금지',
        '내부 검토용, 고객 배포 전 삭제',
        '내부 검토용 자료',
        '팀장님 확인 후 금액 확정',
        '팀장님 피드백 반영',
        '팀장님 코멘트: 가격 낮출 것',
        '작성자 메모: 할인율 15%까지 가능',
        '법무팀 검토 필요 (위약금 조항)',
        '법무팀 검토 후 반영',
        '법무팀 검토 중',
        '폰트 통일 필요 (본문 맑은고딕)',
        '슬라이드 순서 조정 필요 (3장 뒤로)',
        '로고 넣기 (우측 상단)',
        '제목 수정 필요함',
        '[초안] 미확정',
        '초안 - 수정 중',
        '초안 - 검토 전',
        '초안 – 영업팀 검토 전',
        '초안: 검토 전',
    ],
    'beta L (review9 l3: fixed vocabulary of internal words, any other rule aside)': [
        '작성자: 김대리',
        '이 문서의 작성자는 영업팀',
        '팀장님 확인 후 수정',
        '법무팀 검토 필요',
        '법무팀 검토 후 삭제',
        '내부 검토용',
        '이 슬라이드는 내부용',
        '[내부용] 단가표',
        '내부용: 원가 구조',
        '초안',
        '(초안)',
        '[초안]',
        '초안 - 2차 수정 예정',
        '초안: 영업팀',
        '제안 개요 (초안)',
        '오타',
        '제목 오타 수정',
        '제목 오타',
        '오타 확인',
        '폰트 통일',
        '폰트 변경 필요',
        '슬라이드 순서 변경',
        '고객에게 보여주지 말 것',
        '보여주지 말 것',
        '(수치 입력)',
        '(금액 입력)',
        '(입력)',
        '로고 넣기',
        '고객 로고 넣기',
        '제목 수정 필요',
    ],
    'beta M (review10 a1: memo-shaped lines of the fixed vocabulary still move)': [
        '작성자 확인 필요',
        '팀장님 검토 요망',
        '법무팀 검토 필요',
        '내부 검토용',
        '초안',
        '오타 수정',
        '폰트 통일 필요',
        '슬라이드 순서 조정 필요',
        '제목 수정 필요',
        '로고 넣기',
        '고객에게 보여주지 말 것',
        '문서 작성자 확인',
    ],
}

AUTHOR_STAY = {
    'beta N': [
        '고객에게 보여주지 말아야 할 정보 자동 마스킹', '내부 검토용 대시보드 제공', '내부 검토용 자료 자동 생성', '팀장님 승인 한 번으로 결재 완료', '법무팀 검토 프로세스 자동화', '계약서 법무팀 검토 기간 5일 → 1일 단축', '법무팀 검토 요청부터 승인까지 한 화면에서', '오타 자동 수정', '오타 실시간 체크', '화면에 보여주지 말고 마스킹 처리',
    ],
    'beta O (review12: customer feature lines that start with a memo head or end with a reason-like tail)': [
        '팀장님 확인 한 번으로 휴가 결재 완료', '작성자 메모 기능으로 결재 의견 전달', '팀장님 피드백 반영 시간 절반으로 단축', '오타, 누락 조항, 금액 불일치 자동 탐지', '로고 넣기, 색상 변경 등 화이트라벨 지원', '슬라이드 순서 변경, 템플릿 교체를 클릭 한 번으로', '내부 검토용, 외부 제출용 양식 자동 전환', '작성자 본인 확인 (2단계 인증)', '팀장님 보고 자료 자동 생성', '팀장님 확인 없이 자동 결재', '작성자 의견 등록 기능 제공', '작성자 메모 기능', '고객에게 보여주지 말고 마스킹 처리', '오타, 띄어쓰기 자동 교정', '법무팀 검토 - 승인 - 날인 자동 연결', '폰트 변경, 색상 변경 지원', '내부 검토용 - 외부 공유용 권한 분리', '작성자 확인 (본인 인증)', '법무팀 검토 (자동)', '법무팀 검토, 승인 자동화',
    ],
    'test_beta_b': [
        '단계별 로드맵 제시', '견적을 제시합니다', '도입 효과를 확인할 수 있습니다', '필요한 자원을 확인합니다', '월 4천만원 수준의 운영비', '총액은 1억원이며 견적 유효기간은 30일입니다', '문의 주세요', '제안서 작성 일정',
    ],
    'test_beta_c': [
        'AI가 회의록 초안을 작성', '부족한 현장 인력을 보완', '고객 데이터를 입력', '월간 운영 보고서를 제시', '기존 시스템의 한계를 보완', '계약서에 책임 범위를 명시', '○○시청 도입 사례', '구축 후 3개월간 개선 방안을 제시.',
        '현장 사진을 삽입', '지금 바로 문의하세요', '반드시 지켜야 할 3가지 약속',
    ],
    'test_beta_e': [
        '많은 관심 바랍니다', '긍정적인 검토 부탁드리며 연락 바랍니다.', '설치·교육 함께 포함', '보안 인증은 반드시 필요한 항목으로 기본 포함', '매월 보고서로 성과를 확인할 것', '24시간 장애 대응 요망',
    ],
    'test_beta_f': [
        '제안 내용을 검토해 주시기 바랍니다', '귀사의 검토 바랍니다', '점검 결과를 앱에서 확인하세요', 'QR 코드로 설치 현황을 확인해 주세요', '네트워크 구성 확인 필요', '사전 현장 확인 필요', '기존 설비 보완 필요',
        '현장 여건 검토 요망', 'ROI 분석 결과를 함께 제시', 'TODO 앱 연동', '서비스명: TBD Cloud', '담당: OOO 팀', '운영 인력 2명 추가 필요', '운영 매뉴얼과 교육 자료를 함께 제시', '서버실 온도 관리 필요',
    ],
    'test_beta_g': [
        '제안 내용을 검토해 주세요', '유지보수 기간 중 기능 업데이트 예정', '2차 사업 기간에 센서 300대 추가 예정', '월간 실적 보고서 자동 작성 예정', '유지보수 기간 연장 시 별도 계약 추가 필요', '예산 수립 시 운영비 포함 필요',
        '보안 점검 내용 연 1회 업데이트 필요',
    ],
    'test_beta_h': [
        '해당 장비는 철거 후 제거 바랍니다', '이 장치는 설치 후 보호 필름을 제거하세요', '현장 담당자 확인 필요', '귀사 담당자 확인 필요', '도입 규모 검토 필요', '규모 확대 검토 필요',
    ],
    'h7.cust': [
        '귀사 일정에 맞춰 센서 추가 예정', '계약 기간 내 무상 업데이트 예정', '1단계 구축 기간 중 교육 자료 작성 예정', '운영 실적 보고서 매월 작성 예정', '해당 장비는 설치 후 기존 설비를 제거할 것',
        '이 장치는 필터를 주기적으로 제거해 주세요', '해당 페이지는 관리자 화면에서 삭제 바랍니다', '이 쪽은 고객이 직접 삭제할 것', '착수 전 귀사 보안 정책 확인 필요', '설치 일정은 귀사와 협의 후 계획서 작성 예정',
        '도입 일정 협의 필요', '도입 일정 사전 확인 필요', '설치 전 전력 용량 수치 확인 필요', '귀사 예산 범위 내 구성 검토 필요', '사업 기간 연장 검토 필요', '연간 사용 실적 기준 요금제 검토 필요',
        '도입 사례 기반 PoC 추가 예정', '신규 기능은 분기별 업데이트 예정', '교육 내용은 귀사 요청에 따라 추가 예정', '교육 일정은 착수 후 2주 내 확정하여 계획서 작성 예정', '가격은 물량에 따라 조정 예정',
        '가격은 별도 견적서에 기재 예정', '보안 진단 결과는 보고서에 기재 예정', '수량 변경 시 견적 수정 필요', '도입 시기 조율 필요', '현장 수량 실사 후 견적 확정', '월 1회 정기 점검 보고서 작성',
        '데이터 수집 근거 법령 검토 필요', '개인정보 처리 근거 확인 필요', '기존 장비 교체 필요', '노후 센서 교체 필요', '펌웨어 업데이트 필요', '정기 점검 일정 수립 필요', '데이터 백업 주기 확인 필요', '현장 실사 일정 확인 필요',
        '추가 비용 발생 여부 확인 필요', '설치 공간 확인 필요', '무상 보증 기간 2년 포함', '사전 교육 일정 포함 필요', '통신 요금 별도 확인 필요', '연동 대상 시스템 내용 확인 필요', '도입 효과는 3개월 운영 실적으로 검증 예정',
        '실적 데이터 대시보드에 자동 추가 예정', '센서 수량 추가 필요 시 증설 가능', '월별 통계 보고서에 자동 포함 예정', '일정은 귀사 사정에 맞춰 조정 가능하며 착수 보고서에 기재 예정', '이 장은 귀사 맞춤 요약입니다',
    ],
    'beta J (review7 a1: customer sentences the I rules moved)': [
        '세부 기능은 추후 업데이트 예정', '기능은 추후 업데이트', '상세 일정은 추후 확인', '노후 장비를 실제 운영 장비로 교체', '시범 장비는 실제 양산 장비로 교체 예정', '실제 운영 환경으로 교체',
        '증빙 자료 필요', '법적 근거 필요', '추가 자료 필요', '현장 도면 확인 필요', '설치 위치 도면 확인 필요', '현장 사진 첨부 필요', 'CCTV 이미지 확인 필요',
    ],
    'beta K (review8 k1: customer sentences with a deictic lead)': [
        '위 일정은 귀사 사정에 따라 수정 예정', '아래 금액은 수량 변동 시 수정 예정', '본 견적은 수량 확정 후 수정 예정', '위 금액은 VAT 별도이며 계약 시 재확인 필요', '본 계약 기간 내 무상 교체 예정',
        '본 사업 기간 중 장비 추가 예정', '이 기간 동안 전담 인력 추가 예정', '본 제안 가격은 환율 변동 시 수정 예정',
    ],
    'h7c.extra': [
        '도입 후 3개월간 운영 실적 확인 필요', '설치 후 통신 품질 수치 검토 필요', '납품 후 검수 기간 확인 필요', '별도 예산 확보 필요', '연간 유지보수 예산 포함 필요', '방화벽 예외 정책 추가 필요', '계정 권한 수정 필요',
        '월 사용량 통계 확인 필요', '관리자 페이지에서 통계 확인 가능', '서비스 기간 연장 시 재계약 필요', '해당 페이지는 로그인 후 확인하세요',
    ],
    'beta L (review9 l3/l7: customer bullets that share a word with the fixed vocabulary)': [
        '초안 검토 후 최종안 제출', '설계 초안 제출 2주', '내부망 연동', '폰트 라이선스 포함', '오타 없는 OCR 인식률 99%', '팀장급 PM 투입', '로그인 화면 (ID 입력)', '내부용 서버 구축', 'OO건설 사례',
        'XX전자 협력', 'OOO 시스템 도입', '시범 데이터는 실제 운영 데이터로 교체 예정',
    ],
    'beta M (review10 a1: customer feature sentences that contain a fixed-vocabulary word)': [
        '작성자 이력 관리 기능', '문서 작성자·승인자 추적', '작성자 본인만 수정 가능', '작성자', '법무팀 검토 프로세스 자동화', '계약서 법무팀 검토 기간 5일 → 1일 단축', '초안: 2주 내 제출', '(초안) 일정표',
        '1차 산출물: 설계서 (초안)', '음성 인식 후 오타 검토 단계 제공', '상담 기록의 맞춤법·오타 수정 자동화', '문서 내 오타 발견 시 알림', '폰트 변경 기능 제공', '사용자별 테마·폰트 변경 지원',
        '보고서 템플릿 폰트 통일 기능', '배포 대상: 내부용', '사내 직원 내부용', '대상: 임직원 (내부용)', '용도: 내부용', '슬라이드 순서 자동 정렬', '발표 자료 슬라이드 순서 추천 기능', '로고 넣기 기능',
        '보고서에 회사 로고 넣기 지원', '제목 수정 필요 시 관리자 문의', '게시글 제목 수정 필요 시 이력 저장', '(금액 입력) 화면', '견적 요청 화면 (수량 입력)', '주문 화면 [수량 입력]', '조회 조건 (기간 입력)',
        '신청서 (이름 입력) 단계 생략', '내부 검토용 대시보드 제공', '팀장님 승인 한 번으로 결재 완료', '고객에게 보여주지 말아야 할 정보 자동 마스킹',
    ],
}

# 아래는 표에 넣지 않은 줄이다 (규칙으로 가를 수 없어 지금 동작을 그대로 둔다: 슬라이드에 두거나, 앞 묶음이 정한 둘 낱말 규칙을 따른다)
AUTHOR_UNDECIDED = ['??? 확인', 'N개사 도입', 'XX% 절감', '가격 협의 중(내부)', '가격은 미확정', '계약 기간 확인 필요', '금액 다시 확인', '도입 규모 확인 요망', '레퍼런스 넣기', '사례 추가하기', '센서 수량 추가 예정', '수치는 임의 값임', '약 00억 원', '여기에 고객 로고 삽입', '이 슬라이드는 내부용', '이 슬라이드에 사례 추가', '작성 중', '장비 수량 확인 필요', '추가 예산 검토 필요', '확인 후 수정', '회선 수량 추가 필요']

# ---- 비밀 가리기 회귀표: MASK = (글, 사라져야 하는 값 또는 None) 가려야 하고, LEAVE = 그대로 둔다 ----
MASK = [
    # 베타 J (review7 r1/r2): 값은 런타임에 조립한다 (키 모양 글자가 소스에 그대로 있으면 비밀 검사기가 잡는다)
    ('{"Authorization":["Bearer Zq7pX2mK9xxxxxx"]}', 'Zq7pX2mK9'),
    ('https://user:p@ssZq7pX2mK9@proxy:8080', 'Zq7pX2mK9'),
    ('http://Zq7pX2mK9@proxy:8080', 'Zq7pX2mK9'),
    ('\\"api_key\\": \\"Zq7pX2mK9\\"', 'Zq7pX2mK9'),
    ('api_key\\":\\"Zq7pX2mK9\\"', 'Zq7pX2mK9'),
    ('SECRET_KEY_BASE=Zq7pX2mK9', 'Zq7pX2mK9'),
    ('API_KEY_V2=Zq7pX2mK9', 'Zq7pX2mK9'),
    ('ANTHROPIC_KEY=Zq7pX2mK9', 'Zq7pX2mK9'),
    ('ACCESS_KEY=Zq7pX2mK9', 'Zq7pX2mK9'),
    ('ENCRYPTION_KEY=Zq7pX2mK9', 'Zq7pX2mK9'),
    ('DB_PW=Zq7pX2mK9', 'Zq7pX2mK9'),
    ('비밀번호: Zq7pX2mK9', 'Zq7pX2mK9'),
    ('토큰: Zq7pX2mK9', 'Zq7pX2mK9'),
    ('-u user:Zq7pX2mK9', 'Zq7pX2mK9'),
    ('curl -u admin:Zq7pX2mK9 https://x', 'Zq7pX2mK9'),
    ('-----BEG' 'IN PRIVATE KEY-----\\nMIIEZq7pX2mK9\\n-----END PRIVATE KEY-----', 'Zq7pX2mK9'),
    ('xsk-' 'ant-api03-Zq7pX2mK9abcdef', 'Zq7pX2mK9'),
    ('_sk-' 'ant-api03-Zq7pX2mK9abcdef', 'Zq7pX2mK9'),
    ('0sk-' 'ant-api03-Zq7pX2mK9abcdef', 'Zq7pX2mK9'),
    ('한글sk-' 'ant-api03-Zq7pX2mK9abcdef', 'Zq7pX2mK9'),
    ('KEY_sk-' 'ant-api03-Zq7pX2mK9abcdef', 'Zq7pX2mK9'),
    ('{"result":"bad key:\\nsk-' 'ant-api03-Zq7pX2mK9abcDEF"}', 'Zq7pX2mK9'),
    ('"message":"\\tsk-' 'ant-api03-Zq7pX2mK9abcDEF"', 'Zq7pX2mK9'),
    ('sk-' 'ant-api03-Zq7p.X2mK9secretpart', 'secretpart'),
    ('sk-' 'ant-api03-Zq7p/X2mK9secretpart+abc=', 'secretpart'),
    ('gh' 'p_Zq7pX2mK9abcdefghijklmnopqrstuvwx', 'Zq7pX2mK9'),
    ('KEYgh' 'p_Zq7pX2mK9abcdefghijklmnopqrstuvwx', 'Zq7pX2mK9'),
    ('실패 sk-' 'ant-api03-abcDEF_123-xyz', 'abcDEF_123'),
    ('ANTHROPIC_API_KEY=hunter2value', 'hunter2value'),
    ('Authorization: Bearer abcdef123456', 'abcdef123456'),
    ('x-api-key: abcdef12', 'abcdef12'),
    ("export OPENAI_API_KEY='zzzsecret'", 'zzzsecret'),
    ('{"api_key": "abcdSECRET1234"}', 'SECRET'),
    ('{"apiKey":"abcdSECRET1234"}', 'SECRET'),
    ('"x-api-key": "abcdSECRET1234"', 'SECRET'),
    ("headers: {'x-api-key': 'abcdSECRET1234'}", 'SECRET'),
    ('"ANTHROPIC_API_KEY": "abcdSECRET1234"', 'SECRET'),
    ('ANTHROPIC_AUTH_TOKEN=abcdSECRET1234', 'SECRET'),
    ('CLAUDE_CODE_OAUTH_TOKEN=abcdSECRET1234', 'SECRET'),
    ('apikey=abcdSECRET1234', 'SECRET'),
    ('api-key: abcdSECRET1234', 'SECRET'),
    ('--api-key abcdSECRET1234', 'SECRET'),
    ("Authorization: 'abcdSECRET1234'", 'SECRET'),
    ('Authorization: Bearer abcdSECRET1234', 'SECRET'),
    ('Authorization: Basic dXNl' 'cjpTRUNSRVQ=', 'dXNl' 'cjpTRUNSRVQ'),
    ('HTTPS_PROXY=http://user:abcdSECRET1234@proxy.corp:8080', 'SECRET'),
    ('http://user:abcdSECRET1234@proxy:8080', 'SECRET'),
    ('ANTHROPIC_API_KEY = "my SECRET value SECRETTAIL"', 'SECRET'),
    ('ANTHROPIC_API_KEY=\\\nabcdSECRET1234', 'SECRET'),
    ('x-api-key abcdSECRET1234', 'SECRET'),
    ('sk-' 'ant-api03-AbCdEf_123-xyzSECRET', 'SECRET'),
    ('password=abcdSECRET1234', 'SECRET'),
    ('X-Api-Key:abcdSECRET1234', 'SECRET'),
    ('Proxy-Authorization: Basic dXNl' 'cjpTRUNSRVQ=', 'dXNl' 'cjpTRUNSRVQ'),
    ('ANTHROPIC_API_KEY=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('"api_key": "s3cr3tVALUE9xyzQ"', 's3cr3tVALUE9xyzQ'),
    ("'apiKey': 's3cr3tVALUE9xyzQ'", 's3cr3tVALUE9xyzQ'),
    ('apiKey=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('api key: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('ANTHROPIC_AUTH_TOKEN=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('CLAUDE_CODE_OAUTH_TOKEN=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('--api-key s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('--api-key=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('--apiKey s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('--token s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ("-H 'x-api-key: s3cr3tVALUE9xyzQ'", 's3cr3tVALUE9xyzQ'),
    ('x-api-key: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('X-Api-Key:s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('"x-api-key":"s3cr3tVALUE9xyzQ"', 's3cr3tVALUE9xyzQ'),
    ('Authorization: Bearer s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ("Authorization: 's3cr3tVALUE9xyzQ'", 's3cr3tVALUE9xyzQ'),
    ('authorization: Basic dXNl' 'cjpwYXNz', 'dXNl' 'cjpwYXNz'),
    ('Authorization: Basic dXNl' 'cjpw', 'dXNl' 'cjpw'),
    ('Proxy-Authorization: Basic dXNl' 'cjpwYXNz', 'dXNl' 'cjpwYXNz'),
    ('http://user:s3cr3tVALUE9xyzQ@proxy:8080', 's3cr3tVALUE9xyzQ'),
    ('https://user:p%40ss@proxy.corp:3128/', 'p%40ss'),
    ('http://user:pass@proxy', 'user:pass'),
    ('HTTPS_PROXY=http://kim:s3cr3tVALUE9xyzQ@10.0.0.1:8080', 's3cr3tVALUE9xyzQ'),
    ('http://:pw12345@proxy', 'pw12345'),
    ('http://domain%5Cuser:pw@proxy', 'user:pw'),
    ('http://DOMAIN\\user:pw12345@proxy:8080', 'pw12345'),
    ('socks5h://u:p@h', 'u:p'),
    ('password=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('password=hunter2', 'hunter2'),
    ('password: abc', 'abc'),
    ('passwd=abcd1234', 'abcd1234'),
    ("PASSWORD: 'my pass word'", 'my pass word'),
    ('pwd=Hunter2Hunter2', 'Hunter2Hunter2'),
    ('secret=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('client_secret: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('AWS_SECRET_ACCESS_KEY=s3cr3tVALUE9xyzQ/+abc', 's3cr3tVALUE9xyzQ'),
    ('aws_access_key_id=AK' 'IAIOSFODNN7EXAMPLE', 'IOSFODNN7EXAMPLE'),
    ('token=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('access_token=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('refresh_token: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('session_token=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('GITHUB_TOKEN=gh' 'p_abcdefghij0123456789', 'abcdefghij0123456789'),
    ('gh' 'p_abcdefghij0123456789abcdefghij012345', 'abcdefghij0123456789'),
    ('cookie: sessionKey=sk-' 'ant-sid01-abcDEF', 'abcDEF'),
    ('sk-' 'ant-api03-abc_DEF-123', 'abc_DEF-123'),
    ('key=sk-' 'ant-oat01-xyz', 'oat01'),
    ('Bearer sk-abc', 'sk-abc'),
    ('Bearer s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('bearer ey' 'JhbGciOiJIUzI1NiJ9.ey' 'JzdWIiOiIxIn0.abc', 'ey' 'JzdWIiOiIxIn0'),
    ('ey' 'JhbGciOiJIUzI1NiJ9.ey' 'JzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c', 'SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c'),
    ('ANTHROPIC_API_KEY = s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('ANTHROPIC_API_KEY:s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('export ANTHROPIC_API_KEY=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('ANTHROPIC_API_KEY="s3cr3tVALUE9xyzQ"', 's3cr3tVALUE9xyzQ'),
    ("ANTHROPIC_API_KEY='s3cr3tVALUE9xyzQ with space'", 's3cr3tVALUE9xyzQ'),
    ('ANTHROPIC_API_KEY=short1', 'short1'),
    ('ANTHROPIC_API_KEY=abcdefgh', 'abcdefgh'),
    ('ANTHROPIC_API_KEY=abcdefghijk', 'abcdefghijk'),
    ('ANTHROPIC_API_KEY=!@#$%^&*()secret', 'secret'),
    ('ANTHROPIC_API_KEY=p@ssw0rd!longer', 'longer'),
    ('api_key=가나다라마바사아자차카', '가나다라마바사아자차카'),
    ('api_key => s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('api_key -> s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('api_key is s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('apiKey(s3cr3tVALUE9xyzQ)', 's3cr3tVALUE9xyzQ'),
    ('api_key\ts3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('api_key=\ns3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('api_key=\\\n  s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('api_key:\n  s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('{"apiKey":{"value":"s3cr3tVALUE9xyzQ"}}', 's3cr3tVALUE9xyzQ'),
    ('"api_key": ["s3cr3tVALUE9xyzQ"]', 's3cr3tVALUE9xyzQ'),
    ('api_key%3Ds3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('?api_key=s3cr3tVALUE9xyzQ&x=1', 's3cr3tVALUE9xyzQ'),
    ('?key=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('&sig=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('ANTHROPIC_API_KEY=<s3cr3tVALUE9xyzQ>', 's3cr3tVALUE9xyzQ'),
    ('ANTHROPIC_API_KEY=(s3cr3tVALUE9xyzQ)', 's3cr3tVALUE9xyzQ'),
    ('ANTHROPIC_API_KEY=[s3cr3tVALUE9xyzQ]', 's3cr3tVALUE9xyzQ'),
    ('ANTHROPIC_API_KEY=“s3cr3tVALUE9xyzQ”', 's3cr3tVALUE9xyzQ'),
    ("api_key=b's3cr3tVALUE9xyzQ'", 's3cr3tVALUE9xyzQ'),
    ('api_key: "s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('api_key: "line1s3cr3tVALUE9xyzQ\nrest"', 's3cr3tVALUE9xyzQ'),
    ('x-api-key s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('env: {ANTHROPIC_API_KEY: s3cr3tVALUE9xyzQ}', 's3cr3tVALUE9xyzQ'),
    ('primaryApiKey: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('oauthAccount.accessToken=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('accessToken: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('refreshToken: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('claudeAiOauth: {accessToken: s3cr3tVALUE9xyzQ}', 's3cr3tVALUE9xyzQ'),
    ('credentials: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('private_key: -----BEG' 'IN PRIVATE KEY-----MIIEvQ', 'MIIEvQ'),
    ('Set-Cookie: session=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('Cookie: sid=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('apikey s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API-KEY: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('ApiKey:s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('api.key=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('PASS=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('PGPASSWORD=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('DB_PASS=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('passphrase=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('auth=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('Authorization=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('Authorization: Digest username="u", response="s3cr3tVALUE9xyzQ"', 's3cr3tVALUE9xyzQ'),
    ('Authorization: AWS4-HMAC-SHA256 Credential=s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('Authorization: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('Authorization: Negotiate s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('X-Auth-Token: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('x-goog-api-key: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('anthropic-api-key: s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('api_key=${s3cr3tVALUE9xyzQ}', 's3cr3tVALUE9xyzQ'),
    ('token: 12345678', '12345678'),
    ('token=abc', 'abc'),
    ('sk-proj-abcdefghijklmnopqrstuvwx', 'abcdefghijklmnopqrstuvwx'),
    ('sk-abcdefghijklmnopqrstuvwxyz', 'abcdefghijkl'),
    ('x-api-key: short1', 'short1'),
    ('x-api-key: p@ss!word#1', 'word#1'),
    ('MY_API_KEY=${s3cr3tVALUE9xyzQ}', 's3cr3tVALUE9xyzQ'),
    ('OPENAI_API_KEY=가나다라마바사아자차카타파하', '가나다라마바'),
    ('Bearer sk-abcdefg', 'abcdefg'),
    ('API_KEY=[REDACTED-s3cr3tVALUE9xyzQ]', 's3cr3tVALUE9xyzQ'),
    ('X_API_KEY=@s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=:s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=#s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=$s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=%s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=!s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=&s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=*s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=~s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=^s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=\\s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    ('API_KEY=|s3cr3tVALUE9xyzQ', 's3cr3tVALUE9xyzQ'),
    # 베타 K (review8 k5/k6): 따옴표를 지키고 값만 가린다, 접두 이름은 숫자·경로 예외가 아니다, 짧은 값, 인자 목록, 프록시 자격, 키 모양
    ('{"api_key":"abcd1234","model":"x"}', 'abcd1234'),
    ('{"config":{"apiKey":"abcd1234","baseURL":"https://api.anthropic.com"},"status":400}', 'abcd1234'),
    ('{"Authorization":"Zq7pX2mK9","model":"x"}', 'Zq7pX2mK9'),
    ('DB_PASS=1234', '1234'), ('MYSQL_PASS=0000', '0000'), ('smtp_pass: 9999', '9999'), ('PROXY_AUTH=1234', '1234'), ('REDIS_AUTH=session', 'session'),
    ('DB_PWD=./secret1', 'secret1'), ('credentials=/s3cr3t', 's3cr3t'),
    ('KEY=sk-' 'ant-abcdefghijk', 'abcdefghijk'),
    ('https://user:Zq7pX2mK9@host?email=a@b.com', 'Zq7pX2mK9'),
    ('AWS_BEARER_TOKEN_BEDROCK=Zq7pX2mK9abcdef', 'Zq7pX2mK9'),
    ('curl --proxy-user kim:Passw0rd https://x', 'Passw0rd'), ('curl -U kim:Passw0rd https://x', 'Passw0rd'),
    ('private key: MIIEvQZq7pX2mK9abcdefghijk', 'Zq7pX2mK9'),
    ("['--api-key', 'Zq7pX2mK9']", 'Zq7pX2mK9'), ('["claude","--token","Zq7pX2mK9"]', 'Zq7pX2mK9'),
    # 베타 L (review9 l5): 따옴표 값 안의 이스케이프한 따옴표
    ('password: "a b \\"S3cr3t\\" c"', 'S3cr3t'), ("password: 'it\\'s S3cr3t'", 'S3cr3t'), ('{"password":"a\\"S3cr3t\\"b c","model":"x"}', 'S3cr3t'),
]

LEAVE = [
    'PWD=/home/user/bizopt2026/solbox',
    'pwd: /srv/solbox',
    'OLDPWD=/home/user',
    'bypassPermissions mode',
    'permission-mode: bypassPermissions',
    'compass: north',
    'OAuth: https://claude.ai/oauth/authorize?code=true',
    'auth: 401',
    'auth: OAuth',
    'pass: 3 fail: 0',
    'max_token: 4096',
    'credentials: /home/kim/.claude/.credentials.json',
    'at getAccessToken(/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js:123:45)',
    'at getAccessToken(/usr/lib/cli.js:1:2)',
    "The token '--verbose' is not recognized",
    'Cookie: disabled',
    'cookie=none',
    '"token": "***"',
    'ssh://git@github.com/org/repo.git',
    'http://localhost:8080/path',
    'ANTHROPIC_API_KEY: not set',
    'ANTHROPIC_API_KEY is not set',
    'x-api-key: header is required',
    'Authorization: required',
    'Bearer authentication failed',
    'risk-' 'ant-colony',
    'MY_API_KEY_COUNT=3',
    'API_KEY: 설정되지 않았습니다',
    'Authorization: Bearer token expired',
    'API_KEYS: 3 configured',
    'task-management sk-learn-model-selection-pipeline failed',
    'http://example.com:8080/path@x',
    'Error: invalid api key: check your settings',
    'token: expired',
    'token limit exceeded: 200000 tokens',
    'max_tokens: 4096',
    'max_tokens=200000',
    'input_tokens: 123456789012',
    'output_tokens=15000',
    '"input_tokens": 1234, "output_tokens": 5678',
    'cache_read_input_tokens: 123456789',
    'password: incorrect',
    'secret: missing',
    'token: undefined',
    'api_key: null',
    'api_key: None',
    'API key: invalid_request_error',
    'api key: authentication_error',
    'api_key: <redacted>',
    'apiKeySource: environment',
    'apiKeySource: ANTHROPIC_API_KEY',
    'token=unexpected_end_of_input',
    "Unexpected token: '}' at position 12",
    'SyntaxError: Unexpected token } in JSON at position 1024',
    'Unexpected token: identifier123',
    'tokens: 150000/200000',
    'token count: 20000',
    'session_token_expired: true',
    'password_reset_required: yes',
    'see https://docs.anthropic.com/en/api/errors#api-key',
    'at /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js:1234:56',
    'Error: connect ECONNREFUSED 127.0.0.1:8080',
    'https://api.anthropic.com/v1/messages: 500',
    'http://proxy.corp:8080 refused',
    'ftp://files.example.com:21/path@x',
    'request_id: req_011CSabcdefghij123',
    'requestId=req_011CSabcdefghij123',
    'model: claude-fable-5-2',
    'id: msg_01ABCDEFGHIJKLMNOP',
    'tokenizer: cl100k_base_v2',
    'tokenizer=sentencepiece_v15',
    'mailto://a:b@c',
    'git@github.com:org/repo.git',
    'user: kim@example.com',
    'time: 12:30:45@server',
    'auth_token_file: /home/user/.claude/token.json',
    'token_path=/home/user/.config/claude',
    'secret_name: projects/123/secrets/abc',
    'passwd file: /etc/passwd',
    'Bearer realm="example", error="invalid_token"',
    'WWW-Authenticate: Bearer error="invalid_token"',
    'authorization: missing_header_value',
    'Authorization header is malformed',
    'id_token_hint: none',
    'access_token_expires_in: 3600',
    'token_type: bearer',
    '"token_type":"Bearer"',
    '토큰: 만료되었습니다',
    '비밀번호: 틀렸습니다',
    'exit code 1: api_key validation failed',
    "KeyError: 'api_key'",
    'TypeError: token.split is not a function',
    'tokens=[1,2,3]',
    'x-api-key header missing',
    'error --api-key requires a value',
    '--api-key <key>   API key to use',
    'Usage: claude [--token <token>]',
    'password: ********',
    'duration_ms: 123456, num_turns: 12, session_id: 0d1e2f3a-4b5c-6d7e-8f90-a1b2c3d4e5f6',
    'api_error_status: 401',
    'stop_reason: max_tokens',
    'secretary: Kim1234567',
    'tokenization=whitespace_v2x',
    'passwordless: enabled12',
    'C:/Users/kim/api_key.txt: not found',
    'file api_key.json:12:5 error',
    'sk-learn-pipeline-estimator-module',
    'risk-assessment-and-mitigation-plan-2026',
    'task-management-system-version-2024',
    'auth_token_file: /path',
    # 베타 K (review8 k6): 영문자가 붙은 `…sk-ant-` 는 진짜 키 모양일 때만, 개수·설명·알고리즘·모델 이름은 비밀이 아니다
    'task-' 'ant-1', 'disk-' 'ant-2', 'desk-' 'ant-12345678901234567890',
    '입력 토큰: 210000', '토큰: 210000개 (한도 200000개)', '비밀번호: 8자 이상이어야 합니다',
    'docker run -u 1000:1000 img', 'sort -u a:bcd', 'see http://a.com:80,kim@kt.com', 'vertex://claude-sonnet-4-5@20250929',
    'signing_key=RS256', 'ENCRYPTION_KEY=aes-256-gcm',
    '{"api_key":"***","model":"x"}', 'PWD=/tmp', 'credentials: /home/kim/.claude/.credentials.json',
]

# ---- 오류 분류 회귀표: (글, 분류) — 분류 앞의 `!` 는 그 분류가 아니어야 한다 ----
CLASSIFY = [
    ('ANTHROPIC_API_KEY is not set', 'auth'),
    ('Request failed with status code 429', 'limit'),
    ('API Error (401) invalid', 'auth'),
    ('{"status":401,"error":"x"}', 'auth'),
    ('API Error (429)', 'limit'),
    ('Credit balance is too low', 'limit'),
    ('407 Proxy Authentication Required', 'network'),
    ('Error: unauthorized tool use', 'unknown'),
    ('tunneling socket could not be established, statusCode=407', 'network'),
    ('HTTP 407', 'network'),
    ('Error: 407', 'network'),
    ('line 407: proxy_pass', 'unknown'),
    ('See page 407 proxy settings', 'unknown'),
    ('TAVILY_API_KEY is not set', 'unknown'),
    ('CLAUDE_CODE_OAUTH_TOKEN is missing', 'auth'),
    ('statusCode=407', 'network'),
    ('HTTP/1.1 407', 'network'),
    ('status code 407', 'network'),
    ('status: 407', 'network'),
    ('status_code=407', 'network'),
    ('statusCode: 407', 'network'),
    ('"status":407', 'network'),
    ('"status": 407', 'network'),
    ('{"statusCode":407}', 'network'),
    ('API Error: 407', 'network'),
    ('API Error (407)', 'network'),
    ('response code 407', 'network'),
    ('code=407', 'network'),
    ('ERR_TUNNEL_CONNECTION_FAILED 407', 'network'),
    ('Request failed with status code 407', 'network'),
    ('got 407 from proxy', 'network'),
    ('407 Proxy Auth', 'network'),
    ('Proxy returned 407', 'network'),
    ('HTTP/2 407', 'network'),
    ('http 407 unauthorized', 'network'),
    ('Error: 407 Unauthorized', 'network'),
    ('line 407: proxy_pass http://x', '!network'),
    ('nginx.conf:407 proxy', '!network'),
    ('took 407 ms', '!network'),
    ('407 proxy entries loaded', '!network'),
    ('file 407.md', '!network'),
    ('error at line 407', '!network'),
    ('Error: 4070 items', '!network'),
    ('error: 407 files could not be read', '!network'),
    ('status 407ms', '!network'),
    ('ANTHROPIC_API_KEY not set', 'auth'),
    ('CLAUDE_API_KEY is missing', 'auth'),
    ('ANTHROPIC_AUTH_TOKEN is not set', 'auth'),
    ('CLAUDE_CODE_OAUTH_TOKEN is invalid', 'auth'),
    ('ANTHROPIC_API_KEY: not set', 'auth'),
    ('ANTHROPIC_API_KEY environment variable is not set', 'auth'),
    ('ANTHROPIC_API_KEY is empty', 'auth'),
    ('Missing ANTHROPIC_API_KEY', 'auth'),
    ('ANTHROPIC_API_KEY required', 'auth'),
    ('anthropic api key is missing', 'auth'),
    ('API key is missing', 'auth'),
    ('Invalid API key · Please run /login', 'auth'),
    ('ANTHROPIC_API_KEY\tis not set', 'auth'),
    ('ANTHROPIC_API_KEY  is not set', 'auth'),
    ("env ANTHROPIC_API_KEY isn't set", 'auth'),
    ('ANTHROPIC_BEDROCK_API_KEY is not set', 'auth'),
    ('OPENAI_API_KEY is not set', '!auth'),
    ('TAVILY_API_KEY is missing', '!auth'),
    ('GOOGLE_API_KEY is invalid', '!auth'),
    ('MY_ANTHROPIC_API_KEY is not set', '!auth'),
    ('NOTANTHROPIC_API_KEY is not set', '!auth'),
    ('X_CLAUDE_API_KEY is missing', '!auth'),
    ('OPENAI api key is missing', '!auth'),
    ('Tavily API key is invalid', '!auth'),
    ('the weather api key is not set', '!auth'),
    ('tool error: invalid token in JSON', '!auth'),
    ('invalid token at position 3', '!auth'),
    ('invalid credentials for MCP server github', '!auth'),
    # 베타 J (review7 c1): 이름 없는 API key 글은 Claude 로그인, 다른 업체·MCP 서버 이름이 든 글은 아니다
    ('The API key is invalid', 'auth'),
    ('This API key is invalid.', 'auth'),
    ('Provided API key is invalid', 'auth'),
    ('API key expired', 'auth'),
    ('API key is not valid', 'auth'),
    ('ANTHROPIC_API_KEY not found', 'auth'),
    ('Could not find ANTHROPIC_API_KEY', 'auth'),
    ('the provided api key is not set', 'auth'),
    ('ANTHROPIC_API_KEY was not found', 'auth'),
    ('The OpenAI API key is invalid', '!auth'),
    ('The API key is invalid for MCP server github', '!auth'),
    ('exit code: 407', '!network'),
    ('Process exited with code 407', '!network'),
    ('error code 407', 'network'),
    ('407 Proxy Authentication Required', 'network'),
    ('proxy authentication required', 'network'),
    ('mcp-proxy auth ok; rate limit exceeded', 'limit'),
    ('proxy auth plugin loaded; Invalid API key', 'auth'),
    ('Using proxy authentication. Error: usage limit reached', 'limit'),
    # 베타 K (review8 k8): 같은 줄에 다른 업체 이름이 있으면 Claude 로그인이 아니다, 환경 변수 이름은 줄을 넘어 짝짓지 않는다
    ('Tavily: invalid API key', '!auth'),
    ('Brave Search error: the API key is missing', '!auth'),
    ('Invalid API key for Tavily', '!auth'),
    ('No API key provided for OpenAI', '!auth'),
    ('ANTHROPIC_API_KEY\n\n\nmissing dependency foo', 'unknown'),
    ('Error: Invalid API key', 'auth'),
    ('2026-11-21T10:20:30 Error: Invalid API key', 'auth'),
    ('claude: Invalid API key', 'auth'),
    ('Invalid API key for Claude', 'auth'),
    # 베타 L (review9 l2): 알려진 다른 업체 표지가 없으면 Claude 로그인
    ('node: Invalid API key', 'auth'), ('anthropic.AuthenticationError: invalid api key', 'auth'), ('Error [AuthenticationError]: Invalid API key', 'auth'),
    ('Unhandled rejection: Invalid API key', 'auth'), ('Execution error: Invalid API key', 'auth'), ('C:\\Users\\kim\\AppData\\Roaming\\npm\\claude.cmd: Invalid API key', 'auth'),
    ('Process exited: API key is missing', 'auth'), ('Invalid API key. Check for typos', 'auth'), ('Invalid API key for this organization', 'auth'),
    ('Invalid API key for api.anthropic.com', 'auth'), ('Request failed: Invalid API key for this organization', 'auth'),
    ('OPENAI_API_KEY is not set\nInvalid API key for MCP server foo', '!auth'), ('mcp server x: Invalid API key', '!auth'), ('Invalid API key for Foo', '!auth'),
    ('Serper: Invalid API key', '!auth'), ('Perplexity API error: invalid API key', '!auth'),
]


# ================= 사본 경로 =================

@pytest.mark.parametrize("text", _flat(DECK_REMOVE))
def test_deck_table_paths_are_removed(text):
    assert has_internal_mark(text), text


@pytest.mark.parametrize("text", _flat(DECK_KEEP))
def test_deck_table_ordinary_text_is_kept(text):
    assert not has_internal_mark(text), text


_SWEEP_TAILS = ["은", "는", "이", "가", "을", "를", "의", "에", "에서", "로", "으로", "와", "과", "도", "까지", "부터", "처럼", "보다", "이며", "이고", "입니다", "임", "에도", "에는",
                "에서는", "조차", "마저", "라고", "이라는", "대로", "등을", "등의", "인데", "일때", "일자", "번", "호", ".", ",", ")", "]", "」", "'", '"', ":", ";", "!", "?", "…", "·", "/",
                "~", "_", "|", ">", "*", "#", ".md", ".md.", ".md,", ".md)", ".md의", ".MD", ".json", ".txt를", "/card.md", "/card.md에서", "/files/1.pdf", ".md#L3", ".md?x=1",
                ".md:12", ".bak", ".md.bak", ".tar.gz", ".markdown", ".mdx", ".html", "/card", "/files", "/카드", "md", "Draft", "the", "a", "_v2", "main", "stage"]
_SWEEP_BASES = ["solutions/3", "customers/12", "knowledge/solutions/3", "./solutions/3", "근거: solutions/3", "(solutions/3", "opportunities/2", "research/2026"]


@pytest.mark.parametrize("base", _SWEEP_BASES)
def test_deck_sweep_real_path_with_particle_or_punctuation_tail(base):
    leaks = [t for t in _SWEEP_TAILS if not has_internal_mark(base + t)]
    assert not leaks, (base, leaks)


@pytest.mark.parametrize("tail", [".md", ".md의", "/card.md", ".markdown", ".json", ".md,", ".bak", "/files/1.pdf", "개.md", "개/card.md"])
def test_deck_sweep_general_folder_with_file_tail(tail):
    assert has_internal_mark("data/solutions/3" + tail)


_INVISIBLE_CHARS = ["\u2061", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069", "\ufe0f", "\u034f", "\u180e", "\u061c",
                    "\u200b", "\u200d", "\u2060", "\ufeff", "\u00ad", "\u115f", "\u3164", "\ufe00", "\U000e0020"]


@pytest.mark.parametrize("ch", _INVISIBLE_CHARS, ids=lambda c: f"U+{ord(c):04X}")
def test_deck_invisible_characters_do_not_hide_a_path(ch):
    for text in ("solutions" + ch + "/3", "sol" + ch + "utions/3", "solutions/" + ch + "3", "solutions/3." + ch + "md"):
        assert has_internal_mark(text), ascii(text)


def test_deck_every_format_character_is_stripped():
    """유니코드 분류 Cf(서식 글자) 와 변형 선택자는 모두 글을 맞출 때 지워진다."""
    left = []
    for cp in range(sys.maxunicode + 1):
        ch = chr(cp)
        if (unicodedata.category(ch) == "Cf" or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF) and _normalize("a" + ch + "b") != "ab":
            left.append(hex(cp))
    assert not left, left[:20]


_LONG = "가나다라/" * 70


@pytest.mark.parametrize("text", [
    "/" + "a/" * 200 + "solutions/3", "C:/" + "dir/" * 100 + "solutions/3", "C:/knowledge/" + "dir/" * 100 + "solutions/3", "~/" + _LONG + "solutions/3을",
    "출처:/" + "가" * 400 + "/solutions/3", "data/solutions/3/" + "가" * 400 + ".md", "solutions/3개/" + "가" * 400 + ".md", "가" * 400 + "solutions/3",
    "x" * 400 + "\u3000solutions/3", "solutions/3" + "개" * 400,
])
def test_deck_long_lines_keep_the_head_and_tail_rules(text):
    """베타 H 의 300자 한도 때문에 긴 머리말·꼬리 뒤의 경로가 남던 것: 이제 낱말 전체를 본다."""
    assert has_internal_mark(text)


@pytest.mark.parametrize("text", [
    "https://a.com/" + "p/" * 200 + "solutions/3.html", "https://a.com/" + "p/" * 200 + "solutions/3", "근거=" + "가" * 400 + "/solutions/3",
    "a" * 400 + "/solutions/3", "www.acme.co.kr/" + "p/" * 200 + "customers/12",
])
def test_deck_long_urls_and_general_prefixes_are_still_kept(text):
    assert not has_internal_mark(text)


def test_deck_normalize_is_linear_on_percent_runs():
    """`(?:%25)+(?=2f|5c)` 는 `%25` 가 아주 길게 이어지면 제곱 시간이었다."""
    for text in ("%25" * 340_000 + "x", "%25" * 340_000 + "2f", "%25" * 170_000 + " " + "%25" * 170_000 + "x", "%" + "25" * 400_000 + "x"):
        t0 = time.perf_counter()
        has_internal_mark(text)
        assert time.perf_counter() - t0 < _LINEAR_LIMIT_S
    assert _normalize("solutions%25252F3") == "solutions/3" and _normalize("solutions%2525252f3") == "solutions/3" and _normalize("할인 %25 적용") == "할인 %25 적용"


_LINEAR_LIMIT_S = 20.0
# (1MB 줄의 선형 시험은 베타 K 부터 `tests/_linear.py` 의 자식 프로세스 시험이다: 제곱 시간이어도 시험이 멈추지 않고 30초 안에 실패한다)


# ================= 작성자 말투 =================

@pytest.mark.parametrize("line", _flat(AUTHOR_MOVE))
def test_author_table_notes_move(line):
    assert is_author_instruction(line), line


@pytest.mark.parametrize("line", _flat(AUTHOR_STAY))
def test_author_table_customer_lines_stay(line):
    assert not is_author_instruction(line), line


@pytest.mark.parametrize("line", _flat(AUTHOR_WORD_RULE_RETIRED))
def test_author_word_rule_retired_lines_stay_on_the_slide(line):
    # 사용자 결정 2026-11-22: 내부 단어 규칙(베타 L~O)을 뺐다. 이 줄은 이제 어떤 규칙에도 걸리지 않고 슬라이드에 남는다 (고객용 PPT 는 사람이 읽고 보낸다).
    assert not is_author_instruction(line), line


# ================= 비밀 가리기 =================

@pytest.mark.parametrize("text,gone", MASK)
def test_redact_table_masks_secrets(text, gone):
    out = redact(text)
    assert out != text and "***" in out, out
    assert gone and gone not in out, out  # 모든 줄에 사라져야 하는 값이 있다 (`***` 만 보는 줄은 없다)
    assert redact(out) == out, (text, out)  # 두 번 가려도 같다


@pytest.mark.parametrize("text", LEAVE)
def test_redact_leave_rows_are_idempotent(text):
    assert redact(redact(text)) == redact(text) == text


@pytest.mark.parametrize("text", LEAVE)
def test_redact_table_leaves_diagnostics(text):
    assert redact(text) == text


def test_redact_secret_names_are_exact_not_prefixes():
    for text in ("tokens: 150000/200000", "tokenizer: cl100k_base_v2", "token_path=/home/u/.config/claude", "secretary: Kim1234567", "passwordless: enabled12",
                 "api_keys: 3 configured", "token_type: bearer", "max_tokens: 4096", "auth_token_file: /home/user/.claude/token.json"):
        assert redact(text) == text, text
    for text in ("token=abc", "password: abc", "x-api-key: short1", "ANTHROPIC_API_KEY=abcdefghijk", "api_key => abcdef", "apiKey(abcdef)", "secret=xyz"):
        assert "***" in redact(text), text


# ================= 오류 분류 =================

@pytest.mark.parametrize("text,kind", CLASSIFY)
def test_classify_table(text, kind):
    got = classify_error(text)
    if kind.startswith("!"):
        assert got != kind[1:], (text, got)
    else:
        assert got == kind, (text, got)


@pytest.mark.parametrize("text,kind", [
    ('{"statusCode":407}', "network"), ('"status": 407', "network"), ("API Error (407)", "network"), ("error: 407 files could not be read", "!network"),
    ("ANTHROPIC_API_KEY: not set", "auth"), ("Missing ANTHROPIC_API_KEY", "auth"), ("ANTHROPIC_API_KEY environment variable is not set", "auth"),
    ("OPENAI api key is missing", "!auth"), ("Tavily API key is invalid", "!auth"), ("the weather api key is not set", "!auth"), ("invalid credentials for MCP server github", "!auth"),
    ("tool error: invalid token in JSON", "!auth"), ("invalid token at position 3", "!auth"), ("API key invalid", "auth"), ("Invalid credentials", "auth"),
])
def test_i7_classification(text, kind):
    got = classify_error(text)
    assert (got != kind[1:]) if kind.startswith("!") else (got == kind), (text, got)


# ================= I1: 진짜 SQLite 잠금 아래의 여러 파일 올리기 =================

def _client(app, db):
    from tests.conftest import make_user

    make_user(db, "kim", "user")
    c = TestClient(app, raise_server_exceptions=False)
    assert c.post("/login", data={"username": "kim", "password": "pw1234"}, follow_redirects=False).status_code == 303
    return c


def _short_busy_timeout(app, ms=300):
    """시험이 5초씩 기다리지 않게 새 연결의 잠금 대기를 줄인다."""
    from sqlalchemy import event

    engine = app.state.engine
    engine.dispose()

    @event.listens_for(engine, "connect")
    def _short(dbapi_conn, _record):
        dbapi_conn.execute(f"PRAGMA busy_timeout={ms}")


class _Locker:
    """다른 연결이 `BEGIN IMMEDIATE` 로 쓰기 잠금을 쥔다 (주입한 예외가 아니라 진짜 잠금)."""

    def __init__(self, app):
        self.path = str(app.state.engine.url.database)
        self.conn = None

    def lock(self):
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.execute("BEGIN IMMEDIATE")

    def release(self):
        if self.conn is not None:
            self.conn.execute("ROLLBACK")
            self.conn.close()
            self.conn = None


def _solution(db):
    from app.models import Solution

    s = Solution(name="A")
    db.add(s)
    db.commit()
    return s.id


def _text(html: str) -> str:
    import re

    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _files(*names):
    return [("files", (n, n.encode() * 3, "text/plain")) for n in names]


def _saved_names(db):
    from app.models import SolutionFile

    db.expire_all()
    return sorted(f.original_name for f in db.query(SolutionFile))


@pytest.fixture
def locked_upload(app, db):
    """(client, 솔루션 번호, 잠금 도구). 끝나면 잠금을 푼다."""
    _short_busy_timeout(app)
    client = _client(app, db)
    sid = _solution(db)
    locker = _Locker(app)
    try:
        yield client, sid, locker
    finally:
        locker.release()


def test_i1_real_lock_before_the_first_file_is_503_not_500(locked_upload, db):
    client, sid, locker = locked_upload
    locker.lock()
    r = client.post(f"/solutions/{sid}/files", files=_files("a.txt", "b.txt"), follow_redirects=False)
    locker.release()
    assert r.status_code == 503, r.status_code
    assert "잠시 후 다시 시도" in _text(r.text) and "저장하지 않았" in _text(r.text)
    assert _saved_names(db) == []


def _lock_after_first_save(monkeypatch, locker, n=1):
    from app.routers import files as F

    real = F._save_upload
    state = {"n": 0}

    def wrapped(*a, **k):
        out = real(*a, **k)
        if out == "saved":
            state["n"] += 1
            if state["n"] == n:
                locker.lock()
        return out

    monkeypatch.setattr(F, "_save_upload", wrapped)


def test_i1_real_lock_midway_returns_the_page_naming_unsaved_files(locked_upload, db, monkeypatch):
    client, sid, locker = locked_upload
    _lock_after_first_save(monkeypatch, locker)
    r = client.post(f"/solutions/{sid}/files", files=_files("a.txt", "b.txt", "c.txt"), follow_redirects=False)
    locker.release()
    assert r.status_code == 200, r.status_code
    text = _text(r.text)
    assert "저장하지 못한 파일: b.txt, c.txt" in text and "앞의 파일은 저장했습니다" in text
    assert _saved_names(db) == ["a.txt"]


def test_i1_duplicate_skipped_file_does_not_count_as_saved(locked_upload, db, monkeypatch):
    """첫 파일이 이미 있는 자료(건너뜀)이고 둘째가 잠금에 막히면 저장된 것이 없다: "앞의 파일은 저장했습니다" 가 아니라 503."""
    client, sid, locker = locked_upload
    assert client.post(f"/solutions/{sid}/files", files=_files("a.txt"), follow_redirects=False).status_code == 303
    from app.routers import files as F

    real = F._save_upload
    seen = {"dup_done": False}

    def wrapped(*a, **k):
        if seen["dup_done"] and not locker.conn:
            locker.lock()
        out = real(*a, **k)
        seen["dup_done"] = out == "duplicate"
        return out

    monkeypatch.setattr(F, "_save_upload", wrapped)
    r = client.post(f"/solutions/{sid}/files", files=_files("a.txt", "b.txt"), follow_redirects=False)
    locker.release()
    assert r.status_code == 503, r.status_code
    assert "앞의 파일은 저장했습니다" not in _text(r.text)
    assert _saved_names(db) == ["a.txt"]


def test_i1_duplicate_then_real_save_then_lock_names_only_real_unsaved(locked_upload, db, monkeypatch):
    client, sid, locker = locked_upload
    client.post(f"/solutions/{sid}/files", files=_files("a.txt"), follow_redirects=False)
    _lock_after_first_save(monkeypatch, locker)
    r = client.post(f"/solutions/{sid}/files", files=_files("a.txt", "b.txt", "c.txt"), follow_redirects=False)
    locker.release()
    text = _text(r.text)
    assert r.status_code == 200 and "저장하지 못한 파일: c.txt" in text and "이미 같은 내용의 자료가 있어 건너뛴 파일: a.txt" in text
    assert _saved_names(db) == ["a.txt", "b.txt"]


def test_i1_error_page_after_rollback_does_not_hit_a_detached_user(app, db):
    """라우터가 `db.rollback()` 한 뒤 오류(503)를 내도, 닫힌 세션의 expired 사용자 때문에 오류 화면이 500 이 되지 않는다."""
    from fastapi import Depends
    from sqlalchemy.orm import Session

    from app.db import get_db
    from app.guards import WriteBusy
    from app.models import User
    from app.security import require_user

    @app.get("/_i1_rollback_busy")
    def _route(user: User = Depends(require_user), db_: Session = Depends(get_db)):
        db_.rollback()
        raise WriteBusy()

    client = _client(app, db)
    r = client.get("/_i1_rollback_busy")
    assert r.status_code == 503 and "잠시 후 다시 시도" in _text(r.text)


# ================= I6: /admin/system 의 점검 명령 =================

GRANDCHILD = (
    "import os, subprocess, sys, time\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import os, sys, time; open(sys.argv[1], \"w\").write(str(os.getpid())); time.sleep(120)', sys.argv[1]])\n"
    "time.sleep(120)\n"
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 프로세스 그룹 시험")
def test_i6_timeout_kills_the_whole_process_tree_and_returns(tmp_path):
    from app import system

    pidfile = tmp_path / "grandchild.pid"
    script = tmp_path / "spawn.py"
    script.write_text(GRANDCHILD, encoding="utf-8")
    t0 = time.perf_counter()
    with pytest.raises(subprocess.TimeoutExpired):
        system.run_bounded([sys.executable, str(script), str(pidfile)], 4)  # 느린 기계에서도 손자가 pid 를 적을 시간
    assert time.perf_counter() - t0 < 20  # 손자가 파이프를 쥐고 있어도 끝없이 기다리지 않는다
    assert pidfile.exists(), "손자가 pid 를 적지 못했다 (시험이 아무것도 확인하지 못한다)"
    pid = int(pidfile.read_text())
    for _ in range(100):  # 손자는 죽어 있어야 한다
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail("손자 프로세스가 남았다")


def test_i6_windows_branch_uses_taskkill_for_the_tree(monkeypatch):
    from app.ai import runner

    calls = []

    class Proc:
        pid = 4321

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(runner.sys, "platform", "win32")
    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    runner._kill_tree(Proc())
    assert calls[0] == ["taskkill", "/F", "/T", "/PID", "4321"] and "kill" in calls


def test_i6_popen_options_per_platform(monkeypatch):
    from app import system

    seen = {}

    import io

    class FakePopen:
        returncode = 0

        def __init__(self, cmd, **kw):
            seen.update(kw)
            self.stdout = io.BufferedReader(io.BytesIO(b"1.0\n"))
            self.stderr = io.BufferedReader(io.BytesIO(b""))

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(system.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(system.sys, "platform", "linux")
    system.run_bounded(["x"], 1)
    assert seen.get("start_new_session") is True and "creationflags" not in seen and seen["stdin"] == subprocess.DEVNULL
    seen.clear()
    monkeypatch.setattr(system.sys, "platform", "win32")
    system.run_bounded(["x"], 1)
    assert "start_new_session" not in seen and "creationflags" in seen


def test_i6_version_line_is_truncated(settings, monkeypatch):
    from app import system

    monkeypatch.setattr(system, "run_bounded", lambda cmd, timeout: (0, "9" * 5000 + "\nsecond line"))
    name, problem, detail = system._check_claude(settings)
    assert problem is None and len(detail) == system.VERSION_LINE_MAX


def test_i6_cold_cache_loads_spawn_one_check(app, settings, monkeypatch):
    import threading

    from app.routers import admin as admin_router

    admin_router._CHECK_CACHE.clear()
    runs = []

    def slow_checks(load, skip_port=False, claude_timeout=30):
        runs.append(1)
        time.sleep(0.4)
        return [("Claude CLI", None, "1.0")]

    monkeypatch.setattr(admin_router, "run_checks", slow_checks)
    out = []
    threads = [threading.Thread(target=lambda: out.append(admin_router._check_rows(settings))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(runs) == 1 and len(out) == 4

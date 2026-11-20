"""시스템이 붙이는 확인 항목 문구. 문구는 데이터 안에 저장되므로 옛 문구도 계속 알아본다. 문구를 만드는 코드와 종류를 가르는 `check_kind` 가 같은 상수를 쓴다."""
from __future__ import annotations

NO_EVIDENCE = "근거 자료 없음"
SOURCE_UNVERIFIED = "출처 확인 불가"
NO_LINK = "출처 링크 없음"
UNOPENED = "본문을 열어 확인하지 못함"
UNKNOWN_SOLUTION = "후보에 없는 솔루션"

# 이 문구로 시작하면 근거 문제, 그 밖은 사용자가 정하거나 확인할 값이다.
EVIDENCE_PREFIXES = (NO_EVIDENCE, SOURCE_UNVERIFIED, NO_LINK, UNOPENED, UNKNOWN_SOLUTION)

NO_EVIDENCE_NOTE = f"{NO_EVIDENCE}: 내용 확인 필요"  # 출처 없는 장
UNKNOWN_SOLUTION_NOTE = f"{UNKNOWN_SOLUTION}이 언급됨"
NO_LINK_NOTE = f"{NO_LINK}, 확인 필요"  # 링크 없는 동향


def source_unverified(path: str) -> str:
    return f"{SOURCE_UNVERIFIED}: {path}"


def no_evidence(text: str = "") -> str:
    return f"{NO_EVIDENCE}: {text}" if text else NO_EVIDENCE


def is_unopened(text: str) -> bool:
    """"본문을 열어 확인하지 못함" 항목인가 (근거 문제 중 출처는 있으나 서버가 본문을 열어 보지 못한 경우: 노란 "본문 미확인"으로 보인다)."""
    return str(text or "").lstrip().startswith(UNOPENED)


def check_kind(text: str) -> str:
    """"evidence"(근거 문제) 또는 "decide"(확인할 점). 이름표는 ontology.CHECK_KINDS."""
    return "evidence" if str(text or "").lstrip().startswith(EVIDENCE_PREFIXES) else "decide"

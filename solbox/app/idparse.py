"""화면에서 오는 번호 문자열을 안전하게 정수로: ASCII 10진수이고 18자리 이하일 때만 (SQLite 정수 범위 안, "²" 같은 글자는 거른다)."""
from __future__ import annotations

MAX_ID_DIGITS = 18


def parse_id(raw) -> int | None:
    """번호로 읽을 수 있으면 정수, 아니면 None."""
    text = str(raw if raw is not None else "").strip()
    return int(text) if text.isascii() and text.isdecimal() and len(text) <= MAX_ID_DIGITS else None

"""영업기회 예상 금액: 한국식 단위("3.5억", "5천만", "350,000,000") 읽기와 표시. 저장은 원 단위 정수."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

AMOUNT_HELP = "금액은 3.5억, 5천만, 350000000 처럼 적어 주세요."

_UNITS = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, "천": 1_000, "": 1}
_PART = re.compile(r"(\d+(?:\.\d+)?)(억|천만|백만|만|천)?")
_EOK, _MAN = 100_000_000, 10_000


def parse_amount(text: str | None) -> int | None:
    """빈 글이면 None. 읽을 수 없거나 0 이하이거나 원 밑 자리가 남으면 ValueError(AMOUNT_HELP)."""
    raw = re.sub(r"[\s,]", "", text or "")
    if raw.endswith("원"):
        raw = raw[:-1]
    if not raw:
        return None
    total, pos = Decimal(0), 0
    for m in _PART.finditer(raw):
        if m.start() != pos:
            break
        total += Decimal(m.group(1)) * _UNITS[m.group(2) or ""]
        pos = m.end()
    if pos != len(raw) or total <= 0 or total != total.to_integral_value():
        raise ValueError(AMOUNT_HELP)
    return int(total)


def _eok_text(value: int) -> str:
    n = (Decimal(value) / _EOK).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{n.normalize():f}억"


def format_amount(value: int | None) -> str:
    """표시: 1억 이상 "3.5억"(소수 둘째 자리까지), 1만 이상 "5,000만", 그 밑 "9,000원". None 이면 빈 글."""
    if value is None:
        return ""
    if value >= _MAN:
        man = int((Decimal(value) / _MAN).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if value >= _EOK or man >= _MAN:
            return _eok_text(value)
        return f"{man:,}만"
    return f"{value:,}원"


def edit_text(value: int | None) -> str:
    """폼에 다시 채울 글. 다시 읽으면 같은 값이 되도록, 단위로 딱 떨어지지 않으면 원 단위 숫자로 쓴다."""
    if value is None:
        return ""
    if value >= _EOK and value % 1_000_000 == 0:
        return _eok_text(value)
    if _MAN <= value < _EOK and value % _MAN == 0:
        return f"{value // _MAN:,}만"
    return f"{value:,}"

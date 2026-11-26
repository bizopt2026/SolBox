"""팀 업무 보고의 기간 계산 (순수 함수). 일=하루, 주=월~일(ISO), 월=달력 월. 일간의 다음 기간은 다음 평일 (공휴일은 따지지 않는다)."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from app import ontology

_WEEKDAYS = "월화수목금토일"


def period_of(unit: str, day: date) -> tuple[date, date]:
    """기준 날짜가 든 기간 (시작, 끝)."""
    if unit == "day":
        return day, day
    if unit == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    if unit == "month":
        return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])
    raise ValueError(f"알 수 없는 보고 단위: {unit}")


def next_period(unit: str, start: date, end: date) -> tuple[date, date]:
    """같은 단위의 바로 다음 기간. 일간은 다음 평일 (금 -> 월)."""
    if unit == "day":
        nxt = end + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt, nxt
    return period_of(unit, end + timedelta(days=1))


def _end_text(start: date, end: date, *, same_month_short: bool) -> str:
    if end.year != start.year:
        return end.isoformat()
    if same_month_short and end.month == start.month:
        return f"{end.day:02d}"
    return f"{end.month:02d}-{end.day:02d}"


def label(unit: str, start: date, end: date) -> str:
    """화면 이름: "주간 2026-11-23 ~ 11-29", "일간 2026-11-24 (화)", "월간 2026-10"."""
    name = ontology.REPORT_UNITS.get(unit, unit)
    if unit == "month":
        return f"{name} {start:%Y-%m}"
    if unit == "day" or start == end:
        return f"{name} {start.isoformat()} ({_WEEKDAYS[start.weekday()]})"
    return f"{name} {start.isoformat()} ~ {_end_text(start, end, same_month_short=False)}"


def file_stem(unit: str, start: date, end: date) -> str:
    """파일 이름 (확장자 제외): "업무보고_주간_2026-11-23~29"."""
    name = ontology.REPORT_UNITS.get(unit, unit)
    if unit == "month":
        return f"업무보고_{name}_{start:%Y-%m}"
    if unit == "day" or start == end:
        return f"업무보고_{name}_{start.isoformat()}"
    return f"업무보고_{name}_{start.isoformat()}~{_end_text(start, end, same_month_short=True)}"

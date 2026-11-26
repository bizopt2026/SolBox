"""팀 업무 보고 기간 계산."""
from datetime import date

import pytest

from app import report_periods as rp


def test_week_period():
    assert rp.period_of("week", date(2026, 11, 25)) == (date(2026, 11, 23), date(2026, 11, 29))
    assert rp.period_of("week", date(2026, 11, 23)) == (date(2026, 11, 23), date(2026, 11, 29))
    assert rp.period_of("week", date(2026, 11, 29)) == (date(2026, 11, 23), date(2026, 11, 29))
    assert rp.next_period("week", date(2026, 11, 23), date(2026, 11, 29)) == (date(2026, 11, 30), date(2026, 12, 6))


def test_month_period():
    assert rp.period_of("month", date(2026, 11, 25)) == (date(2026, 11, 1), date(2026, 11, 30))
    assert rp.period_of("month", date(2028, 2, 3)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert rp.next_period("month", date(2026, 12, 1), date(2026, 12, 31)) == (date(2027, 1, 1), date(2027, 1, 31))


def test_day_period():
    assert rp.period_of("day", date(2026, 11, 25)) == (date(2026, 11, 25), date(2026, 11, 25))


def test_day_next_is_weekday():
    fri = date(2026, 11, 27)
    assert rp.next_period("day", fri, fri) == (date(2026, 11, 30), date(2026, 11, 30))
    thu = date(2026, 11, 26)
    assert rp.next_period("day", thu, thu) == (fri, fri)
    sat = date(2026, 11, 28)  # 주말 기준 날짜도 다음 평일로
    assert rp.next_period("day", sat, sat) == (date(2026, 11, 30), date(2026, 11, 30))


def test_unknown_unit_raises():
    with pytest.raises(ValueError):
        rp.period_of("year", date(2026, 1, 1))


def test_labels_and_file_stem():
    assert rp.label("week", date(2026, 11, 23), date(2026, 11, 29)) == "주간 2026-11-23 ~ 11-29"
    assert rp.label("day", date(2026, 11, 24), date(2026, 11, 24)) == "일간 2026-11-24 (화)"
    assert rp.label("month", date(2026, 10, 1), date(2026, 10, 31)) == "월간 2026-10"
    assert rp.file_stem("week", date(2026, 11, 23), date(2026, 11, 29)) == "업무보고_주간_2026-11-23~29"
    assert rp.file_stem("day", date(2026, 11, 24), date(2026, 11, 24)) == "업무보고_일간_2026-11-24"
    assert rp.file_stem("month", date(2026, 10, 1), date(2026, 10, 31)) == "업무보고_월간_2026-10"
    # 달·해가 걸치는 주
    assert rp.label("week", date(2026, 11, 30), date(2026, 12, 6)) == "주간 2026-11-30 ~ 12-06"
    assert rp.file_stem("week", date(2026, 11, 30), date(2026, 12, 6)) == "업무보고_주간_2026-11-30~12-06"
    assert rp.label("week", date(2026, 12, 28), date(2027, 1, 3)) == "주간 2026-12-28 ~ 2027-01-03"
    assert rp.file_stem("week", date(2026, 12, 28), date(2027, 1, 3)) == "업무보고_주간_2026-12-28~2027-01-03"


def test_prev_period():
    assert rp.prev_period("day", date(2026, 11, 30), date(2026, 11, 30)) == (date(2026, 11, 27), date(2026, 11, 27))  # 월 -> 금
    assert rp.prev_period("day", date(2026, 11, 26), date(2026, 11, 26)) == (date(2026, 11, 25), date(2026, 11, 25))
    assert rp.prev_period("week", date(2026, 11, 23), date(2026, 11, 29)) == (date(2026, 11, 16), date(2026, 11, 22))
    assert rp.prev_period("month", date(2026, 3, 1), date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert rp.prev_period("month", date(2028, 3, 1), date(2028, 3, 31)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert rp.prev_period("month", date(2027, 1, 1), date(2027, 1, 31)) == (date(2026, 12, 1), date(2026, 12, 31))
    # next_period 와 서로 거꾸로
    for unit, s, e in (("day", date(2026, 11, 27), date(2026, 11, 27)), ("week", date(2026, 11, 23), date(2026, 11, 29)), ("month", date(2026, 2, 1), date(2026, 2, 28))):
        assert rp.prev_period(unit, *rp.next_period(unit, s, e)) == (s, e)

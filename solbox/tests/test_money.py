"""영업기회 예상 금액: 한국식 단위 읽기와 표시 (app/money.py)."""
import pytest

from app import money


@pytest.mark.parametrize("text, value", [
    ("3.5억", 350_000_000),
    ("3억 5천만", 350_000_000),
    ("3억5천만원", 350_000_000),
    ("5천만", 50_000_000),
    ("5,000만 원", 50_000_000),
    ("350,000,000", 350_000_000),
    ("12억", 1_200_000_000),
    ("9000", 9000),
    ("9,000원", 9000),
    ("1.23억", 123_000_000),
])
def test_parse_amount_reads_korean_units(text, value):
    assert money.parse_amount(text) == value


@pytest.mark.parametrize("text", ["", "   ", None])
def test_parse_amount_blank_is_none(text):
    assert money.parse_amount(text) is None


@pytest.mark.parametrize("text", ["0", "-3억", "삼억", "3.5.1억", "억", "1.5", "3억 abc", "0억"])
def test_parse_amount_rejects_unreadable(text):
    with pytest.raises(ValueError) as e:
        money.parse_amount(text)
    assert str(e.value) == money.AMOUNT_HELP == "금액은 3.5억, 5천만, 350000000 처럼 적어 주세요."


@pytest.mark.parametrize("value, text", [
    (1_200_000_000, "12억"),
    (350_000_000, "3.5억"),
    (123_456_789, "1.23억"),
    (100_000_000, "1억"),
    (50_000_000, "5,000만"),
    (12_345_678, "1,235만"),
    (9000, "9,000원"),
    (None, ""),
])
def test_format_amount(value, text):
    assert money.format_amount(value) == text


@pytest.mark.parametrize("value, text", [
    (1_200_000_000, "12억"),
    (350_000_000, "3.5억"),
    (123_456_789, "123,456,789"),
    (50_000_000, "5,000만"),
    (12_345_678, "12,345,678"),
    (9000, "9,000"),
    (None, ""),
])
def test_edit_text_round_trips(value, text):
    assert money.edit_text(value) == text
    assert money.parse_amount(text) == value

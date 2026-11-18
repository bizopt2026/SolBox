import pytest
from pydantic import BaseModel

from app.ai.parsing import parse_json_output
from app.ai.runner import ClaudeError


class M(BaseModel):
    title: str


def test_parse_plain_fenced_and_wrapped():
    assert parse_json_output('{"title":"가"}', M).title == "가"
    assert parse_json_output('정리했습니다.\n```json\n{"title":"가"}\n```\n끝', M).title == "가"


def test_parse_failure_is_bad_output():
    for bad in ["JSON 없음", '{"title": 1, ', '{"other": "x"}']:
        with pytest.raises(ClaudeError) as e:
            parse_json_output(bad, M)
        assert e.value.kind == "bad_output"

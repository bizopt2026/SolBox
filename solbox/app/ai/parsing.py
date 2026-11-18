"""AI 출력 텍스트에서 JSON 객체를 찾아 검증한다."""
from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.runner import ClaudeError


T = TypeVar("T", bound=BaseModel)


def parse_json_output(text: str, model: type[T]) -> T:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ClaudeError("bad_output", "AI 응답에서 JSON을 찾지 못했습니다.")
    try:
        return model.model_validate_json(text[start:end + 1])
    except ValidationError as e:
        raise ClaudeError("bad_output", f"AI 응답 JSON이 올바르지 않습니다: {e}"[-500:]) from e

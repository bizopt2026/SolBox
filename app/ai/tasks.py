"""AI 작업 처리기 등록. 종류(kind) 이름으로 처리 함수를 찾는다.

처리기 모양: (db, input, runner, settings) -> 결과 문자열.
새 처리기는 `HANDLERS["kind"] = fn` 으로 등록한다. 처리기는 `runner.run(..., timeout_s=settings.ai_timeout_s)` 로 부른다.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.ai.runner import ClaudeRunner
from app.config import Settings

Handler = Callable[[Session, dict, ClaudeRunner, Settings], str]

HANDLERS: dict[str, Handler] = {}

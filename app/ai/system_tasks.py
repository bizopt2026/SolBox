"""시스템 점검용 AI 작업: Claude CLI 가 응답하는지 확인한다 (도구 없음)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import HANDLERS
from app.config import Settings

PING_PROMPT = "이 메시지에는 다른 설명 없이 '확인' 이라는 한 단어로만 답하세요."
PING_TIMEOUT_S = 60


def ai_ping(db: Session, job_input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    db.rollback()
    text = runner.run(PING_PROMPT, allowed_tools=(), timeout_s=PING_TIMEOUT_S)
    if not text.strip():
        raise ClaudeError("bad_output", "AI가 빈 답을 돌려주었습니다.")
    return "ok"


HANDLERS["ai_ping"] = ai_ping

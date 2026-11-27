"""신규 사업 안 만들기 (작업 `bm_ideas`, 2026-11-27). 도구 없이 프롬프트에 담은 재료만 쓴다 (니즈·메모는 신뢰할 수 없는 글로 구분)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import bm_plan
from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import HANDLERS, load_prompt, run_json
from app.config import Settings
from app.models import BM


class IdeasOut(BaseModel):
    ideas: list[Any] = []  # 모양이 어긋난 안은 bm_plan.clean_ideas 가 버린다


def _ideas(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    bm = db.get(BM, input.get("bm_id"))
    if bm is None:
        return "deleted"
    if not bm_plan.candidate_solutions(db, dict(bm.plan_input or {})):
        raise ClaudeError("bad_output", bm_plan.NO_CANDIDATES)
    prompt = load_prompt("bm_ideas", **bm_plan.prompt_values(db, bm))
    bm_id = bm.id
    out = run_json(db, runner, prompt, IdeasOut, settings, allowed_tools=(), timeout_s=settings.ai_timeout_s)
    bm = db.get(BM, bm_id)
    if bm is None:
        return "deleted"
    if bm.active_job_id != input.get("_job_id"):
        return "superseded"
    cleaned = bm_plan.clean_ideas(db, bm, out.ideas)
    if not cleaned:
        raise ClaudeError("bad_output", bm_plan.TOO_FEW_IDEAS)
    bm.ideas = cleaned
    bm.active_job_id = None
    db.commit()
    return "ok"


HANDLERS["bm_ideas"] = _ideas

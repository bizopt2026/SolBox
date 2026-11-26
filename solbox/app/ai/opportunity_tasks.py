"""영업기회 AI 분석 (영업 B): 연결된 활동 기록을 읽고 진행 칸·단계·확인 항목 제안과 브리핑을 만든다.

도구는 쓰지 않는다 (글을 프롬프트에 넣는다). 결과는 `opportunity_insights` 에 초안으로만 저장되고 사람이 반영해야 값이 바뀐다.
규칙(무엇을 넣고, 무엇을 버리고, 어떻게 저장하는지)은 `app/opportunity_ai.py` 한 곳이다."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app import ontology, opportunity_ai as oai
from app.ai.runner import ClaudeRunner
from app.ai.tasks import HANDLERS, run_json
from app.config import Settings
from app.models import Opportunity


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Briefing(_Loose):
    situation: str = ""
    risks: list[str] = []
    next_actions: list[str] = []
    outlook: str = ""


class FieldSug(_Loose):
    value: Any = None
    due: str | None = None
    evidence: str = ""
    activity_id: int | None = None


class CheckSug(_Loose):
    item: str = ""
    evidence: str = ""
    activity_id: int | None = None


class ReviewOut(_Loose):
    briefing: Briefing = Briefing()
    fields: dict[str, FieldSug | None] = {}
    stage: FieldSug | None = None
    checks: list[CheckSug] = []


def _review(db: Session, job_input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    oid = job_input.get("opportunity_id")
    opp = db.get(Opportunity, oid) if isinstance(oid, int) else None
    if opp is None or opp.stage not in ontology.OPPORTUNITY_OPEN_STAGES or not opp.activities:
        return json.dumps({"skipped": True}, ensure_ascii=False)
    today = oai.today()
    prompt, texts = oai.build_prompt(db, opp, today)
    basis = oai.basis_of(opp, texts)
    out = run_json(db, runner, prompt, ReviewOut, settings, allowed_tools=(), timeout_s=settings.ai_timeout_s)
    opp = db.get(Opportunity, oid)  # 실행하는 동안 지워졌거나 닫혔을 수 있다
    if opp is None or opp.stage not in ontology.OPPORTUNITY_OPEN_STAGES:
        return json.dumps({"skipped": True}, ensure_ascii=False)
    suggestions = oai.clean_suggestions(out, opp, texts, today)
    insight = oai.save_insight(db, opp, job_input.get("_job_id"), oai.clean_briefing(out), suggestions, basis)
    db.commit()
    return json.dumps({"insight_id": insight.id, "suggestions": len(suggestions)}, ensure_ascii=False)


HANDLERS[oai.REVIEW_KIND] = _review

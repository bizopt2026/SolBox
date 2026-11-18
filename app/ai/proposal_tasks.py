"""제안 건 AI 작업 처리기. `queue` 가 이 모듈을 불러와 처리기를 등록한다."""
from __future__ import annotations

import json
import logging
import time

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import HANDLERS, MEMO_CLOSE, MEMO_OPEN, load_prompt, run_json
from app.config import Settings
from app.models import AIJob, Proposal, ProposalMessage
from app.proposals import candidate_solutions, clean_directions


log = logging.getLogger(__name__)

CHAT_HISTORY_LIMIT = 30
CHAT_PARTIAL_INTERVAL_S = 0.5
CHAT_OPEN, CHAT_CLOSE = "<<<CHAT", "CHAT>>>"
ROLE_LABELS = {"user": "사용자", "assistant": "AI"}


class Evidence(BaseModel):
    text: str
    sources: list[str] = []


class DirectionSolution(BaseModel):
    solution_id: int
    role: str = ""


class Direction(BaseModel):
    title: str
    summary: str = ""
    solutions: list[DirectionSolution]
    rationale: list[Evidence] = []
    effects: list[str] = []
    needs_check: list[str] = []


class DirectionsOut(BaseModel):
    directions: list[Direction]


def _directions(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    p = db.get(Proposal, input.get("proposal_id"))
    if p is None:
        return "deleted"
    candidates = candidate_solutions(db, p)
    if not candidates:
        raise ClaudeError("bad_output", "박스에 등록된 솔루션이 없습니다.")
    memo = (p.request_memo or "").replace(MEMO_CLOSE, "MEMO> >>").replace(MEMO_OPEN, "<<< MEMO")
    prompt = load_prompt(
        "proposal_directions",
        customer_path=f"customers/{p.customer_id}.md",
        business_line=f"- 기존 사업 사본: `businesses/{p.business_id}.md`" if p.business_id else "- 기존 사업: 지정되지 않음",
        request_memo=memo.strip() or "(없음)",
        solutions="\n".join(f"- id {s.id}: {s.name} (`solutions/{s.id}/`)" for s in candidates),
    )
    pid = p.id
    out = run_json(db, runner, prompt, DirectionsOut, settings)  # 읽기 트랜잭션은 여기서 끝난다
    p = db.get(Proposal, pid)
    if p is None:
        return "deleted"
    if p.active_job_id != input.get("_job_id"):
        return "superseded"  # 더 이상 이 제안 건의 현재 작업이 아니면 결과를 쓰지 않는다
    cleaned = clean_directions(db, p, out, settings.knowledge_dir)
    if len(cleaned) < 2:
        raise ClaudeError("bad_output", "제안 방향을 충분히 만들지 못했습니다.")
    p.directions = cleaned
    p.selected_index = None
    p.solutions = []
    p.status = "directions"
    p.active_job_id = None
    db.commit()
    return "ok"


HANDLERS["proposal_directions"] = _directions


def _partial_writer(bind, job_id: int):
    """누적 텍스트를 0.5초에 한 번 이하로 `AIJob.result` 에 쓰는 on_text. 별도 짧은 세션으로 바로 커밋하고, 실패해도 작업은 계속된다."""
    last = [None]

    def on_text(text: str) -> None:
        now = time.monotonic()
        if last[0] is not None and now - last[0] < CHAT_PARTIAL_INTERVAL_S:
            return
        last[0] = now
        try:
            with Session(bind) as other:
                other.execute(update(AIJob).where(AIJob.id == job_id, AIJob.status == "running").values(result=text))
                other.commit()
        except Exception:  # noqa: BLE001 - 부분 답변을 못 써도 작업은 실패하지 않는다
            log.warning("부분 답변 기록 실패 (작업 %s)", job_id, exc_info=True)

    return on_text


def _chat(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    p = db.get(Proposal, input.get("proposal_id"))
    if p is None:
        return "deleted"
    directions = p.directions or []
    i = p.selected_index
    if i is None or not 0 <= i < len(directions):
        raise ClaudeError("bad_output", "먼저 제안 방향을 선택하세요.")
    direction = directions[i]
    recent = list(db.scalars(
        select(ProposalMessage).where(ProposalMessage.proposal_id == p.id)
        .order_by(ProposalMessage.id.desc()).limit(CHAT_HISTORY_LIMIT)
    ))[::-1]
    conversation = "\n\n".join(
        f"[{ROLE_LABELS.get(m.role, m.role)}]\n" + (m.content or "").replace(CHAT_CLOSE, "CHAT> >>").replace(CHAT_OPEN, "<<< CHAT")
        for m in recent
    )
    prompt = load_prompt(
        "proposal_chat",
        direction=json.dumps(direction, ensure_ascii=False, indent=2),
        customer_path=f"customers/{p.customer_id}.md",
        business_line=f"- 기존 사업 사본: `businesses/{p.business_id}.md`" if p.business_id else "- 기존 사업: 지정되지 않음",
        solutions="\n".join(
            f"- {s.get('name', '')} (`solutions/{s['solution_id']}/`)"
            for s in direction.get("solutions", []) if isinstance(s.get("solution_id"), int)
        ) or "- (없음)",
        conversation=conversation or "(없음)",
    )
    pid, job_id, bind = p.id, input.get("_job_id"), db.get_bind()
    db.rollback()  # 긴 실행 동안 읽기 트랜잭션을 잡고 있지 않는다
    text = runner.run_stream(prompt, on_text=_partial_writer(bind, job_id), timeout_s=settings.ai_timeout_s)
    text = text.strip()
    if not text:
        raise ClaudeError("bad_output", "AI 답변이 비어 있습니다.")
    p = db.get(Proposal, pid)
    if p is None:
        return "deleted"
    if p.active_job_id != job_id:
        return "superseded"
    db.add(ProposalMessage(proposal_id=pid, role="assistant", content=text))
    p.active_job_id = None
    db.commit()
    return text


HANDLERS["proposal_chat"] = _chat

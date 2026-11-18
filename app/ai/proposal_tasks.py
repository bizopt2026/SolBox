"""제안 건 AI 작업 처리기. `queue` 가 이 모듈을 불러와 처리기를 등록한다."""
from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import HANDLERS, MEMO_CLOSE, MEMO_OPEN, load_prompt, run_json
from app.config import Settings
from app.models import Proposal
from app.proposals import candidate_solutions, clean_directions


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

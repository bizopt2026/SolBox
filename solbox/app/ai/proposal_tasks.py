"""제안 건 AI 작업 처리기. `queue` 가 이 모듈을 불러와 처리기를 등록한다."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import ontology
from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import HANDLERS, MEMO_CLOSE, MEMO_OPEN, load_prompt, run_json
from app.config import Settings
from app.models import AIJob, Proposal, ProposalMessage
from app.proposals import candidate_solutions, clean_directions, clean_outline, priority_groups, reset_for_new_direction


log = logging.getLogger(__name__)

CHAT_HISTORY_LIMIT = 30
CHAT_PARTIAL_INTERVAL_S = 0.5
CHAT_OPEN, CHAT_CLOSE = "<<<CHAT", "CHAT>>>"
ROLE_LABELS = {"user": "사용자", "assistant": "AI"}
OUTLINE_OPEN, OUTLINE_CLOSE = "<<<MATERIAL", "MATERIAL>>>"
OUTLINE_MIN_SLIDES, OUTLINE_MAX_SLIDES = 6, 14


def _project_line(p: Proposal) -> str:
    """프롬프트의 프로젝트 줄 (프로젝트 사본 경로는 ontology 가 정한다)."""
    if p.project_id:
        return f"- 프로젝트 사본: `{ontology.rel_path('project', id=p.project_id)}`"
    return "- 프로젝트: 지정되지 않음"


def _opportunity_line(p: Proposal) -> str:
    """프롬프트의 영업기회·BM 줄 (사본 경로는 ontology 가 정한다. 이름은 DB 의 것이지만 경로만 적는다)."""
    if not p.opportunity_id:
        return f"- {ontology.label('opportunity')}: 지정되지 않음"
    line = f"- {ontology.label('opportunity')} 사본: `{ontology.rel_path('opportunity', id=p.opportunity_id)}`"
    if p.opportunity is not None and p.opportunity.bm_id:
        line += f"\n- {ontology.label('bm')} 사본: `{ontology.rel_path('bm', id=p.opportunity.bm_id)}`"
    return line


def _priority_section(db: Session, p: Proposal) -> str:
    """후보 앞쪽에 둔 솔루션과 그 이유 (번호와 이름만). 없으면 빈 글."""
    groups = priority_groups(db, p)
    if not groups:
        return ""
    lines = ["## 우선 검토할 솔루션", "",
             "아래 솔루션은 같은 후보 목록에서 앞에 둔 것입니다. 이유가 적힌 순서대로 먼저 살펴보되, 후보가 아닌 솔루션은 쓰지 마세요.", ""]
    for g in groups:
        lines.append(f"{g['reason']}:")
        lines += [f"- id {s.id}: {s.name}" for s in g["solutions"]]
        lines.append("")
    return "\n".join(lines) + "\n"


def directions_prompt(db: Session, p: Proposal) -> str:
    """제안 방향 만들기 프롬프트. 후보 솔루션은 영업기회의 검토 솔루션 → BM 의 솔루션 → 나머지 순서다."""
    candidates = candidate_solutions(db, p)
    memo = (p.request_memo or "").replace(MEMO_CLOSE, "MEMO> >>").replace(MEMO_OPEN, "<<< MEMO")
    return load_prompt(
        "proposal_directions",
        customer_path=ontology.rel_path("customer", id=p.customer_id),
        project_line=_project_line(p),
        opportunity_line=_opportunity_line(p),
        request_memo=memo.strip() or "(없음)",
        solutions="\n".join(f"- id {s.id}: {s.name} (`{ontology.mirror_dir('solution', id=s.id)}`)" for s in candidates),
        priority_section=_priority_section(db, p),
    )


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


class Step(BaseModel):
    name: str
    detail: str = ""


class AISlide(BaseModel):
    """AI 가 채우는 장. 확인을 끝낸 표시는 사용자 몫이라 여기에 없다."""

    kind: str
    title: str = ""
    key_message: str = ""
    subtitle: str = ""
    bullets: list[str] = []
    left_title: str = ""
    left: list[str] = []
    right_title: str = ""
    right: list[str] = []
    columns: list[str] = []
    rows: list[list[str]] = []
    steps: list[Step] = []
    sources: list[str] = []
    needs_check: list[str] = []


class Slide(AISlide):
    """저장되는 장. `checked` 는 확인을 끝낸 needs_check 문구이고, 옛 구성안에는 이 키가 없다."""
    checked: list[str] = []


class Outline(BaseModel):
    title: str
    slides: list[AISlide]


def _directions(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    p = db.get(Proposal, input.get("proposal_id"))
    if p is None:
        return "deleted"
    if not candidate_solutions(db, p):
        raise ClaudeError("bad_output", "제안 후보가 될 솔루션이 없습니다. 보류가 아닌 솔루션이 있거나 제안 건에 직접 추가해야 합니다.")
    prompt = directions_prompt(db, p)
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
    reset_for_new_direction(p, settings.decks_dir)
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


def _recent_messages(db: Session, p: Proposal) -> list[ProposalMessage]:
    """현재 방향에서 나눈 최근 대화 (방향이 바뀌기 전의 대화는 뺀다), 오래된 것부터."""
    stmt = select(ProposalMessage).where(ProposalMessage.proposal_id == p.id)
    if p.chat_from_id is not None:
        stmt = stmt.where(ProposalMessage.id > p.chat_from_id)
    return list(db.scalars(stmt.order_by(ProposalMessage.id.desc()).limit(CHAT_HISTORY_LIMIT)))[::-1]


def _chat(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    p = db.get(Proposal, input.get("proposal_id"))
    if p is None:
        return "deleted"
    directions = p.directions or []
    i = p.selected_index
    if i is None or not 0 <= i < len(directions):
        raise ClaudeError("bad_output", "먼저 제안 방향을 선택하세요.")
    direction = directions[i]
    recent = _recent_messages(db, p)
    conversation = "\n\n".join(
        f"[{ROLE_LABELS.get(m.role, m.role)}]\n" + (m.content or "").replace(CHAT_CLOSE, "CHAT> >>").replace(CHAT_OPEN, "<<< CHAT")
        for m in recent
    )
    prompt = load_prompt(
        "proposal_chat",
        direction=json.dumps(direction, ensure_ascii=False, indent=2),
        customer_path=ontology.rel_path("customer", id=p.customer_id),
        project_line=_project_line(p),
        solutions="\n".join(
            f"- {s.get('name', '')} (`{ontology.mirror_dir('solution', id=s['solution_id'])}`)"
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


def _material(text: str) -> str:
    return text.replace(OUTLINE_CLOSE, "MATERIAL> >>").replace(OUTLINE_OPEN, "<<< MATERIAL")


def _outline(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    p = db.get(Proposal, input.get("proposal_id"))
    if p is None:
        return "deleted"
    directions = p.directions or []
    i = p.selected_index
    if i is None or not 0 <= i < len(directions):
        raise ClaudeError("bad_output", "먼저 제안 방향을 선택하세요.")
    direction = directions[i]
    recent = _recent_messages(db, p)
    conversation = "\n\n".join(f"[{ROLE_LABELS.get(m.role, m.role)}]\n{_material(m.content or '')}" for m in recent)
    instruction = str(input.get("instruction") or "").strip()
    prompt = load_prompt(
        "proposal_outline",
        direction=json.dumps(direction, ensure_ascii=False, indent=2),
        customer_path=ontology.rel_path("customer", id=p.customer_id),
        project_line=_project_line(p),
        solutions="\n".join(
            f"- {s.get('name', '')} (`{ontology.mirror_dir('solution', id=s['solution_id'])}`)"
            for s in direction.get("solutions", []) if isinstance(s.get("solution_id"), int)
        ) or "- (없음)",
        conversation=conversation if conversation else "(없음)",
        instruction=_material(instruction) if instruction else "(없음)",
    )
    pid, job_id = p.id, input.get("_job_id")
    out = run_json(db, runner, prompt, Outline, settings)  # 읽기 트랜잭션은 여기서 끝난다
    p = db.get(Proposal, pid)
    if p is None:
        return "deleted"
    if p.active_job_id != job_id:
        return "superseded"
    outline = clean_outline(out, settings.knowledge_dir)
    if not OUTLINE_MIN_SLIDES <= len(outline["slides"]) <= OUTLINE_MAX_SLIDES:
        raise ClaudeError("bad_output", "슬라이드 수가 범위를 벗어났습니다.")
    p.outline = outline
    p.outline_updated_at = datetime.now()
    p.status = "outlined"
    if not p.title:
        p.title = outline["title"]
    p.active_job_id = None
    db.commit()
    return "ok"


HANDLERS["proposal_outline"] = _outline

"""AI 동료 대화 작업 `cowork_chat` (AI 코워크 ②). 도구 없이, 대상의 사본 글·합의된 사업 방향·공간 자료 글을 자료로 넣어 답한다."""
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cowork, guards, ontology
from app.ai.proposal_tasks import ROLE_LABELS, _chat_answer
from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import HANDLERS, load_prompt
from app.config import Settings
from app.knowledge import KnowledgeMirror
from app.models import AIMessage, AIThread

log = logging.getLogger(__name__)
SUGGEST_OPEN, SUGGEST_CLOSE = "<<<SUGGEST", "SUGGEST>>>"
NO_TOOLS: tuple[str, ...] = ()


def _safe(text: str) -> str:
    """자료 글이 구역 표지를 흉내 내지 못하게 바꾼다."""
    for a, b in (("<<<", "< < <"), (">>>", "> > >")):
        text = text.replace(a, b)
    return text


def split_answer(text: str) -> tuple[str, list]:
    head, sep, tail = text.partition(SUGGEST_OPEN)
    if not sep:
        return text.strip(), []
    body = tail.split(SUGGEST_CLOSE, 1)[0].strip()
    try:
        raw = json.loads(body)
    except ValueError:
        start, end = body.find("["), body.rfind("]")
        try:
            raw = json.loads(body[start:end + 1]) if start >= 0 < end else []
        except ValueError:
            raw = []
    return head.strip(), raw if isinstance(raw, list) else []


def _kinds_text(code: str) -> str:
    kinds = cowork.SUGGESTION_KINDS.get(code, ())
    if not kinds:
        return "없음 (이 기록에는 제안을 붙이지 말고 글로만 답하세요)"
    return ", ".join(f"`{k}`({cowork.SUGGESTION_LABELS[k]})" for k in kinds)


def _chat(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    thread = db.get(AIThread, input.get("thread_id"))
    if thread is None:
        return "deleted"
    target = cowork.get_target(db, thread.target_type, thread.target_id)
    if target is None:
        return "deleted"
    ctx = cowork.build_context(db, KnowledgeMirror(settings.knowledge_dir), target)
    recent = list(db.scalars(select(AIMessage).where(AIMessage.thread_id == thread.id).order_by(AIMessage.id.desc()).limit(cowork.HISTORY_LIMIT)))[::-1]
    conversation = "\n\n".join(f"[{ROLE_LABELS.get(m.role, m.role)}]\n{_safe(m.content or '')}" for m in recent)
    prompt = load_prompt(
        "cowork_chat", today=date.today().isoformat(), instructions=_safe(ctx.instructions), strategies=_safe(ctx.strategies),
        record=_safe(ctx.record), files=_safe(ctx.files), conversation=conversation or "(없음)",
        suggest_kinds=_kinds_text(thread.target_type), status_codes=", ".join(f"`{c}`({n})" for c, n in ontology.INITIATIVE_STATUSES.items()),
    )
    tid, code, target_id, job_id, bind = thread.id, thread.target_type, thread.target_id, input.get("_job_id"), db.get_bind()
    asker = next((m.user_id for m in reversed(recent) if m.role == "user"), None)
    source, said = ctx.source, "\n".join(m.content or "" for m in recent if m.role == "user")
    basis_rev = guards.rev_of(target)  # AI 가 읽은 때의 기록: 반영할 때 그 뒤 바뀌었으면 막는다
    db.rollback()  # 긴 실행 동안 읽기 트랜잭션을 잡지 않는다
    text = _chat_answer(runner, prompt, bind, job_id, settings.ai_timeout_s, allowed_tools=NO_TOOLS).strip()
    answer, raw = split_answer(text)
    if not answer:
        raise ClaudeError("bad_output", "AI 답변이 비어 있습니다.")
    thread = db.get(AIThread, tid)
    if thread is None:
        return "deleted"
    if thread.active_job_id != job_id:
        return "superseded"
    cowork.supersede(db, thread)
    moved = (thread.target_type, thread.target_id) != (code, target_id)  # 답을 쓰는 동안 과제로 옮겨졌다: 옛 대상의 제안은 붙이지 않는다
    target = None if moved else cowork.get_target(db, code, target_id)
    db.add(AIMessage(thread_id=tid, role="assistant", content=answer, user_id=asker,
                     suggestions=[] if target is None else cowork.clean_suggestions(raw, code, source, said, basis_rev, target)))
    thread.active_job_id = None
    db.commit()
    return answer


HANDLERS["cowork_chat"] = _chat

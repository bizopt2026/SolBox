"""정렬 점검 작업 `alignment_check` (AI 코워크 ③). 도구 없이, 합의된 사업 방향과 대상의 사본 글만 읽는다."""
from __future__ import annotations

import json
from datetime import date

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app import alignment, ontology, workspaces
from app.ai.runner import ClaudeRunner
from app.ai.tasks import HANDLERS, load_prompt, run_json
from app.config import Settings
from app.knowledge import KnowledgeMirror


class AlignmentOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verdict: str = "unclear"
    strategy_id: int | None = None
    reason: str = ""
    evidence: str = ""


def _safe(text: str) -> str:
    return text.replace("<<<", "< < <").replace(">>>", "> > >")


def _check(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    code, tid = input.get("target_type"), input.get("target_id")
    model = alignment.MODELS.get(code)
    obj = db.get(model, tid) if model is not None and isinstance(tid, int) else None
    if obj is None or not alignment.is_target(obj):
        return json.dumps({"skipped": True}, ensure_ascii=False)
    strategies = workspaces.active_strategies(db, date.today())
    if not strategies:
        return json.dumps({"skipped": "no_strategy"}, ensure_ascii=False)
    record = KnowledgeMirror(settings.knowledge_dir).render_text(obj)
    text = "\n\n".join(f"### 번호 {s.id}: {s.title}\n{(s.consensus or '').strip() or '(합의 내용 없음)'}" for s in strategies)
    prompt = load_prompt("alignment_check", strategies=_safe(text), record=_safe(record))
    ids, basis = {s.id for s in strategies}, alignment.basis_of(strategies)
    out = run_json(db, runner, prompt, AlignmentOut, settings, allowed_tools=())  # 읽기 트랜잭션은 여기서 끝난다
    obj = db.get(model, tid)
    if obj is None:
        return json.dumps({"skipped": True}, ensure_ascii=False)
    note = alignment.save(db, code, tid, out.model_dump(), record, ids, basis, input.get("_job_id"))
    db.commit()
    return json.dumps({"note_id": note.id, "verdict": note.verdict}, ensure_ascii=False)


HANDLERS[alignment.KIND] = _check

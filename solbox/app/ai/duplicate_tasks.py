"""중복 후보 AI 확인 (베타 AE): 고객·파트너의 이름 목록만 보내 같은 회사로 보이는 쌍을 받는다.

보내는 것은 번호·이름·다른 이름뿐이다 (연락처·메모·소개·활동 기록 같은 것은 보내지 않는다). 도구는 쓰지 않는다.
결과는 작업 결과(`AIJob.result`)에 JSON 으로만 남고 아무것도 합치거나 저장하지 않는다 (화면이 읽어 후보로 보인다)."""
from __future__ import annotations

import json

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import aliases
from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import HANDLERS, load_prompt, run_json
from app.config import Settings
from app.duplicates import JOB_KIND
from app.merge import KIND_LABEL, KINDS

NAMES_OPEN, NAMES_CLOSE = "<<<NAMES", "NAMES>>>"
MAX_RECORDS = 1500
MAX_PAIRS = 100
MAX_REASON = 200


class DuplicatePair(BaseModel):
    a: int
    b: int
    reason: str = ""


class DuplicatesOut(BaseModel):
    pairs: list[DuplicatePair] = []


def _safe(text: str) -> str:
    """사용자가 적은 이름이 구분 표시를 닫거나 줄·칸을 바꾸지 못하게 한다."""
    return " ".join((text or "").replace(NAMES_CLOSE, "NAMES> >>").replace(NAMES_OPEN, "<<< NAMES").replace("|", "/").split())


def name_lines(db: Session, kind: str) -> tuple[list[str], set[int]]:
    """AI 에 보낼 줄들(`번호 | 이름 | 다른 이름들`)과 번호 집합. 이름과 다른 이름 말고는 담지 않는다."""
    model = KINDS[kind]
    lines, ids = [], set()
    for o in db.scalars(select(model).order_by(model.id)):
        others = "; ".join(_safe(a) for a in aliases.of(o))
        lines.append(f"{o.id} | {_safe(o.name)} | {others}")
        ids.add(o.id)
    return lines, ids


def clean_pairs(out: DuplicatesOut, ids: set[int]) -> list[dict]:
    """없는 번호·같은 번호·되풀이 쌍을 버린다 (쌍은 작은 번호가 앞)."""
    seen: set[tuple[int, int]] = set()
    cleaned = []
    for p in out.pairs:
        if p.a == p.b or p.a not in ids or p.b not in ids:
            continue
        key = (min(p.a, p.b), max(p.a, p.b))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"a": key[0], "b": key[1], "reason": " ".join(p.reason.split())[:MAX_REASON]})
        if len(cleaned) >= MAX_PAIRS:
            break
    return cleaned


def _duplicate_names(db: Session, job_input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    kind = job_input.get("type")
    if kind not in KINDS:
        raise ClaudeError("bad_output", "알 수 없는 종류입니다.")
    lines, ids = name_lines(db, kind)
    if len(lines) < 2:
        return json.dumps({"pairs": []}, ensure_ascii=False)
    if len(lines) > MAX_RECORDS:
        raise ClaudeError("bad_output", f"기록이 {MAX_RECORDS}개를 넘어 한 번에 보낼 수 없습니다.")
    prompt = load_prompt("duplicate_names", kind_label=KIND_LABEL[kind], names="\n".join(lines))
    out = run_json(db, runner, prompt, DuplicatesOut, settings, allowed_tools=(), timeout_s=settings.ai_timeout_s)
    return json.dumps({"pairs": clean_pairs(out, ids)}, ensure_ascii=False)


HANDLERS[JOB_KIND] = _duplicate_names

"""AI 작업 처리기 등록. 종류(kind) 이름으로 처리 함수를 찾는다.

처리기 모양: (db, input, runner, settings) -> 결과 문자열.
새 처리기는 `HANDLERS["kind"] = fn` 으로 등록한다. 처리기는 `runner.run(..., timeout_s=settings.ai_timeout_s)` 로 부른다.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.parsing import parse_json_output
from app.ai.runner import ClaudeError, ClaudeRunner
from app.config import Settings
from app.labels import ACTIVITY_KINDS
from app.models import Business, Customer, Partner, Solution

T = TypeVar("T", bound=BaseModel)
Handler = Callable[[Session, dict, ClaudeRunner, Settings], str]

HANDLERS: dict[str, Handler] = {}

PROMPT_DIR = Path(__file__).parent / "prompts"
NO_READABLE_FILES = "텍스트를 읽을 수 있는 자료 파일이 없습니다."
MEMO_OPEN, MEMO_CLOSE = "<<<MEMO", "MEMO>>>"


def load_prompt(name: str, **values) -> str:
    """`prompts/<name>.md` 를 읽어 `{{키}}` 를 값으로 바꾼다. 한 번에 바꾸므로 값 안의 `{{...}}` 는 다시 바뀌지 않는다."""
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def run_json(runner: ClaudeRunner, prompt: str, model: type[T], settings: Settings) -> T:
    """프롬프트를 실행해 JSON 을 `model` 로 검증한다. 형식 오류(bad_output)면 한 번만 다시 시도한다."""
    for attempt in (1, 2):
        text = runner.run(prompt, timeout_s=settings.ai_timeout_s)
        try:
            return parse_json_output(text, model)
        except ClaudeError as e:
            if e.kind != "bad_output" or attempt == 2:
                raise
    raise ClaudeError("bad_output")  # 도달하지 않는다


class FollowUpDraft(BaseModel):
    task: str = ""
    owner: str = ""
    due: str = ""


class SignalDraft(BaseModel):
    customer: str = ""
    content: str = ""


class Mentions(BaseModel):
    solutions: list[str] = []
    customers: list[str] = []
    businesses: list[str] = []
    partners: list[str] = []


class ActivityDraft(BaseModel):
    kind: str = "meeting"
    date: str | None = None
    title: str = ""
    attendees: list[str] = []
    discussion: list[str] = []
    decisions: list[str] = []
    follow_ups: list[FollowUpDraft] = []
    demand_signals: list[SignalDraft] = []
    mentions: Mentions = Mentions()


def _valid_day(value: str | None) -> bool:
    if not value or len(value) != 10:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def normalize_draft(d: ActivityDraft, today: date) -> ActivityDraft:
    return d.model_copy(update={
        "kind": d.kind if d.kind in ACTIVITY_KINDS else "meeting",
        "date": d.date if _valid_day(d.date) else today.isoformat(),
        "title": d.title.strip() or "제목 없음",
        "follow_ups": [f.model_copy(update={"due": f.due if _valid_day(f.due) else ""}) for f in d.follow_ups],
    })


_MENTION_MODELS = {
    "solutions": Solution, "customers": Customer, "businesses": Business, "partners": Partner,
}


def match_mentions(db: Session, mentions: Mentions) -> dict:
    """종류별로 {"matched": [id...], "new": [이름...]}. 이름 비교는 앞뒤 공백·대소문자를 무시한 완전 일치."""
    result = {}
    for rel, model in _MENTION_MODELS.items():
        existing = {o.name.strip().casefold(): o.id for o in db.scalars(select(model).order_by(model.id))}
        matched: list[int] = []
        new: list[str] = []
        seen: set[str] = set()
        for name in getattr(mentions, rel):
            key = name.strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            if key in existing:
                if existing[key] not in matched:
                    matched.append(existing[key])
            elif rel != "businesses":
                new.append(name.strip())
        result[rel] = {"matched": matched, "new": new}
    return result


def draft_summary_md(d: ActivityDraft) -> str:
    sections = []
    for title, items in (("참석자", d.attendees), ("논의 내용", d.discussion), ("결정 사항", d.decisions)):
        items = [i.strip() for i in items if i.strip()]
        if items:
            sections.append(f"## {title}\n" + "\n".join(f"- {i}" for i in items))
    return "\n\n".join(sections)


def _name_list(db: Session, model) -> str:
    names = [o.name for o in db.scalars(select(model).order_by(model.name))]
    return "\n".join(f"- {n}" for n in names) or "(없음)"


def _structure_activity(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    # 원문이 구분 표시를 닫아 버리지 못하게 한다.
    memo = str(input.get("raw_text", "")).replace(MEMO_CLOSE, "MEMO> >>").replace(MEMO_OPEN, "<<< MEMO")
    prompt = load_prompt(
        "activity_structure", raw_text=memo, today=date.today().isoformat(),
        solutions=_name_list(db, Solution), customers=_name_list(db, Customer),
        businesses=_name_list(db, Business), partners=_name_list(db, Partner),
    )
    draft = run_json(runner, prompt, ActivityDraft, settings)
    return normalize_draft(draft, date.today()).model_dump_json()


class SolutionDraft(BaseModel):
    summary: str = ""
    description: str = ""
    tags: list[str] = []


def _draft_solution(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    solution = db.get(Solution, input.get("solution_id"))
    if solution is None:
        raise ClaudeError("unknown", "솔루션을 찾을 수 없습니다.")
    folder = f"solutions/{solution.id}/files"
    texts = settings.knowledge_dir / "solutions" / str(solution.id) / "files"
    if not texts.is_dir() or not any(texts.glob("*.md")):
        raise ClaudeError("bad_output", NO_READABLE_FILES)
    prompt = load_prompt("solution_draft", solution_name=solution.name, folder=f"{folder}/")
    return run_json(runner, prompt, SolutionDraft, settings).model_dump_json()


HANDLERS["activity_structure"] = _structure_activity
HANDLERS["solution_draft"] = _draft_solution

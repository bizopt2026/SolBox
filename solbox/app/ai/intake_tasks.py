"""올리기 묶음 AI 작업 처리기: 올라온 파일을 읽고 솔루션 박스에 어떻게 정리할지 제안한다. `queue` 가 이 모듈을 불러와 처리기를 등록한다."""
from __future__ import annotations

import logging
from datetime import date

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ValidationError, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import intake as intake_lib
from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import AFTER_JOB, HANDLERS, MEMO_CLOSE, MEMO_OPEN, ActivityDraft, load_prompt, run_json
from app.config import Settings
from app.models import Customer, Intake, Partner, Solution

log = logging.getLogger(__name__)


def _text(v) -> str:
    """글 칸: 문자열은 그대로, 숫자는 글로, 그 밖(null·목록 등)은 빈 글."""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return ""


def _int(v) -> int | None:
    """번호 칸: 정수, 정수인 실수, 숫자만 있는 글은 정수로. 그 밖은 None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    if isinstance(v, str) and v.strip().isdecimal():
        return int(v.strip())
    return None


def _int_list(v) -> list[int]:
    return [n for n in map(_int, v) if n is not None] if isinstance(v, list) else []


def _str_list(v) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


Text = Annotated[str, BeforeValidator(_text)]
MaybeInt = Annotated[int | None, BeforeValidator(_int)]
IntList = Annotated[list[int], BeforeValidator(_int_list)]
StrList = Annotated[list[str], BeforeValidator(_str_list)]


def _dicts(v) -> list[dict]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _draft(v) -> ActivityDraft | None:
    """AI 가 준 활동 초안을 느슨하게 받아 `ActivityDraft` 로 만든다 (쓸 수 없으면 None). 다른 곳의 `ActivityDraft` 는 그대로 둔다."""
    if isinstance(v, ActivityDraft):
        return v
    if not isinstance(v, dict):
        return None
    mentions = v.get("mentions") if isinstance(v.get("mentions"), dict) else {}
    clean = {
        "kind": _text(v.get("kind")) or "meeting", "date": _text(v.get("date")) or None, "title": _text(v.get("title")),
        "attendees": _str_list(v.get("attendees")), "discussion": _str_list(v.get("discussion")),
        "decisions": _str_list(v.get("decisions")),
        "follow_ups": [{"task": _text(f.get("task")), "owner": _text(f.get("owner")), "due": _text(f.get("due"))} for f in _dicts(v.get("follow_ups"))],
        "demand_signals": [{"customer": _text(d.get("customer")), "content": _text(d.get("content"))} for d in _dicts(v.get("demand_signals"))],
        "mentions": {k: _str_list(mentions.get(k)) for k in ("solutions", "customers", "businesses", "partners")},
    }
    try:
        return ActivityDraft.model_validate(clean)
    except ValidationError:
        return None


class PlanItem(BaseModel):
    """정리안 항목의 공통 칸. AI 가 엉뚱한 값을 줘도 분석 전체가 실패하지 않게 모든 칸을 느슨하게 받는다."""

    files: IntList = []
    reason: Text = ""


class NewSolution(PlanItem):
    name: Text = ""
    summary: Text = ""
    description: Text = ""
    tags: StrList = []
    partner: Text = ""


class AddToSolution(PlanItem):
    solution_id: MaybeInt = None
    note: Text = ""


class ActivityItem(PlanItem):
    draft: Annotated[ActivityDraft | None, BeforeValidator(_draft)] = None
    raw_from: MaybeInt = None  # 원문으로 쓸 파일 번호


class NewCustomer(PlanItem):
    name: Text = ""
    industry: Text = ""
    contact: Text = ""
    memo: Text = ""


class NewPartner(PlanItem):
    name: Text = ""
    contact: Text = ""
    description: Text = ""
    memo: Text = ""


class Unplaced(PlanItem):
    pass


class IntakePlanOut(BaseModel):
    solutions: list[NewSolution] = []
    additions: list[AddToSolution] = []
    activities: list[ActivityItem] = []
    customers: list[NewCustomer] = []
    partners: list[NewPartner] = []
    unplaced: list[Unplaced] = []

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data):
        """목록이 아니거나 항목이 객체가 아니면 그 항목만 버린다."""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for field, model in (("solutions", NewSolution), ("additions", AddToSolution), ("activities", ActivityItem),
                             ("customers", NewCustomer), ("partners", NewPartner), ("unplaced", Unplaced)):
            kept = []
            for d in data.get(field) if isinstance(data.get(field), list) else []:
                if isinstance(d, model):
                    kept.append(d)
                elif isinstance(d, dict):
                    try:
                        kept.append(model.model_validate(d))
                    except ValidationError:
                        pass
            out[field] = kept
        return out


def _names(db: Session, model, with_id: bool = False) -> str:
    rows = db.scalars(select(model).order_by(model.name))
    lines = [f"- id {o.id}: {o.name}" if with_id else f"- {o.name}" for o in rows]
    return "\n".join(lines) or "(없음)"


def _analyze(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    intake = db.get(Intake, input.get("intake_id"))
    if intake is None:
        return "deleted"
    job_id, intake_id = input.get("_job_id"), intake.id
    if intake.status != "analyzing" or intake.job_id != job_id:
        return "superseded"
    folder = intake_lib.work_dir(settings, intake)
    if not (folder / intake_lib.FILE_LIST_NAME).is_file():  # 작업 폴더가 지워졌으면 원본으로 다시 만든다
        intake_lib.prepare_workdir(settings, intake)
    note = (intake.note or "").replace(MEMO_CLOSE, "MEMO> >>").replace(MEMO_OPEN, "<<< MEMO").strip()
    prompt = load_prompt(
        "intake_analyze", today=date.today().isoformat(), note=note or "(없음)",
        solutions=_names(db, Solution, with_id=True), customers=_names(db, Customer), partners=_names(db, Partner),
    )
    out = run_json(db, runner, prompt, IntakePlanOut, settings, cwd=folder)  # 읽기 트랜잭션은 여기서 끝난다
    intake = db.get(Intake, intake_id)
    if intake is None:
        return "deleted"
    if intake.status != "analyzing" or intake.job_id != job_id:
        return "superseded"  # 그 사이 다시 정리가 시작되었다
    plan = intake_lib.clean_plan(db, intake, out, date.today(), settings)
    saved = db.execute(
        update(Intake).where(Intake.id == intake_id, Intake.status == "analyzing", Intake.job_id == job_id)
        .values(plan=plan, status="ready", error="")
    )
    db.commit()
    return "ok" if saved.rowcount == 1 else "superseded"


def _after_analyze(db: Session, input: dict) -> None:
    """작업이 실패했는데 묶음이 정리 중으로 남아 있으면 실패로 바꾸고 이유를 적는다."""
    intake_id = input.get("intake_id")
    if isinstance(intake_id, int):
        intake_lib.finish_failed_analysis(db, intake_id)


HANDLERS["intake_analyze"] = _analyze
AFTER_JOB["intake_analyze"] = _after_analyze

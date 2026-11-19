"""올리기 묶음 AI 작업 처리기: 올라온 파일을 읽고 솔루션 박스에 어떻게 정리할지 제안한다. `queue` 가 이 모듈을 불러와 처리기를 등록한다."""
from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import intake as intake_lib
from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import AFTER_JOB, HANDLERS, MEMO_CLOSE, MEMO_OPEN, ActivityDraft, load_prompt, run_json
from app.config import Settings
from app.models import Customer, Intake, Partner, Solution

log = logging.getLogger(__name__)


class PlanItem(BaseModel):
    files: list[int] = []
    reason: str = ""


class NewSolution(PlanItem):
    name: str
    summary: str = ""
    description: str = ""
    tags: list[str] = []
    partner: str = ""


class AddToSolution(PlanItem):
    solution_id: int
    note: str = ""


class ActivityItem(PlanItem):
    draft: ActivityDraft
    raw_from: int | None = None  # 원문으로 쓸 파일 번호


class NewCustomer(PlanItem):
    name: str
    industry: str = ""
    contact: str = ""
    memo: str = ""


class NewPartner(PlanItem):
    name: str
    contact: str = ""
    description: str = ""
    memo: str = ""


class Unplaced(PlanItem):
    pass


class IntakePlanOut(BaseModel):
    solutions: list[NewSolution] = []
    additions: list[AddToSolution] = []
    activities: list[ActivityItem] = []
    customers: list[NewCustomer] = []
    partners: list[NewPartner] = []
    unplaced: list[Unplaced] = []


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

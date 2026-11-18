"""분야별 웹 리서치 AI 작업 처리기. `queue` 가 이 모듈을 불러와 처리기를 등록한다."""
from __future__ import annotations

import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.runner import WEB_TOOLS, ClaudeError, ClaudeRunner
from app.ai.tasks import AFTER_JOB, HANDLERS, load_prompt, run_json
from app.config import Settings
from app.labels import RESEARCH_AREAS
from app.models import ResearchReport, ResearchRun, ResearchTopic
from app.research import advance_run, clean_area

log = logging.getLogger(__name__)

TOPICS_OPEN, TOPICS_CLOSE = "<<<TOPICS", "TOPICS>>>"
EMPTY_RESULT = "조사 결과가 비어 있습니다."
RECENT_DAYS, MAX_DAYS = 7, 30


class SourceLink(BaseModel):
    title: str = ""
    url: str


class Trend(BaseModel):
    headline: str
    summary: str = ""
    sources: list[SourceLink] = []


class AreaOut(BaseModel):
    title: str = ""
    trends: list[Trend]
    insights: list[str] = []


def _safe(text: str) -> str:
    """관리자가 적은 글이 구분 표시를 닫지 못하게 한다."""
    return text.replace(TOPICS_CLOSE, "TOPICS> >>").replace(TOPICS_OPEN, "<<< TOPICS").strip()


def _empty_folder(folder: Path) -> None:
    """웹 도구 작업의 작업 폴더에는 아무것도 없어야 한다."""
    folder.mkdir(parents=True, exist_ok=True)
    for child in folder.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _research_area(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    report = db.get(ResearchReport, input.get("report_id"))
    if report is None or report.run is None:
        return ""
    report_id, area = report.id, report.area
    topics = db.query(ResearchTopic).filter_by(area=area, is_active=True).order_by(ResearchTopic.id).all()
    if not topics:
        raise ClaudeError("bad_output", "이 분야에 사용 중인 주제가 없습니다.")
    lines = [f"- {_safe(t.name)}" + (f" (키워드: {_safe(t.keywords)})" if t.keywords.strip() else "") for t in topics]
    today = date.today()
    prompt = load_prompt(
        "research_area", area=RESEARCH_AREAS.get(area, area), topics="\n".join(lines),
        today=today.isoformat(), recent_from=(today - timedelta(days=RECENT_DAYS)).isoformat(),
        oldest=(today - timedelta(days=MAX_DAYS)).isoformat(),
    )
    _empty_folder(settings.research_work_dir)
    out = run_json(db, runner, prompt, AreaOut, settings, allowed_tools=WEB_TOOLS,
                   timeout_s=settings.research_timeout_s, cwd=settings.research_work_dir)
    cleaned = clean_area(out)
    if not cleaned["trends"]:
        raise ClaudeError("bad_output", EMPTY_RESULT)
    report = db.get(ResearchReport, report_id)  # 긴 실행 동안 지워졌을 수 있다.
    if report is None or db.get(ResearchRun, report.run_id) is None:
        return ""
    report.title = cleaned["title"] or f"{RESEARCH_AREAS.get(area, area)} 동향"
    report.trends = cleaned["trends"]
    report.insights = cleaned["insights"]
    report.status = "done"
    report.error = ""
    db.commit()
    advance_run(db, report.run_id)
    return ""


def _after_area(db: Session, input: dict) -> None:
    report = db.get(ResearchReport, input.get("report_id"))
    if report is not None:
        advance_run(db, report.run_id)


HANDLERS["research_area"] = _research_area
AFTER_JOB["research_area"] = _after_area

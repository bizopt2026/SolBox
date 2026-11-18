"""분야별 웹 리서치 AI 작업 처리기. `queue` 가 이 모듈을 불러와 처리기를 등록한다."""
from __future__ import annotations

import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.runner import DEFAULT_TOOLS, WEB_TOOLS, ClaudeError, ClaudeRunner
from app.ai.tasks import AFTER_JOB, HANDLERS, load_prompt, run_json
from app.config import Settings
from app.knowledge import KnowledgeMirror
from app.labels import RESEARCH_AREAS, STAGES
from app.models import Activity, ResearchReport, ResearchRun, ResearchTopic, Solution
from app.research import advance_run, clean_area, clean_suggestions

log = logging.getLogger(__name__)

TOPICS_OPEN, TOPICS_CLOSE = "<<<TOPICS", "TOPICS>>>"
EMPTY_RESULT = "조사 결과가 비어 있습니다."
RECENT_DAYS, MAX_DAYS = 7, 30
ACTIVITY_DAYS, MAX_ACTIVITIES = 60, 100
EMPTY_SUGGEST = "발굴 방향 제안이 비어 있습니다."


class SourceLink(BaseModel):
    title: str = ""
    url: str = ""
    opened: bool = False  # 모델이 실제로 페이지 본문을 열었을 때만 true


class Trend(BaseModel):
    headline: str
    summary: str = ""
    sources: list[SourceLink] = []


class AreaOut(BaseModel):
    title: str = ""
    trends: list[Trend]
    insights: list[str] = []


class Suggestion(BaseModel):
    kind: str = "new"
    title: str
    detail: str = ""
    reason: str = ""
    solution_ids: list[int] = []
    sources: list[str] = []


class SuggestOut(BaseModel):
    suggestions: list[Suggestion]
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
    _write_copy(db, report, settings)
    advance_run(db, report.run_id)
    return ""


def _write_copy(db: Session, report: ResearchReport, settings: Settings) -> None:
    """보고서가 done 이 되면 지식 폴더에 사본을 쓴다. 사본을 못 써도 보고서는 유효하다."""
    try:
        KnowledgeMirror(settings.knowledge_dir).sync(report)
    except OSError:
        log.exception("보고서 %s 사본 쓰기 실패", report.id)


def _research_suggest(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    report = db.get(ResearchReport, input.get("report_id"))
    if report is None or report.run is None:
        return ""
    report_id, run_id = report.id, report.run_id
    mirror = KnowledgeMirror(settings.knowledge_dir)
    areas = [r for r in report.run.reports if r.kind == "area" and r.status == "done"]
    if not areas:
        raise ClaudeError("bad_output", "제안할 분야 보고서가 없습니다.")
    copies = []
    for r in areas:
        if not mirror.path_for(r).is_file():
            _write_copy(db, r, settings)
        copies.append(f"- {RESEARCH_AREAS.get(r.area, r.area)}: `{mirror.rel_path(r)}`")
    solutions = db.query(Solution).filter_by(stage="boxed").order_by(Solution.id).all()
    since = date.today() - timedelta(days=ACTIVITY_DAYS)
    activities = (db.query(Activity).filter(Activity.date >= since).order_by(Activity.date.desc(), Activity.id.desc())
                  .limit(MAX_ACTIVITIES).all())
    prompt = load_prompt(
        "research_suggest", today=date.today().isoformat(), reports="\n".join(copies),
        solutions="\n".join(f"- id {s.id}: {s.name} ({STAGES.get(s.stage, s.stage)}, `{mirror.rel_path(s)}`)" for s in solutions) or "- (없음)",
        activities="\n".join(f"- {a.date.isoformat()} {a.title or '(제목 없음)'} (`{mirror.rel_path(a)}`)" for a in activities) or "- (없음)",
        days=ACTIVITY_DAYS,
    )
    out = run_json(db, runner, prompt, SuggestOut, settings, allowed_tools=DEFAULT_TOOLS,
                   timeout_s=settings.research_timeout_s)  # 작업 폴더는 지식 폴더(러너 기본값), 웹 도구 없음
    cleaned = clean_suggestions(db, out, settings.knowledge_dir)
    if not cleaned["suggestions"]:
        raise ClaudeError("bad_output", EMPTY_SUGGEST)
    report = db.get(ResearchReport, report_id)  # 긴 실행 동안 지워졌을 수 있다.
    if report is None or db.get(ResearchRun, run_id) is None:
        return ""
    report.title = "발굴 방향 제안"
    report.suggestions = cleaned["suggestions"]
    report.insights = cleaned["insights"]
    report.status = "done"
    report.error = ""
    db.commit()
    _write_copy(db, report, settings)
    advance_run(db, run_id)
    return ""


def _after_area(db: Session, input: dict) -> None:
    report = db.get(ResearchReport, input.get("report_id"))
    if report is not None:
        advance_run(db, report.run_id)


HANDLERS["research_area"] = _research_area
AFTER_JOB["research_area"] = _after_area
HANDLERS["research_suggest"] = _research_suggest
AFTER_JOB["research_suggest"] = _after_area  # 입력이 같은 모양({"report_id"})이라 같은 후처리를 쓴다

"""웹 리서치: 주제와 실행 주기 설정."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.labels import ERROR_KINDS, RESEARCH_AREAS
from app.models import AIJob, AppSetting, ResearchReport, ResearchRun, ResearchTopic

if TYPE_CHECKING:
    from app.ai.research_tasks import AreaOut

log = logging.getLogger(__name__)

SCHEDULE_KEY = "research_schedule"


@dataclass
class Schedule:
    enabled: bool = True
    weekday: int = 0
    hour: int = 6
    minute: int = 0


def _int_in(value, low: int, high: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return default
    return value


def get_schedule(db: Session) -> Schedule:
    default = Schedule()
    row = db.get(AppSetting, SCHEDULE_KEY)
    raw = row.value if row is not None and isinstance(row.value, dict) else {}
    enabled = raw.get("enabled", default.enabled)
    return Schedule(
        enabled=enabled if isinstance(enabled, bool) else default.enabled,
        weekday=_int_in(raw.get("weekday"), 0, 6, default.weekday),
        hour=_int_in(raw.get("hour"), 0, 23, default.hour),
        minute=_int_in(raw.get("minute"), 0, 59, default.minute),
    )


def set_schedule(db: Session, s: Schedule) -> None:
    value = asdict(s)
    row = db.get(AppSetting, SCHEDULE_KEY)
    if row is None:
        db.add(AppSetting(key=SCHEDULE_KEY, value=value))
    else:
        row.value = value
    db.commit()


def active_topics(db: Session) -> dict[str, list[ResearchTopic]]:
    """분야별 사용 중인 주제 (주제가 없는 분야는 키가 없다)."""
    topics = db.query(ResearchTopic).filter_by(is_active=True).order_by(ResearchTopic.id).all()
    result: dict[str, list[ResearchTopic]] = {}
    for area in RESEARCH_AREAS:
        items = [t for t in topics if t.area == area]
        if items:
            result[area] = items
    return result


URL_MAX = 2000
MAX_TRENDS, MAX_INSIGHTS = 10, 7
NO_SOURCE_NOTE = "출처 링크 없음, 확인 필요"
NO_TOPICS_MESSAGE = "사용 중인 리서치 주제가 없습니다."


def clean_url(url: str) -> str | None:
    """http(s) 주소만 돌려준다. 공백·제어문자가 있거나 호스트가 없거나 너무 길면 None."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")) or len(url) > URL_MAX:
        return None
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return url if host else None


def clean_area(out: AreaOut) -> dict:
    """AI 가 만든 분야 보고서를 저장할 모양으로 다듬는다 (링크 걸러내기, 개수 제한)."""
    trends = []
    for t in out.trends:
        headline = t.headline.strip()
        if not headline:
            continue
        sources, seen = [], set()
        for s in t.sources:
            url = clean_url(s.url)
            if url is None or url in seen:
                continue
            seen.add(url)
            sources.append({"title": s.title.strip(), "url": url})
        trends.append({
            "headline": headline, "summary": t.summary.strip(), "sources": sources,
            "needs_check": [] if sources else [NO_SOURCE_NOTE],
        })
    insights = [i.strip() for i in out.insights if i.strip()]
    return {"title": out.title.strip(), "trends": trends[:MAX_TRENDS], "insights": insights[:MAX_INSIGHTS]}


def start_run(db: Session, trigger: str, requester_id: int | None, slot: datetime | None = None) -> ResearchRun | None:
    """조사 실행을 시작한다. 이미 진행 중이면 None. 동시에 와도 하나만 만들어진다."""
    areas = {area: [t.id for t in items] for area, items in active_topics(db).items()}
    if not areas:
        raise ValueError(NO_TOPICS_MESSAGE)
    db.rollback()  # 읽기 트랜잭션을 끝낸다. 첫 문장이 쓰기여야 SQLite 쓰기 잠금을 먼저 잡는다.
    run = ResearchRun(trigger=trigger, slot=slot, requester_id=requester_id, status="running")
    db.add(run)
    db.flush()  # 쓰기 잠금: 다른 요청은 우리가 끝낼 때까지 기다린다.
    other = db.query(ResearchRun.id).filter(ResearchRun.status == "running", ResearchRun.id != run.id).first()
    if other is not None:
        db.rollback()
        return None
    for area in areas:
        report = ResearchReport(run_id=run.id, kind="area", area=area, status="pending")
        db.add(report)
        db.flush()
        job = AIJob(kind="research_area", input={"report_id": report.id}, requester_id=requester_id,
                    priority=10, status="pending")
        db.add(job)
        db.flush()
        report.job_id = job.id
    db.commit()
    return run


def sync_failed_reports(db: Session, run_id: int) -> None:
    """작업이 실패한 분야 보고서를 failed 로 맞추고 안내문을 채운다."""
    reports = db.query(ResearchReport).filter_by(run_id=run_id, kind="area", status="pending").all()
    for r in reports:
        job = db.get(AIJob, r.job_id) if r.job_id else None
        if job is None or job.status != "failed":
            continue
        r.status = "failed"
        r.error = job.error if job.error_kind == "bad_output" and job.error else ERROR_KINDS.get(job.error_kind, ERROR_KINDS["unknown"])
    db.commit()


def advance_run(db: Session, run_id: int) -> None:
    """분야 보고서가 모두 끝났으면 실행을 마감한다. 이미 마감된 실행에는 아무것도 하지 않는다."""
    db.rollback()
    run = db.get(ResearchRun, run_id)
    if run is None or run.status != "running":
        return
    sync_failed_reports(db, run_id)
    db.refresh(run)
    areas = [r for r in run.reports if r.kind == "area"]
    if not areas or any(r.status not in ("done", "failed") for r in areas):
        return
    done = sum(r.status == "done" for r in areas)
    # 3단계 Task 5: done > 0 이면 여기서 제안 작업을 시작하고 마감은 그 작업이 한다.
    status = "failed" if done == 0 else "done" if done == len(areas) else "partial"
    _close_run(db, run_id, status)


def _close_run(db: Session, run_id: int, status: str) -> None:
    """조건부 UPDATE 라 두 작업이 동시에 마감하려 해도 한 번만 반영된다."""
    db.execute(update(ResearchRun).where(ResearchRun.id == run_id, ResearchRun.status == "running")
               .values(status=status, finished_at=datetime.now()))
    db.commit()

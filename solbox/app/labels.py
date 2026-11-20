"""화면에 보일 한국어 이름. 정의는 `app/ontology.py` 한 곳에 있고, 여기서는 기존 import 가 계속 되도록 다시 내보낸다."""
from datetime import datetime

from app.ontology import (  # noqa: F401  (다시 내보내기)
    ACTIVITY_KINDS, ERROR_KINDS, FILE_KINDS, INTAKE_FILE_KINDS, INTAKE_ITEM_KINDS, INTAKE_STATUSES, JOB_EXPECTED, JOB_KINDS,
    AI_OFF_NOTICE, AI_OFF_STATUS, JOB_STATUSES, PROPOSAL_STATUSES, READ_STATUSES, RESEARCH_AREAS, RUN_STATUSES, SLIDE_KINDS, STAGES, SUGGESTION_KINDS,
    WEEKDAYS,
)


def elapsed_text(started_at: datetime, now: datetime | None = None) -> str:
    """"35초 경과", "1분 35초 경과" 처럼 시작 뒤 지난 시간."""
    seconds = max(0, int(((now or datetime.now()) - started_at).total_seconds()))
    minutes, rest = divmod(seconds, 60)
    return (f"{minutes}분 {rest}초" if minutes and rest else f"{minutes}분" if minutes else f"{rest}초") + " 경과"


def wait_detail(job, now: datetime | None = None, expected: bool = True) -> str:
    """진행 중인 작업 옆에 붙일 안내: "(보통 1분 안에 끝납니다 · 35초 경과)". 시작 전이면 빈 글."""
    if job.started_at is None:
        return ""
    hint = JOB_EXPECTED.get(job.kind, "") if expected else ""
    parts = []
    if hint:
        parts.append(hint + " 끝납니다" if hint.endswith("안에") else hint)
    parts.append(elapsed_text(job.started_at, now))
    return "(" + " · ".join(parts) + ")"


AHEAD_LIMIT = 3


def ahead_lines(jobs, now: datetime | None = None, limit: int = AHEAD_LIMIT) -> list[str]:
    """앞에 있는 작업을 평이한 말로: "리서치 분야 조사 진행 중 (3분 경과)", "올린 파일 정리 대기 중".
    종류 이름과 경과 시간만 쓴다 (다른 사람의 작업 내용은 드러내지 않는다). 많으면 "외 n개"."""
    lines = []
    for job in list(jobs)[:limit]:
        name = JOB_KINDS.get(job.kind, "AI 작업")
        if job.status == "running" and job.started_at is not None:
            lines.append(f"{name} 진행 중 ({elapsed_text(job.started_at, now)})")
        elif job.status == "running":
            lines.append(f"{name} 진행 중")
        else:
            lines.append(f"{name} 대기 중")
    if len(jobs) > limit:
        lines.append(f"외 {len(jobs) - limit}개")
    return lines

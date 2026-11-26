"""영업기회 분석 AI 해석 (영업 C): 코드가 계산한 숫자(`pipeline_stats.compute`)를 읽고 해석 글만 쓴다. 도구 없음.
결과는 작업 결과(`AIJob.result`)에만 둔다: 해석 글 + 그 숫자의 해시·기간 (화면이 지금 숫자와 견준다)."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app import pipeline_stats as ps
from app.ai.runner import ClaudeRunner
from app.ai.tasks import HANDLERS, load_prompt, run_json
from app.config import Settings

JOB_KIND = "pipeline_insight"
MAX_TEXT, MAX_ITEMS = 300, 5


class InsightOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    highlights: list[str] = []
    causes: list[str] = []
    actions: list[str] = []


def team_key(raw: str):
    if raw == ps.TEAM_NONE:
        return ps.TEAM_NONE
    return int(raw) if raw.isdigit() else None


def _numbers(stats: dict) -> str:
    keep = {k: stats[k] for k in ("tiles", "stages", "months", "reasons", "teams", "excluded")}
    keep["stages"] = [{k: v for k, v in r.items() if k != "name"} | {"stage": r["name"]} for r in keep["stages"]]
    from app import ontology

    keep["teams"] = [{**t, "dwell": {ontology.OPPORTUNITY_STAGES.get(k, k): v for k, v in (t.get("dwell") or {}).items()}} for t in keep["teams"]]
    return json.dumps(keep, ensure_ascii=False, default=str)


def _one(t) -> str:
    return " ".join(str(t or "").split())[:MAX_TEXT]


def _insight(db: Session, job_input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    from app.models import Team
    from app.routers.home import today

    period, team_raw = job_input.get("period", ps.PERIOD_DEFAULT), str(job_input.get("team") or "")
    key = team_key(team_raw)
    stats = ps.compute(db, period, key, today())
    if key is None:
        team_name = "전체"
    elif key == ps.TEAM_NONE:
        team_name = ps.NO_TEAM
    else:
        t = db.get(Team, key)
        team_name = t.name if t else "전체"
    start, end = stats["range"]
    prompt = load_prompt("pipeline_insight", period_label=stats["period_label"], start=start.isoformat(), end=end.isoformat(),
                         team_name=team_name, numbers=_numbers(stats))
    out = run_json(db, runner, prompt, InsightOut, settings, allowed_tools=(), timeout_s=settings.ai_timeout_s)
    return json.dumps({"summary": _one(out.summary), "highlights": [_one(x) for x in out.highlights if _one(x)][:MAX_ITEMS],
                       "causes": [_one(x) for x in out.causes if _one(x)][:MAX_ITEMS], "actions": [_one(x) for x in out.actions if _one(x)][:MAX_ITEMS],
                       "numbers_hash": stats["numbers_hash"], "start": start.isoformat(), "end": end.isoformat()}, ensure_ascii=False)


def find_jobs(db: Session, period: str, team_raw: str) -> tuple:
    """(진행 중 작업, 마지막으로 끝난 해석 결과 dict) — 같은 기간 코드·팀만. 최근 작업 100개 안에서 찾는다.
    끝난 해석의 dict 에는 그보다 새 작업이 실패했는지(`failed_after`)를 단다. 끝난 해석이 없고 가장 새 작업이 실패했으면 {"failed_only": True}."""
    from sqlalchemy import select

    from app.models import AIJob

    running, done, failed_newest = None, None, None
    for job in db.scalars(select(AIJob).where(AIJob.kind == JOB_KIND).order_by(AIJob.id.desc()).limit(100)):
        inp = job.input or {}
        if inp.get("period") != period or str(inp.get("team") or "") != team_raw:
            continue
        if job.status in ("pending", "running"):
            running = running or job
        elif job.status == "failed" and done is None and failed_newest is None:
            failed_newest = job
        elif job.status == "done" and done is None and job.result:
            try:
                done = {**json.loads(job.result), "finished_at": job.finished_at, "failed_after": failed_newest is not None}
            except ValueError:
                continue
    if done is None and failed_newest is not None:
        done = {"failed_only": True}
    return running, done


HANDLERS[JOB_KIND] = _insight

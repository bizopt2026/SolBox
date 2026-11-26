"""웹 리서치: 실행 목록·상세, 솔루션 후보 등록, 지금 조사, 실행 상태, 실패한 분야 다시 시도, 실행 삭제."""
from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app import ontology, origin, ui
from app.ai.gate import require_ai_on
from app.ai.queue import queue_position
from app import notices
from app.db import get_db
from app.knowledge import KnowledgeMirror, get_mirror
from app.labels import RESEARCH_AREAS
from app.main import render
from app.models import AIJob, ResearchReport, ResearchRun, Solution, User
from app.research import UNOPENED_LABEL, clean_url, humanize_ids, start_run
from app.routers._common import paginate
from app.security import require_admin, require_user
from app.checks import is_unopened
from app.sources import SourceLookup, describe_source

router = APIRouter(prefix="/research")

ADMIN_PAGE = "/admin/research"
NAME_MAX = 80
RUNNING_DELETE = "진행 중인 조사는 삭제할 수 없습니다."


def _link(url: str) -> dict:
    """화면에 낼 출처 주소. 안전한 http(s) 주소가 아니면 `href` 를 비워 글자만 보이게 한다."""
    safe = clean_url(url) if isinstance(url, str) else None
    return {"href": safe, "host": urlsplit(safe).hostname if safe else ""}


def _trend_view(trend: dict) -> dict:
    sources = []
    for s in trend.get("sources") or []:
        url = s.get("url", "") if isinstance(s, dict) else ""
        title = s.get("title", "") if isinstance(s, dict) else ""
        opened = s.get("opened", True) if isinstance(s, dict) else True  # 옛 기록은 열어 본 것으로 본다
        sources.append({"title": title or url, "url": url, "opened": bool(opened), **_link(url)})
    return {**trend, "sources": sources}


def _search_text(run: ResearchRun) -> str:
    parts: list[str] = []
    for r in run.reports:
        parts.append(r.title or "")
        for t in r.trends or []:
            parts += [t.get("headline", ""), t.get("summary", "")]
        parts += [str(i) for i in r.insights or []]
        parts += [s.get("title", "") for s in r.suggestions or []]
    return "\n".join(parts).lower()


def run_summary(run: ResearchRun) -> dict:
    done = [r for r in run.reports if r.kind == "area" and r.status == "done"]
    insights = [i for r in done for i in r.insights or []]
    return {
        "run": run,
        "counts": [(RESEARCH_AREAS.get(r.area, r.area), len(r.trends or [])) for r in done],
        "insight": insights[0] if insights else "",
    }


@router.get("")
def list_runs(request: Request, q: str = "", area: str = "", page: str = "1", user: User = Depends(require_user), db: Session = Depends(get_db)):
    runs = db.query(ResearchRun).options(selectinload(ResearchRun.reports)).order_by(ResearchRun.started_at.desc(), ResearchRun.id.desc()).all()
    q = q.strip()
    if q:
        runs = [r for r in runs if q.lower() in _search_text(r)]
    # 구분 탭의 건수: 검색어를 적용한 목록에서 그 분야 보고서가 있는 조사 수
    counts = {code: sum(1 for r in runs if any(rep.kind == "area" and rep.area == code for rep in r.reports)) for code in RESEARCH_AREAS}
    total = len(runs)
    if area in RESEARCH_AREAS:
        runs = [r for r in runs if any(rep.kind == "area" and rep.area == area for rep in r.reports)]
    else:
        area = ""
    pg = paginate(runs, page)
    pg = replace(pg, items=[run_summary(r) for r in pg.items])
    tabs = ui.seg_tabs(request, "area", list(RESEARCH_AREAS.items()), counts, area, total)
    return render(request, "research/list.html", pg=pg, q=q, area=area, tabs=tabs)


@router.get("/runs/{run_id}")
def run_page(run_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="조사 실행을 찾을 수 없습니다.")
    suggest = next((r for r in run.reports if r.kind == "suggest" and r.status == "done"), None)
    suggestions = []
    for index, s in enumerate(suggest.suggestions if suggest else []):
        ids = [i for i in s.get("solution_ids") or [] if isinstance(i, int)]
        solutions = [x for x in (db.get(Solution, i) for i in ids) if x is not None]
        shown = {k: humanize_ids(db, s.get(k) or "") for k in ("title", "detail", "reason")}
        suggestions.append({**s, **shown, "index": index, "solutions": solutions})
    report_ids = [r.id for r in run.reports]  # 이 조사에서 등록된 솔루션 (유래 키로 찾는다)
    registered = {}
    if report_ids:
        for sol in db.scalars(select(Solution).where(Solution.origin_type == ontology.ORIGIN_RESEARCH, Solution.origin_id.in_(report_ids)).order_by(Solution.id.desc())):
            registered[f"{sol.origin_id}:{sol.origin_item}"] = sol  # 같은 항목이 둘이면 가장 먼저 등록한 것
    areas = []
    for r in run.reports:
        if r.kind != "area":
            continue
        trends = [{**_trend_view(t), "index": i} for i, t in enumerate(r.trends or [])]
        insights = [humanize_ids(db, i) for i in r.insights or []]
        areas.append({"report": r, "name": RESEARCH_AREAS.get(r.area, r.area), "trends": trends, "insights": insights})
    suggest_insights = [humanize_ids(db, i) for i in (suggest.insights if suggest else None) or []]
    all_trends = [t for a in areas if a["report"].status == "done" for t in a["trends"]]
    # 모든 동향이 같은 "본문 미확인" 이유뿐이면 줄마다 배지를 달지 않고 조사 전체에 안내 한 번 (서버가 웹 페이지를 열 수 없을 때는 늘 이렇다)
    unopened_all = bool(all_trends) and all(t.get("needs_check") and all(is_unopened(n) for n in t["needs_check"]) for t in all_trends)
    return render(request, "research/run.html", unopened_all=unopened_all, run=run, suggest=suggest, suggestions=suggestions, areas=areas,
                  suggest_insights=suggest_insights, src=SourceLookup(db, ui=True), registered=registered)


@router.post("/runs/{run_id}/candidate")
def register_candidate(
    run_id: int, source: str = Form(""), report_id: int = Form(...), index: int = Form(...),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    if source not in ("suggestion", "trend"):
        raise HTTPException(status_code=400, detail="알 수 없는 등록 종류입니다.")
    report = db.get(ResearchReport, report_id)
    wanted_kind = "suggest" if source == "suggestion" else "area"
    if report is None or report.run_id != run_id or report.kind != wanted_kind or report.status != "done":
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")
    items = report.suggestions if source == "suggestion" else report.trends
    if not 0 <= index < len(items or []):
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    item = items[index]
    item_key = f"{source}:{index}"
    already = db.scalar(select(Solution).where(Solution.origin_type == ontology.ORIGIN_RESEARCH, Solution.origin_id == report.id,
                                               Solution.origin_item == item_key).order_by(Solution.id))
    if already is not None:  # 같은 제안·동향을 두 번 등록하지 않는다 (지워졌으면 다시 등록할 수 있다)
        return notices.redirect(f"/solutions/{already.id}", "already_registered")
    lines: list[str] = []
    if source == "suggestion":
        name, summary = humanize_ids(db, item.get("title", "")), humanize_ids(db, item.get("reason", ""))
        lines += [x for x in (humanize_ids(db, item.get("detail", "")), f"근거: {summary}" if summary else "") if x]
        lines += [f"- {describe_source(db, p)['label']}" for p in item.get("sources") or [] if isinstance(p, str)]
    else:
        name, summary = humanize_ids(db, item.get("headline", "")), humanize_ids(db, item.get("summary", ""))
        lines += [x for x in (summary,) if x]
        for s in item.get("sources") or []:
            url = clean_url(s.get("url", ""))
            if url:
                note = "" if s.get("opened", True) else f" ({UNOPENED_LABEL})"
                lines.append(f"- {s.get('title') or url}: {url}{note}")
    day = report.run.started_at.date().isoformat()
    lines += ["", f"리서치({day})에서 등록"]
    solution = Solution(name=(name.strip() or "리서치 후보")[:NAME_MAX], summary=summary.strip(),
                        description="\n".join(lines), stage=ontology.STAGE_DEFAULT, created_by_id=user.id,
                        origin_type=ontology.ORIGIN_RESEARCH, origin_id=report.id, origin_item=item_key)
    db.add(solution)
    db.commit()
    mirror.sync(solution)
    return notices.redirect(f"/solutions/{solution.id}/edit", "candidate_registered")


@router.post("/runs/{run_id}/delete")
def delete_run(run_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db),
               mirror: KnowledgeMirror = Depends(get_mirror)):
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="조사 실행을 찾을 수 없습니다.")
    if run.status == "running":
        raise HTTPException(status_code=400, detail=RUNNING_DELETE)
    for report in list(run.reports):  # 사본 경로는 보고서 행에서 나오므로 지우기 전에 먼저 지운다
        mirror.remove(report)
    origin.clear_dangling(db, ontology.ORIGIN_RESEARCH, [r.id for r in run.reports])  # 같은 트랜잭션에서 유래의 번호를 비운다
    db.delete(run)
    db.commit()
    return notices.redirect("/research", "deleted")


@router.post("/run", dependencies=[Depends(require_admin), Depends(require_ai_on)])
def run_now(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        run = start_run(db, "manual", admin.id)
    except ValueError:
        return notices.redirect(ADMIN_PAGE, "no_topics")
    return notices.redirect(ADMIN_PAGE, "research_started" if run else "research_busy")


@router.get("/runs/{run_id}/status")
def run_status(run_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="조사 실행을 찾을 수 없습니다.")
    rows = []
    for r in run.reports:
        job = db.get(AIJob, r.job_id) if r.job_id else None
        position = queue_position(db, job) if job is not None else 0
        label = RESEARCH_AREAS.get(r.area, r.area) if r.kind == "area" else "발굴 방향 제안"
        rows.append({"report": r, "area": label, "job": job, "position": position})
    return render(request, "research/_run_status.html", run=run, rows=rows, show_note=request.query_params.get("note") != "0")


@router.post("/reports/{report_id}/retry", dependencies=[Depends(require_admin), Depends(require_ai_on)])
def retry_report(report_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    report = db.get(ResearchReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")
    if report.status == ontology.JOB_PENDING:  # 다른 요청이 먼저 다시 시도했다 (늦게 누른 쪽도 오류 없이 돌아간다).
        return RedirectResponse(ADMIN_PAGE, status_code=303)
    if report.status != "failed":
        raise HTTPException(status_code=400, detail="실패한 보고서만 다시 시도할 수 있습니다.")
    run = report.run
    if run.status != "running":
        other = db.query(ResearchRun.id).filter(ResearchRun.status == "running", ResearchRun.id != run.id).first()
        if other is not None:  # 조사는 한 번에 하나만 돈다.
            return notices.redirect(ADMIN_PAGE, "research_busy")
    rid, run_id = report.id, run.id
    job_kind = "research_area" if report.kind == "area" else "research_suggest"
    db.rollback()  # 쓰기를 새로 시작한다
    job = AIJob(kind=job_kind, input={"report_id": rid}, requester_id=admin.id, priority=10, status="pending")
    db.add(job)
    db.flush()
    claimed = db.execute(update(ResearchReport).where(ResearchReport.id == rid, ResearchReport.status == "failed")
                         .values(job_id=job.id, status="pending", error=""))
    if claimed.rowcount != 1:  # 다른 요청이 먼저 다시 시도했다.
        db.rollback()
        return RedirectResponse(ADMIN_PAGE, status_code=303)
    db.execute(update(ResearchRun).where(ResearchRun.id == run_id).values(status="running", finished_at=None))
    db.commit()
    return RedirectResponse(ADMIN_PAGE, status_code=303)

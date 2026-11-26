"""홈 화면(오늘 할 일): 발굴 흐름, 후속 조치, 올리기 현황, 영업기회 단계, 제안 건, 최근 활동, 최신 리서치. 질의 수는 기록 수와 무관하다."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app import follow_ups as fu_lib
from app import labels, money, ontology, opportunities as opps, transcribe, ui
from app.db import get_db
from app.main import render
from app import intake as intake_lib
from app.models import AIJob, Activity, Customer, CustomerNeed, FollowUp, Intake, IntakeFile, Opportunity, Proposal, ResearchRun, Solution, User
from app.security import require_user

router = APIRouter()
RECENT_LIMIT = 8  # 왼쪽 열이 오른쪽보다 길어지지 않게 (나머지는 "모두 보기")
OPEN_PROPOSAL_LIMIT = 5
FOLLOW_UP_DAYS = 14
LATEST_INSIGHTS, LATEST_SUGGESTIONS = 5, 3
INTAKE_LINK_LIMIT = 5
INTAKE_ANALYZING_LIMIT = 3
FOLLOW_UP_SHOWN = 8
STALE_SHOWN = 6  # 홈 "챙길 영업기회" 카드 (나머지는 목록의 멈춘 건 걸러 보기)
STALE_VIEWS = {"": "mine", "team": "team", "all": "all"}  # 주소 ?opp= -> 보기 (기본 내 건)
AI_PROBLEMS = ("not_installed", "auth", "limit", "network")


def today() -> date:
    return date.today()


def upcoming_follow_ups(db: Session, today: date, days: int = FOLLOW_UP_DAYS) -> list[dict]:
    """끝나지 않은 후속 조치 중 기한이 `today + days` 이내(지난 것 포함)인 것. 기한순. 줄마다 상대 기한 글(`rel`)도 담는다."""
    limit = today + timedelta(days=days)
    stmt = (
        select(FollowUp, Activity).join(Activity, FollowUp.activity_id == Activity.id)
        .where(FollowUp.done.is_(False), FollowUp.due.is_not(None), FollowUp.due <= limit)
        .order_by(FollowUp.due, Activity.id, FollowUp.position, FollowUp.id)
    )
    return [
        {"activity": a, "id": fu.id, "index": fu.position, "task": fu.task, "owner": fu.owner, "due": fu.due, "overdue": fu.due < today,
         "done": False, "rel": ui.relative_due(fu.due, today), "customers": [], "owner_user_id": fu.owner_user_id}
        for fu, a in db.execute(stmt)
    ]


def stage_flow(db: Session, today: date) -> dict:
    """발굴 흐름: 단계 4개(순서대로)의 솔루션 수, 막대 길이(가장 많은 단계 = 100%), 이번 주(7일) 새로 생겨 지금 그 단계에 있는 수. 보류는 따로. 질의 한 번."""
    since = datetime.combine(today - timedelta(days=7), datetime.min.time())
    rows = db.execute(select(Solution.stage, func.count(), func.coalesce(func.sum(case((Solution.created_at >= since, 1), else_=0)), 0))
                      .group_by(Solution.stage)).all()
    count = {stage: n for stage, n, _ in rows}
    fresh = {stage: int(w) for stage, _, w in rows}
    top = max((count.get(c, 0) for c in ontology.STAGE_FLOW), default=0)
    cells = [{"code": c, "name": ontology.STAGES[c], "count": count.get(c, 0), "week": fresh.get(c, 0),
              "pct": round(100 * count.get(c, 0) / top) if top else 0} for c in ontology.STAGE_FLOW]
    return {"cells": cells, "hold": count.get(ontology.STAGE_HOLD, 0), "total": sum(count.values())}


def opportunity_bars(db: Session) -> dict:
    """영업기회: 진행 중 단계 + 수주의 건수(막대 길이는 가장 많은 단계 = 100%)와 금액 합, 진행 중 합계(건수·금액·금액 없는 건수), 전체. 질의 한 번.
    금액 합은 늘 전체 기준이다 (챙길 영업기회 카드의 내 건·우리 팀 탭을 따르지 않는다)."""
    rows = db.execute(select(Opportunity.stage, func.count(), func.sum(Opportunity.amount), func.count(Opportunity.amount)).group_by(Opportunity.stage)).all()
    count = {r[0]: r[1] for r in rows}
    amount = {r[0]: r[2] or 0 for r in rows}
    missing = {r[0]: r[1] - r[3] for r in rows}
    codes = (*ontology.OPPORTUNITY_OPEN_STAGES, ontology.OPPORTUNITY_STAGE_WON)
    top = max((count.get(c, 0) for c in codes), default=0)
    bars = [{"code": c, "name": ontology.OPPORTUNITY_STAGES[c], "count": count.get(c, 0), "pct": round(100 * count.get(c, 0) / top) if top else 0,
             "amount": money.format_amount(amount.get(c) or None)} for c in codes]
    open_codes = ontology.OPPORTUNITY_OPEN_STAGES
    return {"bars": bars, "open": sum(count.get(c, 0) for c in open_codes), "total": sum(count.values()),
            "open_amount": money.format_amount(sum(amount.get(c, 0) for c in open_codes) or None), "open_missing": sum(missing.get(c, 0) for c in open_codes)}


def stale_opportunities(db: Session, user: User, now: date, view: str) -> dict:
    """홈 "챙길 영업기회": 진행 중이고 멈춘 영업기회. view = mine / team / all. 질의 수는 건수와 무관하다
    (진행 중 영업기회 한 번 + 담당·팀 미리 읽기 + 마지막 접촉). 날짜가 지난 사유가 있는 건 → 금액 큰 건 순."""
    rows = list(db.scalars(select(Opportunity).where(Opportunity.stage.in_(ontology.OPPORTUNITY_OPEN_STAGES))
                           .options(selectinload(Opportunity.customer), selectinload(Opportunity.owner).selectinload(User.team))))
    badges = opps.stale_view(db, rows, now)
    stale = [o for o in rows if badges[o.id]]
    mine = [o for o in stale if o.owner_user_id == user.id]
    team = [o for o in stale if user.team_id is not None and opps.team_id_of(o) == user.team_id]
    picked = {"mine": mine, "team": team, "all": stale}[view]
    picked.sort(key=lambda o: (not any(t == "bad" for t, _ in badges[o.id]), -(o.amount or 0), o.id))
    from app import opportunity_ai

    shown = picked[:STALE_SHOWN]
    ai = opportunity_ai.pending_counts(db, [o.id for o in shown]) if shown else {}
    items = [{"opp": o, "badges": badges[o.id], "amount": money.format_amount(o.amount), "ai": ai.get(o.id, 0)} for o in shown]
    return {"items": items, "counts": {"mine": len(mine), "team": len(team), "all": len(stale)}, "who": view, "more": max(0, len(picked) - STALE_SHOWN),
            "has_team": user.team_id is not None}


def ai_problem(db: Session) -> str | None:
    """가장 최근에 끝난 AI 작업이 환경 문제로 실패했으면 안내문을 돌려준다."""
    job = db.scalars(
        select(AIJob).where(AIJob.finished_at.is_not(None)).order_by(AIJob.finished_at.desc(), AIJob.id.desc())
    ).first()
    if job and job.status == "failed" and job.error_kind in AI_PROBLEMS:
        return labels.ERROR_KINDS[job.error_kind]
    return None


def open_proposals(db: Session) -> list[Proposal]:
    """아직 제안서를 끝내지 않은 제안 건, 최근 수정순."""
    stmt = select(Proposal).where(Proposal.status != ontology.PROPOSAL_STATUS_DECK).options(selectinload(Proposal.customer))
    return list(db.scalars(stmt.order_by(Proposal.updated_at.desc(), Proposal.id.desc()).limit(OPEN_PROPOSAL_LIMIT)))


def latest_research(db: Session) -> dict | None:
    """가장 최근에 마감된 조사 실행의 시사점(5개)과 발굴 방향 제안(3개). 없으면 None."""
    stmt = select(ResearchRun).where(ResearchRun.status.in_(("done", "partial")))
    run = db.scalars(stmt.order_by(ResearchRun.finished_at.desc(), ResearchRun.id.desc())).first()
    if run is None:
        return None
    done = [r for r in run.reports if r.status == "done"]
    insights = [i for r in done if r.kind == "area" for i in r.insights or []]
    insights += [i for r in done if r.kind == "suggest" for i in r.insights or []]
    suggestions = [s for r in done if r.kind == "suggest" for s in r.suggestions or []]
    return {"run": run, "insights": insights[:LATEST_INSIGHTS], "suggestions": suggestions[:LATEST_SUGGESTIONS]}


def intake_summary(db: Session, user: User) -> dict:
    """올리기 묶음 중 확인 대기(최대 5개 목록과 전체 수)와 정리 중(최대 3개 목록과 전체 수). 관리자도 내 것만 (남의 묶음은 올리기 목록에서 본다).
    각 묶음에는 파일 수(`files_n`)를 담는다. 질의 네 번 이하."""
    mine = Intake.created_by_id == user.id
    counts = dict(db.execute(select(Intake.status, func.count()).where(mine, Intake.status.in_(("ready", "analyzing"))).group_by(Intake.status)).all())

    def top(status: str, limit: int) -> list[Intake]:
        if not counts.get(status):
            return []
        return list(db.scalars(select(Intake).where(mine, Intake.status == status).order_by(Intake.id.desc()).limit(limit)))

    ready, analyzing = top("ready", INTAKE_LINK_LIMIT), top("analyzing", INTAKE_ANALYZING_LIMIT)
    ids = [i.id for i in (*ready, *analyzing)]
    files = dict(db.execute(select(IntakeFile.intake_id, func.count()).where(IntakeFile.intake_id.in_(ids)).group_by(IntakeFile.intake_id)).all()) if ids else {}
    for it in (*ready, *analyzing):
        it.files_n = files.get(it.id, 0)
    return {"ready": ready, "ready_count": counts.get("ready", 0), "analyzing": analyzing, "analyzing_count": counts.get("analyzing", 0)}


def recent_activities(db: Session) -> list[dict]:
    """최근 활동 기록 8건 (일자 최신순, 일자 없는 것은 뒤)과 각각의 고객 니즈·후속 조치 수. 질의 세 번."""
    rows = list(db.scalars(select(Activity).order_by(Activity.date.is_(None), Activity.date.desc(), Activity.id.desc()).limit(RECENT_LIMIT)))
    ids = [a.id for a in rows]
    needs = dict(db.execute(select(CustomerNeed.activity_id, func.count()).where(CustomerNeed.activity_id.in_(ids)).group_by(CustomerNeed.activity_id)).all()) if ids else {}
    fus = dict(db.execute(select(FollowUp.activity_id, func.count()).where(FollowUp.activity_id.in_(ids)).group_by(FollowUp.activity_id)).all()) if ids else {}
    return [{"a": a, "needs": needs.get(a.id, 0), "follow_ups": fus.get(a.id, 0)} for a in rows]


def my_work(db: Session, user: User, follow_ups: list[dict], stale: dict, ready_count: int, now: date) -> list[dict]:
    """홈 맨 위 "내 할 일" 띠 (UX 개선 2026-11-26): 지금 챙길 것의 건수와 바로 가기. 0 인 칸도 보인다 (할 일이 없다는 것도 정보다).
    질의 수는 기록 수와 무관하다 (과제 한 번, AI 제안함, 정렬 판단 두 번)."""
    from app import ai_inbox, alignment
    from app.models import Initiative

    mine = [i for i in db.scalars(select(Initiative).where(Initiative.owner_user_id == user.id, Initiative.status.in_(ontology.INITIATIVE_OPEN)))]
    late = sum(1 for i in mine if i.due and i.due < now)
    off = len(alignment.misaligned_ids(db, "initiative")) + len(alignment.misaligned_ids(db, "opportunity"))
    overdue = sum(1 for f in follow_ups if f["overdue"])
    return [
        {"key": "fu", "label": "기한 지난 후속 조치", "n": overdue, "href": "/follow-ups", "tone": "bad" if overdue else ""},
        {"key": "initiative", "label": "내 열린 과제", "n": len(mine), "sub": f"기한 지남 {late}" if late else "", "href": "/initiatives?who=mine", "tone": "bad" if late else ""},
        {"key": "stale", "label": "멈춘 내 영업기회", "n": stale["counts"]["mine"], "href": "/opportunities?stale=1&who=mine", "tone": "warn" if stale["counts"]["mine"] else ""},
        {"key": "ai", "label": "반영 대기 AI 제안", "n": ai_inbox.pending_count(db), "href": "/ai/inbox", "tone": "brand"},
        {"key": "align", "label": "방향 밖 기록", "n": off, "href": "/initiatives?align=off", "tone": "warn" if off else ""},
        {"key": "intake", "label": "올리기 정리 확인", "n": ready_count, "href": "/intake", "tone": "brand" if ready_count else ""},
    ]


def is_fresh_install(db: Session) -> bool:
    """솔루션·활동 기록·고객이 하나도 없으면 막 설치한 상태다 (홈에 "처음 시작하기" 안내를 보인다)."""
    return not any(db.scalar(select(func.count()).select_from(m)) for m in (Solution, Activity, Customer))


def summary_sentence(overdue: int, ready: int) -> str:
    """제목 줄 한 문장: 실제 숫자로 만들고 0 인 부분은 뺀다. 모두 0 이면 그렇다고 말한다."""
    parts = []
    if overdue:
        parts.append(f"기한이 지난 후속 조치 {overdue}건")
    if ready:
        parts.append(f"확인 대기 {ready}건")
    return ", ".join(parts) if parts else "지금 처리할 급한 일은 없습니다"


@router.get("/")
def home(request: Request, fu_toggled: str = "", fu: str = "", opp: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    now = today()
    follow_ups = upcoming_follow_ups(db, now)
    mine_only = fu == "mine"  # 후속 조치 카드의 "내 할 일" 보기: 주소로만 기억한다. 내 것 = 담당 칸이 내 이름과 정확히 일치해 연결된 것 (owner_user_id)
    mine_count = sum(1 for f in follow_ups if f["owner_user_id"] == user.id)
    all_count = len(follow_ups)
    overdue = sum(1 for f in follow_ups if f["overdue"])  # 제목 줄 문장은 보기와 상관없이 전체 기준
    if mine_only:
        follow_ups = [f for f in follow_ups if f["owner_user_id"] == user.id]
    fu_tabs = [ui.SegTab("", f"{FOLLOW_UP_DAYS}일 이내", all_count, "/", not mine_only), ui.SegTab("mine", "내 할 일", mine_count, "/?fu=mine", mine_only)]
    intake = intake_summary(db, user)
    view = STALE_VIEWS.get(opp, "mine")
    if view == "team" and user.team_id is None:
        view = "mine"
    stale = stale_opportunities(db, user, now, view)
    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in ("opp", "notice", "nt", "fu_toggled")]
    from urllib.parse import urlencode

    def stale_href(code: str) -> str:
        pairs = keep + ([("opp", code)] if code else [])
        return "/" + (f"?{urlencode(pairs)}" if pairs else "")

    stale_tabs = [ui.SegTab("", "내 건", stale["counts"]["mine"], stale_href(""), view == "mine")]
    if stale["has_team"]:
        stale_tabs.append(ui.SegTab("team", "우리 팀", stale["counts"]["team"], stale_href("team"), view == "team"))
    stale_tabs.append(ui.SegTab("all", "전체", stale["counts"]["all"], stale_href("all"), view == "all"))
    stale_list = "/opportunities?stale=1" + {"mine": "&who=mine", "team": "&who=team", "all": ""}[view]
    flow, opp = stage_flow(db, now), opportunity_bars(db)
    recent = recent_activities(db)
    fresh_install = is_fresh_install(db)
    return render(
        request, "home.html",
        today=now, date_line=ui.today_line(now), summary=summary_sentence(overdue, intake["ready_count"]), fresh_install=fresh_install,
        recent=recent, follow_ups=follow_ups[:FOLLOW_UP_SHOWN], fu_tabs=fu_tabs, mine_only=mine_only,
        follow_up_more=max(0, len(follow_ups) - FOLLOW_UP_SHOWN),
        intake=intake, max_files=intake_lib.MAX_FILES,
        max_mb=request.app.state.settings.max_upload_mb, max_note=intake_lib.MAX_NOTE, max_paste=intake_lib.MAX_PASTE,
        transcriber_ready=transcribe.get(request.app.state.settings).available(),
        flow=flow, opp=opp, stale=stale, stale_tabs=stale_tabs, stale_list=stale_list, research=latest_research(db), proposals=open_proposals(db), ai_problem=ai_problem(db),
        toggled=fu_lib.toggled_notice(db, fu_toggled) if fu_toggled else None,
        work=my_work(db, user, upcoming_follow_ups(db, now) if mine_only else follow_ups, stale_opportunities(db, user, now, "mine") if view != "mine" else stale,
                     intake["ready_count"], now),
    )

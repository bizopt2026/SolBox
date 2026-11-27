"""영업기회 화면: 목록·상세·등록·수정·삭제, 단계 변경, 프로젝트로 전환. 행위 조건은 `app/opportunities.py` 한 곳이다."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from urllib.parse import urlencode
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.idparse import parse_id
from app import business_types as bts, money, ontology, opportunities as opps, opportunity_ai as oai, relations, teams, ui
from app import guards, notices
from app import alignment
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import BM, Customer, Opportunity, Project, Solution, User
from app.records import pick
from app.routers._common import (
    list_filter,
    get_deletable_or_404, get_or_404, newest_first, duplicate_warning, paginate, resync_objects, row_info, search_by_name, sort_rows,
)
from app.ai.gate import require_ai_on
from app.security import can_delete, require_user

router = APIRouter(prefix="/opportunities")


def _form_page(request: Request, db: Session, form: dict, opp: Opportunity | None = None, error: str | None = None, conflict=None, duplicate=None):
    cid = opps.parse_id(form.get("customer_id"))
    customer = db.get(Customer, cid) if cid is not None else None
    needs = [SimpleNamespace(id=n.id, name=opps.need_label(n)) for n in opps.bundlable_needs(db, cid, opp)]
    return render(
        request, "opportunities/form.html", status_code=409 if conflict else 400 if error else 200,
        form=form, opp=opp, error=error, conflict=conflict, duplicate=duplicate, customer=customer, customers=search_by_name(db, Customer),
        bms=search_by_name(db, BM), solutions=search_by_name(db, Solution), needs=needs, contact_options=opps.contact_choices(db, cid),
        unlinked_needs=opps.unlinked_same_name_needs(db, customer),
        owners=teams.grouped_users(db, keep=opp.owner if opp is not None else None),
    )


def _blank_form(customer_id: str = "", owner_id: int | None = None) -> dict:
    return {"name": "", "customer_id": customer_id, "bm_id": "", "memo": "", "need_ids": [], "solution_ids": [], "contact_ids": [], "business_types": [],
            **opps.progress_form(None, owner_id)}


class ProgressForm:
    """폼의 진행 칸 (영업 A). `present` 는 숨은 칸 progress=1 이 왔는지: 없는 옛 화면·API 는 진행 칸을 건드리지 않는다."""

    def __init__(self, progress: str = Form(""), amount: str = Form(""), expected_close: str = Form(""), owner_user_id: str = Form(""),
                 next_step: str = Form(""), next_step_due: str = Form("")):
        self.present = bool(progress)
        self.typed = {"amount": amount.strip(), "expected_close": expected_close.strip(), "owner_user_id": owner_user_id.strip(),
                      "next_step": next_step.strip(), "next_step_due": next_step_due.strip()}

    def parse(self, db: Session, current_owner_id: int | None = None) -> tuple[dict, str | None]:
        t = self.typed
        return opps.parse_progress(db, t["amount"], t["expected_close"], t["owner_user_id"], t["next_step"], t["next_step_due"], current_owner_id)


def _parse(db: Session, name: str, customer_id: str, bm_id: str, memo: str) -> tuple[dict, str | None]:
    """폼 값을 다듬고 오류 문구를 돌려준다. 고객은 필수이고 BM 은 있으면 등록된 것이어야 한다."""
    values = {"name": name.strip(), "customer_id": customer_id.strip(), "bm_id": bm_id.strip(), "memo": memo.strip()}
    if not values["name"]:
        return values, opps.NAME_REQUIRED
    cid = opps.parse_id(values["customer_id"])
    if cid is None or db.get(Customer, cid) is None:
        return values, opps.CUSTOMER_REQUIRED
    values["customer_id"] = cid
    if values["bm_id"]:
        bid = opps.parse_id(values["bm_id"])
        if bid is None or db.get(BM, bid) is None:
            return values, opps.BAD_BM
        values["bm_id"] = bid
    else:
        values["bm_id"] = None
    return values, None


def _submitted(values: dict, need_ids, solution_ids, bt: "bts.BTForm | None" = None, opp: Opportunity | None = None, contact_ids=None) -> dict:
    """오류로 폼을 다시 그릴 때의 값. 사업방식 칸이 안 왔으면 저장된 값(고치기)이나 빈 값(만들기)."""
    shown = ontology.clean_business_types(bt.codes) if bt is not None and bt.present else (opp.business_type_codes if opp is not None else [])
    return {**values, "business_types": shown, "need_ids": [n for i in need_ids if (n := parse_id(i)) is not None],
            "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None],
            "contact_ids": [n for i in (contact_ids or []) if (n := parse_id(i)) is not None] if contact_ids is not None else [c.id for c in opp.contacts] if opp is not None else []}


def _today():
    from app.routers.home import today

    return today()


def _detail_page(request: Request, db: Session, user: User, opp: Opportunity, error: str | None = None, status_code: int | None = None, **extra):
    now = _today()
    touch = opps.last_touches(db, [opp])[opp.id]
    return render(
        request, "opportunities/detail.html", status_code=status_code or (400 if error else 200), **extra,
        opp=opp, error=error, **relations.page_related(db, opp, user),
        can_delete=can_delete(user, opp), can_convert=opps.can_convert(opp), meta=ui.stamp_info(db, opp),
        close_reason=opps.close_reason_label(opp), today=now, money=money,
        stale=opps.stale_badges(opp, now, touch), decision_gaps=opps.decision_gaps(db, opp, now),
        history=opps.history_rows(db, opp, now), history_shown=opps.HISTORY_SHOWN, open_fus=opps.open_follow_ups(opp),
        owner_team=teams.team_name(opp.owner), checks=oai.check_view(opp), ai_job=oai.pending_job(db, opp.id), insight=(insight := oai.latest_insight(db, opp.id)), ai_failed=oai.failed_review(db, opp.id, insight),
        ai_rows=oai.suggestion_rows(db, opp, insight, now),
        step_notes={s: f"확인 {d}/{t}" for s, (d, t) in oai.stage_counts(opp).items()},
    )


@router.get("")
def list_opportunities(
    request: Request, q: str = "", stage: str = "", page: str = "1", who: str = "", stale: str = "", ai: str = "", align: str = "", view: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    found = search_by_name(db, Opportunity, q, Customer.name, joins=(Opportunity.customer,),
                           loaders=(selectinload(Opportunity.customer), selectinload(Opportunity.owner).selectinload(User.team)))
    found.sort(key=lambda o: (o.updated_at, o.id), reverse=True)  # 기본(최근 수정순)
    found, flt = list_filter(db, request, "opportunity", sort_rows(request, "opportunity", found, db=db))
    if ui.current_sort(request) == ontology.LIST_SORTS["opportunity"][0][0]:
        # 기본 순서는 진행 중(발굴·제안·협상)이 위, 그 안에서 최근 수정순 (닫힌 수주·실패·보류가 맨 위에 오지 않게. UX 2순위 2026-11-27)
        found.sort(key=lambda o: o.stage not in ontology.OPPORTUNITY_OPEN_STAGES)
    found, type_tabs, type_now = bts.type_tabs(request, found, lambda o: o.business_type_codes)
    now = _today()
    stale_all = opps.stale_view(db, found, now)  # 멈춘 사유 (걸러 보기 칩 건수와 줄 배지)
    stale_on = stale == "1"
    if stale_on:
        found = [o for o in found if stale_all[o.id]]
    ai_all = oai.pending_counts(db, [o.id for o in found])  # 반영 대기 AI 제안 수 (영업 B)
    ai_on = ai == "1"
    if ai_on:
        found = [o for o in found if ai_all.get(o.id)]
    off_ids = alignment.misaligned_ids(db, "opportunity")  # 정렬 점검의 최신 판단이 "방향 밖" (AI 코워크 ③)
    align_on = align == "off"
    align_n = sum(1 for o in found if o.id in off_ids)
    if align_on:
        found = [o for o in found if o.id in off_ids]
    who_n, who_total = opps.who_counts(found, user), len(found)
    found, who = opps.who_filter(found, user, who)
    who_choices = [c for c in ontology.OPPORTUNITY_WHO if c[0] != "team" or user.team_id is not None]
    who_tabs = ui.seg_tabs(request, "who", who_choices, who_n, who, who_total)
    board_rows = list(found)  # 보드는 단계 탭을 쓰지 않는다 (영업 C)
    counts = Counter(o.stage for o in found)  # 구분 탭의 건수: 검색어·걸러 보기를 적용한 목록 기준
    counts[ontology.OPPORTUNITY_FILTER_OPEN] = sum(counts[c] for c in ontology.OPPORTUNITY_OPEN_STAGES)
    total = len(found)
    if stage == ontology.OPPORTUNITY_FILTER_OPEN:
        found = [o for o in found if o.stage in ontology.OPPORTUNITY_OPEN_STAGES]
    elif stage in ontology.OPPORTUNITY_STAGES:
        found = [o for o in found if o.stage == stage]
    else:
        stage = ""
    open_rows = [o for o in found if o.stage in ontology.OPPORTUNITY_OPEN_STAGES]
    open_sum = {"n": len(open_rows), "amount": sum(o.amount or 0 for o in open_rows), "unpriced": sum(1 for o in open_rows if not o.amount)}  # 보이는 목록 중 진행 중 건의 합계 (표 위 한 줄)
    pg = paginate(found, page)
    choices = [(ontology.OPPORTUNITY_FILTER_OPEN, "진행 중"), *ontology.OPPORTUNITY_STAGES.items()]
    tabs = ui.seg_tabs(request, "stage", choices, counts, stage, total)
    stale_n = sum(1 for o in found if stale_all.get(o.id))
    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in ("stale", "page", "notice", "nt")]
    from urllib.parse import urlencode

    stale_href = "/opportunities?" + urlencode(keep + ([] if stale_on else [("stale", "1")]))
    keep_ai = [(k, v) for k, v in request.query_params.multi_items() if k not in ("ai", "page", "notice", "nt")]
    ai_href = ("/opportunities?" + urlencode(keep_ai + ([] if ai_on else [("ai", "1")]))).rstrip("?")
    ai_n = sum(1 for o in found if ai_all.get(o.id))
    keep_al = [(k, v) for k, v in request.query_params.multi_items() if k not in ("align", "page", "notice", "nt")]
    align_href = ("/opportunities?" + urlencode(keep_al + ([] if align_on else [("align", "off")]))).rstrip("?")
    board = view == "board"
    view_qs = urlencode([(k, v) for k, v in request.query_params.multi_items() if k in opps.BOARD_QS_KEYS])
    common = dict(view_qs=view_qs, board=board)
    if board:
        return render(request, "opportunities/board.html", pg=pg, q=q.strip(), flt=flt, type_tabs=type_tabs, type_now=type_now,
                      who_tabs=who_tabs, who=who, stale_on=stale_on, stale_n=stale_n, stale_href=stale_href.rstrip("?"),
                      stale=stale_all, today=now, money=money, ai_counts=ai_all, ai_on=ai_on, ai_href=ai_href, ai_n=ai_n,
                      columns=opps.board_columns(board_rows, now), closed=opps.recent_closes(board_rows, now, db),
                      hold_n=sum(1 for o in board_rows if o.stage == "hold"), **common)
    return render(request, "opportunities/list.html", **common, pg=pg, q=q.strip(), stage=stage, flt=flt, tabs=tabs, type_tabs=type_tabs, type_now=type_now,
                  info=row_info(db, "opportunity", pg.items), who_tabs=who_tabs, who=who, stale_on=stale_on, stale_n=stale_n, stale_href=stale_href.rstrip("?"),
                  stale=stale_all, today=now, money=money, ai_counts=ai_all, ai_on=ai_on, ai_href=ai_href, ai_n=ai_n,
                  align_on=align_on, align_n=align_n, align_href=align_href, off_ids=off_ids, open_sum=open_sum)


@router.get("/analysis")
def analysis(request: Request, period: str = "", team: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    """영업기회 분석 (영업 C): 기간·팀을 골라 수주율·단계 체류·전환·월별·사유·팀별 비교. 숫자 정의는 `app/pipeline_stats.py` 한 곳."""
    from app import pipeline_stats as ps

    team_list = teams.ordered(db)
    team_key = ps.TEAM_NONE if team == ps.TEAM_NONE else next((t.id for t in team_list if str(t.id) == team), None)  # 없는 팀은 전체
    now = _today()
    stats = ps.compute(db, period, team_key, now)

    def href(p: str, t) -> str:
        pairs = ([("period", p)] if p != ps.PERIOD_DEFAULT else []) + ([("team", str(t))] if t is not None else [])
        return "/opportunities/analysis" + (("?" + urlencode(pairs)) if pairs else "")

    period_tabs = [ui.SegTab(code, label, None, href(code, team_key), code == stats["period"]) for code, label in ps.PERIODS.items()]
    team_tabs = [ui.SegTab("", "전체", None, href(stats["period"], None), team_key is None)]
    team_tabs += [ui.SegTab(str(t.id), t.name, None, href(stats["period"], t.id), team_key == t.id) for t in team_list]
    team_tabs.append(ui.SegTab(ps.TEAM_NONE, ps.NO_TEAM, None, href(stats["period"], ps.TEAM_NONE), team_key == ps.TEAM_NONE))
    from app.ai import pipeline_tasks

    team_raw = "" if team_key is None else str(team_key)
    ai_job, ai_done = pipeline_tasks.find_jobs(db, stats["period"], team_raw)
    team_name = "전체" if team_key is None else (ps.NO_TEAM if team_key == ps.TEAM_NONE else next(t.name for t in team_list if t.id == team_key))
    return render(request, "opportunities/analysis.html", stats=stats, chart=ps.month_chart(stats["months"]), period_tabs=period_tabs,
                  team_tabs=team_tabs, team_key=team_key, team_name=team_name, money=money, generated=datetime.now(), today=now,
                  team_raw=team_raw, ai_job=ai_job, ai_done=ai_done)


def _analysis_url(period: str, team_raw: str) -> str:
    from app import pipeline_stats as ps

    pairs = ([("period", period)] if period != ps.PERIOD_DEFAULT else []) + ([("team", team_raw)] if team_raw else [])
    return "/opportunities/analysis" + (("?" + urlencode(pairs)) if pairs else "")


def _analysis_params(db: Session, period: str, team: str) -> tuple[str, str]:
    from app import pipeline_stats as ps

    period = period if period in ps.PERIODS else ps.PERIOD_DEFAULT
    if team == ps.TEAM_NONE:
        return period, team
    return period, team if team.isdigit() and any(str(t.id) == team for t in teams.ordered(db)) else ""


@router.post("/analysis/ai", dependencies=[Depends(require_ai_on)])
def start_insight(period: str = Form(""), team: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db)):
    """[AI 해석 받기] (영업 C): 지금 고른 기간·팀의 숫자를 AI 가 읽고 해석을 쓴다. 같은 기간·팀 작업이 있으면 그것을 기다린다."""
    from app.ai import pipeline_tasks
    from app.ai.queue import enqueue

    period, team_raw = _analysis_params(db, period, team)
    url = _analysis_url(period, team_raw)
    running, _ = pipeline_tasks.find_jobs(db, period, team_raw)
    if running is not None:
        return notices.redirect(url, "ai_insight_waiting")
    enqueue(db, pipeline_tasks.JOB_KIND, {"period": period, "team": team_raw}, user.id, 0)
    return notices.redirect(url, "ai_insight_started")


@router.get("/analysis/ai/progress")
def insight_progress(request: Request, period: str = "", team: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    from app.ai import pipeline_tasks

    period, team_raw = _analysis_params(db, period, team)
    running, _ = pipeline_tasks.find_jobs(db, period, team_raw)
    if running is None:
        return Response("끝났습니다.", headers={"HX-Refresh": "true"})
    return render(request, "opportunities/_insight_job.html", job=running, period=period, team_raw=team_raw)


@router.get("/new")
def new_form(
    request: Request, customer_id: str = "", need_id: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    cid = opps.parse_id(customer_id)
    form = _blank_form(str(cid) if cid is not None and db.get(Customer, cid) is not None else "", user.id)  # 없는 고객 번호는 무시한다
    nid = opps.parse_id(need_id)
    need = next((n for n in opps.bundlable_needs(db, opps.parse_id(customer_id)) if n.id == nid), None) if nid is not None else None
    if need is not None:
        form["need_ids"] = [nid]  # 고객 니즈에서 만들기: 그 니즈를 체크해 둔다 (그 고객의, 아직 묶이지 않은 니즈일 때만)
        # 이름 칸도 비워 두지 않는다: 니즈 첫 줄을 고쳐 쓰면 된다. 고객 이름은 화면마다 따로 붙으므로 넣지 않는다 (베타 점검 1·4차 2026-11-27)
        form["name"] = ui.first_line(need.content, 40)
    return _form_page(request, db, form)


@router.post("")
def create(
    request: Request,
    name: str = Form(""), customer_id: str = Form(""), bm_id: str = Form(""), memo: str = Form(""),
    need_ids: list[str] = Form([]), solution_ids: list[str] = Form([]), confirm_dup: str = Form(""),
    contact_ids: list[str] = Form([]), contact_field: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once), bt: bts.BTForm = Depends(bts.bt_form),
    pf: ProgressForm = Depends(),
):
    values, error = _parse(db, name, customer_id, bm_id, memo)
    form = {**_submitted(values, need_ids, solution_ids, bt, contact_ids=contact_ids), **(pf.typed if pf.present else opps.progress_form(None, user.id))}
    error = error or (bts.codes_problem(bt.codes) if bt.present else None)
    progress, problem = pf.parse(db) if pf.present else ({"owner_user_id": user.id}, None)
    error = error or problem
    if error:
        return _form_page(request, db, form, error=error)
    if dup := duplicate_warning(db, Opportunity, name, confirm_dup):
        return _form_page(request, db, form, duplicate=dup)
    opp = Opportunity(**values, **progress, business_types=",".join(ontology.clean_business_types(bt.codes)) if bt.present else "", created_by_id=user.id)
    db.add(opp)
    db.flush()
    if error := opps.apply_needs(db, opp, need_ids):
        db.rollback()
        return _form_page(request, db, form, error=error)
    opp.solutions = pick(db, Solution, solution_ids)
    opps.apply_contacts(db, opp, contact_ids)
    db.commit()
    db.refresh(opp)
    mirror.sync(opp)
    resync_objects(db, mirror, opps.dependents(db, opp))
    alignment.request(db, opp, user.id, request)  # 합의된 방향이 있으면 정렬 점검을 뒤에 넣는다 (AI 코워크 ③)
    return once.done(notices.redirect(f"/opportunities/{opp.id}", "created"))


@router.get("/{opportunity_id}")
def detail(opportunity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, Opportunity, opportunity_id))


@router.get("/{opportunity_id}/edit")
def edit_form(opportunity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    opp = get_or_404(db, Opportunity, opportunity_id)
    form = {"name": opp.name, "customer_id": opp.customer_id, "bm_id": opp.bm_id or "", "memo": opp.memo, "business_types": opp.business_type_codes,
            "need_ids": [n.id for n in opps.needs_of(db, opp)], "solution_ids": [s.id for s in opp.solutions], "contact_ids": [c.id for c in opp.contacts], **opps.progress_form(opp)}
    return _form_page(request, db, form, opp)


@router.post("/{opportunity_id}")
def update(
    opportunity_id: int, request: Request,
    name: str = Form(""), customer_id: str = Form(""), bm_id: str = Form(""), memo: str = Form(""),
    need_ids: list[str] = Form([]), solution_ids: list[str] = Form([]), confirm_dup: str = Form(""),
    contact_ids: list[str] = Form([]), contact_field: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), rev: Rev = Depends(rev_form), bt: bts.BTForm = Depends(bts.bt_form),
    pf: ProgressForm = Depends(),
):
    opp = get_or_404(db, Opportunity, opportunity_id)
    values, error = _parse(db, name, customer_id, bm_id, memo)
    form = {**_submitted(values, need_ids, solution_ids, bt, opp, contact_ids if contact_field else None), **(pf.typed if pf.present else opps.progress_form(opp))}
    typed = {**values, "business_types": ontology.business_types_text(bt.codes)} if bt.present else dict(values)
    stored = {**{f: getattr(opp, f) for f in values}, "business_types": ontology.business_types_text(opp.business_types)}
    if pf.present:  # 진행 칸도 견준다 (담당은 이름표가 없는 번호 칸이라 diff_fields 가 건너뛴다)
        typed.update(pf.typed)
        stored.update(opps.progress_form(opp))
    if conflict := guards.check_rev(opp, rev, values=typed, stored=stored):
        return _form_page(request, db, form, opp, conflict=conflict)
    error = error or (bts.codes_problem(bt.codes) if bt.present else None)
    progress, problem = pf.parse(db, opp.owner_user_id) if pf.present else ({}, None)
    error = error or problem
    if error is None:
        error = opps.customer_change_problem(db, opp, values["customer_id"])
    if error:
        return _form_page(request, db, form, opp, error)
    if dup := duplicate_warning(db, Opportunity, name, confirm_dup, exclude=opp):
        return _form_page(request, db, form, opp, duplicate=dup)
    before = opps.dependents(db, opp)
    needs_before = {n.id for n in opps.needs_of(db, opp)}
    for field, value in {**values, **progress}.items():
        setattr(opp, field, value)
    if bt.present:
        opp.business_types = ",".join(ontology.clean_business_types(bt.codes))
    db.flush()
    if error := opps.apply_needs(db, opp, need_ids):
        db.rollback()
        return _form_page(request, db, form, opp, error)
    opp.solutions = pick(db, Solution, solution_ids)
    contacts_before = {c.id for c in opp.contacts}
    if contact_field:  # 숨은 칸: 담당자 칸이 있는 폼만 담당자를 바꾼다 (빈 선택 = 모두 풀기)
        opps.apply_contacts(db, opp, contact_ids)
    else:
        opps.drop_foreign_contacts(opp)
    if {n.id for n in opps.needs_of(db, opp)} != needs_before or {c.id for c in opp.contacts} != contacts_before:
        guards.touch(opp, user)  # 묶인 니즈만 바뀐 저장도 고친 표시를 올린다 (니즈 줄은 코어 UPDATE 라 기록 자신은 그대로이므로)
    db.commit()
    db.refresh(opp)
    mirror.sync(opp)
    resync_objects(db, mirror, [*before, *opps.dependents(db, opp)])
    alignment.request(db, opp, user.id, request)  # 합의된 방향이 있으면 정렬 점검을 뒤에 넣는다 (AI 코워크 ③)
    return notices.redirect(f"/opportunities/{opp.id}", "saved")


@router.post("/{opportunity_id}/stage")
def change_stage(
    opportunity_id: int, request: Request, stage: str = Form(""), back: str = Form(""), qs: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    opp = get_or_404(db, Opportunity, opportunity_id)
    if error := opps.stage_problem(stage):
        raise HTTPException(status_code=400, detail=error)
    if opp.stage != stage and opps.needs_reason(stage):  # 수주·실패는 사유 화면을 거친다 (스크립트 없이도 같은 길)
        from urllib.parse import quote

        tail = f"&back=board&qs={quote(qs)}" if back == "board" else ""
        return RedirectResponse(f"/opportunities/{opp.id}/close?to={stage}{tail}", status_code=303)
    code = f"opportunity_stage.{stage}"
    if opp.stage != stage:  # 어디서 어디로든 바꿀 수 있다
        code = oai.leftovers_notice(opp, opp.stage, stage) or code  # 앞으로 넘기면 떠난 단계의 남은 확인 항목을 알린다 (막지 않는다)
        opps.apply_stage(opp, stage)
        db.commit()
        db.refresh(opp)
        mirror.sync(opp)
        resync_objects(db, mirror, [opp.customer, opp.bm])
        alignment.request(db, opp, user.id, request)  # 단계가 바뀌면 정렬 점검 (AI 코워크 ③)
    return notices.redirect(opps.back_url(back, qs, opp.id), code)


@router.post("/{opportunity_id}/ai/review", dependencies=[Depends(require_ai_on)])
def start_review(opportunity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """[다시 분석] (영업 B): 사람이 누른 분석은 대기열 앞쪽(우선순위 0). 같은 영업기회의 작업이 이미 있으면 그것을 기다린다."""
    opp = get_or_404(db, Opportunity, opportunity_id)
    if opp.stage not in ontology.OPPORTUNITY_OPEN_STAGES:
        return notices.redirect(f"/opportunities/{opp.id}", "ai_not_open")
    if oai.pending_job(db, opp.id) is not None:
        return notices.redirect(f"/opportunities/{opp.id}", "ai_review_waiting")
    if not oai.has_activities(db, opp):
        return notices.redirect(f"/opportunities/{opp.id}", "ai_no_activity")
    from app.ai.queue import enqueue

    enqueue(db, oai.REVIEW_KIND, {"opportunity_id": opp.id}, user.id, 0)
    return notices.redirect(f"/opportunities/{opp.id}", "ai_review_started")


@router.post("/{opportunity_id}/ai/{insight_id}/{sid}/{what}")
def decide_suggestion(
    opportunity_id: int, insight_id: int, sid: int, what: str, back: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    """AI 제안 하나 [반영] (`apply`) 또는 [무시] (`dismiss`). 반영은 이력에 'AI 제안'과 근거를 남긴다."""
    if what not in ("apply", "dismiss"):
        raise HTTPException(status_code=404, detail="없는 동작입니다.")
    opp = get_or_404(db, Opportunity, opportunity_id)
    code = oai.decide(db, opp, insight_id, sid, user.id, what == "apply")
    db.commit()
    db.info.pop("change_source", None)
    db.info.pop("change_evidence", None)
    if code != "ai_stale" and what == "apply":
        db.refresh(opp)
        mirror.sync(opp)
        resync_objects(db, mirror, [opp.customer, opp.bm])
    from app.routers.ai_inbox import back_target

    return notices.redirect(back_target(back, f"/opportunities/{opp.id}"), code)


@router.get("/{opportunity_id}/ai/progress")
def review_progress(opportunity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """분석 진행 상자 조각: 끝나면 쪽을 새로 고쳐(HX-Refresh) 제안·브리핑을 보인다."""
    opp = db.get(Opportunity, opportunity_id)
    if opp is None:
        return Response("", headers={"HX-Redirect": "/opportunities"})
    job = oai.pending_job(db, opp.id)
    if job is not None:
        return render(request, "opportunities/_ai_job.html", opp=opp, job=job)
    return Response("분석이 끝났습니다.", headers={"HX-Refresh": "true"})


@router.post("/{opportunity_id}/checks")
def toggle_check(
    opportunity_id: int, request: Request, item: str = Form(""), checked: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    """확인 항목 켜기·끄기 (영업 B). 알림 없이 상세의 카드로 돌아간다. HTMX 면 카드 조각만."""
    opp = get_or_404(db, Opportunity, opportunity_id)
    from sqlalchemy.exc import IntegrityError

    try:
        changed = oai.set_check(db, opp, item, checked == "1", user.id)
        if changed:
            db.commit()
    except ValueError:
        raise HTTPException(status_code=400, detail="알 수 없는 확인 항목입니다.") from None
    except IntegrityError:  # 다른 탭이 같은 항목을 방금 켰다: 이미 켜져 있으니 그대로 보인다
        db.rollback()
        changed = False
    if changed:
        db.refresh(opp)
        mirror.sync(opp)
    if request.headers.get("HX-Request"):
        return render(request, "opportunities/_checks.html", opp=opp, checks=oai.check_view(opp))
    return RedirectResponse(f"/opportunities/{opp.id}#checks", status_code=303)


def _close_page(request: Request, opp: Opportunity, stage: str, reason: str = "", note: str = "", error: str | None = None, back: str = "", qs: str = "", conflict=None):
    back = "board" if back == "board" else ""
    return render(
        request, "opportunities/close.html", status_code=409 if conflict else (400 if error else 200),
        opp=opp, stage=stage, reasons=ontology.close_reasons(stage), reason=reason, note=note, error=error, note_max=opps.NOTE_MAX, conflict=conflict,
        back=back, qs=qs if back else "", cancel_url=opps.back_url(back, qs, opp.id),
    )


@router.get("/{opportunity_id}/close")
def close_form(opportunity_id: int, request: Request, to: str = "", back: str = "", qs: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    opp = get_or_404(db, Opportunity, opportunity_id)
    if not opps.needs_reason(to):
        return RedirectResponse(f"/opportunities/{opp.id}", status_code=303)
    same = opp.stage == to
    return _close_page(request, opp, to, opp.close_reason if same else "", opp.close_note if same else "", back=back, qs=qs)


@router.post("/{opportunity_id}/close")
def close(
    opportunity_id: int, request: Request, stage: str = Form(""), reason: str = Form(""), note: str = Form(""),
    back: str = Form(""), qs: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    rev: Rev = Depends(rev_form),
):
    """수주·실패로 바꾸면서 사유(필수)와 메모를 한 번에 저장한다. 사유 화면을 연 뒤 다른 곳에서 바뀌었으면 충돌 화면 (베타 점검 2026-11-27:
    두 탭에서 수주·실패를 따로 열면 늦은 쪽이 알림 없이 덮어썼다)."""
    opp = get_or_404(db, Opportunity, opportunity_id)
    if conflict := guards.check_rev(opp, rev):
        return _close_page(request, opp, stage, reason, note, back=back, qs=qs, conflict=conflict)
    if error := opps.close_problem(stage, reason):
        if not opps.needs_reason(stage):
            raise HTTPException(status_code=400, detail=error)
        return _close_page(request, opp, stage, reason, note, error, back=back, qs=qs)
    opps.apply_stage(opp, stage, reason, note)
    db.commit()
    db.refresh(opp)
    mirror.sync(opp)
    resync_objects(db, mirror, [opp.customer, opp.bm])
    return notices.redirect(opps.back_url(back, qs, opp.id), f"opportunity_stage.{stage}")


def _convert_page(request: Request, db: Session, opp: Opportunity, form: dict, error: str | None = None):
    return render(
        request, "opportunities/convert.html", status_code=400 if error else 200,
        opp=opp, form=form, error=error, projects=opps.linkable_projects(db, opp),
    )


@router.get("/{opportunity_id}/convert")
def convert_form(opportunity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    opp = get_or_404(db, Opportunity, opportunity_id)
    if opp.project_id is not None:  # 두 번째부터는 있던 프로젝트를 보여 준다
        return RedirectResponse(f"/projects/{opp.project_id}", status_code=303)
    if error := opps.convert_problem(opp):
        return _detail_page(request, db, user, opp, error)
    return _convert_page(request, db, opp, {"mode": "create", "name": opp.name, "project_id": ""})


def _lost_claim(request: Request, db: Session, user: User, opp: Opportunity, form: dict, error: str):
    """프로젝트 자리를 차지하지 못했을 때: 이미 이어진 프로젝트가 있으면 거기로, 없으면(다른 영업기회가 그 프로젝트를 먼저 이은 경우) 오류 화면."""
    db.refresh(opp)
    if opp.project_id is not None:
        return RedirectResponse(f"/projects/{opp.project_id}", status_code=303)
    return _convert_page(request, db, opp, form, error)


@router.post("/{opportunity_id}/convert")
def convert(
    opportunity_id: int, request: Request,
    mode: str = Form("create"), name: str = Form(""), project_id: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    """수주한 영업기회를 프로젝트로: 새 프로젝트를 만들거나 같은 고객의 기존 프로젝트를 잇는다. 한 번만."""
    opp = get_or_404(db, Opportunity, opportunity_id)
    if opp.project_id is not None:
        return RedirectResponse(f"/projects/{opp.project_id}", status_code=303)
    if error := opps.convert_problem(opp):
        return _detail_page(request, db, user, opp, error)
    form = {"mode": "link" if mode == "link" else "create", "name": name.strip(), "project_id": project_id.strip()}
    if form["mode"] == "link":
        pid = opps.parse_id(project_id)
        project = db.get(Project, pid) if pid is not None else None
        if error := opps.link_problem(db, opp, project):
            return _convert_page(request, db, opp, form, error)
        if error := opps.link_project(db, opp, project):
            return _lost_claim(request, db, user, opp, form, error)
    else:
        if not form["name"]:
            return _convert_page(request, db, opp, form, opps.NAME_REQUIRED)
        project = opps.convert_create(db, opp, form["name"], user)
        if project is None:  # 그 사이 다른 요청이 먼저 전환했다: 새로 만든 프로젝트는 되돌려졌고 있던 프로젝트를 보여 준다
            return _lost_claim(request, db, user, opp, form, "")
    db.commit()
    db.refresh(opp)
    mirror.sync(project)
    mirror.sync(opp)
    resync_objects(db, mirror, [opp.customer, opp.bm])
    return notices.redirect(f"/projects/{project.id}", "linked_project" if form["mode"] == "link" else "converted")


@router.post("/{opportunity_id}/delete")
def delete(
    opportunity_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    opp = get_deletable_or_404(db, Opportunity, opportunity_id, user)
    related = opps.dependents(db, opp)  # 묶인 니즈의 활동 기록까지 (지우기 전에 알아 둔다)
    opps.release_opportunity(db, opp)
    mirror.remove(opp)
    db.delete(opp)
    db.commit()
    resync_objects(db, mirror, related)
    return notices.redirect("/opportunities", "deleted")

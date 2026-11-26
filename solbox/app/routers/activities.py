"""활동 기록 화면."""
from __future__ import annotations

import logging

from collections import Counter
from datetime import date
from urllib.parse import urlencode
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.gate import keeps_text, require_ai_on
from app.idparse import parse_id
from app import follow_ups as fu_lib
from app import labels, ontology, origin, relations, ui
from app.ai.queue import enqueue, queue_position
from app.ai.tasks import ActivityDraft, draft_summary_md, match_mentions, normalize_draft
from app import guards, notices
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.extract import decode_text
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import AIJob, Activity, Project, Customer, FollowUp, IntakeFile, Opportunity, Partner, Solution, User
from app.records import (
    LINK_TYPES, NEW_TYPES, ambiguous_view, apply_activity_fields, first_line, linked, rows, sync_all,
)
from app.routers._common import (
    clear_file_targets, get_deletable_or_404, get_or_404, list_filter, newest_first, origin_filter, paginate, row_info, submitted_fields, sort_rows,
)
from app.security import can_delete, require_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/activities")

MEMO_REQUIRED = ui.FieldError("메모를 입력하세요.", "raw_text")
BAD_MEMO_FILE = "텍스트 파일(.txt, .md)만 올릴 수 있습니다."


def read_memo(raw_text: str, memo_file: UploadFile | None) -> str:
    """입력창 글과 파일 내용을 빈 줄 하나로 이어 붙인다. 둘 다 비면 400."""
    parts = [raw_text.strip()]
    if memo_file is not None and memo_file.filename:
        if Path(memo_file.filename).suffix.lower() not in (".txt", ".md"):
            raise HTTPException(status_code=400, detail=BAD_MEMO_FILE)
        parts.append(decode_text(memo_file.file.read()).strip())
    text = "\n\n".join(p for p in parts if p)
    if not text:
        raise HTTPException(status_code=400, detail=MEMO_REQUIRED)
    return text


def prefill_from(db: Session, params) -> dict[str, list[int]]:
    """`?customer_id=` 같은 미리 고르기 값 중 실제로 있는 기록만 {유형: [번호]} 로 (숫자가 아니거나 없는 번호는 무시한다)."""
    out: dict[str, list[int]] = {}
    for param, code in ontology.ACTIVITY_PREFILL.items():
        n = parse_id(params.get(param))
        if n is not None and db.get(ontology.TYPES[code].model, n) is not None:
            out[code] = [n]
    return out


def prefill_form(customer_id: str = Form(""), partner_id: str = Form(""), project_id: str = Form(""), solution_id: str = Form(""),
                 opportunity_id: str = Form("")) -> dict[str, str]:
    """등록 화면이 숨은 칸으로 다시 보내는 미리 고르기 값."""
    return {"customer_id": customer_id, "partner_id": partner_id, "project_id": project_id, "solution_id": solution_id, "opportunity_id": opportunity_id}


def prefill_chips(db: Session, selected: dict[str, list[int]]) -> list[dict]:
    """화면에 보일 "연결: 고객 한빛병원" 칩과 다시 보낼 숨은 칸 (param, value)."""
    param_of = {code: param for param, code in ontology.ACTIVITY_PREFILL.items()}
    chips = []
    for code, ids in selected.items():
        obj = db.get(ontology.TYPES[code].model, ids[0])
        chips.append({"code": code, "label": ontology.label(code), "name": obj.name, "param": param_of[code], "value": ids[0]})
    return chips


def _choices(db: Session) -> dict:
    return {
        "solution": list(db.scalars(select(Solution).order_by(Solution.name))),
        "customer": list(db.scalars(select(Customer).order_by(Customer.name))),
        "project": list(db.scalars(select(Project).order_by(Project.name))),
        "partner": list(db.scalars(select(Partner).order_by(Partner.name))),
        # 영업기회는 이름만으로 새로 만들 수 없고 여기서 고르기만 한다. 같은 이름이 있을 수 있어 고객 이름을 앞에 붙인다
        "opportunity": [SimpleNamespace(id=o.id, name=f"{o.customer.name} · {o.name}") for o in
                        db.scalars(select(Opportunity).join(Opportunity.customer).order_by(Customer.name, Opportunity.name, Opportunity.id))],
    }


def blank_values() -> dict:
    """`activities/_form_fields.html` 이 받는 값 묶음의 기본형."""
    return {
        "kind": ontology.ACTIVITY_KIND_DEFAULT, "date": date.today().isoformat(), "title": "", "raw_text": "", "summary_md": "",
        "follow_ups": [], "demand_signals": [],
        "selected": {t: [] for t in (*LINK_TYPES, *ontology.ACTIVITY_MANUAL_LINKS)},
        "new_names": {t: [] for t in NEW_TYPES},        # 체크된 채로 보이는 새 이름
        "new_candidates": {t: [] for t in NEW_TYPES},   # 체크 해제로 보이는 새 이름 후보 (AI 초안)
    }


def form_values(a: Activity) -> dict:
    values = blank_values()
    values.update(
        kind=a.kind, date=a.date.isoformat() if a.date else "", title=a.title, raw_text=a.raw_text,
        summary_md=a.summary_md,
        follow_ups=[{"id": f.id, "task": f.task, "owner": f.owner, "due": f.due.isoformat() if f.due else "", "done": f.done,
                     "linked": f.owner_user_id is not None} for f in a.follow_ups],
        demand_signals=[{"id": n.id, "customer": n.customer_name, "content": n.content, "customer_id": n.customer_id}
                        for n in a.needs],
        selected={**{t: [o.id for o in getattr(a, rel)] for t, (_, rel) in LINK_TYPES.items()},
                  **{t: [o.id for o in getattr(a, rel)] for t, rel in ontology.ACTIVITY_MANUAL_LINKS.items()}},
    )
    return values


def _form_page(request: Request, db: Session, values: dict, activity: Activity | None = None, error: str | None = None, conflict=None):
    return render(
        request, "activities/form.html", status_code=409 if conflict else 400 if error else 200,
        activity=activity, f=values, choices=_choices(db), error=error, conflict=conflict,
    )


def _int_or_none(raw) -> int | None:
    return parse_id(raw)


def _pad(ids: list[str], column: list[str]) -> list[str]:
    """줄 번호 칸이 없거나 개수가 안 맞는 옛 화면의 제출은 번호 없이 보여 준다."""
    return list(ids) if len(ids) == len(column) else [""] * len(column)


def _submitted_values(
    kind, date_, title, raw_text, summary_md, fu_task, fu_owner, fu_due, ds_customer, ds_content,
    solution_ids, customer_ids, project_ids, partner_ids, new_solutions, new_customers, new_partners,
    fu_id=(), ds_id=(), fu_done=(), opportunity_ids=(),
) -> dict:
    """제출된 값을 오류 화면에 다시 보여 줄 형태로 모은다."""
    ids = dict(solution=solution_ids, customer=customer_ids, project=project_ids, partner=partner_ids, opportunity=opportunity_ids)
    return {
        "kind": kind, "date": date_, "title": title, "raw_text": raw_text, "summary_md": summary_md,
        "follow_ups": [{"id": _int_or_none(i), "task": t, "owner": o, "due": d, "done": str(n) in set(fu_done)}
                       for n, (i, t, o, d) in enumerate(rows(_pad(fu_id, fu_task), fu_task, fu_owner, fu_due))],
        "demand_signals": [{"id": _int_or_none(i), "customer": c, "content": t}
                           for i, c, t in rows(_pad(ds_id, ds_content), ds_customer, ds_content)],
        "selected": {t: [n for i in v if (n := parse_id(i)) is not None] for t, v in ids.items()},
        "new_names": {
            rel: [n.strip() for n in names if n.strip()]
            for rel, names in (("solutions", new_solutions), ("customers", new_customers), ("partners", new_partners))
        },
        "new_candidates": {t: [] for t in NEW_TYPES},
    }


def review_linked(db: Session, activity: Activity, user: User, request: Request) -> None:
    """영업 B: 저장한 활동 기록에 연결된 진행 중 영업기회의 AI 분석을 자동으로 요청한다 (AI 꺼짐이면 아무것도 안 함)."""
    from sqlalchemy.exc import OperationalError

    from app import opportunity_ai

    db.refresh(activity)
    try:
        opportunity_ai.request_review(db, [o.id for o in activity.opportunities], user.id, request)
    except (OperationalError, guards.WriteBusy):  # 활동 기록은 이미 저장됐다: 잠금에 막힌 분석 요청만 버린다 ([다시 분석]으로 다시 할 수 있다)
        db.rollback()
        log.warning("영업기회 AI 분석 요청이 쓰기 잠금에 막혀 건너뜀 (활동 기록 %s)", activity.id)


def _save(request, db, user, mirror, activity: Activity | None, raw_text, memo_file, kind, date_, title, summary_md,
          fu_task, fu_owner, fu_due, fu_done, ds_customer, ds_content, solution_ids, customer_ids, project_ids,
          partner_ids, new_solutions, new_customers, new_partners, fu_id=(), ds_id=(), opportunity_ids=(),
          rev: Rev | None = None, once: Once | None = None):
    submitted = _submitted_values(
        kind, date_, title, raw_text, summary_md, fu_task, fu_owner, fu_due, ds_customer, ds_content,
        solution_ids, customer_ids, project_ids, partner_ids, new_solutions, new_customers, new_partners,
        fu_id=fu_id, ds_id=ds_id, fu_done=fu_done, opportunity_ids=opportunity_ids or (),
    )
    if activity is not None and rev is not None and (conflict := guards.check_rev(activity, rev, values={k: submitted[k] for k in ("kind", "date", "title", "raw_text", "summary_md")})):
        # 그 사이 다른 곳에서 바뀌었거나 같은 번호의 다른 기록이다: 후속 조치·니즈 줄까지 쓴 내용 그대로 보여 주고 저장하지 않는다
        return _form_page(request, db, submitted, activity, conflict=conflict)
    try:
        text = read_memo(raw_text, memo_file)
    except HTTPException as e:
        return _form_page(request, db, submitted, activity, e.detail)
    old = linked(activity) if activity is not None else []
    is_new = activity is None
    if activity is None:
        activity = Activity(created_by_id=user.id, raw_text=text, origin_type=ontology.ORIGIN_MANUAL)
        db.add(activity)
    apply_activity_fields(
        db, user, activity, kind=kind, date_=date_, title=title, raw_text=text, summary_md=summary_md,
        fu_task=fu_task, fu_owner=fu_owner, fu_due=fu_due, fu_done=fu_done,
        ds_customer=ds_customer, ds_content=ds_content, solution_ids=solution_ids, customer_ids=customer_ids,
        project_ids=project_ids, partner_ids=partner_ids,
        new_solutions=new_solutions, new_customers=new_customers, new_partners=new_partners,
        fu_id=fu_id, ds_id=ds_id, opportunity_ids=opportunity_ids,
    )  # 새 이름으로 만든 기록은 직접 입력 유래 (기본값)
    db.commit()
    sync_all(mirror, activity, old)
    review_linked(db, activity, user, request)
    response = notices.redirect(f"/activities/{activity.id}", "created" if is_new else "saved")
    return once.done(response) if once is not None else response


@router.get("")
def list_activities(
    request: Request, q: str = "", kind: str = "", page: str = "1", user: User = Depends(require_user), db: Session = Depends(get_db),
):
    stmt = select(Activity)
    q = q.strip()
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        stmt = stmt.where(or_(*(c.like(like, escape="\\") for c in (Activity.title, Activity.raw_text, Activity.summary_md))))
    rows, flt = list_filter(db, request, "activity", sort_rows(request, "activity", newest_first(db.scalars(stmt)), lambda a: a.title or ""))
    rows, origin_tabs, origin_now = origin_filter(request, rows)
    counts = Counter(a.kind for a in rows)  # 구분 탭의 건수: 검색어·걸러 보기를 적용한 목록 기준
    total = len(rows)
    if kind in labels.ACTIVITY_KINDS:
        rows = [a for a in rows if a.kind == kind]
    else:
        kind = ""
    pg = paginate(rows, page)
    tabs = ui.seg_tabs(request, "kind", list(labels.ACTIVITY_KINDS.items()), counts, kind, total)
    return render(request, "activities/list.html", pg=pg, q=q, kind=kind, flt=flt, tabs=tabs, origin_tabs=origin_tabs, origin_now=origin_now, info=row_info(db, "activity", pg.items))


@router.get("/new")
def new_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """`?customer_id=`·`?partner_id=`·`?project_id=`·`?solution_id=`·`?opportunity_id=` 는 상세 화면의 "+ 활동 기록 쓰기": 그 연결을 미리 골라 둔다.
    메모 화면에서는 저장·AI 정리가 그 연결을 이어받고, `?manual=1` 은 모든 칸이 있는 직접 입력 폼의 연결 칸에 미리 고른다."""
    selected = prefill_from(db, request.query_params)
    if request.query_params.get("manual") == "1":
        values = blank_values()
        values["selected"].update(selected)
        return _form_page(request, db, values)
    chips = prefill_chips(db, selected)
    manual = "/activities/new?" + urlencode([("manual", "1")] + [(c["param"], c["value"]) for c in chips])
    return render(request, "activities/new.html", error=None, raw_text="", chips=chips, manual_url=manual)


@router.post("/raw")
def save_raw(
    request: Request, raw_text: str = Form(""), memo_file: UploadFile | None = None, pre: dict = Depends(prefill_form),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
):
    try:
        text = read_memo(raw_text, memo_file)
    except HTTPException as e:
        return render(request, "activities/new.html", status_code=400, error=e.detail, raw_text=raw_text)
    activity = Activity(
        kind=ontology.ACTIVITY_KIND_DEFAULT, date=date.today(), title=first_line(text), raw_text=text, summary_md="",
        created_by_id=user.id, origin_type=ontology.ORIGIN_MANUAL,
    )
    for code, ids in prefill_from(db, pre).items():  # 상세 화면에서 시작했으면 그 연결을 이어받는다
        rel = LINK_TYPES[code][1] if code in LINK_TYPES else ontology.ACTIVITY_MANUAL_LINKS[code]
        getattr(activity, rel).append(db.get(ontology.TYPES[code].model, ids[0]))
    db.add(activity)
    db.commit()
    sync_all(mirror, activity)
    review_linked(db, activity, user, request)
    return once.done(notices.redirect(f"/activities/{activity.id}", "created"))


DRAFT_KIND = "activity_structure"


def _draft_job(db: Session, job_id: int, user: User) -> AIJob:
    job = get_or_404(db, AIJob, job_id)
    if job.kind != DRAFT_KIND:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    if job.requester_id != user.id:
        raise HTTPException(status_code=403, detail="본인이 요청한 초안만 볼 수 있습니다.")
    return job


def draft_values(db: Session, d: ActivityDraft, raw_text: str, prefill: dict | None = None) -> dict:
    """AI 초안을 정리 폼 값으로 바꾼다. 새 이름은 체크 해제 후보로 둔다."""
    d = normalize_draft(d, date.today())
    found = match_mentions(db, d.mentions)
    values = blank_values()
    values.update(
        kind=d.kind, date=d.date, title=d.title, raw_text=raw_text, summary_md=draft_summary_md(d),
        follow_ups=[{**f.model_dump(), "id": None, "done": False} for f in d.follow_ups if f.task.strip()],
        demand_signals=[{**s.model_dump(), "id": None} for s in d.demand_signals if s.content.strip()],
        selected={"solution": found["solutions"]["matched"], "customer": found["customers"]["matched"],
                  "project": found["projects"]["matched"], "partner": found["partners"]["matched"], "opportunity": []},
        new_candidates={rel: found[rel]["new"] for rel in NEW_TYPES},
        ambiguous={t: ambiguous_view(db, model, found[rel]["ambiguous"]) for t, (model, rel) in LINK_TYPES.items()},
    )
    merge_prefill(values, prefill)
    return values


def merge_prefill(values: dict, prefill: dict | None) -> None:
    """등록 화면에서 이어받은 연결을 폼 값의 선택에 더한다 (AI 가 찾은 연결은 그대로 두고 중복은 뺀다). 형식이 틀린 값은 무시한다."""
    for code, ids in (prefill or {}).items():
        if code in values["selected"] and isinstance(ids, list):
            values["selected"][code] += [i for i in ids if isinstance(i, int) and i not in values["selected"][code]]


def _draft_ai_off(request: Request, exc) -> Response:
    """AI 꺼짐: 쓴 메모와 연결 칩을 그대로 채운 메모 화면을 다시 그린다 (알림 `ai_off`). 폼 표는 쓰지 않았다 (새 화면의 새 표)."""
    if not exc.first("raw_text").strip():
        return None  # 쓴 글이 없으면 보통의 돌려보내기
    pre = {param: exc.first(param) for param in ontology.ACTIVITY_PREFILL}
    with request.app.state.session_factory() as db:
        chips = prefill_chips(db, prefill_from(db, pre))
    manual = "/activities/new?" + urlencode([("manual", "1")] + [(c["param"], c["value"]) for c in chips])
    return render(request, "activities/new.html", status_code=409, error=None, raw_text=exc.first("raw_text"), chips=chips, manual_url=manual,
                  flash=notices.inline("ai_off", "/activities/new"))


@router.post("/draft", dependencies=[Depends(require_ai_on)])
@keeps_text(_draft_ai_off)
def start_draft(
    request: Request, raw_text: str = Form(""), memo_file: UploadFile | None = None, pre: dict = Depends(prefill_form),
    user: User = Depends(require_user), db: Session = Depends(get_db), once: Once = Depends(form_once),
):
    try:
        text = read_memo(raw_text, memo_file)
    except HTTPException as e:
        return render(request, "activities/new.html", status_code=400, error=e.detail, raw_text=raw_text)
    prefill = prefill_from(db, pre)
    job = enqueue(db, DRAFT_KIND, {"raw_text": text, **({"prefill": prefill} if prefill else {})}, user.id)
    return once.done(RedirectResponse(f"/activities/draft/{job.id}", status_code=303))


@router.get("/draft/{job_id}")
def draft_page(job_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    job = _draft_job(db, job_id, user)
    raw_text = str((job.input or {}).get("raw_text", ""))
    ctx = dict(job=job, raw_text=raw_text, next=f"/activities/draft/{job.id}", f=None, choices=None, error_kind="")
    if job.status in ontology.JOB_ACTIVE:
        return render(request, "activities/draft.html", position=queue_position(db, job), **ctx)
    draft = None
    if job.status == "done":
        try:
            draft = ActivityDraft.model_validate_json(job.result)
        except ValidationError:
            draft = None
    ctx["choices"] = _choices(db)
    if draft is None:
        values = blank_values()
        values["raw_text"] = raw_text
        merge_prefill(values, (job.input or {}).get("prefill"))
        ctx["error_kind"] = job.shown_error_kind if job.status == "failed" else "bad_output"
        return render(request, "activities/draft.html", position=0, f=values, **{k: v for k, v in ctx.items() if k != "f"})
    return render(request, "activities/draft.html", position=0, f=draft_values(db, draft, raw_text, (job.input or {}).get("prefill")),
                  **{k: v for k, v in ctx.items() if k != "f"})


@router.post("/draft/{job_id}/retry", dependencies=[Depends(require_ai_on)])
def retry_draft(job_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    job = _draft_job(db, job_id, user)
    if job.status != "failed":
        raise HTTPException(status_code=400, detail="실패한 작업만 다시 시도할 수 있습니다.")
    new = enqueue(db, DRAFT_KIND, dict(job.input or {}), user.id, job.priority)
    return RedirectResponse(f"/activities/draft/{new.id}", status_code=303)


def _form_params(
    kind: str = Form(ontology.ACTIVITY_KIND_DEFAULT), date_: str = Form("", alias="date"), title: str = Form(""),
    raw_text: str = Form(""), memo_file: UploadFile | None = None, summary_md: str = Form(""),
    fu_task: list[str] = Form([]), fu_owner: list[str] = Form([]), fu_due: list[str] = Form([]),
    fu_done: list[str] = Form([]), ds_customer: list[str] = Form([]), ds_content: list[str] = Form([]),
    fu_id: list[str] = Form([]), ds_id: list[str] = Form([]),
    solution_ids: list[str] = Form([]), customer_ids: list[str] = Form([]),
    project_ids: list[str] = Form([]), partner_ids: list[str] = Form([]), opportunity_ids: list[str] = Form([]),
    new_solutions: list[str] = Form([]), new_customers: list[str] = Form([]), new_partners: list[str] = Form([]),
    fields: frozenset = Depends(submitted_fields),
) -> dict:
    if "opportunity_ids" not in fields:  # 영업기회 칸이 없던 옛 화면에서 온 저장: 영업기회 연결은 그대로 둔다 (칸이 와서 비어 있으면 비운다)
        opportunity_ids = None
    return dict(
        kind=kind, date_=date_, title=title, raw_text=raw_text, memo_file=memo_file, summary_md=summary_md,
        fu_task=fu_task, fu_owner=fu_owner, fu_due=fu_due, fu_done=fu_done, fu_id=fu_id, ds_id=ds_id,
        ds_customer=ds_customer, ds_content=ds_content, solution_ids=solution_ids, customer_ids=customer_ids,
        project_ids=project_ids, partner_ids=partner_ids, opportunity_ids=opportunity_ids,
        new_solutions=new_solutions, new_customers=new_customers, new_partners=new_partners,
    )


@router.post("")
def create(
    request: Request, params: dict = Depends(_form_params),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
):
    return _save(request, db, user, mirror, None, **params, once=once)


@router.get("/{activity_id}")
def detail(activity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    a = get_or_404(db, Activity, activity_id)
    originals = list(db.scalars(  # 올리기로 만든 기록이면 올린 원본 파일
        select(IntakeFile).where(IntakeFile.target_type == "activity", IntakeFile.target_id == a.id).order_by(IntakeFile.id)
    ))
    bundled = {o.id: o for o in db.scalars(select(Opportunity).where(Opportunity.id.in_({n.opportunity_id for n in a.needs if n.opportunity_id})))}
    return render(request, "activities/detail.html", activity=a, can_delete=can_delete(user, a), originals=originals,
                  origin=origin.view(db, user, a), need_opportunities=bundled,
                  related=relations.related_for_page(db, a, user), meta=ui.stamp_info(db, a))


@router.get("/{activity_id}/edit")
def edit_form(activity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    a = get_or_404(db, Activity, activity_id)
    return _form_page(request, db, form_values(a), a)


@router.post("/{activity_id}")
def update(
    activity_id: int, request: Request, params: dict = Depends(_form_params),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), rev: Rev = Depends(rev_form),
):
    return _save(request, db, user, mirror, get_or_404(db, Activity, activity_id), **params, rev=rev)


@router.post("/{activity_id}/delete")
def delete(
    activity_id: int, user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    a = get_deletable_or_404(db, Activity, activity_id, user)
    related = linked(a)
    mirror.remove(a)
    clear_file_targets(db, "activity", a.id)  # 번호를 다시 쓰는 새 기록이 올린 원본을 물려받지 않게
    db.delete(a)
    db.commit()
    for obj in related:
        mirror.sync(obj)
    return notices.redirect("/activities", "deleted")


def _toggle(db: Session, mirror: KnowledgeMirror, fu: FollowUp, request: Request) -> RedirectResponse:
    fu.done = not fu.done
    db.commit()
    mirror.sync(fu.activity)
    back = fu_lib.redirect_after_toggle(request.headers.get("referer"), fu.id)
    return RedirectResponse(back or request.headers.get("referer") or f"/activities/{fu.activity_id}", status_code=303)


@router.post("/{activity_id}/followups/{index}/toggle")
def toggle_follow_up_by_index(
    activity_id: int, index: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    """옛 주소: 열려 있던 화면이 순번으로 보내는 요청. 순번은 그 활동 기록 안의 위치(순서)로 행을 찾는다."""
    a = get_or_404(db, Activity, activity_id)
    if not 0 <= index < len(a.follow_ups):
        raise HTTPException(status_code=404, detail="후속 조치를 찾을 수 없습니다.")
    return _toggle(db, mirror, a.follow_ups[index], request)


follow_up_router = APIRouter(prefix="/follow-ups")


@follow_up_router.post("/{follow_up_id}/mine")
def take_follow_up(
    follow_up_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    """"내 할 일로": 담당을 내 이름으로 하고 나에게 연결한다 (담당 칸 글이 이름과 달라 연결되지 않은 줄을 위해)."""
    fu = get_or_404(db, FollowUp, follow_up_id)
    fu.owner, fu.owner_user_id = (user.name or user.username).strip(), user.id
    db.commit()
    mirror.sync(fu.activity)
    referer = fu_lib.redirect_after_toggle(request.headers.get("referer"), fu.id)
    back = referer.split("?")[0] if referer else f"/activities/{fu.activity_id}"
    return notices.redirect(back, "fu_mine")


@follow_up_router.post("/{follow_up_id}/toggle")
def toggle_follow_up(
    follow_up_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    return _toggle(db, mirror, get_or_404(db, FollowUp, follow_up_id), request)

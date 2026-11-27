"""BM(사업 모델) 화면. BM 은 고객에 묶이지 않는다. 삭제 조건은 `app/opportunities.py` 한 곳이다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.idparse import parse_id
from app import business_types as bts, ontology, opportunities as opps, relations, ui
from app import bm_plan, guards, money, notices
from app.ai.gate import require_ai_on
from app.ai.queue import queue_position
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import BM, AIJob, CustomerNeed, Solution, User
from app.records import pick
from app.routers._common import get_deletable_or_404, get_or_404, name_error, duplicate_warning, paginate, resync_objects, row_info, search_by_name, sort_rows, submitted_fields
from app.security import can_delete, require_user

router = APIRouter(prefix="/bms")
FIELDS = ("name", "summary", "target_customers", "revenue_model", "memo", "business_type", "dept", "op_status", "dev_stage")
BAD_OP_STATUS = "알 수 없는 운영 상태입니다. 목록에서 다시 골라 주세요."
BAD_DEV_STAGE = "알 수 없는 개발 단계입니다. 목록에서 다시 골라 주세요."


def _form_page(request: Request, db: Session, form: dict, bm: BM | None = None, error: str | None = None, conflict=None, duplicate=None):
    kind = bm.kind if bm is not None else (form.get("kind") if form.get("kind") in ontology.BM_KINDS else ontology.BM_KIND_EXISTING)
    return render(
        request, "bms/form.html", status_code=409 if conflict else 400 if error else 200,
        form=form, bm=bm, error=error, solutions=search_by_name(db, Solution), conflict=conflict, duplicate=duplicate,
        kind=kind, crumbs=bm_plan.crumbs(bm, kind, (bm.name, f"/bms/{bm.id}") if bm else ("", None), ("수정" if bm else "등록", None)),
    )


def _detail_page(request: Request, db: Session, user: User, bm: BM, error: str | None = None):
    job = db.get(AIJob, bm.active_job_id) if bm.active_job_id else None
    sols = {s.id: s for s in db.scalars(select(Solution).where(Solution.id.in_({i for x in (bm.ideas or []) for i in x.get("solution_ids", [])})))} if bm.ideas else {}
    need_ids = {i for x in (bm.ideas or []) for i in x.get("need_ids", [])}
    needs = {n.id: n for n in db.scalars(select(CustomerNeed).where(CustomerNeed.id.in_(need_ids)))} if need_ids else {}
    return render(
        request, "bms/detail.html", status_code=400 if error else 200, bm=bm, error=error,
        is_new=bm_plan.is_new(bm), summary=None if bm_plan.is_new(bm) else bm_plan.op_summary(db, bm), job=job,
        position=queue_position(db, job) if job is not None and job.status in ontology.JOB_ACTIVE else 0, next=f"/bms/{bm.id}",
        idea_solutions=sols, idea_needs=needs, money=money, idea_sig=bm_plan.idea_sig, crumbs=bm_plan.crumbs(bm, bm.kind, (bm.name, None)),
        **relations.page_related(db, bm, user), has_dependents=bool(bm.projects or bm.opportunities or bm.proposals), can_delete=can_delete(user, bm), meta=ui.stamp_info(db, bm),
    )


def _values(name, summary, target_customers, revenue_model, memo, business_type=None, dept=None, op_status=None, dev_stage=None) -> dict:
    """폼 값 다듬기. `business_type` 은 대표 사업방식 코드 (비면 비움, 칸이 오지 않았으면 None: 고칠 때 그대로 둔다).
    담당 부서·운영 상태(기존 BM)와 개발 단계(신규 BM)도 칸이 왔을 때만 바꾼다 (사업기획 2026-11-27)."""
    values = {"name": name.strip(), "summary": summary.strip(), "target_customers": target_customers.strip(),
              "revenue_model": revenue_model.strip(), "memo": memo.strip()}
    for key, raw in (("business_type", business_type), ("dept", dept), ("op_status", op_status), ("dev_stage", dev_stage)):
        if raw is not None:
            values[key] = raw.strip()
    return values


def _type_error(values: dict):
    """대표 사업방식·운영 상태·개발 단계는 비었거나 목록의 값이어야 한다."""
    bt = values.get("business_type") or ""
    if bt and bt not in ontology.BUSINESS_TYPES:
        return bts.BAD_BM_TYPE
    if (values.get("op_status") or "") and values["op_status"] not in ontology.BM_OP_STATUSES:
        return BAD_OP_STATUS
    if (values.get("dev_stage") or "") and values["dev_stage"] not in ontology.BM_DEV_STAGES:
        return BAD_DEV_STAGE
    return None


def _db_values(values: dict) -> dict:
    out = dict(values)
    for key in ("business_type", "op_status", "dev_stage"):
        if key in out:
            out[key] = out[key] or None
    return out


def _form_values(request_fields: frozenset, business_type, dept, op_status, dev_stage) -> tuple:
    return tuple(v if k in request_fields else None for k, v in (("business_type", business_type), ("dept", dept), ("op_status", op_status), ("dev_stage", dev_stage)))


@router.get("")
def list_bms(request: Request, q: str = "", page: str = "1", user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = [b for b in search_by_name(db, BM, q, BM.summary, BM.dept) if not bm_plan.is_new(b)]  # 기존 BM 만 (신규 BM 은 /bm-lab)
    found, type_tabs, type_now = bts.type_tabs(request, sort_rows(request, "bm", rows), lambda b: [b.business_type] if b.business_type else [])
    pg = paginate(found, page)
    return render(request, "bms/list.html", pg=pg, q=q.strip(), type_tabs=type_tabs, type_now=type_now, info=row_info(db, "bm", pg.items))


@router.get("/new")
def new_form(request: Request, kind: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    kind = kind if kind in ontology.BM_KINDS else ontology.BM_KIND_EXISTING
    form = {**{f: "" for f in FIELDS}, "solution_ids": [], "kind": kind}
    form["dev_stage" if kind == ontology.BM_KIND_NEW else "op_status"] = ontology.BM_DEV_STAGE_DEFAULT if kind == ontology.BM_KIND_NEW else "running"
    return _form_page(request, db, form)


@router.post("")
def create(
    request: Request,
    name: str = Form(""), summary: str = Form(""), target_customers: str = Form(""), revenue_model: str = Form(""),
    memo: str = Form(""), solution_ids: list[str] = Form([]), confirm_dup: str = Form(""), business_type: str = Form(""), fields: frozenset = Depends(submitted_fields),
    kind: str = Form(""), dept: str = Form(""), op_status: str = Form(""), dev_stage: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    once: Once = Depends(form_once),
):
    kind = kind if kind in ontology.BM_KINDS else ontology.BM_KIND_EXISTING
    values = _values(name, summary, target_customers, revenue_model, memo, *_form_values(fields, business_type, dept, op_status, dev_stage))
    if kind == ontology.BM_KIND_NEW and not values.get("dev_stage"):
        values["dev_stage"] = ontology.BM_DEV_STAGE_DEFAULT
    if error := name_error(name) or _type_error(values):
        return _form_page(request, db, {**values, "kind": kind, "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None]}, error=error)
    if dup := duplicate_warning(db, BM, name, confirm_dup):
        return _form_page(request, db, {**values, "kind": kind, "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None]}, duplicate=dup)
    bm = BM(**_db_values(values), kind=kind, created_by_id=user.id)
    bm.solutions = pick(db, Solution, solution_ids)
    db.add(bm)
    db.commit()
    db.refresh(bm)
    mirror.sync(bm)
    resync_objects(db, mirror, opps.dependents(db, bm))  # 솔루션 카드의 BM 줄
    return once.done(notices.redirect(f"/bms/{bm.id}", "created"))


@router.get("/{bm_id}")
def detail(bm_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, BM, bm_id))


@router.get("/{bm_id}/edit")
def edit_form(bm_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    bm = get_or_404(db, BM, bm_id)
    return _form_page(request, db, {**{f: getattr(bm, f) or "" for f in FIELDS}, "solution_ids": [s.id for s in bm.solutions], "kind": bm.kind}, bm)


@router.post("/{bm_id}")
def update(
    bm_id: int, request: Request,
    name: str = Form(""), summary: str = Form(""), target_customers: str = Form(""), revenue_model: str = Form(""),
    memo: str = Form(""), solution_ids: list[str] = Form([]), confirm_dup: str = Form(""), business_type: str = Form(""), fields: frozenset = Depends(submitted_fields),
    dept: str = Form(""), op_status: str = Form(""), dev_stage: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    rev: Rev = Depends(rev_form),
):
    bm = get_or_404(db, BM, bm_id)
    values = _values(name, summary, target_customers, revenue_model, memo, *_form_values(fields, business_type, dept, op_status, dev_stage))
    if conflict := guards.check_rev(bm, rev, values=values):
        return _form_page(request, db, {**values, "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None]}, bm, conflict=conflict)
    if error := name_error(name) or _type_error(values):
        return _form_page(request, db, {**values, "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None]}, bm, error)
    if dup := duplicate_warning(db, BM, name, confirm_dup, exclude=bm):
        return _form_page(request, db, {**values, "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None]}, bm, duplicate=dup)
    before = opps.dependents(db, bm)  # 빠지는 솔루션의 카드도 다시 쓴다
    for field, value in _db_values(values).items():
        setattr(bm, field, value)
    bm.solutions = pick(db, Solution, solution_ids)
    db.commit()
    db.refresh(bm)
    mirror.sync(bm)
    resync_objects(db, mirror, [*before, *opps.dependents(db, bm)])
    return notices.redirect(f"/bms/{bm.id}", "saved")


@router.post("/{bm_id}/delete")
def delete(
    bm_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    bm = get_deletable_or_404(db, BM, bm_id, user)
    if error := opps.bm_delete_problem(db, bm):
        return _detail_page(request, db, user, bm, error)
    related = opps.dependents(db, bm)
    mirror.remove(bm)
    db.delete(bm)  # 솔루션과의 연결 행만 함께 지워진다
    db.commit()
    resync_objects(db, mirror, related)
    return notices.redirect(bm_plan.list_url(bm), "deleted")


# ---- 신규 BM: AI 안·개발 단계·기존 BM 으로 옮기기 (사업기획 2026-11-27) ----

@router.post("/{bm_id}/ideas", dependencies=[Depends(require_ai_on)])
def regenerate_ideas(bm_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    bm = get_or_404(db, BM, bm_id)
    if not bm_plan.is_new(bm):
        raise HTTPException(status_code=400, detail=bm_plan.NOT_NEW)
    job = db.get(AIJob, bm.active_job_id) if bm.active_job_id else None
    if job is not None and job.status in ontology.JOB_ACTIVE:
        return notices.redirect(f"/bms/{bm.id}", "busy")
    bm_plan.start_ideas(db, bm, user)
    return notices.redirect(f"/bms/{bm.id}", "bm_ideas_started")


@router.post("/{bm_id}/ideas/{index}/apply")
def apply_idea(
    bm_id: int, index: int, request: Request, sig: str | None = Form(None), user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    bm = get_or_404(db, BM, bm_id)
    before = opps.dependents(db, bm)
    if error := bm_plan.apply_idea(db, bm, index, sig):
        return _detail_page(request, db, user, bm, error)
    db.commit()
    db.refresh(bm)
    mirror.sync(bm)
    resync_objects(db, mirror, [*before, *opps.dependents(db, bm)])
    return notices.redirect(f"/bms/{bm.id}", "bm_idea_applied")


@router.post("/{bm_id}/stage")
def change_stage(bm_id: int, stage: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db),
                 mirror: KnowledgeMirror = Depends(get_mirror)):
    bm = get_or_404(db, BM, bm_id)
    if not bm_plan.is_new(bm):
        raise HTTPException(status_code=400, detail=bm_plan.NOT_NEW)
    if stage not in ontology.BM_DEV_STAGES:
        raise HTTPException(status_code=400, detail=BAD_DEV_STAGE)
    if bm.dev_stage != stage:
        bm.dev_stage = stage
        db.commit()
        mirror.sync(bm)
    return notices.redirect(f"/bms/{bm.id}", f"bm_dev_stage.{stage}")


@router.post("/{bm_id}/launch")
def launch(bm_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db),
           mirror: KnowledgeMirror = Depends(get_mirror)):
    bm = get_or_404(db, BM, bm_id)
    if error := bm_plan.launch(bm):
        return _detail_page(request, db, user, bm, error)
    db.commit()
    mirror.sync(bm)
    return notices.redirect(f"/bms/{bm.id}", "bm_launched")

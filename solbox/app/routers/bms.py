"""BM(사업 모델) 화면. BM 은 고객에 묶이지 않는다. 삭제 조건은 `app/opportunities.py` 한 곳이다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.idparse import parse_id
from app import business_types as bts, ontology, opportunities as opps, relations, ui
from app import guards, notices
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import BM, Solution, User
from app.records import pick
from app.routers._common import get_deletable_or_404, get_or_404, name_error, duplicate_warning, paginate, resync_objects, row_info, search_by_name, sort_rows, submitted_fields
from app.security import can_delete, require_user

router = APIRouter(prefix="/bms")
FIELDS = ("name", "summary", "target_customers", "revenue_model", "memo", "business_type")


def _form_page(request: Request, db: Session, form: dict, bm: BM | None = None, error: str | None = None, conflict=None, duplicate=None):
    return render(
        request, "bms/form.html", status_code=409 if conflict else 400 if error else 200,
        form=form, bm=bm, error=error, solutions=search_by_name(db, Solution), conflict=conflict, duplicate=duplicate,
    )


def _detail_page(request: Request, db: Session, user: User, bm: BM, error: str | None = None):
    return render(
        request, "bms/detail.html", status_code=400 if error else 200, bm=bm, error=error,
        **relations.page_related(db, bm, user), has_dependents=bool(bm.projects or bm.opportunities or bm.proposals), can_delete=can_delete(user, bm), meta=ui.stamp_info(db, bm),
    )


def _values(name, summary, target_customers, revenue_model, memo, business_type=None) -> dict:
    """폼 값 다듬기. `business_type` 은 대표 사업방식 코드 (비면 비움, 칸이 오지 않았으면 None: 고칠 때 그대로 둔다)."""
    values = {"name": name.strip(), "summary": summary.strip(), "target_customers": target_customers.strip(),
              "revenue_model": revenue_model.strip(), "memo": memo.strip()}
    if business_type is not None:
        values["business_type"] = business_type.strip()
    return values


def _type_error(values: dict):
    """대표 사업방식은 비었거나 목록의 값이어야 한다."""
    bt = values.get("business_type") or ""
    return bts.BAD_BM_TYPE if bt and bt not in ontology.BUSINESS_TYPES else None


def _db_values(values: dict) -> dict:
    out = dict(values)
    if "business_type" in out:
        out["business_type"] = out["business_type"] or None
    return out


@router.get("")
def list_bms(request: Request, q: str = "", page: str = "1", user: User = Depends(require_user), db: Session = Depends(get_db)):
    found, type_tabs, type_now = bts.type_tabs(request, sort_rows(request, "bm", search_by_name(db, BM, q, BM.summary)), lambda b: [b.business_type] if b.business_type else [])
    pg = paginate(found, page)
    return render(request, "bms/list.html", pg=pg, q=q.strip(), type_tabs=type_tabs, type_now=type_now, info=row_info(db, "bm", pg.items))


@router.get("/new")
def new_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "solution_ids": []})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), summary: str = Form(""), target_customers: str = Form(""), revenue_model: str = Form(""),
    memo: str = Form(""), solution_ids: list[str] = Form([]), confirm_dup: str = Form(""), business_type: str = Form(""), fields: frozenset = Depends(submitted_fields),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    once: Once = Depends(form_once),
):
    values = _values(name, summary, target_customers, revenue_model, memo, business_type if "business_type" in fields else None)
    if error := name_error(name) or _type_error(values):
        return _form_page(request, db, {**values, "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None]}, error=error)
    if dup := duplicate_warning(db, BM, name, confirm_dup):
        return _form_page(request, db, {**values, "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None]}, duplicate=dup)
    bm = BM(**_db_values(values), created_by_id=user.id)
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
    return _form_page(request, db, {**{f: getattr(bm, f) or "" for f in FIELDS}, "solution_ids": [s.id for s in bm.solutions]}, bm)


@router.post("/{bm_id}")
def update(
    bm_id: int, request: Request,
    name: str = Form(""), summary: str = Form(""), target_customers: str = Form(""), revenue_model: str = Form(""),
    memo: str = Form(""), solution_ids: list[str] = Form([]), confirm_dup: str = Form(""), business_type: str = Form(""), fields: frozenset = Depends(submitted_fields),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    rev: Rev = Depends(rev_form),
):
    bm = get_or_404(db, BM, bm_id)
    values = _values(name, summary, target_customers, revenue_model, memo, business_type if "business_type" in fields else None)
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
    return notices.redirect("/bms", "deleted")

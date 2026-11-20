"""BM(사업 모델) 화면. BM 은 고객에 묶이지 않는다. 삭제 조건은 `app/opportunities.py` 한 곳이다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import opportunities as opps, relations
from app.db import get_db
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import BM, Solution, User
from app.records import pick
from app.routers._common import get_deletable_or_404, get_or_404, name_error, paginate, resync_objects, row_info, search_by_name
from app.security import can_delete, require_user

router = APIRouter(prefix="/bms")
FIELDS = ("name", "summary", "target_customers", "revenue_model", "memo")


def _form_page(request: Request, db: Session, form: dict, bm: BM | None = None, error: str | None = None):
    return render(
        request, "bms/form.html", status_code=400 if error else 200,
        form=form, bm=bm, error=error, solutions=search_by_name(db, Solution),
    )


def _detail_page(request: Request, db: Session, user: User, bm: BM, error: str | None = None):
    return render(
        request, "bms/detail.html", status_code=400 if error else 200, bm=bm, error=error,
        related=relations.related_for_page(db, bm, user), has_dependents=bool(bm.projects or bm.opportunities), can_delete=can_delete(user, bm),
    )


def _values(name, summary, target_customers, revenue_model, memo) -> dict:
    return {"name": name.strip(), "summary": summary.strip(), "target_customers": target_customers.strip(),
            "revenue_model": revenue_model.strip(), "memo": memo.strip()}


@router.get("")
def list_bms(request: Request, q: str = "", page: str = "1", user: User = Depends(require_user), db: Session = Depends(get_db)):
    pg = paginate(search_by_name(db, BM, q, BM.summary), page)
    return render(request, "bms/list.html", pg=pg, q=q.strip(), info=row_info(db, "bm", pg.items))


@router.get("/new")
def new_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "solution_ids": []})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), summary: str = Form(""), target_customers: str = Form(""), revenue_model: str = Form(""),
    memo: str = Form(""), solution_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    values = _values(name, summary, target_customers, revenue_model, memo)
    if error := name_error(name):
        return _form_page(request, db, {**values, "solution_ids": [int(i) for i in solution_ids if i.strip().isdigit()]}, error=error)
    bm = BM(**values, created_by_id=user.id)
    bm.solutions = pick(db, Solution, solution_ids)
    db.add(bm)
    db.commit()
    db.refresh(bm)
    mirror.sync(bm)
    resync_objects(db, mirror, opps.dependents(db, bm))  # 솔루션 카드의 BM 줄
    return RedirectResponse(f"/bms/{bm.id}", status_code=303)


@router.get("/{bm_id}")
def detail(bm_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, BM, bm_id))


@router.get("/{bm_id}/edit")
def edit_form(bm_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    bm = get_or_404(db, BM, bm_id)
    return _form_page(request, db, {**{f: getattr(bm, f) for f in FIELDS}, "solution_ids": [s.id for s in bm.solutions]}, bm)


@router.post("/{bm_id}")
def update(
    bm_id: int, request: Request,
    name: str = Form(""), summary: str = Form(""), target_customers: str = Form(""), revenue_model: str = Form(""),
    memo: str = Form(""), solution_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    bm = get_or_404(db, BM, bm_id)
    values = _values(name, summary, target_customers, revenue_model, memo)
    if error := name_error(name):
        return _form_page(request, db, {**values, "solution_ids": [int(i) for i in solution_ids if i.strip().isdigit()]}, bm, error)
    before = opps.dependents(db, bm)  # 빠지는 솔루션의 카드도 다시 쓴다
    for field, value in values.items():
        setattr(bm, field, value)
    bm.solutions = pick(db, Solution, solution_ids)
    db.commit()
    db.refresh(bm)
    mirror.sync(bm)
    resync_objects(db, mirror, [*before, *opps.dependents(db, bm)])
    return RedirectResponse(f"/bms/{bm.id}", status_code=303)


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
    return RedirectResponse("/bms", status_code=303)

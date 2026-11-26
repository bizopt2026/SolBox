"""파트너 화면."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.idparse import parse_id
from app import contacts, ontology, origin, relations, same_company, ui
from app import aliases as alias_utils, guards, notices
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Activity, Customer, Partner, User
from app.routers._common import (
    activities_linked_to, alias_error, clear_file_targets, get_deletable_or_404, get_or_404, name_error, newest_first, duplicate_warning,
    resync_activities, resync_related, row_info, search_by_name, paginate, sort_rows, origin_filter,
)
from app.security import can_delete, require_user

router = APIRouter(prefix="/partners")
FIELDS = ("name", "aliases", "contact", "description", "memo")


def _form_page(request: Request, db: Session, form: dict, partner: Partner | None = None, error: str | None = None, conflict=None, duplicate=None):
    return render(
        request, "partners/form.html", status_code=409 if conflict else 400 if error else 200,
        form=form, partner=partner, error=error, customers=same_company.customer_choices(db, partner), conflict=conflict, duplicate=duplicate,
    )


def _detail_page(request: Request, db: Session, user: User, partner: Partner, error: str | None = None, status_code: int | None = None, **extra):
    return render(
        request, "partners/detail.html", status_code=status_code or (400 if error else 200), partner=partner, **extra,
        **relations.page_related(db, partner, user),
        can_delete=can_delete(user, partner), error=error, origin=origin.view(db, user, partner), meta=ui.stamp_info(db, partner),
        same_name=same_company.candidate_customer(db, partner), contact_split=contacts.pending_split_count(partner),
    )


@router.get("")
def list_partners(
    request: Request, q: str = "", page: str = "1",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    rows, origin_tabs, origin_now = origin_filter(request, sort_rows(request, "partner", search_by_name(db, Partner, q), db=db))
    pg = paginate(rows, page)
    return render(request, "partners/list.html", pg=pg, q=q.strip(), info=row_info(db, "partner", pg.items), origin_tabs=origin_tabs, origin_now=origin_now)


@router.get("/new")
def new_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "customer_id": ""})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), aliases: str = Form(""), contact: str = Form(""), description: str = Form(""), memo: str = Form(""),
    customer_id: str = Form(""), confirm_dup: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
):
    values = dict(name=name.strip(), aliases=alias_utils.parse_text(aliases), contact=contact.replace("\r\n", "\n").strip(), description=description.strip(), memo=memo.strip())
    if error := name_error(name):
        return _form_page(request, db, {**values, "customer_id": customer_id}, error=error)
    if error := alias_error(db, Partner, name, aliases):
        return _form_page(request, db, {**values, "aliases": aliases, "customer_id": customer_id}, error=error)
    if dup := duplicate_warning(db, Partner, name, confirm_dup):
        return _form_page(request, db, {**values, "customer_id": customer_id}, duplicate=dup)
    partner = Partner(**values, created_by_id=user.id, origin_type=ontology.ORIGIN_MANUAL)
    if error := same_company.set_from_partner(db, partner, customer_id):  # 오류면 아무것도 바꾸지 않았다
        return _form_page(request, db, {**values, "customer_id": customer_id}, error=error)
    db.add(partner)
    db.commit()
    mirror.sync(partner)
    if partner.customer is not None:
        mirror.sync(partner.customer)
    return once.done(notices.redirect(f"/partners/{partner.id}", "created"))


@router.get("/{partner_id}")
def detail(partner_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, Partner, partner_id))


@router.get("/{partner_id}/edit")
def edit_form(partner_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    partner = get_or_404(db, Partner, partner_id)
    return _form_page(request, db, {**{f: getattr(partner, f) for f in FIELDS}, "customer_id": partner.customer_id or ""}, partner)


@router.post("/{partner_id}")
def update(
    partner_id: int, request: Request,
    name: str = Form(""), aliases: str = Form(""), contact: str = Form(""), description: str = Form(""), memo: str = Form(""),
    customer_id: str = Form(""), confirm_dup: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), rev: Rev = Depends(rev_form),
):
    partner = get_or_404(db, Partner, partner_id)
    values = dict(name=name.strip(), aliases=alias_utils.parse_text(aliases), contact=contact.replace("\r\n", "\n").strip(), description=description.strip(), memo=memo.strip())
    if conflict := guards.check_rev(partner, rev, values=values):
        return _form_page(request, db, {**values, "customer_id": customer_id}, partner, conflict=conflict)
    if error := name_error(name):
        return _form_page(request, db, {**values, "customer_id": customer_id}, partner, error)
    if error := alias_error(db, Partner, name, aliases, partner):
        return _form_page(request, db, {**values, "aliases": aliases, "customer_id": customer_id}, partner, error)
    if dup := duplicate_warning(db, Partner, name, confirm_dup, exclude=partner):
        return _form_page(request, db, {**values, "customer_id": customer_id}, partner, duplicate=dup)
    old_customer = partner.customer
    if error := same_company.set_from_partner(db, partner, customer_id):  # 오류면 아무것도 바꾸지 않았다
        return _form_page(request, db, {**values, "customer_id": customer_id}, partner, error)
    for field, value in values.items():
        setattr(partner, field, value)
    db.commit()
    db.refresh(partner)
    mirror.sync(partner)
    resync_related(db, mirror, partner)
    if old_customer is not None and old_customer is not partner.customer:  # 풀린 고객의 사본
        db.refresh(old_customer)
        mirror.sync(old_customer)
    return notices.redirect(f"/partners/{partner.id}", "saved")


@router.post("/{partner_id}/same-company")
def link_same_company(
    partner_id: int, request: Request, customer_id: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    """이름이 같은 고객 안내의 한 번 누르기: 아직 다른 곳과 이어지지 않았을 때만 잇는다."""
    partner = get_or_404(db, Partner, partner_id)
    customer = db.get(Customer, cid) if (cid := parse_id(customer_id)) is not None else None
    error = same_company.NO_CUSTOMER if customer is None else same_company.link_unlinked(db, partner, customer)
    if error:
        db.rollback()
        return _detail_page(request, db, user, partner, error)
    db.commit()
    db.refresh(partner)
    db.refresh(customer)
    mirror.sync(partner)
    mirror.sync(customer)
    return notices.redirect(f"/partners/{partner.id}", "same_company")


@router.post("/{partner_id}/delete")
def delete(
    partner_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    partner = get_deletable_or_404(db, Partner, partner_id, user)
    solutions = list(partner.solutions)
    activities = activities_linked_to(db, Activity.partners, partner)
    customer = partner.customer
    for s in solutions:
        s.partner_id = None
    mirror.remove(partner)
    clear_file_targets(db, "partner", partner.id)
    db.delete(partner)
    db.commit()
    if customer is not None:  # 사본에 이 파트너가 적혀 있었다
        db.refresh(customer)
        mirror.sync(customer)
    for s in solutions:
        db.refresh(s)
        mirror.sync(s)
    resync_activities(db, mirror, activities)
    return notices.redirect("/partners", "deleted")

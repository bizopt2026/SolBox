"""고객 화면."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from app.idparse import parse_id
from app import ontology, opportunities as opps, origin, relations, same_company, ui
from app import notices
from app.db import get_db
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Activity, Customer, CustomerNeed, Partner, User
from app.routers._common import (
    activities_linked_to, activities_with_needs_of, clear_file_targets, get_deletable_or_404, get_or_404, name_error, newest_first,
    resync_activities, resync_related, search_by_name, paginate, row_info, sort_rows,
)
from app.security import can_delete, require_user

router = APIRouter(prefix="/customers")
FIELDS = ("name", "industry", "contact", "memo")
HAS_PROJECTS = "이 고객에 연결된 프로젝트가 있어 삭제할 수 없습니다. 프로젝트를 먼저 삭제해 주세요."


HAS_PROPOSALS = "제안 건이 있는 고객은 삭제할 수 없습니다."
HAS_OPPORTUNITIES = opps.HAS_OPPORTUNITIES


def _form_page(request: Request, db: Session, form: dict, customer: Customer | None = None, error: str | None = None):
    return render(
        request, "customers/form.html", status_code=400 if error else 200,
        form=form, customer=customer, error=error, partners=same_company.partner_choices(db, customer),
    )


def _detail_page(request: Request, db: Session, user: User, customer: Customer, error: str | None = None):
    return render(
        request, "customers/detail.html", status_code=400 if error else 200,
        customer=customer, **relations.page_related(db, customer, user),
        can_delete=can_delete(user, customer), error=error, origin=origin.view(db, user, customer), meta=ui.stamp_info(db, customer),
        same_name=same_company.candidate_partner(db, customer),
    )


@router.get("")
def list_customers(
    request: Request, q: str = "", page: str = "1",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    pg = paginate(sort_rows(request, "customer", search_by_name(db, Customer, q)), page)
    return render(request, "customers/list.html", pg=pg, q=q.strip(), info=row_info(db, "customer", pg.items))


@router.get("/new")
def new_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "partner_id": ""})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), industry: str = Form(""), contact: str = Form(""), memo: str = Form(""), partner_id: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    values = dict(name=name.strip(), industry=industry.strip(), contact=contact.replace("\r\n", "\n").strip(), memo=memo.strip())
    if error := name_error(name):
        return _form_page(request, db, {**values, "partner_id": partner_id}, error=error)
    customer = Customer(**values, created_by_id=user.id, origin_type=ontology.ORIGIN_MANUAL)
    db.add(customer)
    db.flush()
    if error := same_company.set_from_customer(db, customer, partner_id):
        db.rollback()
        return _form_page(request, db, {**values, "partner_id": partner_id}, error=error)
    db.commit()
    mirror.sync(customer)
    if customer.partner is not None:
        mirror.sync(customer.partner)
    return notices.redirect(f"/customers/{customer.id}", "created")


@router.get("/{customer_id}")
def detail(customer_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, Customer, customer_id))


@router.get("/{customer_id}/edit")
def edit_form(customer_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    customer = get_or_404(db, Customer, customer_id)
    current = same_company.partner_of(db, customer)
    return _form_page(request, db, {**{f: getattr(customer, f) for f in FIELDS}, "partner_id": current.id if current else ""}, customer)


@router.post("/{customer_id}")
def update(
    customer_id: int, request: Request,
    name: str = Form(""), industry: str = Form(""), contact: str = Form(""), memo: str = Form(""), partner_id: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    customer = get_or_404(db, Customer, customer_id)
    values = dict(name=name.strip(), industry=industry.strip(), contact=contact.replace("\r\n", "\n").strip(), memo=memo.strip())
    if error := name_error(name):
        return _form_page(request, db, {**values, "partner_id": partner_id}, customer, error)
    old_partner = same_company.partner_of(db, customer)
    if error := same_company.set_from_customer(db, customer, partner_id):  # 오류면 아무것도 바꾸지 않았다
        return _form_page(request, db, {**values, "partner_id": partner_id}, customer, error)
    for field, value in values.items():
        setattr(customer, field, value)
    db.commit()
    db.refresh(customer)
    mirror.sync(customer)
    resync_related(db, mirror, customer)
    if old_partner is not None and old_partner is not customer.partner:  # 풀린 파트너의 사본
        db.refresh(old_partner)
        mirror.sync(old_partner)
    return notices.redirect(f"/customers/{customer.id}", "saved")


@router.post("/{customer_id}/same-company")
def link_same_company(
    customer_id: int, request: Request, partner_id: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    """이름이 같은 파트너 안내의 한 번 누르기: 아직 다른 곳과 이어지지 않았을 때만 잇는다."""
    customer = get_or_404(db, Customer, customer_id)
    partner = db.get(Partner, pid) if (pid := parse_id(partner_id)) is not None else None
    error = same_company.NO_PARTNER if partner is None else same_company.link_unlinked(db, partner, customer)
    if error:
        db.rollback()
        return _detail_page(request, db, user, customer, error)
    db.commit()
    db.refresh(partner)
    db.refresh(customer)
    mirror.sync(partner)
    mirror.sync(customer)
    return notices.redirect(f"/customers/{customer.id}", "same_company")


@router.post("/{customer_id}/delete")
def delete(
    customer_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    customer = get_deletable_or_404(db, Customer, customer_id, user)
    if customer.projects:
        return _detail_page(request, db, user, customer, HAS_PROJECTS)
    if customer.proposals:
        return _detail_page(request, db, user, customer, HAS_PROPOSALS)
    if customer.opportunities:
        return _detail_page(request, db, user, customer, HAS_OPPORTUNITIES)
    activities = activities_linked_to(db, Activity.customers, customer)
    for a in activities_with_needs_of(db, customer):
        if a not in activities:
            activities.append(a)
    # 고객 니즈 줄은 남기고 고객 연결만 푼다 (고객 이름 글자는 그대로). DB 의 SET NULL 규칙과 같지만 열려 있는 세션에도 반영한다
    db.execute(sql_update(CustomerNeed).where(CustomerNeed.customer_id == customer.id).values(customer_id=None))
    partner = same_company.partner_of(db, customer)
    if partner is not None:  # 같은 회사 연결을 푼다 (파트너 기록은 남는다)
        partner.customer_id = None
        db.flush()
    clear_file_targets(db, "customer", customer.id)
    mirror.remove(customer)
    db.delete(customer)
    db.commit()
    if partner is not None:
        db.refresh(partner)
        mirror.sync(partner)
    resync_activities(db, mirror, activities)
    return notices.redirect("/customers", "deleted")

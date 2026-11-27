"""고객 화면."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from app.idparse import parse_id
from app import contact_history
from app import contacts, ontology, opportunities as opps, origin, relations, same_company, ui
from app import aliases as alias_utils, guards, notices
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Activity, Customer, CustomerNeed, Partner, User
from app.routers._common import (
    activities_linked_to, alias_error, activities_with_needs_of, clear_file_targets, get_deletable_or_404, get_or_404, name_error, newest_first, duplicate_warning,
    resync_activities, resync_related, search_by_name, paginate, row_info, sort_rows, origin_filter,
)
from app.security import can_delete, require_user

router = APIRouter(prefix="/customers")
FIELDS = ("name", "aliases", "industry", "contact", "memo")
HAS_PROJECTS = "이 고객에 연결된 프로젝트가 있어 삭제할 수 없습니다. 프로젝트를 먼저 삭제해 주세요."


HAS_PROPOSALS = "제안 건이 있는 고객은 삭제할 수 없습니다."
HAS_OPPORTUNITIES = opps.HAS_OPPORTUNITIES


def _form_page(request: Request, db: Session, form: dict, customer: Customer | None = None, error: str | None = None, conflict=None, duplicate=None):
    return render(
        request, "customers/form.html", status_code=409 if conflict else 400 if error else 200,
        form=form, customer=customer, error=error, partners=same_company.partner_choices(db, customer), conflict=conflict, duplicate=duplicate,
    )


def _contact_line(db: Session, customer: Customer, today) -> str:
    """상세 제목 밑 한 줄: "마지막 접촉 24일 전 · 진행 중 영업기회 2건 (합계 4억)" (영업 A 단계). 둘 다 없으면 빈 글 (줄을 그리지 않는다)."""
    from app import money

    last = relations.latest_activity_dates(db, "customer", [customer.id]).get(customer.id)
    parts = [f"마지막 접촉 {ui.days_ago(last, today)}"] if last else []  # 활동 기록이 아직 없으면 접촉 부분은 빼고 (빈 항목은 조용히: 화면 규칙)
    n, total = opps.open_by_customer(db, [customer.id]).get(customer.id, (0, 0))
    if n:
        parts.append(f"진행 중 {ontology.label('opportunity')} {n}건" + (f" (합계 {money.format_amount(total)})" if total else ""))
    return " · ".join(parts)


def _detail_page(request: Request, db: Session, user: User, customer: Customer, error: str | None = None):
    from app.routers.home import today

    return render(
        request, "customers/detail.html", status_code=400 if error else 200,
        customer=customer, **relations.page_related(db, customer, user),
        can_delete=can_delete(user, customer), error=error, origin=origin.view(db, user, customer), meta=ui.stamp_info(db, customer),
        history=contact_history.history(db, customer),
        same_name=same_company.candidate_partner(db, customer), contact_line=_contact_line(db, customer, today()), contact_split=contacts.pending_split_count(customer), contact_memo=contacts.memo_view(customer),
    )


@router.get("")
def list_customers(
    request: Request, q: str = "", page: str = "1", quiet: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    from urllib.parse import urlencode

    from app import money
    from app.routers.home import today

    now = today()
    rows, origin_tabs, origin_now = origin_filter(request, sort_rows(request, "customer", search_by_name(db, Customer, q), db=db))
    quiet_on = quiet == "1"
    if quiet_on:  # 마지막 접촉이 기준 날수보다 오래됐거나 한 번도 없는 고객 (영업 A)
        latest = relations.latest_activity_dates(db, "customer", [c.id for c in rows])
        rows = [c for c in rows if latest.get(c.id) is None or (now - latest[c.id]).days > ontology.CUSTOMER_QUIET_DAYS]
    pg = paginate(rows, page)
    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in ("quiet", "page", "notice", "nt")]
    quiet_href = "/customers" + (f"?{urlencode(keep + ([] if quiet_on else [('quiet', '1')]))}" if keep or not quiet_on else "")
    return render(request, "customers/list.html", pg=pg, q=q.strip(), info=row_info(db, "customer", pg.items), origin_tabs=origin_tabs, origin_now=origin_now,
                  quiet_on=quiet_on, quiet_href=quiet_href, quiet_days=ontology.CUSTOMER_QUIET_DAYS, today=now, money=money,
                  open_opps=opps.open_by_customer(db, [c.id for c in pg.items]))


@router.get("/new")
def new_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "partner_id": ""})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), aliases: str = Form(""), industry: str = Form(""), contact: str = Form(""), memo: str = Form(""), partner_id: str = Form(""),
    confirm_dup: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
):
    values = dict(name=name.strip(), aliases=alias_utils.parse_text(aliases), industry=industry.strip(), contact=contact.replace("\r\n", "\n").strip(), memo=memo.strip())
    if error := name_error(name):
        return _form_page(request, db, {**values, "partner_id": partner_id}, error=error)
    if error := alias_error(db, Customer, name, aliases):
        return _form_page(request, db, {**values, "aliases": aliases, "partner_id": partner_id}, error=error)
    if dup := duplicate_warning(db, Customer, name, confirm_dup):
        return _form_page(request, db, {**values, "partner_id": partner_id}, duplicate=dup)
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
    return once.done(notices.redirect(f"/customers/{customer.id}", "created"))


@router.get("/{customer_id}")
def detail(customer_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, Customer, customer_id))


@router.get("/{customer_id}/history")
def history_page(customer_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """접촉 이력 전체 (상세 화면 카드의 "모두 보기"). 활동 기록 목록의 고객 걸러 보기는 연결(R5)만 보므로 따로 둔다."""
    customer = get_or_404(db, Customer, customer_id)
    return render(request, "customers/history.html", customer=customer, history=contact_history.history(db, customer, limit=None))


@router.get("/{customer_id}/edit")
def edit_form(customer_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    customer = get_or_404(db, Customer, customer_id)
    current = same_company.partner_of(db, customer)
    return _form_page(request, db, {**{f: getattr(customer, f) for f in FIELDS}, "partner_id": current.id if current else ""}, customer)


@router.post("/{customer_id}")
def update(
    customer_id: int, request: Request,
    name: str = Form(""), aliases: str = Form(""), industry: str = Form(""), contact: str = Form(""), memo: str = Form(""), partner_id: str = Form(""),
    confirm_dup: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), rev: Rev = Depends(rev_form),
):
    customer = get_or_404(db, Customer, customer_id)
    values = dict(name=name.strip(), aliases=alias_utils.parse_text(aliases), industry=industry.strip(), contact=contact.replace("\r\n", "\n").strip(), memo=memo.strip())
    if conflict := guards.check_rev(customer, rev, values=values):  # 그 사이 다른 곳에서 바뀌었거나 같은 번호의 다른 기록이다: 저장하지 않고 쓴 내용을 그대로 보여 준다
        return _form_page(request, db, {**values, "partner_id": partner_id}, customer, conflict=conflict)
    if error := name_error(name):
        return _form_page(request, db, {**values, "partner_id": partner_id}, customer, error)
    if error := alias_error(db, Customer, name, aliases, customer):
        return _form_page(request, db, {**values, "aliases": aliases, "partner_id": partner_id}, customer, error)
    if dup := duplicate_warning(db, Customer, name, confirm_dup, exclude=customer):
        return _form_page(request, db, {**values, "partner_id": partner_id}, customer, duplicate=dup)
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

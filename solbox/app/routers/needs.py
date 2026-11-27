"""소스 › 고객니즈 (2026-11-27, 설계 docs/superpowers/specs/2026-11-27-customer-needs-design.md).
니즈 한 줄을 상태·분야·메모와 함께 고객별·분야별로 본다. 활동 기록 없이 직접 추가할 수 있다. 행위 규칙은 `app/needs.py`."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app import guards, needs as needs_lib, notices, ontology, opportunities as opps, relations, solution_fields as sfields, ui
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.idparse import parse_id
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Customer, CustomerNeed, Opportunity, SolutionField, User
from app.routers._common import PER_PAGE, get_or_404, paginate, resync_objects, search_by_name
from app.security import require_user

router = APIRouter(prefix="/needs")

CONTENT_REQUIRED = ui.FieldError("니즈 내용을 적어 주세요.", "content")
CUSTOMER_REQUIRED = ui.FieldError("고객을 골라 주세요. 직접 추가하는 니즈는 등록된 고객이 있어야 합니다.", "customer_id")
BAD_CUSTOMER = ui.FieldError("알 수 없는 고객입니다. 목록에서 다시 골라 주세요.", "customer_id")
BAD_STATUS = ui.FieldError("알 수 없는 상태입니다. 목록에서 다시 골라 주세요.", "status")
GROUPS = {"customer": "고객", "field": "분야"}
NO_CUSTOMER = "고객 미연결"


def title_of(need: CustomerNeed) -> str:
    return ui.first_line(need.content, 60) or "(내용 없음)"


def _lookup(db: Session, raw_customer, raw_status, raw_field, need: CustomerNeed | None, direct: bool):
    """폼 값 → (고객, 상태, 분야 번호, 오류). `direct` 면 고객이 필수."""
    customer = None
    cid = parse_id(raw_customer) if (raw_customer or "").strip() else None
    if (raw_customer or "").strip():
        customer = db.get(Customer, cid) if cid is not None else None
        if customer is None:
            return None, None, None, BAD_CUSTOMER
    elif direct:
        return None, None, None, CUSTOMER_REQUIRED
    status = (raw_status or "").strip() or needs_lib.STATUS_NEW
    if status not in needs_lib.STORED_STATUSES:
        return None, None, None, BAD_STATUS
    field_id, err = sfields.resolve_form(db, raw_field, need)
    if err:
        return None, None, None, err
    return customer, status, field_id, None


def _form(request: Request, db: Session, form: dict, need: CustomerNeed | None = None, error=None, conflict=None):
    customers = search_by_name(db, Customer)
    field_opts = sfields.select_options(db, need, form.get("field_id"))
    status_opts = [(c, ontology.NEED_STATUSES[c]) for c in needs_lib.STORED_STATUSES]
    return render(request, "needs/form.html", status_code=409 if conflict else 400 if error else 200,
                  form=form, need=need, error=error, conflict=conflict, customers=customers, field_opts=field_opts, status_opts=status_opts,
                  bound=need is not None and need.opportunity_id is not None, title=title_of(need) if need else None)


def _related_objects(db: Session, need: CustomerNeed, extra=()) -> list:
    """니즈가 바뀌면 사본을 다시 쓸 기록: 나온 활동 기록·묶인 영업기회·고객."""
    opp = db.get(Opportunity, need.opportunity_id) if need.opportunity_id is not None else None
    customer = db.get(Customer, need.customer_id) if need.customer_id is not None else None
    return [x for x in (need.activity, opp, customer, *extra) if x is not None]


def _rows(db: Session, q: str, customer_id, field_id) -> list[CustomerNeed]:
    stmt = select(CustomerNeed).options(selectinload(CustomerNeed.activity), selectinload(CustomerNeed.field))
    text = q.strip()
    if text:
        like = f"%{text}%"
        stmt = stmt.where(or_(CustomerNeed.content.ilike(like), CustomerNeed.customer_name.ilike(like), CustomerNeed.memo.ilike(like)))
    if customer_id is not None:
        stmt = stmt.where(CustomerNeed.customer_id == customer_id)
    if field_id is not None:
        stmt = stmt.where(CustomerNeed.field_id == field_id)
    return list(db.scalars(stmt.order_by(CustomerNeed.created_at.desc(), CustomerNeed.id.desc())))


def _sorted(request: Request, rows: list[CustomerNeed], names: dict[int, str]) -> list[CustomerNeed]:
    choices = dict(ontology.LIST_SORTS["customer_need"])
    sort = request.query_params.get("sort")
    if sort == "customer" and sort in choices:
        return sorted(rows, key=lambda n: (n.customer_id is None, names.get(n.customer_id, n.customer_name or ""), -n.id))
    if sort == "recent" and sort in choices:
        return sorted(rows, key=lambda n: (n.updated_at, n.id), reverse=True)
    return rows  # 최신순 (만든 순)


def _customer_groups(rows: list[CustomerNeed], names: dict[int, str]) -> list[sfields.Group]:
    by: dict[int | None, list] = {}
    for n in rows:
        by.setdefault(n.customer_id, []).append(n)
    named = sorted((cid for cid in by if cid is not None), key=lambda cid: (names.get(cid, ""), cid))
    out = [sfields.Group(str(cid), names.get(cid, ""), by[cid], False) for cid in named]
    return out + ([sfields.Group("", NO_CUSTOMER, by[None], True)] if None in by else [])


@router.get("")
def list_needs(request: Request, q: str = "", page: str = "1", status: str = "", customer_id: str = "", field_id: str = "", group: str = "",
               user: User = Depends(require_user), db: Session = Depends(get_db)):
    bad_filter = False
    cid = fid = None
    if customer_id.strip():
        cid = parse_id(customer_id)
        bad_filter = cid is None
    if field_id.strip():
        fid = parse_id(field_id)
        bad_filter = bad_filter or fid is None
    rows = [] if bad_filter else _rows(db, q, cid, fid)
    counts: dict[str, int] = {}
    for n in rows:
        counts[needs_lib.effective_status(n)] = counts.get(needs_lib.effective_status(n), 0) + 1
    status_now = status if status in ontology.NEED_STATUSES else ""
    tabs = ui.seg_tabs(request, "status", list(ontology.NEED_STATUSES.items()), counts, status_now, len(rows))
    if status_now:
        rows = [n for n in rows if needs_lib.effective_status(n) == status_now]
    names = dict(db.execute(select(Customer.id, Customer.name)).all())
    rows = _sorted(request, rows, names)
    opp_ids = {n.opportunity_id for n in rows if n.opportunity_id is not None}
    opp_names = dict(db.execute(select(Opportunity.id, Opportunity.name).where(Opportunity.id.in_(opp_ids))).all()) if opp_ids else {}
    group_now = group if group in GROUPS else ""
    groups = None
    if group_now == "customer":
        groups = _customer_groups(rows, names)
    elif group_now == "field":
        groups = sfields.build_groups(db, "field", rows)
    pg = paginate(rows, page if not groups else "1", per_page=max(len(rows), 1) if groups else PER_PAGE)
    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in ("group", "page", "notice", "nt")]
    group_links = [{"code": code, "label": label, "active": code == group_now,
                    "href": request.url.path + (("?" + urlencode(keep + ([("group", code)] if code else []))) if keep or code else "")}
                   for code, label in (("", "없음"), *GROUPS.items())]
    return render(request, "needs/list.html", pg=pg, q=q.strip(), tabs=tabs, status_now=status_now, groups=groups, group_now=group_now,
                  group_choices=group_links, names=names, opp_names=opp_names, searching=bool(q.strip() or status_now or cid or fid or bad_filter),
                  source_label=needs_lib.source_label, effective_status=needs_lib.effective_status, title_of=title_of)


@router.get("/new")
def new_form(request: Request, customer_id: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    cid = parse_id(customer_id) if customer_id else None
    pre = str(cid) if cid is not None and db.get(Customer, cid) is not None else ""
    return _form(request, db, {"customer_id": pre, "content": "", "field_id": "", "status": needs_lib.STATUS_NEW, "memo": ""})


@router.post("")
def create(
    request: Request, customer_id: str = Form(""), content: str = Form(""), field_id: str = Form(""), status: str = Form(""), memo: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
):
    form = {"customer_id": customer_id, "content": content, "field_id": field_id, "status": status, "memo": memo}
    if not content.strip():
        return _form(request, db, form, error=CONTENT_REQUIRED)
    customer, st, fid, err = _lookup(db, customer_id, status, field_id, None, direct=True)
    if err:
        return _form(request, db, form, error=err)
    need = CustomerNeed(customer_id=customer.id, customer_name=customer.name, content=content.strip(), status=st, field_id=fid,
                        memo=memo.strip(), created_by_id=user.id)
    db.add(need)
    db.commit()
    db.refresh(need)
    resync_objects(db, mirror, _related_objects(db, need))
    return once.done(notices.redirect(f"/needs/{need.id}", "created"))


@router.get("/{need_id}")
def detail(need_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    need = get_or_404(db, CustomerNeed, need_id)
    opp = db.get(Opportunity, need.opportunity_id) if need.opportunity_id is not None else None
    customer = db.get(Customer, need.customer_id) if need.customer_id is not None else None
    return render(request, "needs/detail.html", need=need, opp=opp, customer=customer, title=title_of(need),
                  status=needs_lib.effective_status(need), source=needs_lib.source_label(need),
                  can_delete=needs_lib.can_delete_need(user, need), meta=ui.stamp_info(db, need), **relations.page_related(db, need, user))


@router.get("/{need_id}/edit")
def edit_form(need_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    need = get_or_404(db, CustomerNeed, need_id)
    return _form(request, db, {"customer_id": str(need.customer_id or ""), "content": need.content, "field_id": str(need.field_id or ""),
                               "status": need.status or needs_lib.STATUS_NEW, "memo": need.memo or ""}, need)


def _name_of(db: Session, model, raw: str) -> str:
    """폼의 번호 칸 → 그 기록 이름 (충돌 비교용). 비었거나 없는 번호면 빈 글."""
    try:
        obj = db.get(model, int(raw)) if str(raw).strip() else None
    except ValueError:
        obj = None
    return getattr(obj, "name", "") if obj is not None else ""


@router.post("/{need_id}")
def update(
    need_id: int, request: Request, customer_id: str = Form(""), content: str = Form(""), field_id: str = Form(""), status: str = Form(""),
    memo: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    rev: Rev = Depends(rev_form),
):
    need = get_or_404(db, CustomerNeed, need_id)
    form = {"customer_id": customer_id, "content": content, "field_id": field_id, "status": status, "memo": memo}
    # 영업기회에 묶인 니즈는 상태 칸이 폼에 없다(빈 글): 비교에서 빼야 바꾸지 않은 "상태"가 충돌 칸으로 나오지 않는다 (베타 점검 2026-11-27)
    # 고객·분야는 번호라 그대로는 비교되지 않는다: 이름으로 바꿔 비교한다 (베타 점검 3차 2026-11-27: 다른 탭이 고객을 바꿔도 충돌 표에 안 나와
    # "그래도 내 내용으로 저장" 이 고객을 몰래 되돌렸다)
    typed = {k: v for k, v in form.items() if k not in ("customer_id", "field_id") and (k != "status" or v)}
    typed["customer"] = _name_of(db, Customer, customer_id)
    typed["field"] = _name_of(db, SolutionField, field_id)
    stored = {k: getattr(need, k) for k in ("content", "status", "memo")}
    stored["customer"] = need.customer.name if need.customer is not None else ""
    stored["field"] = need.field.name if need.field is not None else ""
    if conflict := guards.check_rev(need, rev, values=typed, stored=stored):
        return _form(request, db, form, need, conflict=conflict)
    if not content.strip():
        return _form(request, db, form, need, error=CONTENT_REQUIRED)
    customer, st, fid, err = _lookup(db, customer_id, status if need.opportunity_id is None else need.status, field_id, need,
                                     direct=need.activity_id is None and need.customer_id is not None)  # 고객이 있던 직접 니즈만 고객을 비울 수 없다 (고객 없이 떨어져 나온 니즈도 고칠 수 있게)
    if err:
        return _form(request, db, form, need, error=err)
    before = _related_objects(db, need)
    if customer is not None:
        if customer.id != need.customer_id:
            opps.retarget_need_customer(need, customer.id)  # 고객이 바뀌면 묶인 영업기회에서 푼다
        need.customer_name = customer.name  # 활동 기록 폼이 같은 이름을 보여 다음 저장 때 다시 풀리지 않게
    need.content, need.field_id, need.memo = content.strip(), fid, memo.strip()
    if need.opportunity_id is None:
        need.status = st
    db.commit()
    db.refresh(need)
    resync_objects(db, mirror, [*before, *_related_objects(db, need)])
    return notices.redirect(f"/needs/{need.id}", "saved")


def load_deletable(db: Session, user: User, need_id) -> CustomerNeed:
    need = get_or_404(db, CustomerNeed, need_id)
    if not needs_lib.can_delete_need(user, need):
        raise HTTPException(status_code=403, detail="본인이 등록한 항목만 삭제할 수 있습니다.")
    return need


@router.post("/{need_id}/delete")
def delete(need_id: int, user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror)):
    need = load_deletable(db, user, need_id)
    related = _related_objects(db, need)
    db.delete(need)
    db.commit()
    resync_objects(db, mirror, related)
    return notices.redirect("/needs", "deleted")

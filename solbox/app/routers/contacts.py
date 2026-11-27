"""담당자(고객·파트너의 사람) 화면 (영업 D 단계).
- 만들기 `/contacts/new?customer_id=|partner_id=` (폼 표) · 고치기 `/contacts/{id}/edit` (고침 표시) · 상세 `/contacts/{id}` · 지우기(확인 화면 SPECS).
- 연락처 메모를 담당자로 나누기 `/customers/{id}/contacts/split`·`/partners/{id}/contacts/split`: 코드 파서가 만든 후보를 사람이 확인해 저장 (AI 없음).
규칙(같은 사람·파서)은 `app/contacts.py` 한 곳."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from app import contacts as ct
from app import guards, notices, ontology, origin, ui
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.idparse import parse_id
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Contact, Customer, Partner, User
from app.routers._common import NOT_FOUND, get_deletable_or_404, get_or_404
from app.security import can_delete, require_admin, require_user

router = APIRouter()

COMPANY_MODELS = {"customer": Customer, "partner": Partner}
SAME_PERSON = "같은 사람일 수 있어요"


def _company(db: Session, customer_id: str = "", partner_id: str = ""):
    """폼의 회사 번호 → (고객 또는 파트너). 없거나 둘 다 비면 404."""
    if (cid := parse_id(customer_id)) is not None:
        obj = db.get(Customer, cid)
    elif (pid := parse_id(partner_id)) is not None:
        obj = db.get(Partner, pid)
    else:
        obj = None
    if obj is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return obj


def _sug_text(s) -> str:
    from app import contact_suggestions as cs

    return cs.text(s)


def _code(company) -> str:
    return "customer" if isinstance(company, Customer) else "partner"


def _url(company) -> str:
    return ontology.detail_url(_code(company), company.id)


def _crumbs(company, last: str):
    return ui.crumbs(_code(company), company.name, _url(company), last)


def _role_options():
    return [("", "정하지 않음")] + list(ontology.CONTACT_ROLES.items())


def _form_page(request, company, form: dict, person: Contact | None = None, error=None, conflict=None, same=None):
    return render(
        request, "contacts/form.html", status_code=409 if conflict else 400 if error else 200,
        company=company, company_code=_code(company), company_url=_url(company), form=form, person=person, error=error, conflict=conflict, same=same,
        roles=_role_options(), crumbs=_crumbs(company, "담당자 " + ("수정" if person else "추가")),
    )


def _sync_company(db: Session, mirror: KnowledgeMirror, company) -> None:
    db.refresh(company)
    mirror.sync(company)


@router.get("/contacts/new")
def new_form(request: Request, customer_id: str = "", partner_id: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    company = _company(db, customer_id, partner_id)
    return _form_page(request, company, {f: "" for f in ct.FIELDS})


@router.post("/contacts")
def create(
    request: Request, customer_id: str = Form(""), partner_id: str = Form(""),
    name: str = Form(""), title: str = Form(""), role: str = Form(""), phone: str = Form(""), office_phone: str = Form(""), email: str = Form(""),
    note: str = Form(""), confirm_dup: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
):
    company = _company(db, customer_id, partner_id)
    values = ct.clean(dict(name=name, title=title, role=role, phone=phone, office_phone=office_phone, email=email, note=note))
    if not values["name"]:
        return _form_page(request, company, values, error="이름을 입력하세요.")
    if confirm_dup != "1" and (same := ct.same_person(company.contacts, values["name"], values["phone"], values["email"])):
        return _form_page(request, company, values, same=same)
    person = Contact(**values, created_by_id=user.id, origin_type=ontology.ORIGIN_MANUAL)
    setattr(person, _code(company), company)
    db.add(person)
    db.commit()
    _sync_company(db, mirror, company)
    return once.done(notices.redirect(_url(company), "created"))


@router.get("/contacts/{contact_id}")
def detail(contact_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    person = get_or_404(db, Contact, contact_id)
    company = person.company
    return render(
        request, "contacts/detail.html", person=person, company=company, company_code=_code(company), company_url=_url(company),
        role_label=ct.role_label(person.role), can_delete=can_delete(user, person), sug_text=_sug_text, origin=origin.view(db, user, person), meta=ui.stamp_info(db, person),
        crumbs=_crumbs(company, person.name),
    )


@router.get("/contacts/{contact_id}/edit")
def edit_form(contact_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    person = get_or_404(db, Contact, contact_id)
    return _form_page(request, person.company, {f: getattr(person, f) or "" for f in ct.FIELDS}, person)


@router.post("/contacts/{contact_id}")
def update(
    contact_id: int, request: Request,
    name: str = Form(""), title: str = Form(""), role: str = Form(""), phone: str = Form(""), office_phone: str = Form(""), email: str = Form(""),
    note: str = Form(""), confirm_dup: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), rev: Rev = Depends(rev_form),
):
    person = get_or_404(db, Contact, contact_id)
    company = person.company
    values = ct.clean(dict(name=name, title=title, role=role, phone=phone, office_phone=office_phone, email=email, note=note))
    if conflict := guards.check_rev(person, rev, values=values):
        return _form_page(request, company, values, person, conflict=conflict)
    if not values["name"]:
        return _form_page(request, company, values, person, error="이름을 입력하세요.")
    changed_keys = (ct.digits(values["phone"]) != ct.digits(person.phone) or values["email"].lower() != (person.email or "").lower()
                    or (not values["phone"] and not values["email"] and values["name"] != person.name))
    if confirm_dup != "1" and changed_keys and (same := ct.same_person([c for c in company.contacts if c is not person], values["name"], values["phone"], values["email"])):
        return _form_page(request, company, values, person, same=same)
    for field, value in values.items():
        setattr(person, field, value)
    db.commit()
    _sync_company(db, mirror, company)
    for o in person.opportunities:  # 영업기회 사본에 담당자 이름이 적힌다
        db.refresh(o)
        mirror.sync(o)
    return notices.redirect(f"/contacts/{person.id}", "saved")


@router.post("/contacts/{contact_id}/delete")
def delete(
    contact_id: int, user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    person = get_deletable_or_404(db, Contact, contact_id, user)
    company, opportunities = person.company, list(person.opportunities)
    back = _url(company)
    db.delete(person)
    db.commit()
    _sync_company(db, mirror, company)
    for o in opportunities:
        db.refresh(o)
        mirror.sync(o)
    return notices.redirect(back, "deleted")


def _split_page(request, company, rows, error=None):
    return render(
        request, "contacts/split.html", status_code=400 if error else 200, company=company, company_url=_url(company), rows=rows, roles=_role_options(),
        error=error, crumbs=_crumbs(company, "담당자로 나누기"), action=f"{_url(company)}/contacts/split",
    )


def _split_get(kind: str):
    def page(company_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
        company = get_or_404(db, COMPANY_MODELS[kind], company_id)
        return _split_page(request, company, ct.split_rows(company))
    page.__name__ = f"split_{kind}_form"
    return page


def _split_post(kind: str):
    async def save(
        company_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db),
        mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
    ):
        company = get_or_404(db, COMPANY_MODELS[kind], company_id)
        form = await request.form()
        picked = {p for p in form.getlist("pick") if str(p).isdecimal()}
        made = 0
        for i in sorted(picked, key=int):
            values = ct.clean({f: str(form.get(f"{f}_{i}", "")) for f in ct.FIELDS})
            if not values["name"]:  # 이름 없는 줄은 넣을 수 없다
                continue
            if ct.same_person(company.contacts, values["name"], values["phone"], values["email"]):
                continue  # 그사이 같은 사람이 생겼다
            person = Contact(**values, created_by_id=user.id, origin_type=ontology.ORIGIN_MANUAL)
            setattr(person, kind, company)
            db.add(person)
            db.flush()
            made += 1
        db.commit()
        if made:
            _sync_company(db, mirror, company)
        return once.done(notices.redirect(_url(company), "contacts_split" if made else "contacts_split_none"))
    save.__name__ = f"split_{kind}_save"
    return save


for _kind, _prefix in (("customer", "/customers"), ("partner", "/partners")):
    router.add_api_route(f"{_prefix}/{{company_id}}/contacts/split", _split_get(_kind), methods=["GET"])
    router.add_api_route(f"{_prefix}/{{company_id}}/contacts/split", _split_post(_kind), methods=["POST"])


# ---- 관리 › 연락처 메모 정리 (③): 모든 회사의 확실한 줄을 한 번에 담당자로 ----

@router.get("/admin/contacts-cleanup")
def cleanup_page(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    groups = ct.bulk_rows(db)
    return render(request, "contacts/cleanup.html", groups=groups, total=sum(len(r) for _, _, r in groups))


@router.post("/admin/contacts-cleanup")
def cleanup_apply(
    user: User = Depends(require_admin), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
):
    companies = [c for c, _, _ in ct.bulk_rows(db)]
    made = ct.bulk_apply(db, user)
    db.commit()
    for c in companies:
        _sync_company(db, mirror, c)
    return once.done(notices.redirect("/admin/contacts-cleanup", "contacts_cleanup" if made else "contacts_cleanup_none"))


# ---- 담당자 변경 제안 (④): 반영·다른 사람·무시·모두 반영 ----

def _suggestion(db: Session, sid: int):
    from app.models import ContactSuggestion

    return get_or_404(db, ContactSuggestion, sid)


def _after(back: str, contact_id: int | None) -> str:
    from app.routers.ai_inbox import back_target

    return back_target(back, f"/contacts/{contact_id}" if contact_id else "/ai/inbox")


def _suggestion_post(action: str):
    def post(sid: int, back: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db),
             mirror: KnowledgeMirror = Depends(get_mirror)):
        from app import contact_suggestions as cs

        s = _suggestion(db, sid)
        contact = s.contact
        if action == "apply":
            state = cs.apply(db, s, user)
            code = {"applied": "contact_suggestion_applied", "changed": "contact_suggestion_changed"}.get(state, "contact_suggestion_done")
        elif action == "new":
            code = "contact_suggestion_new" if cs.apply_as_new(db, s, user) is not None else "contact_suggestion_done"
        else:
            code = "contact_suggestion_dismissed" if cs.dismiss(db, s, user) == "dismissed" else "contact_suggestion_done"
        db.commit()
        _sync_company(db, mirror, contact.company)
        return notices.redirect(_after(back, contact.id), code)
    post.__name__ = f"suggestion_{action}"
    return post


for _action in ("apply", "new", "dismiss"):
    router.add_api_route(f"/contacts/suggestions/{{sid}}/{_action}", _suggestion_post(_action), methods=["POST"])


@router.post("/contacts/suggestions/apply-all")
def suggestions_apply_all(back: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db),
                          mirror: KnowledgeMirror = Depends(get_mirror)):
    """같은 이름의 새 명함(person)을 뺀 열린 제안을 모두 반영한다 (그사이 바뀐 것은 반영하지 않고 닫는다)."""
    from app import contact_suggestions as cs

    companies = []
    for s in cs.pending(db):
        if s.field == "person":
            continue
        cs.apply(db, s, user)
        companies.append(s.contact.company)
    db.commit()
    for c in dict.fromkeys(x for x in companies if x is not None):
        _sync_company(db, mirror, c)
    return notices.redirect(_after(back, None) if back else "/ai/inbox", "contact_suggestion_applied")

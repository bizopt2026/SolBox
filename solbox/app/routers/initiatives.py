"""과제 화면 (AI 코워크 ①): 이슈·개별 과제. 담당·기한·상태·사업 방향을 두고 솔루션·파트너·고객·영업기회·프로젝트와 잇는다."""
from __future__ import annotations

from collections import Counter
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app import guards, notices, ontology, relations, ui, workspaces as ws
from app import alignment
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.idparse import parse_id
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Customer, Initiative, Opportunity, Partner, Project, Solution, Strategy, User
from app.records import pick, remove_workspace_files
from app.routers._common import get_deletable_or_404, get_or_404, list_filter, paginate, resync_objects, row_info, sort_rows
from app.security import can_delete, require_user

router = APIRouter(prefix="/initiatives")
FIELDS = ("title", "kind", "status", "owner_user_id", "due", "strategy_id", "description", "instructions", "progress")
LINKS = {  # 폼 칸 -> (모델, 과제의 관계 이름)
    "solution_ids": (Solution, "solutions"), "partner_ids": (Partner, "partners"), "customer_ids": (Customer, "customers"),
    "opportunity_ids": (Opportunity, "opportunities"), "project_ids": (Project, "projects"),
}
BAD_OWNER = ui.FieldError("담당은 목록의 사용자여야 합니다.", "owner_user_id")
BAD_STRATEGY = ui.FieldError("없는 사업 방향입니다.", "strategy_id")


def _choices(db: Session, keep_strategy_id: int | None = None) -> dict:
    """폼의 고르는 칸 목록. 연결 고르기는 이름(name) 속성으로 그린다."""
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.name, User.id)).all()
    return {
        "users": [(str(u.id), u.name or u.username) for u in users],
        "strategies": [(str(s.id), s.title + ("" if s.status == ontology.STRATEGY_STATUS_AGREED else f" ({ontology.STRATEGY_STATUSES.get(s.status, s.status)})"))
                       for s in db.scalars(select(Strategy).where(or_(Strategy.status != "closed", Strategy.id == keep_strategy_id))
                                           .order_by(Strategy.title, Strategy.id))],  # 종료한 방향은 새로 고를 수 없지만 지금 속한 방향은 남긴다
        "solutions": db.scalars(select(Solution).order_by(Solution.name, Solution.id)).all(),
        "partners": db.scalars(select(Partner).order_by(Partner.name, Partner.id)).all(),
        "customers": db.scalars(select(Customer).order_by(Customer.name, Customer.id)).all(),
        "opportunities": db.scalars(select(Opportunity).order_by(Opportunity.name, Opportunity.id)).all(),
        "projects": db.scalars(select(Project).order_by(Project.name, Project.id)).all(),
    }


def _form_page(request: Request, db: Session, form: dict, item: Initiative | None = None, error=None, conflict=None):
    return render(request, "initiatives/form.html", status_code=409 if conflict else 400 if error else 200,
                  form=form, item=item, error=error, conflict=conflict,
                  **_choices(db, parse_id(str(form.get("strategy_id") or "")) or (item.strategy_id if item else None)))


def _detail_page(request: Request, db: Session, user: User, item: Initiative, status_code: int = 200, conflict=None, **extra):
    today = date.today()
    return render(request, "initiatives/detail.html", status_code=status_code, item=item, **extra, today=today, changes=ws.change_rows(db, item),
                  files=item.files, file_del={f.id: can_delete(user, f) for f in item.files}, conflict=conflict,
                  strategy_active=bool(item.strategy and ontology.strategy_is_active(item.strategy, today)),
                  **relations.page_related(db, item, user), can_delete=can_delete(user, item), meta=ui.stamp_info(db, item))


def _values(title, kind, status, owner_user_id, due, strategy_id, description, instructions, progress, links: dict) -> dict:
    return {"title": title.strip(), "kind": kind.strip() or ontology.INITIATIVE_KIND_DEFAULT, "status": status.strip() or ontology.INITIATIVE_STATUS_DEFAULT,
            "owner_user_id": owner_user_id.strip(), "due": due.strip(), "strategy_id": strategy_id.strip(),
            "description": description.strip(), "instructions": instructions.strip(), "progress": progress.strip(),
            **{k: [n for v in vals if (n := parse_id(v)) is not None] for k, vals in links.items()}}


def _check(db: Session, values: dict) -> tuple[dict | None, ui.FieldError | None]:
    if not values["title"]:
        return None, ws.TITLE_REQUIRED
    if values["kind"] not in ontology.INITIATIVE_KINDS:
        return None, ws.BAD_KIND
    if values["status"] not in ontology.INITIATIVE_STATUSES:
        return None, ws.BAD_STATUS
    due = ws.parse_day(values["due"])
    if due == "bad":
        return None, ws.BAD_DATE
    owner = None
    if values["owner_user_id"]:
        oid = parse_id(values["owner_user_id"])
        if oid is None or db.get(User, oid) is None:
            return None, BAD_OWNER
        owner = oid
    strategy = None
    if values["strategy_id"]:
        sid = parse_id(values["strategy_id"])
        if sid is None or db.get(Strategy, sid) is None:
            return None, BAD_STRATEGY
        strategy = sid
    clean = {k: values[k] for k in ("title", "kind", "status", "description", "instructions", "progress")}
    return {**clean, "due": due, "owner_user_id": owner, "strategy_id": strategy}, None


def _set_links(db: Session, item: Initiative, values: dict) -> None:
    for field, (model, rel) in LINKS.items():
        setattr(item, rel, pick(db, model, [str(n) for n in values.get(field, [])]))


def _linked(item: Initiative) -> list:
    return [*item.solutions, *item.partners, *item.customers, *item.opportunities, *item.projects]


@router.get("")
def list_initiatives(
    request: Request, q: str = "", kind: str = "", open: str = "1", who: str = "", align: str = "", page: str = "1",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    found = ws.search(db, Initiative, q, Initiative.description, Initiative.progress,
                      loaders=(selectinload(Initiative.owner), selectinload(Initiative.strategy)))
    found, flt = list_filter(db, request, "initiative", sort_rows(request, "initiative", found, name_of=lambda i: i.title))
    open_on = open != "0"
    open_n = sum(1 for i in found if i.status in ontology.INITIATIVE_OPEN)
    if open_on:
        found = [i for i in found if i.status in ontology.INITIATIVE_OPEN]
    mine_on = who == "mine"
    mine_n = sum(1 for i in found if i.owner_user_id == user.id)
    if mine_on:
        found = [i for i in found if i.owner_user_id == user.id]
    off_ids = alignment.misaligned_ids(db, "initiative")  # 정렬 점검의 최신 판단이 "방향 밖" (AI 코워크 ③)
    align_on = align == "off"
    align_n = sum(1 for i in found if i.id in off_ids)
    if align_on:
        found = [i for i in found if i.id in off_ids]
    counts = Counter(i.kind for i in found)
    total = len(found)
    if kind in ontology.INITIATIVE_KINDS:
        found = [i for i in found if i.kind == kind]
    else:
        kind = ""
    pg = paginate(found, page)
    tabs = ui.seg_tabs(request, "kind", list(ontology.INITIATIVE_KINDS.items()), counts, kind, total)

    def toggle(param: str, on_value: str | None) -> str:
        keep = [(k, v) for k, v in request.query_params.multi_items() if k not in (param, "page", "notice", "nt")]
        return ("/initiatives?" + urlencode(keep + ([(param, on_value)] if on_value is not None else []))).rstrip("?")

    return render(request, "initiatives/list.html", pg=pg, q=q.strip(), kind=kind, flt=flt, tabs=tabs, info=row_info(db, "initiative", pg.items),
                  open_on=open_on, open_n=open_n, open_href=toggle("open", "0" if open_on else None),
                  mine_on=mine_on, mine_n=mine_n, mine_href=toggle("who", None if mine_on else "mine"), today=date.today(),
                  align_on=align_on, align_n=align_n, align_href=toggle("align", None if align_on else "off"), off_ids=off_ids)


@router.get("/new")
def new_form(request: Request, strategy_id: str = "", kind: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    sid = parse_id(strategy_id)
    form = {**{f: "" for f in FIELDS}, **{k: [] for k in LINKS}, "kind": kind if kind in ontology.INITIATIVE_KINDS else ontology.INITIATIVE_KIND_DEFAULT,
            "status": ontology.INITIATIVE_STATUS_DEFAULT, "owner_user_id": str(user.id),
            "strategy_id": str(sid) if sid is not None and db.get(Strategy, sid) is not None else ""}  # 없는 방향 번호는 무시한다
    for field, (model, _rel) in LINKS.items():  # 상세 화면의 "+ 과제" 미리 채우기 (?solution_id= …): 있는 기록일 때만
        n = parse_id(request.query_params.get(field[:-1], ""))
        if n is not None and db.get(model, n) is not None:
            form[field] = [n]
    return _form_page(request, db, form)


@router.post("")
def create(
    request: Request,
    title: str = Form(""), kind: str = Form(""), status: str = Form(""), owner_user_id: str = Form(""), due: str = Form(""),
    strategy_id: str = Form(""), description: str = Form(""), instructions: str = Form(""), progress: str = Form(""),
    solution_ids: list[str] = Form([]), partner_ids: list[str] = Form([]), customer_ids: list[str] = Form([]),
    opportunity_ids: list[str] = Form([]), project_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    once: Once = Depends(form_once),
):
    values = _values(title, kind, status, owner_user_id, due, strategy_id, description, instructions, progress,
                      {"solution_ids": solution_ids, "partner_ids": partner_ids, "customer_ids": customer_ids,
                       "opportunity_ids": opportunity_ids, "project_ids": project_ids})
    clean, error = _check(db, values)
    if error:
        return _form_page(request, db, values, error=error)
    item = Initiative(**clean, created_by_id=user.id)
    _set_links(db, item, values)
    db.add(item)
    db.commit()
    db.refresh(item)
    mirror.sync(item)
    resync_objects(db, mirror, [*_linked(item), item.strategy])
    alignment.request(db, item, user.id, request)  # 합의된 방향이 있으면 정렬 점검을 뒤에 넣는다 (AI 코워크 ③)
    return once.done(notices.redirect(f"/initiatives/{item.id}", "created"))


@router.get("/{item_id}")
def detail(item_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, Initiative, item_id))


def _form_of(item: Initiative) -> dict:
    return {"title": item.title, "kind": item.kind, "status": item.status, "owner_user_id": str(item.owner_user_id or ""),
            "due": item.due.isoformat() if item.due else "", "strategy_id": str(item.strategy_id or ""),
            "description": item.description or "", "instructions": item.instructions or "", "progress": item.progress or "",
            **{field: [o.id for o in getattr(item, rel)] for field, (_m, rel) in LINKS.items()}}


@router.get("/{item_id}/edit")
def edit_form(item_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    item = get_or_404(db, Initiative, item_id)
    return _form_page(request, db, _form_of(item), item)


@router.post("/{item_id}")
def update(
    item_id: int, request: Request,
    title: str = Form(""), kind: str = Form(""), status: str = Form(""), owner_user_id: str = Form(""), due: str = Form(""),
    strategy_id: str = Form(""), description: str = Form(""), instructions: str = Form(""), progress: str = Form(""),
    solution_ids: list[str] = Form([]), partner_ids: list[str] = Form([]), customer_ids: list[str] = Form([]),
    opportunity_ids: list[str] = Form([]), project_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    rev: Rev = Depends(rev_form),
):
    item = get_or_404(db, Initiative, item_id)
    values = _values(title, kind, status, owner_user_id, due, strategy_id, description, instructions, progress,
                     {"solution_ids": solution_ids, "partner_ids": partner_ids, "customer_ids": customer_ids,
                      "opportunity_ids": opportunity_ids, "project_ids": project_ids})
    if conflict := guards.check_rev(item, rev, values={k: values[k] for k in FIELDS}):
        return _form_page(request, db, values, item, conflict=conflict)
    clean, error = _check(db, values)
    if error:
        return _form_page(request, db, values, item, error)
    before = [*_linked(item), item.strategy]
    for field, value in clean.items():
        setattr(item, field, value)
    _set_links(db, item, values)
    db.commit()
    db.refresh(item)
    mirror.sync(item)
    resync_objects(db, mirror, [*before, *_linked(item), item.strategy])
    alignment.request(db, item, user.id, request)  # 합의된 방향이 있으면 정렬 점검을 뒤에 넣는다 (AI 코워크 ③)
    return notices.redirect(f"/initiatives/{item.id}", "saved")


@router.post("/{item_id}/status")
def change_status(
    item_id: int, request: Request, status: str = Form(""), progress_line: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    rev: Rev = Depends(rev_form),
):
    """상세 화면에서 상태만 바꾸기 (진행 메모 한 줄을 함께 덧붙일 수 있다)."""
    item = get_or_404(db, Initiative, item_id)
    if conflict := guards.check_rev(item, rev):  # 쓴 값 그대로 다시 그린다 ("그래도 내 내용으로 저장" 도 그 값을 보낸다)
        return _detail_page(request, db, user, item, status_code=409, conflict=conflict, typed_status=status, typed_line=progress_line)
    if status not in ontology.INITIATIVE_STATUSES:
        raise HTTPException(status_code=400, detail=str(ws.BAD_STATUS))
    item.status = status
    line = " ".join(progress_line.split())
    if line:
        item.progress = f"{date.today().isoformat()} {line}\n{item.progress or ''}".strip()
    db.commit()
    db.refresh(item)
    mirror.sync(item)
    return notices.redirect(f"/initiatives/{item.id}", "saved")


@router.post("/{item_id}/delete")
def delete(
    item_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    item = get_deletable_or_404(db, Initiative, item_id, user)
    related = [*_linked(item), item.strategy]
    paths = remove_workspace_files(request.app.state.settings, item)
    mirror.remove(item)
    db.delete(item)  # 연결 줄·자료·이력은 함께 지워지고, 연결된 기록은 남는다
    db.commit()
    for p in paths:
        p.unlink(missing_ok=True)
    resync_objects(db, mirror, related)
    return notices.redirect("/initiatives", "deleted")

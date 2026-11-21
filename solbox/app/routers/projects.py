"""프로젝트 화면 (옛 "기존 사업"). 옛 주소 /businesses 는 /projects 로 영구 이동한다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.idparse import parse_id
from app import ontology, opportunities as opps, relations, ui
from app import guards, notices
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import BM, Activity, Customer, Project, User
from app.routers._common import (
    list_filter,
    activities_linked_to, get_deletable_or_404, get_or_404, name_error, newest_first, duplicate_warning, submitted_fields,
    resync_activities, resync_objects, resync_related, row_info, search_by_name, paginate, sort_rows,
)
from app.security import can_delete, require_user

router = APIRouter(prefix="/projects")
FIELDS = ("name", "customer_id", "description", "period", "status", "memo", "bm_id")
CUSTOMER_REQUIRED = ui.FieldError("고객을 선택하세요.", "customer_id")


def status_choices(db: Session) -> list[str]:
    """이미 쓰인 상태 값 (많이 쓴 순): 입력칸 제안에 쓴다. 상태 값을 정하지는 않는다 (자유 글)."""
    rows = db.execute(select(Project.status, func.count()).where(Project.status != "").group_by(Project.status).order_by(func.count().desc(), Project.status))
    seen: dict[str, None] = {}
    for value, _ in rows:
        seen.setdefault(" ".join((value or "").split()), None)
    return [v for v in seen if v]


def _form_page(
    request: Request, db: Session, form: dict, project: Project | None = None, error: str | None = None, conflict=None, duplicate=None
):
    return render(
        request, "projects/form.html", status_code=409 if conflict else 400 if error else 200,
        form=form, project=project, customers=search_by_name(db, Customer), bms=search_by_name(db, BM), error=error, conflict=conflict, duplicate=duplicate,
        status_choices=status_choices(db),
    )


def _parse(name, customer_id, description, period, status, memo, db: Session, bm_id: str | None = None) -> tuple[dict, str | None]:
    """폼 값을 다듬고 오류 문구를 돌려준다. `customer_id` 는 존재하는 고객이어야 하고 `bm_id` 는 있으면 등록된 BM 이어야 한다
    (칸이 아예 오지 않으면(None) BM 은 그대로 둔다)."""
    values = dict(
        name=name.strip(), customer_id=customer_id.strip(), description=description.strip(),
        period=period.strip(), status=" ".join(status.split()), memo=memo.strip(),  # 상태는 자유 글이라 앞뒤·가운데 공백만 정리한다
    )
    if error := name_error(name):
        return values, error
    if (cid := parse_id(values["customer_id"])) is None or db.get(Customer, cid) is None:
        return values, CUSTOMER_REQUIRED
    values["customer_id"] = cid
    if bm_id is not None:
        values["bm_id"] = bm_id.strip()
        if values["bm_id"]:
            if (bid := parse_id(values["bm_id"])) is None or db.get(BM, bid) is None:
                return values, opps.BAD_BM
            values["bm_id"] = bid
        else:
            values["bm_id"] = None
    return values, None


@router.get("")
def list_projects(
    request: Request, q: str = "", page: str = "1",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    projects = search_by_name(db, Project, q, Customer.name, joins=(Project.customer,), loaders=(selectinload(Project.customer),))
    projects, flt = list_filter(db, request, "project", sort_rows(request, "project", projects, db=db))
    pg = paginate(projects, page)
    return render(request, "projects/list.html", pg=pg, q=q.strip(), flt=flt, info=row_info(db, "project", pg.items))


@router.get("/new")
def new_form(
    request: Request, customer_id: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)
):
    cid = parse_id(customer_id)
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "customer_id": str(cid) if cid is not None and db.get(Customer, cid) is not None else ""})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), customer_id: str = Form(""), description: str = Form(""),
    period: str = Form(""), status: str = Form(""), memo: str = Form(""), bm_id: str = Form(""), confirm_dup: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), fields: frozenset = Depends(submitted_fields), once: Once = Depends(form_once),
):
    values, error = _parse(name, customer_id, description, period, status, memo, db, bm_id if "bm_id" in fields else None)
    if error:
        return _form_page(request, db, {**{f: "" for f in FIELDS}, **values}, error=error)
    if dup := duplicate_warning(db, Project, name, confirm_dup):
        return _form_page(request, db, {**{f: "" for f in FIELDS}, **values}, duplicate=dup)
    project = Project(**values, created_by_id=user.id)
    db.add(project)
    db.commit()
    db.refresh(project.customer)
    mirror.sync(project)
    mirror.sync(project.customer)  # 고객 사본의 프로젝트 목록
    resync_objects(db, mirror, [project.bm])  # BM 사본의 따르는 프로젝트 목록
    return once.done(notices.redirect(f"/projects/{project.id}", "created"))


@router.get("/{project_id}")
def detail(project_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = get_or_404(db, Project, project_id)
    return render(
        request, "projects/detail.html", project=project,
        **relations.page_related(db, project, user),
        opportunity=opps.opportunity_of_project(db, project),
        can_delete=can_delete(user, project), meta=ui.stamp_info(db, project),
    )


@router.get("/{project_id}/edit")
def edit_form(project_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = get_or_404(db, Project, project_id)
    return _form_page(request, db, {**{f: getattr(project, f) for f in FIELDS}, "bm_id": project.bm_id or ""}, project)


@router.post("/{project_id}")
def update(
    project_id: int, request: Request,
    name: str = Form(""), customer_id: str = Form(""), description: str = Form(""),
    period: str = Form(""), status: str = Form(""), memo: str = Form(""), bm_id: str = Form(""), confirm_dup: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), fields: frozenset = Depends(submitted_fields), rev: Rev = Depends(rev_form),
):
    project = get_or_404(db, Project, project_id)
    values, error = _parse(name, customer_id, description, period, status, memo, db, bm_id if "bm_id" in fields else None)
    if conflict := guards.check_rev(project, rev, values=values):
        return _form_page(request, db, {**{f: getattr(project, f) for f in FIELDS}, **values}, project, conflict=conflict)
    if error is None:
        error = opps.project_customer_problem(db, project, values["customer_id"])
    if error:
        return _form_page(request, db, {**{f: getattr(project, f) for f in FIELDS}, **values}, project, error)
    if dup := duplicate_warning(db, Project, name, confirm_dup, exclude=project):
        return _form_page(request, db, {**{f: getattr(project, f) for f in FIELDS}, **values}, project, duplicate=dup)
    old_customer, old_bm = project.customer, project.bm
    for field, value in values.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    mirror.sync(project)
    resync_related(db, mirror, project)  # 새 고객 사본의 프로젝트 목록과 활동 기록
    if old_customer.id != project.customer_id:  # 옛 고객 사본의 프로젝트 목록
        db.refresh(old_customer)
        mirror.sync(old_customer)
    resync_objects(db, mirror, [old_bm])  # 옛 BM 사본의 따르는 프로젝트 목록 (새 BM 은 resync_related 가 다시 쓴다)
    return notices.redirect(f"/projects/{project.id}", "saved")


@router.post("/{project_id}/delete")
def delete(
    project_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    project = get_deletable_or_404(db, Project, project_id, user)
    customer, bm = project.customer, project.bm
    activities = activities_linked_to(db, Activity.projects, project)
    freed = opps.release_project(db, project)  # 이어진 영업기회의 프로젝트 연결을 비운다 (영업기회는 남는다)
    mirror.remove(project)
    db.delete(project)  # 활동 기록과의 연결 행만 함께 지워진다
    db.commit()
    db.refresh(customer)
    mirror.sync(customer)
    resync_activities(db, mirror, activities)
    resync_objects(db, mirror, [*freed, bm])
    return notices.redirect("/projects", "deleted")


# ---- 옛 주소 (/businesses...) 는 새 주소로 영구 이동한다 (GET: 북마크·옛 링크용. POST: 업그레이드 때 열려 있던 옛 화면의 저장) ----
legacy_router = APIRouter()


@legacy_router.get("/businesses", include_in_schema=False)
@legacy_router.get("/businesses/{rest:path}", include_in_schema=False)
def legacy_redirect(request: Request, rest: str = ""):
    old, new = next(iter(ontology.LEGACY_URL_PREFIXES.items()))
    target = new + (f"/{rest}" if rest else "")
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=301)


@legacy_router.post("/businesses", include_in_schema=False)
@legacy_router.post("/businesses/{rest:path}", include_in_schema=False)
def legacy_post_redirect(request: Request, rest: str = ""):
    """업그레이드 때 열려 있던 옛 화면의 저장·삭제: 방식과 내용을 그대로 두고 새 주소로 보낸다 (307)."""
    old, new = next(iter(ontology.LEGACY_URL_PREFIXES.items()))
    target = new + (f"/{rest}" if rest else "")
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=307)

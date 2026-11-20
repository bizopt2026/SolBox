"""프로젝트 화면 (옛 "기존 사업"). 옛 주소 /businesses 는 /projects 로 영구 이동한다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import ontology
from app.db import get_db
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Activity, Customer, Project, User
from app.routers._common import (
    activities_linked_to, get_deletable_or_404, get_or_404, name_error, newest_first,
    resync_activities, resync_related, search_by_name, paginate,
)
from app.security import can_delete, require_user

router = APIRouter(prefix="/projects")
FIELDS = ("name", "customer_id", "description", "period", "status", "memo")
CUSTOMER_REQUIRED = "고객을 선택하세요."


def _form_page(
    request: Request, db: Session, form: dict, project: Project | None = None, error: str | None = None
):
    return render(
        request, "projects/form.html", status_code=400 if error else 200,
        form=form, project=project, customers=search_by_name(db, Customer), error=error,
    )


def _parse(name, customer_id, description, period, status, memo, db: Session) -> tuple[dict, str | None]:
    """폼 값을 다듬고 오류 문구를 돌려준다. `customer_id` 는 존재하는 고객이어야 한다."""
    values = dict(
        name=name.strip(), customer_id=customer_id.strip(), description=description.strip(),
        period=period.strip(), status=status.strip(), memo=memo.strip(),
    )
    if error := name_error(name):
        return values, error
    if not values["customer_id"].isdigit() or db.get(Customer, int(values["customer_id"])) is None:
        return values, CUSTOMER_REQUIRED
    values["customer_id"] = int(values["customer_id"])
    return values, None


@router.get("")
def list_projects(
    request: Request, q: str = "", page: str = "1",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    projects = search_by_name(db, Project, q, Customer.name, joins=(Project.customer,))
    return render(request, "projects/list.html", pg=paginate(projects, page), q=q.strip())


@router.get("/new")
def new_form(
    request: Request, customer_id: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "customer_id": customer_id})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), customer_id: str = Form(""), description: str = Form(""),
    period: str = Form(""), status: str = Form(""), memo: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    values, error = _parse(name, customer_id, description, period, status, memo, db)
    if error:
        return _form_page(request, db, values, error=error)
    project = Project(**values, created_by_id=user.id)
    db.add(project)
    db.commit()
    db.refresh(project.customer)
    mirror.sync(project)
    mirror.sync(project.customer)  # 고객 사본의 프로젝트 목록
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.get("/{project_id}")
def detail(project_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = get_or_404(db, Project, project_id)
    return render(
        request, "projects/detail.html", project=project,
        activities=newest_first(activities_linked_to(db, Activity.projects, project)),
        proposals=sorted(project.proposals, key=lambda p: p.updated_at, reverse=True),
        can_delete=can_delete(user, project),
    )


@router.get("/{project_id}/edit")
def edit_form(project_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = get_or_404(db, Project, project_id)
    return _form_page(request, db, {f: getattr(project, f) for f in FIELDS}, project)


@router.post("/{project_id}")
def update(
    project_id: int, request: Request,
    name: str = Form(""), customer_id: str = Form(""), description: str = Form(""),
    period: str = Form(""), status: str = Form(""), memo: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    project = get_or_404(db, Project, project_id)
    values, error = _parse(name, customer_id, description, period, status, memo, db)
    if error:
        return _form_page(request, db, values, project, error)
    old_customer = project.customer
    for field, value in values.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    mirror.sync(project)
    resync_related(db, mirror, project)  # 새 고객 사본의 프로젝트 목록과 활동 기록
    if old_customer.id != project.customer_id:  # 옛 고객 사본의 프로젝트 목록
        db.refresh(old_customer)
        mirror.sync(old_customer)
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.post("/{project_id}/delete")
def delete(
    project_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    project = get_deletable_or_404(db, Project, project_id, user)
    customer = project.customer
    activities = activities_linked_to(db, Activity.projects, project)
    mirror.remove(project)
    db.delete(project)  # 활동 기록과의 연결 행만 함께 지워진다
    db.commit()
    db.refresh(customer)
    mirror.sync(customer)
    resync_activities(db, mirror, activities)
    return RedirectResponse("/projects", status_code=303)


# ---- 옛 주소 (/businesses...) 는 새 주소로 영구 이동한다 (GET 만. 북마크·옛 링크용) ----
legacy_router = APIRouter()


@legacy_router.get("/businesses", include_in_schema=False)
@legacy_router.get("/businesses/{rest:path}", include_in_schema=False)
def legacy_redirect(request: Request, rest: str = ""):
    old, new = next(iter(ontology.LEGACY_URL_PREFIXES.items()))
    target = new + (f"/{rest}" if rest else "")
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=301)

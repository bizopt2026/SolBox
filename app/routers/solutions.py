"""솔루션 화면과 발굴 단계."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import labels
from app.db import get_db
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Activity, Partner, Solution, User
from app.routers._common import (
    activities_linked_to, get_deletable_or_404, get_or_404, name_error, newest_first,
    resync_activities, search_by_name,
)
from app.security import can_delete, require_user

router = APIRouter(prefix="/solutions")
FIELDS = ("name", "summary", "description", "partner_id", "stage", "usage_direction", "applicable_types", "tags")
BAD_STAGE = "알 수 없는 단계입니다."
BAD_PARTNER = "존재하지 않는 파트너입니다."


def _form_page(
    request: Request, db: Session, form: dict, solution: Solution | None = None, error: str | None = None
):
    return render(
        request, "solutions/form.html", status_code=400 if error else 200,
        form=form, solution=solution, partners=search_by_name(db, Partner), error=error,
    )


def _detail_page(request: Request, db: Session, user: User, solution: Solution):
    return render(
        request, "solutions/detail.html",
        solution=solution,
        activities=newest_first(activities_linked_to(db, Activity.solutions, solution)),
        can_delete=can_delete(user, solution),
    )


def _normalize_tags(tags: str) -> str:
    return ",".join(t for t in (p.strip() for p in tags.split(",")) if t)


def _parse(db: Session, name, summary, description, partner_id, stage, usage_direction, applicable_types, tags):
    """폼 값을 다듬고 오류 문구를 돌려준다. 단계 코드와 파트너가 올바른지 확인한다."""
    values = dict(
        name=name.strip(), summary=summary.strip(), description=description.strip(),
        partner_id=partner_id.strip(), stage=stage.strip() or "intake",
        usage_direction=usage_direction.strip(), applicable_types=applicable_types.strip(),
        tags=_normalize_tags(tags),
    )
    if error := name_error(name):
        return values, error
    if values["stage"] not in labels.STAGES:
        return values, BAD_STAGE
    if values["partner_id"]:
        pid = values["partner_id"]
        if not pid.isdigit() or db.get(Partner, int(pid)) is None:
            return values, BAD_PARTNER
        values["partner_id"] = int(pid)
    else:
        values["partner_id"] = None
    return values, None


@router.get("")
def list_solutions(
    request: Request, q: str = "", stage: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    solutions = search_by_name(db, Solution, q, Solution.summary, Solution.tags)
    if stage:
        solutions = [s for s in solutions if s.stage == stage]
    return render(request, "solutions/list.html", solutions=solutions, q=q.strip(), stage=stage)


@router.get("/new")
def new_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "stage": "intake"})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), summary: str = Form(""), description: str = Form(""),
    partner_id: str = Form(""), stage: str = Form("intake"), usage_direction: str = Form(""),
    applicable_types: str = Form(""), tags: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    values, error = _parse(db, name, summary, description, partner_id, stage, usage_direction, applicable_types, tags)
    if error:
        return _form_page(request, db, values, error=error)
    solution = Solution(**values, created_by_id=user.id)
    db.add(solution)
    db.commit()
    mirror.sync(solution)
    return RedirectResponse(f"/solutions/{solution.id}", status_code=303)


@router.get("/{solution_id}")
def detail(solution_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, Solution, solution_id))


@router.get("/{solution_id}/edit")
def edit_form(solution_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    solution = get_or_404(db, Solution, solution_id)
    form = {f: getattr(solution, f) for f in FIELDS}
    form["partner_id"] = solution.partner_id or ""
    return _form_page(request, db, form, solution)


@router.post("/{solution_id}/stage")
def change_stage(
    solution_id: int, stage: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    solution = get_or_404(db, Solution, solution_id)
    if stage not in labels.STAGES:
        raise HTTPException(status_code=400, detail=BAD_STAGE)
    solution.stage = stage
    db.commit()
    mirror.sync(solution)
    return RedirectResponse(f"/solutions/{solution.id}", status_code=303)


@router.post("/{solution_id}")
def update(
    solution_id: int, request: Request,
    name: str = Form(""), summary: str = Form(""), description: str = Form(""),
    partner_id: str = Form(""), stage: str = Form("intake"), usage_direction: str = Form(""),
    applicable_types: str = Form(""), tags: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    solution = get_or_404(db, Solution, solution_id)
    values, error = _parse(db, name, summary, description, partner_id, stage, usage_direction, applicable_types, tags)
    if error:
        return _form_page(request, db, values, solution, error)
    for field, value in values.items():
        setattr(solution, field, value)
    db.commit()
    mirror.sync(solution)
    return RedirectResponse(f"/solutions/{solution.id}", status_code=303)


@router.post("/{solution_id}/delete")
def delete(
    solution_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    solution = get_deletable_or_404(db, Solution, solution_id, user)
    activities = activities_linked_to(db, Activity.solutions, solution)
    mirror.remove(solution)
    db.delete(solution)
    db.commit()
    resync_activities(db, mirror, activities)
    return RedirectResponse("/solutions", status_code=303)

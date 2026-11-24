"""솔루션 분야 관리 (관리자 전용, 베타 AD): 더하기·이름 고치기·순서·사용 끄기·지우기. 규칙은 `app/solution_fields.py` 한 곳."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import notices, ontology, solution_fields as sfields
from app.db import get_db
from app.guards import Once, form_once
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import SolutionField, User
from app.routers._common import resync_objects
from app.security import require_admin

router = APIRouter(prefix="/admin/solution-fields")
NOT_FOUND = "분야를 찾을 수 없습니다."


def _page(request: Request, db: Session, error: str | None = None, error_id: int | None = None, typed: str = "", status_code: int = 200):
    """`error_id` 는 오류가 난 줄의 번호 (새로 더하다 난 오류는 None), `typed` 는 그 칸에 쓴 글."""
    fields = sfields.all_fields(db)
    return render(
        request, "admin/solution_fields.html", status_code=status_code, fields=fields, counts=sfields.usage_counts(db),
        error=error, error_id=error_id, typed=typed, name_max=ontology.SOLUTION_FIELD_NAME_MAX, none_count=sfields.none_count(db),
    )


def _get(db: Session, field_id: int) -> SolutionField:
    found = db.get(SolutionField, field_id)
    if found is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return found


def _resync(db: Session, mirror: KnowledgeMirror, field: SolutionField) -> None:
    """이 분야가 적힌 솔루션 사본을 다시 쓴다 (이름이 바뀌거나 사용을 끈 뒤)."""
    resync_objects(db, mirror, sfields.solutions_of(db, field))


@router.get("")
def list_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _page(request, db)


@router.post("")
def create(
    request: Request, name: str = Form(""),
    admin: User = Depends(require_admin), db: Session = Depends(get_db), once: Once = Depends(form_once),
):
    if problem := sfields.name_problem(db, name):
        return _page(request, db, problem, None, name, 400)
    sfields.add(db, name)
    db.commit()
    return once.done(notices.redirect("/admin/solution-fields", "field_created"))


@router.post("/{field_id}")
def rename(
    field_id: int, request: Request, name: str = Form(""),
    admin: User = Depends(require_admin), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    field = _get(db, field_id)
    if problem := sfields.name_problem(db, name, exclude=field):
        return _page(request, db, problem, field.id, name, 400)
    new = sfields.clean_name(name)
    if new != field.name:
        field.name = new
        db.commit()
        _resync(db, mirror, field)
    return notices.redirect("/admin/solution-fields", "field_renamed")


@router.post("/{field_id}/move")
def move(
    field_id: int, direction: str = Form(""),
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    field = _get(db, field_id)
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="움직일 방향이 올바르지 않습니다.")
    if sfields.move(db, field, -1 if direction == "up" else 1):
        db.commit()
    return notices.redirect("/admin/solution-fields", "field_moved")


@router.post("/{field_id}/active")
def set_active(
    field_id: int, active: str = Form(""),
    admin: User = Depends(require_admin), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    field = _get(db, field_id)
    if active not in ("1", "0"):
        raise HTTPException(status_code=400, detail="사용 여부 값이 올바르지 않습니다.")
    want = active == "1"
    if field.active != want:
        field.active = want
        db.commit()
        _resync(db, mirror, field)
    return notices.redirect("/admin/solution-fields", "field_on" if want else "field_off")


@router.post("/{field_id}/delete")
def delete(
    field_id: int, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    field = _get(db, field_id)
    if problem := sfields.delete_problem(db, field):  # 쓰는 솔루션이 있으면 지우지 않고 몇 건인지·사용 끄기를 안내한다
        return _page(request, db, problem, field.id, field.name, 400)
    db.delete(field)
    try:
        db.commit()
    except IntegrityError:  # 확인과 지우기 사이에 다른 곳에서 이 분야를 고른 경우
        db.rollback()
        return _page(request, db, sfields.delete_problem(db, field) or "다른 곳에서 이 분야를 쓰기 시작해 지우지 못했습니다.", field_id, field.name, 400)
    return notices.redirect("/admin/solution-fields", "field_deleted")

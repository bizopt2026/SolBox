"""사용자 관리 (관리자 전용)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import render
from app.models import User
from app.security import hash_password, require_admin

router = APIRouter(prefix="/admin")

ROLES = {"admin": "관리자", "user": "일반 사용자"}


def _page(request: Request, db: Session, error: str | None = None, status_code: int = 200):
    users = db.query(User).order_by(User.id).all()
    return render(request, "admin/users.html", status_code=status_code, users=users, roles=ROLES, error=error)


@router.get("/users")
def users_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _page(request, db)


@router.post("/users")
def create_user(
    request: Request,
    username: str = Form(""),
    name: str = Form(""),
    password: str = Form(""),
    role: str = Form("user"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    username = username.strip()
    if not username or not password:
        return _page(request, db, "아이디와 비밀번호를 입력해 주세요.", 400)
    if role not in ROLES:
        return _page(request, db, "권한 값이 올바르지 않습니다.", 400)
    if db.query(User).filter_by(username=username).first():
        return _page(request, db, "이미 사용 중인 아이디입니다.", 400)
    db.add(User(username=username, name=name.strip(), password_hash=hash_password(password), role=role))
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}")
def update_user(
    user_id: int,
    request: Request,
    name: str = Form(""),
    role: str = Form("user"),
    is_active: str | None = Form(None),
    password: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if role not in ROLES:
        return _page(request, db, "권한 값이 올바르지 않습니다.", 400)
    active = is_active is not None
    if target.id == admin.id and (not active or role != "admin"):
        return _page(request, db, "자기 자신을 비활성화하거나 관리자 권한을 내릴 수 없습니다.", 400)
    target.name = name.strip()
    target.role = role
    target.is_active = active
    if password:
        target.password_hash = hash_password(password)
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)

"""로그인과 로그아웃."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import render
from app.models import User
from app.security import verify_password

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    return render(request, "auth/login.html", error=None)


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=username.strip()).first()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return render(
            request, "auth/login.html", status_code=401,
            error="아이디 또는 비밀번호가 올바르지 않습니다.",
        )
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

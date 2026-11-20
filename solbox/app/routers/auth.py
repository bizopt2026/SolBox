"""로그인과 로그아웃."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import notices
from app.db import get_db
from app.main import render
from app.models import User
from app.security import password_problem, password_version, verify_password

router = APIRouter()

BAD_LOGIN = "아이디 또는 비밀번호가 올바르지 않습니다."
INACTIVE = "사용이 중지된 계정입니다. 관리자에게 문의하세요."


@router.get("/login")
def login_form(request: Request):
    return render(request, "auth/login.html", error=None)


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=username.strip()).first()
    if user is None or not verify_password(password, user.password_hash):
        return render(request, "auth/login.html", status_code=401, error=BAD_LOGIN)
    if not user.is_active:  # 비밀번호가 맞을 때만 알려 준다 (틀리면 위의 일반 문구라서 계정이 있는지 드러나지 않는다)
        return render(request, "auth/login.html", status_code=401, error=INACTIVE)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["pv"] = password_version(user)
    if user.must_change_password:
        return RedirectResponse("/account", status_code=303)
    if password_problem(password, user.username):  # 규칙이 생기기 전의 짧은 비밀번호: 로그인은 되지만 바꾸라고 알린다
        return notices.redirect("/account", "weak_password")
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

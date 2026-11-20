"""내 정보: 이름·아이디·권한 보기와 비밀번호 바꾸기 (로그인한 누구나)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app import notices
from app.db import get_db
from app.main import render
from app.models import User
from app.security import PASSWORD_RULE, current_user, hash_password, password_problem, password_version, verify_password

router = APIRouter(prefix="/account")

WRONG_CURRENT = "현재 비밀번호가 맞지 않습니다."
MISMATCH = "새 비밀번호와 확인이 서로 다릅니다."
SAME_AS_CURRENT = "현재 비밀번호와 다른 비밀번호를 정해 주세요."


def _page(request: Request, user: User, error=None, status_code: int = 200):
    return render(request, "account/index.html", status_code=status_code, user=user, error=error, rule=PASSWORD_RULE,
                  must_change=bool(user.must_change_password))


@router.get("")
def account_page(request: Request, user: User = Depends(current_user)):
    return _page(request, user)


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(""), new_password: str = Form(""), confirm_password: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    if not verify_password(current_password, user.password_hash):
        return _page(request, user, error=_field(WRONG_CURRENT, "current_password"), status_code=400)
    if problem := password_problem(new_password, user.username):
        return _page(request, user, error=_field(problem, "new_password"), status_code=400)
    if new_password == current_password:
        return _page(request, user, error=_field(SAME_AS_CURRENT, "new_password"), status_code=400)
    if new_password != confirm_password:
        return _page(request, user, error=_field(MISMATCH, "confirm_password"), status_code=400)
    user.password_hash = hash_password(new_password)
    user.must_change_password = None
    db.commit()
    request.session["pv"] = password_version(user)  # 이 로그인은 그대로, 다른 곳의 로그인은 끝난다
    return notices.redirect("/account", "password_changed")


def _field(text: str, field: str):
    from app.ui import FieldError

    return FieldError(text, field)

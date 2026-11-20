"""비밀번호 해시와 규칙, 로그인 확인, 권한 검사."""
from __future__ import annotations

import hashlib

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User

_hasher = PasswordHash.recommended()


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    if not hashed:
        return False
    return _hasher.verify(pw, hashed)


MIN_PASSWORD_LENGTH = 8
PASSWORD_RULE = f"{MIN_PASSWORD_LENGTH}자 이상이어야 하고, 아이디와 같거나 같은 글자 하나로만 되어 있으면 안 됩니다."


def password_problem(password: str, username: str = "") -> str | None:
    """새 비밀번호가 규칙에 맞지 않으면 화면에 보일 한국어 문구, 맞으면 None. 규칙: 8자 이상, 아이디와 같지 않음, 한 글자만 되풀이하지 않음.
    규칙은 새로 정할 때만 본다 (관리자가 만들거나 다시 정할 때, 직원이 바꿀 때). 옛 짧은 비밀번호로도 로그인은 계속 된다."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다."
    if username and password.strip().lower() == username.strip().lower():
        return "아이디와 같은 비밀번호는 쓸 수 없습니다."
    if len(set(password)) == 1:
        return "같은 글자 하나로만 된 비밀번호는 쓸 수 없습니다."
    return None


def password_version(user: User) -> str:
    """비밀번호가 바뀌면 달라지는 짧은 값. 로그인 쿠키에 함께 들어 있어, 바뀐 뒤에는 다른 곳의 로그인이 끝난다 (서버에 로그인 기록이 없는 서명 쿠키라서)."""
    return hashlib.sha256((user.password_hash or "").encode("utf-8")).hexdigest()[:12]


class LoginRequired(Exception):
    """로그인이 필요하다. 처리기가 /login 으로 보낸다."""


class MustChangePassword(Exception):
    """관리자가 정해 준 비밀번호를 아직 바꾸지 않았다: 내 정보의 비밀번호 바꾸기로 보낸다."""


MUST_CHANGE_ALLOWED = ("/account", "/account/password", "/logout")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """로그인한 사용자 (비밀번호를 바꿔야 하는 사용자도). 없거나 사용 중지됐거나 비밀번호가 바뀐 뒤의 옛 로그인이면 /login 으로."""
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        request.session.clear()
        raise LoginRequired()
    version = request.session.get("pv")
    if version is None:  # 이 값이 생기기 전에 한 로그인: 한 번 받아 주고 이제부터 따라간다
        request.session["pv"] = password_version(user)
    elif version != password_version(user):  # 다른 곳에서 비밀번호를 바꿨다 (이 로그인은 끝난다)
        request.session.clear()
        raise LoginRequired()
    request.state.user = user
    db.info["user_id"] = user.id  # 고친 사람 기록 (app/guards.py)
    return user


def require_user(request: Request, user: User = Depends(current_user)) -> User:
    if user.must_change_password and request.url.path not in MUST_CHANGE_ALLOWED:
        raise MustChangePassword()
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자만 사용할 수 있습니다.")
    return user


def can_delete(user: User, obj) -> bool:
    return user.role == "admin" or obj.created_by_id == user.id


def ensure_can_delete(user: User, obj) -> None:
    if not can_delete(user, obj):
        raise HTTPException(status_code=403, detail="본인이 등록한 항목만 삭제할 수 있습니다.")

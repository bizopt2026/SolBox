"""비밀번호 해시, 로그인 확인, 권한 검사."""
from __future__ import annotations

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


class LoginRequired(Exception):
    """로그인이 필요하다. 처리기가 /login 으로 보낸다."""


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        request.session.clear()
        raise LoginRequired()
    request.state.user = user
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

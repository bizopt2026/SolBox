"""로그인과 로그아웃."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app import notices
from app.db import get_db
from app.main import render
from app.models import User
from app.security import password_problem, password_version, verify_password

router = APIRouter()

BAD_LOGIN = "아이디 또는 비밀번호가 올바르지 않습니다."
INACTIVE = "사용이 중지된 계정입니다. 관리자에게 문의하세요."


@router.get("/ca.crt")
def ca_certificate(request: Request):
    """HTTPS 발급자 인증서(공개 정보)를 내려받게 한다: 휴대폰·PC 에 한 번 "신뢰"로 설치할 파일 (운영가이드 4장, 2026-11-27).
    로그인 없이 연다 (인증서를 믿기 전에 받아야 하므로). 서버 인증서 옆의 `ca.crt` 이고 PEM 인증서만 보낸다 (키는 절대 보내지 않는다)."""
    certfile = request.app.state.settings.ssl_certfile
    ca = Path(certfile).parent / "ca.crt" if certfile else None
    data = ca.read_bytes() if ca is not None and ca.is_file() else b""
    if not data.lstrip().startswith(b"-----BEGIN CERTIFICATE-----") or b"PRIVATE KEY" in data:
        return render(request, "error.html", status_code=404, message="이 서버에는 내려받을 발급자 인증서(ca.crt)가 없습니다. 관리자가 make-cert 로 인증서를 만들면 생깁니다.",
                      title="항목을 찾을 수 없습니다", code=404)
    return Response(data, media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="solbox-ca.crt"', "Cache-Control": "no-store"})


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

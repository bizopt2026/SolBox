"""AI 제안함 화면 (UX 개선 2026-11-26): 반영을 기다리는 모든 AI 제안. 반영·무시는 각 출처의 POST 로 보내고 `back` 으로 여기 돌아온다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import ai_inbox, ui
from app.db import get_db
from app.main import render
from app.models import User
from app.security import require_user

router = APIRouter(prefix="/ai")
INBOX = "/ai/inbox"


@router.get("/inbox")
def inbox(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = ai_inbox.pending(db)
    return render(request, "ai_inbox.html", rows=rows, back=INBOX)


def back_target(back: str, default: str) -> str:
    """반영·무시 뒤에 돌아갈 곳: AI 제안함에서 눌렀으면 제안함, 아니면 원래 화면 (다른 주소는 받지 않는다)."""
    return INBOX if back == INBOX else default

"""전체 검색 화면."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import search
from app.db import get_db
from app.main import render
from app.models import User
from app.security import require_user

router = APIRouter()


@router.get("/search")
def search_page(request: Request, q: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render(request, "search/results.html", result=search.search(db, q), min_chars=search.MIN_CHARS, per_type=search.PER_TYPE)

"""정렬 점검 화면 (AI 코워크 ③): [다시 점검] 버튼과 진행 조각. 규칙은 `app/alignment.py`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app import alignment, notices, ontology
from app.ai.gate import require_ai_on
from app.db import get_db
from app.main import render
from app.models import User
from app.security import require_user

router = APIRouter(prefix="/alignment")


def _target(db: Session, code: str, target_id: int):
    model = alignment.MODELS.get(code)
    obj = db.get(model, target_id) if model is not None else None
    if obj is None:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    return obj


@router.post("/{code}/{target_id}/check", dependencies=[Depends(require_ai_on)])
def check(code: str, target_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """[다시 점검]: 사람이 누른 작업이라 앞 순서(0)로 넣는다. 합의된 방향이 없거나 점검하지 않는 기록이면 알린다."""
    obj = _target(db, code, target_id)
    url = ontology.TYPES[code].url(obj.id) + "#alignment"
    if not alignment.is_target(obj):
        return notices.redirect(url, "align_not_target")
    if alignment.pending_job(db, code, obj.id) is not None:
        return notices.redirect(url, "align_waiting")
    if alignment.request(db, obj, user.id, request, auto=False) is None:
        return notices.redirect(url, "align_no_strategy")
    return notices.redirect(url, "align_started")


@router.get("/{code}/{target_id}/progress")
def progress(code: str, target_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """점검 카드 조각: 작업이 끝나면 쪽을 새로 고친다."""
    model = alignment.MODELS.get(code)
    obj = db.get(model, target_id) if model is not None else None
    if obj is None:
        return Response("")
    # 끝나도 쪽 전체를 새로 고치지 않고 카드만 바꾼다 (같은 화면의 AI 동료 입력칸에 쓰던 글을 잃지 않게)
    return render(request, "alignment/_card.html", al=alignment.view(db, obj), al_obj=obj)

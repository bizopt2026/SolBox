"""제안 건 구성안 검토·수정 화면."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import labels
from app.db import get_db
from app.main import render
from app.models import Proposal, User
from app.proposals import MAX_SLIDES, NO_SOURCE_NOTE, display_title, save_outline, slide_from_form, slide_to_form
from app.routers._common import get_or_404
from app.security import require_user

router = APIRouter(prefix="/proposals/{proposal_id}/outline")
BUSY = "AI 작업이 끝난 뒤 수정하세요."
FIELDS = (
    "title", "key_message", "subtitle", "bullets", "left_title", "left", "right_title", "right",
    "columns", "rows", "steps", "sources", "needs_check",
)


def _load(db: Session, proposal_id: int) -> Proposal:
    p = get_or_404(db, Proposal, proposal_id)
    if not p.outline or not p.outline.get("slides"):
        raise HTTPException(status_code=404, detail="구성안이 없습니다.")
    return p


def _load_for_edit(db: Session, proposal_id: int, index: int | None = None) -> tuple[Proposal, list[dict]]:
    p = _load(db, proposal_id)
    slides = [dict(s) for s in p.outline["slides"]]
    if index is not None and not 0 <= index < len(slides):
        raise HTTPException(status_code=404, detail="없는 슬라이드입니다.")
    if p.active_job is not None and p.active_job.status in ("pending", "running"):
        raise HTTPException(status_code=400, detail=BUSY)
    return p, slides


def _back(p: Proposal, index: int | None = None) -> RedirectResponse:
    anchor = f"#slide-{index}" if index is not None else ""
    return RedirectResponse(f"/proposals/{p.id}/outline{anchor}", status_code=303)


@router.get("")
def outline_page(
    proposal_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    p = _load(db, proposal_id)
    slides = p.outline["slides"]
    busy = p.active_job is not None and p.active_job.status in ("pending", "running")
    cards = [{"index": i, "slide": s, "form": slide_to_form(s)} for i, s in enumerate(slides)]
    return render(
        request, "proposals/outline.html", proposal=p, title=display_title(p), cards=cards, busy=busy,
        can_add=len(slides) < MAX_SLIDES, slide_kinds=labels.SLIDE_KINDS,
    )


@router.post("/slides/{index}")
def save_slide(
    proposal_id: int, index: int, kind: str = Form(""),
    title: str = Form(""), key_message: str = Form(""), subtitle: str = Form(""), bullets: str = Form(""),
    left_title: str = Form(""), left: str = Form(""), right_title: str = Form(""), right: str = Form(""),
    columns: str = Form(""), rows: str = Form(""), steps: str = Form(""),
    sources: str = Form(""), needs_check: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    p, slides = _load_for_edit(db, proposal_id, index)
    kind = kind.strip() or slides[index]["kind"]
    if kind not in labels.SLIDE_KINDS:
        raise HTTPException(status_code=400, detail="알 수 없는 슬라이드 종류입니다.")
    if (index == 0) != (kind == "cover"):
        raise HTTPException(status_code=400, detail="표지는 첫 장만 쓸 수 있고, 첫 장은 표지여야 합니다.")
    values = locals()
    slides[index] = slide_from_form(kind, {name: values[name] for name in FIELDS})
    save_outline(db, p, slides)
    return _back(p, index)


@router.post("/slides/{index}/delete")
def delete_slide(
    proposal_id: int, index: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    p, slides = _load_for_edit(db, proposal_id, index)
    if index == 0:
        raise HTTPException(status_code=400, detail="표지는 지울 수 없습니다.")
    del slides[index]
    save_outline(db, p, slides)
    return _back(p)


@router.post("/slides/{index}/move")
def move_slide(
    proposal_id: int, index: int, dir: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    p, slides = _load_for_edit(db, proposal_id, index)
    target = index - 1 if dir == "up" else index + 1 if dir == "down" else None
    if target is None:
        raise HTTPException(status_code=400, detail="방향이 올바르지 않습니다.")
    if index == 0 or target < 1 or target >= len(slides):
        raise HTTPException(status_code=400, detail="더 옮길 수 없습니다.")
    slides[index], slides[target] = slides[target], slides[index]
    save_outline(db, p, slides)
    return _back(p, target)


@router.post("/slides")
def add_slide(
    proposal_id: int, after: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    p = _load(db, proposal_id)
    raw = after.strip()
    n = int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 9 else None
    p, slides = _load_for_edit(db, proposal_id, n if n is not None else len(p.outline["slides"]))
    if len(slides) >= MAX_SLIDES:
        raise HTTPException(status_code=400, detail=f"슬라이드는 {MAX_SLIDES}장까지 만들 수 있습니다.")
    slides.insert(n + 1, slide_from_form("bullets", {"title": "새 슬라이드", "needs_check": NO_SOURCE_NOTE}))
    save_outline(db, p, slides)
    return _back(p, n + 1)

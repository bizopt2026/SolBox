"""제안 건 구성안 검토·수정 화면."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from datetime import date
from urllib.parse import quote

from fastapi.responses import RedirectResponse, Response
from sqlalchemy import update
from sqlalchemy.orm import Session

from app import guards, labels, ontology, outline_export
from app import notices
from app.guards import Rev, rev_form
from app.db import get_db
from app.main import render
from app.models import Proposal, User
from app.ai.gate import require_ai_on
from app.ai.queue import queue_position
from app.proposals import MAX_SLIDES, NO_SOURCE_NOTE, contact_lines, start_job, target_name, check_items, display_title, outline_check_summary, keep_checked, slide_sources, save_outline, slide_check_state, slide_from_form, slide_to_form
from app.routers._common import get_or_404
from app.sources import SourceLookup
from app.security import require_user

router = APIRouter(prefix="/proposals/{proposal_id}/outline")
BUSY = "AI 작업이 끝난 뒤 수정하세요."
MAX_SLIDE_INSTRUCTION = 300
TOO_LONG_SLIDE_INSTRUCTION = f"요청은 {MAX_SLIDE_INSTRUCTION}자까지 쓸 수 있습니다."
NO_UNDO = "되돌릴 AI 다시 쓰기가 없습니다."
FIELDS = (
    "title", "key_message", "subtitle", "bullets", "left_title", "left", "right_title", "right",
    "columns", "rows", "steps", "sources", "needs_check",
    "kpis", "chart_type", "chart_data", "unit", "note", "diagram_type", "nodes", "center", "axis_x", "axis_y",  # 시각 자료 장 (베타 AG)
)


def _load(db: Session, proposal_id: int) -> Proposal:
    p = get_or_404(db, Proposal, proposal_id)
    if not p.outline or not p.outline.get("slides"):
        raise HTTPException(status_code=404, detail="구성안이 없습니다.")
    return p


def _lock_proposal(db: Session, proposal_id: int) -> None:
    """쓰기 잠금을 먼저 잡는다. 이 뒤에 읽어야 같은 건의 다른 수정이 끝난 값을 보고 고치므로, 동시에 누른 확인이 서로 덮어쓰지 않는다."""
    db.execute(update(Proposal).where(Proposal.id == proposal_id).values(id=Proposal.id))


def _load_for_edit(db: Session, proposal_id: int, index: int | None = None) -> tuple[Proposal, list[dict]]:
    _lock_proposal(db, proposal_id)
    p = _load(db, proposal_id)
    db.refresh(p)  # 잠금 전에 읽어 둔 값이 있어도 잠금 뒤의 값으로
    slides = [dict(s) for s in p.outline["slides"]]
    if index is not None and not 0 <= index < len(slides):
        raise HTTPException(status_code=404, detail="없는 슬라이드입니다.")
    if p.active_job is not None and p.active_job.status in ontology.JOB_ACTIVE:
        raise HTTPException(status_code=400, detail=BUSY)
    return p, slides


def _back(p: Proposal, index: int, renumber: bool = False, notice: str = "") -> RedirectResponse:
    """처리 뒤 방금 만진 장이 펼쳐진 채로 구성안 화면으로 돌려보낸다. 장 번호가 바뀐 처리는 renumber=1 을 붙인다."""
    flag = "&renumber=1" if renumber else ""
    url = f"/proposals/{p.id}/outline?open={index}{flag}#slide-{index}"
    return RedirectResponse(notices.with_notice(url, notice) if notice else url, status_code=303)


def _export_text(p: Proposal, user: User) -> str:
    """Claude 용 내보내기 글 (베타 AM, `app/outline_export.py`). 화면의 복사 칸과 파일 내려받기가 같은 글을 쓴다."""
    return outline_export.export_markdown(p.outline, deck_title=display_title(p), target=target_name(p), contact=contact_lines(user), today=date.today())


def _outline_page(request: Request, db: Session, p: Proposal, open_index: int | None, status_code: int = 200, conflict=None, submitted: dict | None = None):
    """구성안 화면. `conflict`·`submitted` 가 있으면 그 장의 폼은 저장하지 않은 충돌 상태(쓴 값 그대로 + 알림)로 그린다."""
    slides = p.outline["slides"]
    busy = p.active_job is not None and p.active_job.status in ontology.JOB_ACTIVE
    cards = []
    for i, s in enumerate(slides):
        checks = check_items(s.get("needs_check"), s.get("checked"))
        card = {"index": i, "slide": s, "form": slide_to_form(s), "state": slide_check_state(s), "rev": guards.slide_rev(p, s),
                "checks": checks, "unchecked": sum(1 for c in checks if not c["checked"]), "sources": slide_sources(s)}
        if conflict is not None and i == open_index and submitted is not None:
            card.update(form=submitted, conflict=conflict)
        cards.append(card)
    if open_index is None:  # 고른 장이 없으면 확인할 점이 남은 첫 장만 펼쳐 위에서부터 처리하게 한다
        open_index = next((c["index"] for c in cards if c["unchecked"]), None)
    return render(
        request, "proposals/outline.html", status_code=status_code, proposal=p, title=display_title(p), cards=cards, open_index=open_index, busy=busy,
        position=queue_position(db, p.active_job) if busy else 0,
        export_text=_export_text(p, request.state.user) if getattr(request.state, "user", None) is not None else "",
        summary=outline_check_summary(p.outline), src=SourceLookup(db), can_add=len(slides) < MAX_SLIDES, slide_kinds=labels.SLIDE_KINDS,
        chart_types=ontology.CHART_TYPES, diagram_types=ontology.DIAGRAM_TYPES, diagram_help=ontology.DIAGRAM_TYPE_DESCRIPTIONS, quadrants=list(ontology.MATRIX_QUADRANTS),
    )


@router.get("")
def outline_page(
    proposal_id: int, request: Request, open: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    p = _load(db, proposal_id)
    slides = p.outline["slides"]
    open_index = int(open) if open.isascii() and open.isdigit() and len(open) <= 6 and int(open) < len(slides) else None
    return _outline_page(request, db, p, open_index)


@router.post("/slides/{index}")
def save_slide(
    proposal_id: int, index: int, request: Request, kind: str = Form(""),
    title: str = Form(""), key_message: str = Form(""), subtitle: str = Form(""), bullets: str = Form(""),
    left_title: str = Form(""), left: str = Form(""), right_title: str = Form(""), right: str = Form(""),
    columns: str = Form(""), rows: str = Form(""), steps: str = Form(""),
    sources: str = Form(""), needs_check: str = Form(""),
    kpis: str = Form(""), chart_type: str = Form(""), chart_data: str = Form(""), unit: str = Form(""), note: str = Form(""),
    diagram_type: str = Form(""), nodes: str = Form(""), center: str = Form(""), axis_x: str = Form(""), axis_y: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), rev: Rev = Depends(rev_form),
):
    p, slides = _load_for_edit(db, proposal_id, index)
    values = locals()
    submitted = {"kind": kind, **{name: values[name] for name in FIELDS}}
    if conflict := guards.check_rev(p, rev, current=guards.slide_rev(p, slides[index]),
                                           fresh=lambda row: guards.slide_rev(row, row.outline["slides"][index]),
                                           values=dict(submitted),
                                           stored={**slide_to_form(slides[index]), "kind": slides[index].get("kind", "")}, labels=ontology.SLIDE_FORM_LABELS):
        # 다른 탭이 이 장을 바꾸거나 옮기거나 지웠다 (번호 자리의 장이 달라졌다): 저장하지 않고 쓴 값 그대로 다시 보여 준다
        db.rollback()
        return _outline_page(request, db, p, index, status_code=409, conflict=conflict, submitted=submitted)
    kind = kind.strip() or slides[index]["kind"]
    if kind not in labels.SLIDE_KINDS:
        raise HTTPException(status_code=400, detail="알 수 없는 슬라이드 종류입니다.")
    if (index == 0) != (kind == "cover"):
        raise HTTPException(status_code=400, detail="표지는 첫 장만 쓸 수 있고, 첫 장은 표지여야 합니다.")
    try:
        slides[index] = keep_checked(slides[index], slide_from_form(kind, {name: values[name] for name in FIELDS}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    save_outline(db, p, slides)
    return _back(p, index, notice="slide_saved")


@router.post("/slides/{index}/check")
def check_item(
    proposal_id: int, index: int, item: str = Form(""), text: str = Form(""), done: str = Form("1"),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """확인 항목 하나를 확인함으로 표시하거나 되돌린다."""
    p, slides = _load_for_edit(db, proposal_id, index)
    if done not in ("0", "1"):
        raise HTTPException(status_code=400, detail="확인 여부가 올바르지 않습니다.")
    slide = slides[index]
    notes = slide.get("needs_check") or []
    n = int(item) if item.isascii() and item.isdigit() and len(item) <= 6 else -1
    # 브라우저는 폼 값의 줄바꿈을 CRLF 로 보내므로 줄바꿈을 맞춰 비교한다
    if not 0 <= n < len(notes) or notes[n].replace("\r\n", "\n") != text.replace("\r\n", "\n"):  # 화면이 낡았으면 엉뚱한 항목을 바꾸지 않는다
        raise HTTPException(status_code=404, detail="없는 확인 항목입니다.")
    text = notes[n]
    checked = [t for t in slide.get("checked") or [] if t != text]
    slides[index] = {**slide, "checked": checked + [text] if done == "1" else checked}
    save_outline(db, p, slides)
    return _back(p, index)


@router.post("/slides/{index}/check-all")
def check_all(
    proposal_id: int, index: int, done: str = Form("1"),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """한 장의 확인 항목을 모두 확인함으로 표시하거나 모두 비운다."""
    p, slides = _load_for_edit(db, proposal_id, index)
    if done not in ("0", "1"):
        raise HTTPException(status_code=400, detail="확인 여부가 올바르지 않습니다.")
    slide = slides[index]
    notes = [t for t in slide.get("needs_check") or [] if isinstance(t, str)]
    slides[index] = {**slide, "checked": list(dict.fromkeys(notes)) if done == "1" else []}
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
    return _back(p, min(index, len(slides) - 1), renumber=True, notice="slide_deleted")


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
    return _back(p, target, renumber=True, notice="slide_moved")


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
    return _back(p, n + 1, renumber=True, notice="slide_added")


@router.post("/slides/{index}/ai", dependencies=[Depends(require_ai_on)])
def rewrite_slide(
    proposal_id: int, index: int, instruction: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """한 장만 AI 로 다시 쓴다 (베타 AJ). 표지(0번)는 다시 쓰지 않는다. 작업이 끝나면 구성안 화면의 진행 상자가 쪽을 새로 고친다."""
    p = _load(db, proposal_id)
    slides = p.outline["slides"]
    if not 0 < index < len(slides):
        raise HTTPException(status_code=404, detail="없는 슬라이드입니다.")
    if p.selected_index is None or not 0 <= p.selected_index < len(p.directions or []):
        raise HTTPException(status_code=400, detail="먼저 제안 방향을 선택하세요.")
    text = instruction.strip()
    if len(text) > MAX_SLIDE_INSTRUCTION:
        raise HTTPException(status_code=400, detail=TOO_LONG_SLIDE_INSTRUCTION)
    page = f"/proposals/{p.id}/outline?open={index}#slide-{index}"
    if not start_job(db, p, "proposal_slide", user, {"index": index, "instruction": text, "base": dict(slides[index])}):
        return notices.redirect(f"/proposals/{p.id}/outline?open={index}", "busy")
    return RedirectResponse(page, status_code=303)  # 작업이 시작됨: 진행 상자가 알림을 대신한다


@router.post("/slides/{index}/undo")
def undo_slide(
    proposal_id: int, index: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """마지막 한 장 AI 다시 쓰기를 되돌린다. 그 뒤에 구성안을 다른 방법으로 저장했으면 되돌릴 것이 없다 (`save_outline` 이 지운다)."""
    p, slides = _load_for_edit(db, proposal_id, index)
    undo = p.outline.get("slide_undo") or {}
    if undo.get("index") != index or not isinstance(undo.get("slide"), dict):
        raise HTTPException(status_code=400, detail=NO_UNDO)
    slides[index] = undo["slide"]
    save_outline(db, p, slides)
    return _back(p, index, notice="slide_restored")


@router.get("/export")
def export_outline(
    proposal_id: int, user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """구성안을 Claude 용 마크다운 파일로 내려받는다 (베타 AM). 사용자가 자기 Claude(데스크톱·claude.ai)에 올려 PPT 스킬로 덱을 만든다."""
    p = _load(db, proposal_id)
    from app.knowledge import safe_name

    disposition = f'attachment; filename="proposal-{p.id}-outline.md"; filename*=UTF-8\'\'' + quote(safe_name(display_title(p)) + "_구성안_Claude용.md")
    return Response(_export_text(p, user), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": disposition})

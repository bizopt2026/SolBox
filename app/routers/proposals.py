"""제안 건 화면."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import labels
from app.ai.queue import queue_position
from app.db import get_db
from app.main import render
from app.models import Business, Customer, Proposal, Solution, User
from app.proposals import candidate_solutions, display_title, start_job
from app.routers._common import get_deletable_or_404, get_or_404, search_by_name
from app.security import can_delete, require_user

router = APIRouter(prefix="/proposals")
CUSTOMER_REQUIRED = "고객을 선택하세요."
BUSINESS_MISMATCH = "선택한 고객의 사업이 아닙니다."
NOTICES = {"busy": "이미 AI 작업이 진행 중입니다."}


def _parse_id(raw: str) -> int | None:
    """ASCII 숫자만 담긴 짧은 문자열이면 정수로, 아니면 None."""
    raw = raw.strip()
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 9 else None


def _solution_ids(db: Session, raw: list[str]) -> list[int]:
    """폼에서 온 솔루션 번호 중 실제로 있는 것만, 중복 없이 입력 순서대로."""
    ids: list[int] = []
    for r in raw:
        sid = _parse_id(r)
        if sid is not None and sid not in ids and db.get(Solution, sid) is not None:
            ids.append(sid)
    return ids


def _extra_candidates(db: Session) -> list[Solution]:
    """박스 등록이 아닌 솔루션 (제안 건에서 따로 고를 수 있는 것)."""
    return list(db.scalars(select(Solution).where(Solution.stage != "boxed").order_by(Solution.name, Solution.id)))


def _new_page(request: Request, db: Session, form: dict, error: str | None = None):
    customers = search_by_name(db, Customer)
    businesses = search_by_name(db, Business)
    return render(
        request, "proposals/new.html", status_code=400 if error else 200,
        form=form, error=error, customers=customers, businesses=businesses,
        extra_candidates=_extra_candidates(db),
    )


@router.get("")
def list_proposals(
    request: Request, q: str = "", status: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    stmt = select(Proposal).join(Proposal.customer)
    if status in labels.PROPOSAL_STATUSES:
        stmt = stmt.where(Proposal.status == status)
    proposals = list(db.scalars(stmt.order_by(Proposal.updated_at.desc(), Proposal.id.desc())))
    q = q.strip()
    if q:
        proposals = [p for p in proposals if q in display_title(p) or q in p.customer.name]
    return render(
        request, "proposals/list.html", proposals=proposals, q=q,
        status=status if status in labels.PROPOSAL_STATUSES else "",
    )


@router.get("/new")
def new_form(
    request: Request, customer_id: str = "", business_id: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    form = {"customer_id": customer_id, "business_id": business_id, "request_memo": "", "extra_solution_ids": []}
    return _new_page(request, db, form)


@router.post("")
def create(
    request: Request,
    customer_id: str = Form(""), business_id: str = Form(""), request_memo: str = Form(""),
    extra_solution_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    form = {
        "customer_id": customer_id.strip(), "business_id": business_id.strip(),
        "request_memo": request_memo.strip(), "extra_solution_ids": _solution_ids(db, extra_solution_ids),
    }
    cid = _parse_id(form["customer_id"])
    customer = db.get(Customer, cid) if cid is not None else None
    if customer is None:
        return _new_page(request, db, form, CUSTOMER_REQUIRED)
    business = None
    if form["business_id"]:
        bid = _parse_id(form["business_id"])
        business = db.get(Business, bid) if bid is not None else None
        if business is None or business.customer_id != customer.id:
            return _new_page(request, db, form, BUSINESS_MISMATCH)
    proposal = Proposal(
        customer_id=customer.id, business_id=business.id if business else None,
        request_memo=form["request_memo"], extra_solution_ids=form["extra_solution_ids"],
        created_by_id=user.id,
    )
    db.add(proposal)
    db.commit()
    start_job(db, proposal, "proposal_directions", user)
    return RedirectResponse(f"/proposals/{proposal.id}", status_code=303)


@router.get("/{proposal_id}")
def detail(
    proposal_id: int, request: Request, notice: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    return render(
        request, "proposals/detail.html", proposal=proposal, title=display_title(proposal),
        candidates=candidate_solutions(db, proposal), extra_candidates=_extra_candidates(db),
        notice=NOTICES.get(notice), can_delete=can_delete(user, proposal),
        position=_position(db, proposal),
    )


def _position(db: Session, proposal: Proposal) -> int:
    job = proposal.active_job
    return queue_position(db, job) if job is not None else 0


@router.get("/{proposal_id}/status")
def status(
    proposal_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    job = proposal.active_job
    if job is not None and job.status in ("pending", "running"):
        return render(request, "proposals/_status.html", proposal=proposal, position=_position(db, proposal))
    return Response("작업이 끝났습니다.", headers={"HX-Redirect": f"/proposals/{proposal.id}"})


def _start(db: Session, proposal: Proposal, kind: str, user: User, extra: dict | None = None) -> RedirectResponse:
    started = start_job(db, proposal, kind, user, extra)
    suffix = "" if started else "?notice=busy"
    return RedirectResponse(f"/proposals/{proposal.id}{suffix}", status_code=303)


@router.post("/{proposal_id}/directions")
def make_directions(
    proposal_id: int, user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    return _start(db, proposal, "proposal_directions", user)


@router.post("/{proposal_id}/select")
def select_direction(
    proposal_id: int, index: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    job = proposal.active_job
    if job is not None and job.status in ("pending", "running"):
        return RedirectResponse(f"/proposals/{proposal.id}?notice=busy", status_code=303)
    i = _parse_id(index)
    directions = proposal.directions or []
    if i is None or i >= len(directions):
        raise HTTPException(status_code=400, detail="없는 방향입니다.")
    chosen = directions[i]
    solutions = [s for sid in (x.get("solution_id") for x in chosen.get("solutions", [])) if (s := db.get(Solution, sid))]
    if not solutions:
        raise HTTPException(status_code=400, detail="선택한 방향의 솔루션이 더 이상 없습니다. 방향을 다시 만드세요.")
    proposal.selected_index = i
    proposal.status = "refining"
    proposal.solutions = solutions
    if not proposal.title:
        proposal.title = chosen.get("title", "")
    db.commit()
    return RedirectResponse(f"/proposals/{proposal.id}", status_code=303)


@router.post("/{proposal_id}/target")
def update_target(
    proposal_id: int,
    request_memo: str = Form(""), extra_solution_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    ids = _solution_ids(db, extra_solution_ids)
    # 폼에는 박스 등록이 아닌 솔루션만 나온다. 그 사이 박스에 등록된 기존 선택은 그대로 둔다.
    for sid in proposal.extra_solution_ids or []:
        solution = db.get(Solution, sid)
        if solution is not None and solution.stage == "boxed" and sid not in ids:
            ids.append(sid)
    proposal.request_memo = request_memo.strip()
    proposal.extra_solution_ids = ids
    db.commit()
    return RedirectResponse(f"/proposals/{proposal.id}", status_code=303)


@router.post("/{proposal_id}/delete")
def delete(
    proposal_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_deletable_or_404(db, Proposal, proposal_id, user)
    pid = proposal.id
    db.delete(proposal)
    db.commit()
    shutil.rmtree(request.app.state.settings.decks_dir / str(pid), ignore_errors=True)
    return RedirectResponse("/proposals", status_code=303)

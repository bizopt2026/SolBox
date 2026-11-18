"""제안 건 화면."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import labels
from app.db import get_db
from app.main import render
from app.models import Business, Customer, Proposal, Solution, User
from app.proposals import candidate_solutions, display_title
from app.routers._common import get_deletable_or_404, get_or_404, search_by_name
from app.security import can_delete, require_user

router = APIRouter(prefix="/proposals")
CUSTOMER_REQUIRED = "고객을 선택하세요."
BUSINESS_MISMATCH = "선택한 고객의 사업이 아닙니다."
NOTICES = {"busy": "이미 AI 작업이 진행 중입니다."}


def _solution_ids(db: Session, raw: list[str]) -> list[int]:
    """폼에서 온 솔루션 번호 중 실제로 있는 것만, 중복 없이 입력 순서대로."""
    ids: list[int] = []
    for r in raw:
        if r.strip().isdigit() and int(r) not in ids and db.get(Solution, int(r)) is not None:
            ids.append(int(r))
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
    customer = db.get(Customer, int(form["customer_id"])) if form["customer_id"].isdigit() else None
    if customer is None:
        return _new_page(request, db, form, CUSTOMER_REQUIRED)
    business = None
    if form["business_id"]:
        business = db.get(Business, int(form["business_id"])) if form["business_id"].isdigit() else None
        if business is None or business.customer_id != customer.id:
            return _new_page(request, db, form, BUSINESS_MISMATCH)
    proposal = Proposal(
        customer_id=customer.id, business_id=business.id if business else None,
        request_memo=form["request_memo"], extra_solution_ids=form["extra_solution_ids"],
        solutions=[db.get(Solution, i) for i in form["extra_solution_ids"]],
        created_by_id=user.id,
    )
    db.add(proposal)
    db.commit()
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
    )


@router.post("/{proposal_id}/target")
def update_target(
    proposal_id: int,
    request_memo: str = Form(""), extra_solution_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    ids = _solution_ids(db, extra_solution_ids)
    proposal.request_memo = request_memo.strip()
    proposal.extra_solution_ids = ids
    proposal.solutions = [db.get(Solution, i) for i in ids]
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

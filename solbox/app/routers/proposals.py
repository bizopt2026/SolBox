"""제안 건 화면."""
from __future__ import annotations

import secrets
import shutil
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import labels, ontology, opportunities as opps
from app.ai.queue import queue_position
from app.db import get_db
from app.deck import build_deck
from app.sources import SourceLookup
from app.knowledge import safe_name
from app.main import render
from app.models import Customer, Opportunity, Project, Proposal, ProposalMessage, Solution, User
from app.proposals import (
    candidate_solutions, display_title, open_section, reset_for_new_direction, section_summaries, start_job,
    step_states,
)
from app.routers._common import get_deletable_or_404, get_or_404, paginate, search_by_name, submitted_fields
from app.security import can_delete, require_user

router = APIRouter(prefix="/proposals")
CUSTOMER_REQUIRED = "고객을 선택하세요."
PROJECT_MISMATCH = "선택한 고객의 프로젝트가 아닙니다."
OPPORTUNITY_MISMATCH = opps.OPPORTUNITY_MISMATCH
NO_DIRECTION = "먼저 제안 방향을 선택하세요."
EMPTY_MESSAGE = "메시지를 입력하세요."
MAX_MESSAGE, MAX_INSTRUCTION, MAX_MEMO = 4000, 500, 2000
TOO_LONG_MESSAGE = f"메시지는 {MAX_MESSAGE}자까지 보낼 수 있습니다."
TOO_LONG_INSTRUCTION = f"추가 지시는 {MAX_INSTRUCTION}자까지 쓸 수 있습니다."
TOO_LONG_MEMO = f"요청 메모는 {MAX_MEMO}자까지 쓸 수 있습니다."
DECK_BUSY = "AI 작업이 끝난 뒤 생성하세요."
DECK_FAILED = "제안서 파일을 만들지 못했습니다."
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
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
    """보류 단계 솔루션 (제안 건에 직접 추가해야 후보가 되는 것). 후보 규칙은 `proposals.candidate_rule`."""
    return list(db.scalars(select(Solution).where(Solution.stage == ontology.STAGE_HOLD).order_by(Solution.name, Solution.id)))


def _new_page(request: Request, db: Session, form: dict, error: str | None = None):
    customers = search_by_name(db, Customer)
    projects = search_by_name(db, Project)
    return render(
        request, "proposals/new.html", status_code=400 if error else 200,
        form=form, error=error, customers=customers, projects=projects, opportunities=opps.opportunities_of_customer(db, None),
        extra_candidates=_extra_candidates(db),
    )


@router.get("")
def list_proposals(
    request: Request, q: str = "", status: str = "", page: str = "1",
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
        request, "proposals/list.html", pg=paginate(proposals, page), q=q,
        status=status if status in labels.PROPOSAL_STATUSES else "",
    )


@router.get("/new")
def new_form(
    request: Request, customer_id: str = "", project_id: str = "", opportunity_id: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    form = {"customer_id": customer_id, "project_id": project_id, "opportunity_id": "", "request_memo": "", "extra_solution_ids": []}
    oid = _parse_id(opportunity_id)
    opp = db.get(Opportunity, oid) if oid is not None else None
    if opp is not None and (not customer_id.strip() or customer_id.strip() == str(opp.customer_id)):
        # 영업기회에서 만들기: 고객을 채우고, 프로젝트가 이어져 있으면 미리 채운다 (고칠 수 있다)
        form.update(customer_id=str(opp.customer_id), opportunity_id=str(opp.id))
        if not project_id.strip() and opp.project_id:
            form["project_id"] = str(opp.project_id)
    return _new_page(request, db, form)


@router.post("")
def create(
    request: Request,
    customer_id: str = Form(""), project_id: str = Form(""), opportunity_id: str = Form(""), request_memo: str = Form(""),
    extra_solution_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    form = {
        "customer_id": customer_id.strip(), "project_id": project_id.strip(), "opportunity_id": opportunity_id.strip(),
        "request_memo": request_memo.strip(), "extra_solution_ids": _solution_ids(db, extra_solution_ids),
    }
    if len(form["request_memo"]) > MAX_MEMO:
        return _new_page(request, db, form, TOO_LONG_MEMO)
    cid = _parse_id(form["customer_id"])
    customer = db.get(Customer, cid) if cid is not None else None
    if customer is None:
        return _new_page(request, db, form, CUSTOMER_REQUIRED)
    project = None
    if form["project_id"]:
        bid = _parse_id(form["project_id"])
        project = db.get(Project, bid) if bid is not None else None
        if project is None or project.customer_id != customer.id:
            return _new_page(request, db, form, PROJECT_MISMATCH)
    opportunity = None
    if form["opportunity_id"]:
        oid = _parse_id(form["opportunity_id"])
        opportunity = db.get(Opportunity, oid) if oid is not None else None
        if opportunity is None or opportunity.customer_id != customer.id:
            return _new_page(request, db, form, OPPORTUNITY_MISMATCH)
    proposal = Proposal(
        customer_id=customer.id, project_id=project.id if project else None, opportunity_id=opportunity.id if opportunity else None,
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
    return _detail_page(request, db, proposal, user, notice=NOTICES.get(notice))


def _detail_page(
    request: Request, db: Session, proposal: Proposal, user: User, notice: str | None = None,
    status_code: int = 200, **extra,
):
    candidates = candidate_solutions(db, proposal)
    # 방금 실패한 폼이 들어 있는 구역은 열어 둔다
    open_key = "chat" if extra.get("chat_error") else "deck" if extra.get("deck_error") else open_section(proposal)
    return render(
        request, "proposals/detail.html", status_code=status_code, proposal=proposal, title=display_title(proposal),
        candidates=candidates, extra_candidates=_extra_candidates(db),
        notice=notice, can_delete=can_delete(user, proposal),
        position=_position(db, proposal), steps=step_states(proposal), open_key=open_key,
        customer_opportunities=opps.opportunities_of_customer(db, proposal.customer_id),
        summaries=section_summaries(proposal, candidates), src=SourceLookup(db), **extra,
    )


def _position(db: Session, proposal: Proposal) -> int:
    job = proposal.active_job
    return queue_position(db, job) if job is not None else 0


@router.get("/{proposal_id}/status")
def status(
    proposal_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = db.get(Proposal, proposal_id)
    if proposal is None:  # 그 사이 지워졌다. 조회를 멈추고 목록으로 보낸다.
        return Response("제안 건이 없습니다.", headers={"HX-Redirect": "/proposals"})
    job = proposal.active_job
    if job is not None and job.status in ontology.JOB_ACTIVE:
        return render(request, "proposals/_status.html", proposal=proposal, job=job, position=_position(db, proposal))
    return Response("작업이 끝났습니다.", headers={"HX-Redirect": f"/proposals/{proposal.id}"})


def _start(
    db: Session, proposal: Proposal, kind: str, user: User, extra: dict | None = None, anchor: str = "",
) -> RedirectResponse:
    started = start_job(db, proposal, kind, user, extra)
    suffix = anchor if started else "?notice=busy"
    return RedirectResponse(f"/proposals/{proposal.id}{suffix}", status_code=303)


@router.post("/{proposal_id}/directions")
def make_directions(
    proposal_id: int, user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    return _start(db, proposal, "proposal_directions", user)


@router.post("/{proposal_id}/select")
def select_direction(
    proposal_id: int, request: Request, index: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    job = proposal.active_job
    if job is not None and job.status in ontology.JOB_ACTIVE:
        return RedirectResponse(f"/proposals/{proposal.id}?notice=busy", status_code=303)
    i = _parse_id(index)
    directions = proposal.directions or []
    if i is None or i >= len(directions):
        raise HTTPException(status_code=400, detail="없는 방향입니다.")
    chosen = directions[i]
    solutions = [s for sid in (x.get("solution_id") for x in chosen.get("solutions", [])) if (s := db.get(Solution, sid))]
    if not solutions:
        raise HTTPException(status_code=400, detail="선택한 방향의 솔루션이 더 이상 없습니다. 방향을 다시 만드세요.")
    if proposal.selected_index is not None and proposal.selected_index != i:
        reset_for_new_direction(proposal, request.app.state.settings.decks_dir)
    proposal.selected_index = i
    proposal.status = "refining"
    proposal.solutions = solutions
    if not proposal.title:
        proposal.title = chosen.get("title", "")
    db.commit()
    return RedirectResponse(f"/proposals/{proposal.id}", status_code=303)


def _has_selection(p: Proposal) -> bool:
    return p.selected_index is not None and 0 <= p.selected_index < len(p.directions or [])


def _is_busy(p: Proposal) -> bool:
    return p.active_job is not None and p.active_job.status in ontology.JOB_ACTIVE


@router.post("/{proposal_id}/chat")
def chat(
    proposal_id: int, request: Request, message: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    if not _has_selection(proposal):
        raise HTTPException(status_code=400, detail=NO_DIRECTION)
    text = message.strip()
    if not text:
        return _detail_page(request, db, proposal, user, status_code=400, chat_error=EMPTY_MESSAGE)
    if len(text) > MAX_MESSAGE:
        return _detail_page(request, db, proposal, user, status_code=400, chat_error=TOO_LONG_MESSAGE)
    if _is_busy(proposal):
        return RedirectResponse(f"/proposals/{proposal.id}?notice=busy", status_code=303)
    pid = proposal.id
    message_row = ProposalMessage(proposal_id=pid, role="user", content=text)
    db.add(message_row)
    db.commit()  # start_job 이 트랜잭션을 되돌리므로 메시지를 먼저 확정한다
    message_id = message_row.id
    if start_job(db, proposal, "proposal_chat", user) is None:  # 그 사이 다른 요청이 먼저 작업을 잡았다. 메시지를 거둔다.
        leftover = db.get(ProposalMessage, message_id)
        if leftover is not None:  # 제안 건이 그 사이 지워졌으면 메시지도 이미 없다
            db.delete(leftover)
            db.commit()
        return RedirectResponse(f"/proposals/{pid}?notice=busy", status_code=303)
    return RedirectResponse(f"/proposals/{pid}#chat", status_code=303)


@router.post("/{proposal_id}/chat/retry")
def chat_retry(
    proposal_id: int, user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    job = proposal.active_job
    last = proposal.messages[-1] if proposal.messages else None
    if (
        not _has_selection(proposal) or job is None or job.kind != "proposal_chat" or job.status != "failed"
        or last is None or last.role != "user"
    ):
        raise HTTPException(status_code=400, detail="다시 시도할 수 있는 대화 작업이 없습니다.")
    return _start(db, proposal, "proposal_chat", user, anchor="#chat")


@router.post("/{proposal_id}/outline")
def make_outline(
    proposal_id: int, instruction: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    if not _has_selection(proposal):
        raise HTTPException(status_code=400, detail=NO_DIRECTION)
    if len(instruction.strip()) > MAX_INSTRUCTION:
        raise HTTPException(status_code=400, detail=TOO_LONG_INSTRUCTION)
    return _start(db, proposal, "proposal_outline", user, {"instruction": instruction.strip()}, anchor="#outline")


@router.post("/{proposal_id}/deck")
def build(
    proposal_id: int, request: Request, back: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    p = get_or_404(db, Proposal, proposal_id)
    if not p.outline or not p.outline.get("slides"):
        raise HTTPException(status_code=400, detail="구성안이 없습니다.")
    if _is_busy(p):
        raise HTTPException(status_code=400, detail=DECK_BUSY)
    folder = request.app.state.settings.decks_dir / str(p.id)
    now = datetime.now()
    name = f"deck-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.pptx"
    temp, final = folder / f"{name}.tmp", folder / name
    old_name = p.deck_name
    try:
        folder.mkdir(parents=True, exist_ok=True)
        paths = [src for s in p.outline["slides"] if isinstance(s, dict) for src in s.get("sources") or [] if isinstance(src, str)]
        build_deck(p.outline, temp, customer_name=p.customer.name, today=date.today(),
                   source_labels=SourceLookup(db).labels(paths))
        temp.replace(final)
        p.deck_name, p.deck_built_at, p.status = name, now, "deck"
        db.commit()
    except Exception:
        db.rollback()
        temp.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        db.refresh(p)
        return _detail_page(request, db, p, user, status_code=400, deck_error=DECK_FAILED)
    if old_name and old_name != name:
        (folder / old_name).unlink(missing_ok=True)
    suffix = "/outline#deck" if back == "outline" else "#deck"
    return RedirectResponse(f"/proposals/{p.id}{suffix}", status_code=303)


@router.get("/{proposal_id}/deck")
def download(
    proposal_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    p = get_or_404(db, Proposal, proposal_id)
    path = request.app.state.settings.decks_dir / str(p.id) / p.deck_name if p.deck_name else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="만든 제안서 파일이 없습니다.")
    disposition = f'attachment; filename="proposal-{p.id}.pptx"; filename*=UTF-8\'\'' + quote(safe_name(display_title(p)) + ".pptx")
    return FileResponse(path, media_type=PPTX_TYPE, headers={"Content-Disposition": disposition})


@router.post("/{proposal_id}/target")
def update_target(
    proposal_id: int,
    request_memo: str = Form(""), extra_solution_ids: list[str] = Form([]), opportunity_id: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), fields: frozenset = Depends(submitted_fields),
):
    proposal = get_or_404(db, Proposal, proposal_id)
    if len(request_memo.strip()) > MAX_MEMO:
        raise HTTPException(status_code=400, detail=TOO_LONG_MEMO)
    if "opportunity_id" in fields:  # 칸이 오지 않으면 그대로 둔다. 비어 오면 비운다. 그 고객의 영업기회여야 한다
        if opportunity_id.strip():
            oid = _parse_id(opportunity_id)
            opportunity = db.get(Opportunity, oid) if oid is not None else None
            if opportunity is None or opportunity.customer_id != proposal.customer_id:
                raise HTTPException(status_code=400, detail=OPPORTUNITY_MISMATCH)
            proposal.opportunity_id = opportunity.id
        else:
            proposal.opportunity_id = None
    ids = _solution_ids(db, extra_solution_ids)
    # 폼에는 보류 단계 솔루션만 나온다. 그 사이 보류에서 풀린 솔루션의 기존 선택은 그대로 둔다.
    for sid in proposal.extra_solution_ids or []:
        solution = db.get(Solution, sid)
        if solution is not None and solution.stage != ontology.STAGE_HOLD and sid not in ids:
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

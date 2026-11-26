"""AI 동료 칸 화면 (AI 코워크 ②): 새 대화·보내기·다시 시도·실시간 조각·제안 반영/무시·과제로 옮기기·대화 지우기. 규칙은 `app/cowork.py`."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import update
from sqlalchemy.orm import Session

from app import cowork, guards, notices, ontology
from app.ai.gate import keeps_text, require_ai_on
from app.db import get_db
from app.guards import Once, form_once, rev_form, Rev
from app.idparse import parse_id
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import AIMessage, AIThread, Initiative, User
from app.routers._common import get_or_404, resync_objects
from app.security import ensure_can_delete, require_user

router = APIRouter(prefix="/cowork")


def _target_or_404(db: Session, code: str, target_id: int):
    if code not in ontology.COWORK_TARGETS:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    obj = cowork.get_target(db, code, target_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    return obj


def _thread_url(code: str, target_id: int, thread_id: int | str | None) -> str:
    base = cowork.target_url(code, target_id)
    return f"{base}?thread={thread_id}#cowork" if thread_id is not None else f"{base}#cowork"


def page(request: Request, db: Session, user: User, code: str, obj, status_code: int, **extra):
    """대상의 상세 화면을 대화 칸 값(쓴 글·오류)과 함께 다시 그린다."""
    from app.routers import initiatives, opportunities, partners, solutions, strategies

    if code == "strategy":
        return strategies._detail_page(request, db, user, obj, status_code=status_code, **extra)
    if code == "initiative":
        return initiatives._detail_page(request, db, user, obj, status_code=status_code, **extra)
    if code == "solution":
        return solutions.detail_page(request, db, user, obj, status_code=status_code, **extra)
    if code == "partner":
        return partners._detail_page(request, db, user, obj, status_code=status_code, **extra)
    return opportunities._detail_page(request, db, user, obj, status_code=status_code, **extra)


def _problem(text: str) -> str | None:
    if not text:
        return cowork.EMPTY_MESSAGE
    if len(text) > cowork.MAX_MESSAGE:
        return cowork.TOO_LONG
    return None


def _new_ai_off(request: Request, exc):
    text = exc.first("message").strip()
    if not text:
        return None
    with request.app.state.session_factory() as db:
        code, tid = request.path_params.get("code"), parse_id(str(request.path_params.get("target_id")))
        obj = cowork.get_target(db, code, tid) if tid is not None and code in ontology.COWORK_TARGETS else None
        if obj is None:
            return None
        return page(request, db, request.state.user, code, obj, 409, cowork_text=text, cowork_thread="new")


@router.post("/{code}/{target_id}/threads", dependencies=[Depends(require_ai_on)])
@keeps_text(_new_ai_off)
def new_thread(
    code: str, target_id: int, request: Request, message: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), once: Once = Depends(form_once),
):
    """새 대화: 첫 메시지와 작업을 한 번에 (대화 제목은 첫 메시지에서)."""
    obj = _target_or_404(db, code, target_id)
    text = message.strip()
    if problem := _problem(text):
        return page(request, db, user, code, obj, 400, cowork_text=text, cowork_error=problem, cowork_thread="new")
    thread = AIThread(target_type=code, target_id=obj.id, title=cowork.thread_title(text), created_by_id=user.id)
    db.add(thread)
    db.flush()
    if cowork.start_job(db, thread, user, rows=[AIMessage(thread_id=thread.id, role="user", content=text, user_id=user.id)]) is None:
        raise HTTPException(status_code=409, detail=cowork.BUSY)
    return once.done(RedirectResponse(_thread_url(code, obj.id, thread.id), status_code=303))


def _thread_ai_off(request: Request, exc):
    text = exc.first("message").strip()
    if not text:
        return None
    with request.app.state.session_factory() as db:
        t = db.get(AIThread, request.path_params.get("thread_id"))
        obj = cowork.get_target(db, t.target_type, t.target_id) if t is not None else None
        if obj is None:
            return None
        return page(request, db, request.state.user, t.target_type, obj, 409, cowork_text=text, cowork_thread=str(t.id))


def _thread_and_target(db: Session, thread_id: int):
    t = get_or_404(db, AIThread, thread_id)
    obj = cowork.get_target(db, t.target_type, t.target_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    return t, obj


@router.post("/threads/{thread_id}/messages", dependencies=[Depends(require_ai_on)])
@keeps_text(_thread_ai_off)
def send(thread_id: int, request: Request, message: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db)):
    t, obj = _thread_and_target(db, thread_id)
    text = message.strip()
    extra = dict(cowork_text=text, cowork_thread=str(t.id))
    if problem := _problem(text):
        return page(request, db, user, t.target_type, obj, 400, cowork_error=problem, **extra)
    if cowork.is_busy(t):
        return page(request, db, user, t.target_type, obj, 409, cowork_error=cowork.BUSY, **extra)
    code, oid, tid = t.target_type, obj.id, t.id
    if cowork.start_job(db, t, user, rows=[AIMessage(thread_id=tid, role="user", content=text, user_id=user.id)]) is None:
        return page(request, db, user, code, obj, 409, cowork_error=cowork.BUSY, **extra)
    return RedirectResponse(_thread_url(code, oid, tid), status_code=303)


@router.post("/threads/{thread_id}/retry", dependencies=[Depends(require_ai_on)])
def retry(thread_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    t, obj = _thread_and_target(db, thread_id)
    job = t.active_job
    last = t.messages[-1] if t.messages else None
    if job is None or job.status != ontology.JOB_FAILED or last is None or last.role != "user":
        raise HTTPException(status_code=400, detail="다시 시도할 수 있는 대화 작업이 없습니다.")
    code, oid, tid = t.target_type, obj.id, t.id
    if cowork.start_job(db, t, user) is None:
        return notices.redirect(_thread_url(code, oid, tid), "busy")
    return RedirectResponse(_thread_url(code, oid, tid), status_code=303)


@router.get("/threads/{thread_id}/live")
def live(thread_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """대화 조각: 대기·진행 중에는 맨 끝 말풍선만 스스로 다시 부르고, 끝나면 대화 구역 전체로 바꾼다."""
    t = db.get(AIThread, thread_id)
    obj = cowork.get_target(db, t.target_type, t.target_id) if t is not None else None
    if obj is None:
        return Response("", headers={"HX-Refresh": "true"})
    pnl = cowork.panel(db, t.target_type, obj, str(t.id))
    if cowork.is_busy(t):
        return render(request, "cowork/_tail.html", cw=pnl, thread=t)
    response = render(request, "cowork/_thread.html", cw=pnl, thread=t)
    response.headers["HX-Retarget"] = "#cowork-thread"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


def _decide(db: Session, thread_id: int, message_id: int, sid: int):
    """대화·대상·메시지·제안. 메시지 줄에 먼저 쓰기 잠금을 잡고 다시 읽는다 (두 사람이 같은 답의 제안을 동시에 반영·무시해도 덮어쓰지 않게)."""
    t, obj = _thread_and_target(db, thread_id)
    db.execute(update(AIMessage).where(AIMessage.id == message_id).values(role=AIMessage.role))  # 쓰기 잠금
    m = db.get(AIMessage, message_id)
    if m is None or m.thread_id != t.id:
        raise HTTPException(status_code=404, detail="제안을 찾을 수 없습니다.")
    db.refresh(m)
    s = next((x for x in m.suggestions or [] if x.get("id") == sid), None)
    if s is None:
        raise HTTPException(status_code=404, detail="제안을 찾을 수 없습니다.")
    return t, obj, m, s


@router.post("/threads/{thread_id}/messages/{message_id}/suggestions/{sid}/apply")
def apply_suggestion(
    thread_id: int, message_id: int, sid: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    rev: Rev = Depends(rev_form),
):
    """제안 반영: AI 가 읽은 뒤(`basis_rev`) 또는 화면을 연 뒤(`_rev`) 기록이 바뀌었으면 반영하지 않고 알린다. 이력에 "AI 제안"과 근거를 남긴다."""
    t, obj, m, s = _decide(db, thread_id, message_id, sid)
    code, oid, tid = t.target_type, obj.id, t.id
    url = _thread_url(code, oid, tid)
    if s.get("state") != "pending":
        db.rollback()
        return notices.redirect(url, "cowork_decided")
    if s.get("basis_rev") and s["basis_rev"] != guards.rev_of(obj) or guards.check_rev(obj, rev):
        db.rollback()
        return notices.redirect(url, "cowork_stale")
    db.info["change_source"], db.info["change_evidence"] = "ai", s.get("evidence", "")
    try:
        cowork.apply(db, obj, s)
    except cowork.SuggestionError:
        db.rollback()
        return notices.redirect(url, "cowork_not_applicable")
    cowork.decide(m, sid, "applied", user)
    try:
        db.commit()
    finally:
        db.info.pop("change_source", None)
        db.info.pop("change_evidence", None)
    db.refresh(obj)
    mirror.sync(obj)
    if code == "opportunity":
        resync_objects(db, mirror, [obj.customer, obj.bm])
    return notices.redirect(url, "cowork_applied")


@router.post("/threads/{thread_id}/messages/{message_id}/suggestions/{sid}/dismiss")
def dismiss_suggestion(thread_id: int, message_id: int, sid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    t, obj, m, s = _decide(db, thread_id, message_id, sid)
    code, oid, tid = t.target_type, obj.id, t.id
    if cowork.decide(m, sid, "dismissed", user) is None:
        db.rollback()
        return notices.redirect(_thread_url(code, oid, tid), "cowork_decided")
    db.commit()
    return notices.redirect(_thread_url(code, oid, tid), "cowork_dismissed")


@router.post("/threads/{thread_id}/move")
def move(thread_id: int, initiative_id: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db),
         mirror: KnowledgeMirror = Depends(get_mirror)):
    """솔루션·파트너·영업기회 화면에서 연 대화를 과제 공간으로 옮긴다. 처음 대상은 과제의 연결에 더한다."""
    t, obj = _thread_and_target(db, thread_id)
    if t.target_type in ontology.COWORK_SPACES:
        raise HTTPException(status_code=400, detail="이미 공간에 있는 대화입니다.")
    iid = parse_id(initiative_id)
    item = db.get(Initiative, iid) if iid is not None else None
    if item is None:
        return notices.redirect(_thread_url(t.target_type, obj.id, t.id), "cowork_pick_initiative")
    rel = {"solution": "solutions", "partner": "partners", "opportunity": "opportunities"}[t.target_type]
    linked = getattr(item, rel)
    if obj not in linked:
        setattr(item, rel, [*linked, obj])
    cowork.supersede(db, t)  # 옛 대상에 맞춘 제안은 과제에 반영할 수 없다
    t.origin_type, t.origin_id = t.target_type, t.target_id
    t.target_type, t.target_id = "initiative", item.id
    t.updated_at = datetime.now()
    db.commit()
    db.refresh(item)
    mirror.sync(item)
    mirror.sync(obj)
    return notices.redirect(_thread_url("initiative", item.id, t.id), "cowork_moved")


@router.post("/threads/{thread_id}/delete")
def delete_thread(thread_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    t = get_or_404(db, AIThread, thread_id)
    ensure_can_delete(user, t)
    code, oid = t.target_type, t.target_id
    db.delete(t)
    db.commit()
    return notices.redirect(_thread_url(code, oid, None), "deleted")

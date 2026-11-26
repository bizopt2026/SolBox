"""사업 방향 화면 (AI 코워크 ①). 합의 내용이 다른 기록을 재는 잣대가 된다. 규칙은 `app/workspaces.py`."""
from __future__ import annotations

from collections import Counter
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app import guards, notices, ontology, relations, ui, workspaces as ws
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Strategy, User
from app.records import remove_workspace_files
from app.routers._common import get_deletable_or_404, get_or_404, paginate, resync_objects, row_info, sort_rows
from app.security import can_delete, require_user

router = APIRouter(prefix="/strategies")
FIELDS = ("title", "body", "consensus", "instructions", "period_start", "period_end", "status")


def _form_page(request: Request, form: dict, strategy: Strategy | None = None, error=None, conflict=None):
    return render(request, "strategies/form.html", status_code=409 if conflict else 400 if error else 200,
                  form=form, strategy=strategy, error=error, conflict=conflict)


def _detail_page(request: Request, db: Session, user: User, s: Strategy, status_code: int = 200, **extra):
    return render(request, "strategies/detail.html", status_code=status_code, s=s, **extra, today=date.today(), changes=ws.change_rows(db, s),
                  active=ontology.strategy_is_active(s, date.today()), files=s.files,
                  file_del={f.id: can_delete(user, f) for f in s.files},
                  **relations.page_related(db, s, user), can_delete=can_delete(user, s), meta=ui.stamp_info(db, s))


def _values(title, body, consensus, instructions, period_start, period_end, status) -> dict:
    return {"title": title.strip(), "body": body.strip(), "consensus": consensus.strip(), "instructions": instructions.strip(),
            "period_start": period_start.strip(), "period_end": period_end.strip(), "status": status.strip() or ontology.STRATEGY_STATUS_DEFAULT}


def _check(values: dict) -> tuple[dict | None, ui.FieldError | None]:
    """폼 값 검사: 저장할 값 또는 오류."""
    if not values["title"]:
        return None, ws.TITLE_REQUIRED
    if values["status"] not in ontology.STRATEGY_STATUSES:
        return None, ws.BAD_STATUS
    start, end = ws.parse_day(values["period_start"]), ws.parse_day(values["period_end"])
    if "bad" in (start, end):
        return None, ui.FieldError(ws.BAD_DATE, "period_start" if start == "bad" else "period_end")
    if start and end and end < start:
        return None, ws.BAD_PERIOD
    return {**values, "period_start": start, "period_end": end}, None


@router.get("")
def list_strategies(request: Request, q: str = "", status: str = "", page: str = "1",
                    user: User = Depends(require_user), db: Session = Depends(get_db)):
    found = sort_rows(request, "strategy", ws.search(db, Strategy, q, Strategy.consensus, Strategy.body), name_of=lambda s: s.title)
    counts = Counter(s.status for s in found)
    total = len(found)
    if status in ontology.STRATEGY_STATUSES:
        found = [s for s in found if s.status == status]
    else:
        status = ""
    pg = paginate(found, page)
    tabs = ui.seg_tabs(request, "status", list(ontology.STRATEGY_STATUSES.items()), counts, status, total)
    today = date.today()
    return render(request, "strategies/list.html", pg=pg, q=q.strip(), status=status, tabs=tabs, info=row_info(db, "strategy", pg.items),
                  active={s.id for s in pg.items if ontology.strategy_is_active(s, today)})


@router.get("/new")
def new_form(request: Request, user: User = Depends(require_user)):
    return _form_page(request, {**{f: "" for f in FIELDS}, "status": ontology.STRATEGY_STATUS_DEFAULT})


@router.post("")
def create(
    request: Request,
    title: str = Form(""), body: str = Form(""), consensus: str = Form(""), instructions: str = Form(""),
    period_start: str = Form(""), period_end: str = Form(""), status: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    once: Once = Depends(form_once),
):
    values = _values(title, body, consensus, instructions, period_start, period_end, status)
    clean, error = _check(values)
    if error:
        return _form_page(request, values, error=error)
    s = Strategy(**clean, created_by_id=user.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    mirror.sync(s)
    return once.done(notices.redirect(f"/strategies/{s.id}", "created"))


@router.get("/{strategy_id}")
def detail(strategy_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, Strategy, strategy_id))


def _form_of(s: Strategy) -> dict:
    return {"title": s.title, "body": s.body or "", "consensus": s.consensus or "", "instructions": s.instructions or "",
            "period_start": s.period_start.isoformat() if s.period_start else "", "period_end": s.period_end.isoformat() if s.period_end else "",
            "status": s.status}


@router.get("/{strategy_id}/edit")
def edit_form(strategy_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    s = get_or_404(db, Strategy, strategy_id)
    return _form_page(request, _form_of(s), s)


@router.post("/{strategy_id}")
def update(
    strategy_id: int, request: Request,
    title: str = Form(""), body: str = Form(""), consensus: str = Form(""), instructions: str = Form(""),
    period_start: str = Form(""), period_end: str = Form(""), status: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
    rev: Rev = Depends(rev_form),
):
    s = get_or_404(db, Strategy, strategy_id)
    values = _values(title, body, consensus, instructions, period_start, period_end, status)
    if conflict := guards.check_rev(s, rev, values=values):
        return _form_page(request, values, s, conflict=conflict)
    clean, error = _check(values)
    if error:
        return _form_page(request, values, s, error)
    for field, value in clean.items():
        setattr(s, field, value)
    db.commit()
    db.refresh(s)
    mirror.sync(s)
    resync_objects(db, mirror, s.initiatives)  # 과제 사본의 "사업 방향" 줄
    return notices.redirect(f"/strategies/{s.id}", "saved")


@router.post("/{strategy_id}/delete")
def delete(
    strategy_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db), mirror: KnowledgeMirror = Depends(get_mirror),
):
    s = get_deletable_or_404(db, Strategy, strategy_id, user)
    related = list(s.initiatives)
    paths = remove_workspace_files(request.app.state.settings, s)
    mirror.remove(s)
    db.delete(s)  # 과제의 사업 방향 칸은 비워지고(ON DELETE SET NULL) 자료·이력 줄은 함께 지워진다
    db.commit()
    for p in paths:
        p.unlink(missing_ok=True)
    resync_objects(db, mirror, related)
    return notices.redirect("/strategies", "deleted")

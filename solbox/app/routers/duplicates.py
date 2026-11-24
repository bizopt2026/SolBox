"""중복 정리와 합치기 (베타 AE, 관리자 전용): 관리 › 중복 정리(`/admin/duplicates`)와 고객·파트너 상세의 "다른 {고객|파트너}와 합치기"."""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import duplicates, korean, merge, notices, ontology
from app.ai.gate import require_ai_on
from app.ai.queue import enqueue
from app.db import get_db
from app.guards import Once, form_once
from app.idparse import parse_id
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import AIJob, User
from app.routers._common import get_or_404, resync_related, search_by_name
from app.security import require_admin

log = logging.getLogger(__name__)
router = APIRouter()
admin = APIRouter(prefix="/admin/duplicates")
CHOOSE_MAX = 40
CONFIRM_NEEDED = "합치기는 되돌릴 수 없어요. 아래 확인 칸에 체크해 주세요."


def _kind_of(raw: str) -> str:
    """주소의 종류 글자. 모르는 값은 없는 주소로 404 (예전에는 파트너로 읽었다)."""
    if raw not in merge.KINDS:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    return raw


def _tab_href(kind: str) -> str:
    return f"/admin/duplicates?tab={kind}"


# ---- 관리 › 중복 정리 ----

def _ai_view(db: Session, kind: str) -> dict:
    ai, done, status_job = duplicates.ai_pairs(db, kind)
    failed = status_job if status_job is not None and status_job.status == "failed" else None
    active = status_job if status_job is not None and status_job.status in ontology.JOB_ACTIVE else None
    from app import labels

    text = None
    if failed is not None:
        text = failed.error if failed.error_kind == "bad_output" and failed.error else labels.ERROR_KINDS.get(failed.shown_error_kind, labels.ERROR_KINDS["unknown"])
    return {"done": done, "active": active, "failed": failed, "failed_text": text, "count": len(ai)}


def _page(request: Request, db: Session, kind: str):
    rows, hidden = duplicates.candidates(db, kind)
    other = "customer" if kind == "partner" else "partner"
    other_rows, _ = duplicates.candidates(db, other)
    counts = {kind: len(rows), other: len(other_rows)}
    tabs = [{"href": _tab_href(k), "label": merge.KIND_LABEL[k], "count": counts[k], "active": k == kind} for k in ("partner", "customer")]
    return render(request, "admin/duplicates.html", kind=kind, label=merge.KIND_LABEL[kind], plural=merge.PLURAL[kind], rows=rows, hidden=hidden,
                  tabs=tabs, dup_ai=_ai_view(db, kind), signals=duplicates.SIGNALS)


@admin.get("")
def page(request: Request, tab: str = "partner", admin_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _page(request, db, tab if tab in merge.KINDS else "partner")  # 화면의 탭 글자는 모르면 파트너 탭


@admin.post("/ai/{kind}", dependencies=[Depends(require_admin), Depends(require_ai_on)])
def start_ai(kind: str, admin_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """AI 로 더 찾기: 이름 목록만 보낸다. 같은 종류의 작업이 진행 중이면 새로 만들지 않는다."""
    if kind not in merge.KINDS:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    active = [j for j in db.scalars(select(AIJob).where(AIJob.kind == duplicates.JOB_KIND, AIJob.status.in_(ontology.JOB_ACTIVE)))
              if (j.input or {}).get("type") == kind]
    if not active:
        enqueue(db, duplicates.JOB_KIND, {"type": kind}, admin_user.id)
    return notices.redirect(_tab_href(kind), "dup_ai_started")


@admin.get("/ai/{kind}/status")
def ai_status(kind: str, request: Request, admin_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """진행 카드 다시 불러오기 (HTMX). 끝났으면 후보가 바뀌었으므로 쪽 전체를 다시 연다."""
    kind = _kind_of(kind)
    ai = _ai_view(db, kind)
    if ai["active"] is None:
        from fastapi.responses import Response

        return Response(status_code=204, headers={"HX-Redirect": _tab_href(kind)})
    return render(request, "admin/_dup_ai.html", kind=kind, label=merge.KIND_LABEL[kind], dup_ai=ai)


@admin.post("/dismiss")
def dismiss(kind: str = Form(""), a: str = Form(""), b: str = Form(""), admin_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    kind = _kind_of(kind)
    ia, ib = parse_id(a), parse_id(b)
    if ia is not None and ib is not None and duplicates.dismiss(db, kind, ia, ib, admin_user.id):
        db.commit()
    return notices.redirect(_tab_href(kind), "dup_dismissed")


@admin.post("/undismiss")
def undismiss(kind: str = Form(""), a: str = Form(""), b: str = Form(""), admin_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    kind = _kind_of(kind)
    ia, ib = parse_id(a), parse_id(b)
    if ia is not None and ib is not None:
        duplicates.undismiss(db, kind, ia, ib)
        db.commit()
    return notices.redirect(_tab_href(kind), "dup_undismissed")


# ---- 합치기 (고객·파트너 상세에서) ----

def _preview_page(request: Request, db: Session, kind: str, a, b, keep, error: str | None = None, status_code: int = 200):
    absorb = b if keep.id == a.id else a
    pv = merge.preview(db, kind, keep, absorb)
    return render(request, "merge/preview.html", status_code=400 if error and status_code == 200 else status_code, kind=kind, label=merge.KIND_LABEL[kind],
                  plural=merge.PLURAL[kind], a=a, b=b, pv=pv, error=error, today_iso=date.today().isoformat(),
                  links={a.id: merge.total_links(db, kind, a), b.id: merge.total_links(db, kind, b)})


def _choose_page(request: Request, db: Session, kind: str, record, q: str, error: str | None = None):
    model = merge.KINDS[kind]
    found = [o for o in search_by_name(db, model, q) if o.id != record.id]
    return render(request, "merge/choose.html", status_code=400 if error else 200, kind=kind, label=merge.KIND_LABEL[kind], plural=merge.PLURAL[kind],
                  record=record, q=q.strip(), rows=found[:CHOOSE_MAX], total=len(found), error=error)


def _pair_from(db: Session, kind: str, rid: int, other_raw: str):
    model = merge.KINDS[kind]
    a = get_or_404(db, model, rid)
    oid = parse_id(other_raw)
    b = db.get(model, oid) if oid is not None and oid != rid else None
    return a, b


def _register(kind: str) -> None:
    plural = merge.PLURAL[kind]
    label = merge.KIND_LABEL[kind]
    model = merge.KINDS[kind]

    @router.get(f"/{plural}/{{record_id}}/merge", name=f"merge_{kind}_page")
    def merge_page(record_id: int, request: Request, other: str = "", keep: str = "", q: str = "",
                   admin_user: User = Depends(require_admin), db: Session = Depends(get_db)):
        a, b = _pair_from(db, kind, record_id, other)
        if b is None:
            return _choose_page(request, db, kind, a, q, error=(f"고른 {korean.josa(label, '을/를')} 찾을 수 없거나 같은 기록이에요. 다시 골라 주세요." if other.strip() else None))
        kid = parse_id(keep)
        chosen = a if kid == a.id else b if kid == b.id else merge.default_keep(db, kind, a, b)
        return _preview_page(request, db, kind, a, b, chosen)

    @router.post(f"/{plural}/{{record_id}}/merge", name=f"merge_{kind}_do")
    def merge_do(
        record_id: int, request: Request, other: str = Form(""), keep: str = Form(""), confirm: str = Form(""),
        admin_user: User = Depends(require_admin), db: Session = Depends(get_db),
        mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
    ):
        oid, kid = parse_id(other), parse_id(keep)
        if oid is None or kid is None or kid not in (record_id, oid) or oid == record_id:
            return notices.redirect(f"/{plural}", "merge_gone")
        a, b = db.get(model, record_id), db.get(model, oid)
        if a is None or b is None:  # 그 사이 지워졌거나 이미 합쳐졌다: 남아 있는 쪽으로 (없으면 목록으로)
            alive = a or b
            return notices.redirect(f"/{plural}/{alive.id}" if alive is not None else f"/{plural}", "merge_gone")
        keep_obj = a if kid == a.id else b
        if confirm != "1":
            return _preview_page(request, db, kind, a, b, keep_obj, error=CONFIRM_NEEDED)
        try:
            result = merge.execute(db, kind, kid, oid if kid == record_id else record_id, admin_user, request.app.state.settings)
        except LookupError:
            db.rollback()
            return notices.redirect(f"/{plural}", "merge_gone")
        except merge.MergeStopped as e:
            db.rollback()
            return _preview_page(request, db, kind, db.get(model, record_id) or a, db.get(model, oid) or b, keep_obj, error=str(e), status_code=409)
        kept = result["keep"]
        mirror.remove(result["absorbed"])
        db.commit()
        merge.log_done(admin_user, kind, result)
        db.refresh(kept)
        mirror.sync(kept)
        resync_related(db, mirror, kept)
        return once.done(notices.redirect(f"/{plural}/{kept.id}", "merged"))


for _kind in merge.KINDS:
    _register(_kind)

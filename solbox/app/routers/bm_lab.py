"""사업기획 › 신규 BM (2026-11-27, 설계 docs/superpowers/specs/2026-11-27-planning-bm-design.md).
목록과 "AI로 BM 기획" 화면. 상세·수정·AI 안 쓰기는 BM 화면(`/bms/{id}`, routers/bms.py)이 같이 한다. 규칙은 `app/bm_plan.py`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app import bm_plan, notices, ontology, solution_fields as sfields, ui
from app.ai import gate
from app.db import get_db
from app.guards import Once, form_once
from app.idparse import parse_id
from app.main import render
from app.models import BM, CustomerNeed, Solution, User
from app.routers._common import paginate, row_info, search_by_name, sort_rows
from app.security import require_user

router = APIRouter(prefix="/bm-lab")
TITLE_REQUIRED = ui.FieldError("기획 제목을 적어 주세요.", "name")


@router.get("")
def list_new_bms(request: Request, q: str = "", page: str = "1", stage: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = [b for b in search_by_name(db, BM, q, BM.summary) if bm_plan.is_new(b)]
    counts: dict[str, int] = {}
    for b in rows:
        counts[b.dev_stage or ""] = counts.get(b.dev_stage or "", 0) + 1
    stage_now = stage if stage in ontology.BM_DEV_STAGES else ""
    tabs = ui.seg_tabs(request, "stage", list(ontology.BM_DEV_STAGES.items()), counts, stage_now, len(rows))
    if stage_now:
        rows = [b for b in rows if b.dev_stage == stage_now]
    pg = paginate(sort_rows(request, "bm", rows), page)
    return render(request, "bm_lab/list.html", pg=pg, q=q.strip(), tabs=tabs, stage_now=stage_now, info=row_info(db, "bm", pg.items))


def _form(request: Request, db: Session, form: dict, error=None):
    fid = parse_id(form.get("field_id") or "") if form.get("field_id") else None
    stmt = select(CustomerNeed).options(selectinload(CustomerNeed.activity)).where(CustomerNeed.status != "resolved")
    keep_needs = list(form.get("need_ids") or [])
    if fid is not None:  # 좁혀도 이미 고른 니즈는 남긴다
        stmt = stmt.where(or_(CustomerNeed.field_id == fid, CustomerNeed.id.in_(keep_needs)))
    need_rows = list(db.scalars(stmt.order_by(CustomerNeed.created_at.desc(), CustomerNeed.id.desc()).limit(200)))
    needs = [{"id": n.id, "name": f"{ui.first_line(n.content, 70)} · {n.customer_name or '고객 미연결'}"} for n in need_rows]
    keep_sols = set(form.get("solution_ids") or [])
    sols = [s for s in search_by_name(db, Solution) if s.id in keep_sols or (s.stage != ontology.STAGE_HOLD and (fid is None or s.field_id == fid))]
    return render(request, "bm_lab/new.html", status_code=400 if error else 200, form=form, error=error, needs=needs, solutions=sols,
                  field_opts=sfields.select_options(db, None, form.get("field_id")), crumbs=bm_plan.crumbs(None, ontology.BM_KIND_NEW, ("AI로 BM 기획", None)))


@router.get("/new")
def new_form(
    request: Request, field_id: str = "", need_id: str = "", name: str = "", memo: str = "",
    need_ids: list[str] = Query([]), solution_ids: list[str] = Query([]), business_types: list[str] = Query([]),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """기획 폼. "분야로 좁혀 보기"는 같은 폼을 GET 으로 다시 보내므로 쓴 제목·메모·고른 것을 그대로 다시 채운다."""
    pre_need = parse_id(need_id) if need_id else None
    need = db.get(CustomerNeed, pre_need) if pre_need is not None else None
    fid = field_id or (str(need.field_id) if need is not None and need.field_id else "")
    picked = [n for i in need_ids if (n := parse_id(i)) is not None] or ([need.id] if need else [])
    return _form(request, db, {"name": name, "field_id": fid, "need_ids": picked, "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None],
                               "business_types": [c for c in business_types if c in ontology.BUSINESS_TYPES], "memo": memo})


@router.post("")
def create(
    request: Request, name: str = Form(""), field_id: str = Form(""), need_ids: list[str] = Form([]), solution_ids: list[str] = Form([]),
    business_types: list[str] = Form([]), memo: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), once: Once = Depends(form_once),
):
    form = {"name": name, "field_id": field_id, "need_ids": [n for i in need_ids if (n := parse_id(i)) is not None],
            "solution_ids": [n for i in solution_ids if (n := parse_id(i)) is not None], "business_types": business_types, "memo": memo}
    if not name.strip():
        return _form(request, db, form, TITLE_REQUIRED)
    plan = bm_plan.clean_plan(db, field_id, need_ids, solution_ids, business_types, memo)
    bm = BM(name=name.strip()[:120], kind=ontology.BM_KIND_NEW, dev_stage=ontology.BM_DEV_STAGE_DEFAULT, plan_input=plan, created_by_id=user.id)
    db.add(bm)
    db.commit()
    if gate.ai_is_off(request):  # 처리기가 꺼져 있으면 등록만 한다 (작업을 넣지 않는다)
        return once.done(notices.redirect(f"/bms/{bm.id}", "bm_ideas_ai_off"))
    bm_plan.start_ideas(db, bm, user)
    return once.done(notices.redirect(f"/bms/{bm.id}", "bm_ideas_started"))

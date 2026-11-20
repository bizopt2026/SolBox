"""영업기회 화면: 목록·상세·등록·수정·삭제, 단계 변경, 프로젝트로 전환. 행위 조건은 `app/opportunities.py` 한 곳이다."""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import ontology, opportunities as opps, relations, ui
from app.db import get_db
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import BM, Customer, Opportunity, Project, Solution, User
from app.records import pick
from app.routers._common import (
    list_filter,
    get_deletable_or_404, get_or_404, newest_first, paginate, resync_objects, row_info, search_by_name,
)
from app.security import can_delete, require_user

router = APIRouter(prefix="/opportunities")


def _form_page(request: Request, db: Session, form: dict, opp: Opportunity | None = None, error: str | None = None):
    cid = opps.parse_id(form.get("customer_id"))
    customer = db.get(Customer, cid) if cid is not None else None
    needs = [SimpleNamespace(id=n.id, name=opps.need_label(n)) for n in opps.bundlable_needs(db, cid, opp)]
    return render(
        request, "opportunities/form.html", status_code=400 if error else 200,
        form=form, opp=opp, error=error, customer=customer, customers=search_by_name(db, Customer),
        bms=search_by_name(db, BM), solutions=search_by_name(db, Solution), needs=needs,
        unlinked_needs=opps.unlinked_same_name_needs(db, customer),
    )


def _blank_form(customer_id: str = "") -> dict:
    return {"name": "", "customer_id": customer_id, "bm_id": "", "memo": "", "need_ids": [], "solution_ids": []}


def _parse(db: Session, name: str, customer_id: str, bm_id: str, memo: str) -> tuple[dict, str | None]:
    """폼 값을 다듬고 오류 문구를 돌려준다. 고객은 필수이고 BM 은 있으면 등록된 것이어야 한다."""
    values = {"name": name.strip(), "customer_id": customer_id.strip(), "bm_id": bm_id.strip(), "memo": memo.strip()}
    if not values["name"]:
        return values, opps.NAME_REQUIRED
    cid = opps.parse_id(values["customer_id"])
    if cid is None or db.get(Customer, cid) is None:
        return values, opps.CUSTOMER_REQUIRED
    values["customer_id"] = cid
    if values["bm_id"]:
        bid = opps.parse_id(values["bm_id"])
        if bid is None or db.get(BM, bid) is None:
            return values, opps.BAD_BM
        values["bm_id"] = bid
    else:
        values["bm_id"] = None
    return values, None


def _submitted(values: dict, need_ids, solution_ids) -> dict:
    return {**values, "need_ids": [int(i) for i in need_ids if i.strip().isdigit()],
            "solution_ids": [int(i) for i in solution_ids if i.strip().isdigit()]}


def _detail_page(request: Request, db: Session, user: User, opp: Opportunity, error: str | None = None):
    return render(
        request, "opportunities/detail.html", status_code=400 if error else 200,
        opp=opp, error=error, related=relations.related_for_page(db, opp, user),
        can_delete=can_delete(user, opp), can_convert=opps.can_convert(opp), meta=ui.stamp_info(db, opp),
    )


@router.get("")
def list_opportunities(
    request: Request, q: str = "", stage: str = "", page: str = "1",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    found = search_by_name(db, Opportunity, q, Customer.name, joins=(Opportunity.customer,),
                           loaders=(selectinload(Opportunity.customer), selectinload(Opportunity.bm), selectinload(Opportunity.project)))
    found.sort(key=lambda o: (o.updated_at, o.id), reverse=True)
    found, flt = list_filter(db, request, "opportunity", found)
    counts = Counter(o.stage for o in found)  # 구분 탭의 건수: 검색어·걸러 보기를 적용한 목록 기준
    counts[ontology.OPPORTUNITY_FILTER_OPEN] = sum(counts[c] for c in ontology.OPPORTUNITY_OPEN_STAGES)
    total = len(found)
    if stage == ontology.OPPORTUNITY_FILTER_OPEN:
        found = [o for o in found if o.stage in ontology.OPPORTUNITY_OPEN_STAGES]
    elif stage in ontology.OPPORTUNITY_STAGES:
        found = [o for o in found if o.stage == stage]
    else:
        stage = ""
    pg = paginate(found, page)
    choices = [(ontology.OPPORTUNITY_FILTER_OPEN, "진행 중"), *ontology.OPPORTUNITY_STAGES.items()]
    tabs = ui.seg_tabs(request, "stage", choices, counts, stage, total)
    return render(request, "opportunities/list.html", pg=pg, q=q.strip(), stage=stage, flt=flt, tabs=tabs, info=row_info(db, "opportunity", pg.items))


@router.get("/new")
def new_form(
    request: Request, customer_id: str = "", need_id: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    form = _blank_form(customer_id.strip())
    nid = opps.parse_id(need_id)
    if nid is not None and any(n.id == nid for n in opps.bundlable_needs(db, opps.parse_id(customer_id))):
        form["need_ids"] = [nid]  # 고객 니즈에서 만들기: 그 니즈를 체크해 둔다 (그 고객의, 아직 묶이지 않은 니즈일 때만)
    return _form_page(request, db, form)


@router.post("")
def create(
    request: Request,
    name: str = Form(""), customer_id: str = Form(""), bm_id: str = Form(""), memo: str = Form(""),
    need_ids: list[str] = Form([]), solution_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    values, error = _parse(db, name, customer_id, bm_id, memo)
    form = _submitted(values, need_ids, solution_ids)
    if error:
        return _form_page(request, db, form, error=error)
    opp = Opportunity(**values, created_by_id=user.id)
    db.add(opp)
    db.flush()
    if error := opps.apply_needs(db, opp, need_ids):
        db.rollback()
        return _form_page(request, db, form, error=error)
    opp.solutions = pick(db, Solution, solution_ids)
    db.commit()
    db.refresh(opp)
    mirror.sync(opp)
    resync_objects(db, mirror, opps.dependents(db, opp))
    return RedirectResponse(f"/opportunities/{opp.id}", status_code=303)


@router.get("/{opportunity_id}")
def detail(opportunity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, user, get_or_404(db, Opportunity, opportunity_id))


@router.get("/{opportunity_id}/edit")
def edit_form(opportunity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    opp = get_or_404(db, Opportunity, opportunity_id)
    form = {"name": opp.name, "customer_id": opp.customer_id, "bm_id": opp.bm_id or "", "memo": opp.memo,
            "need_ids": [n.id for n in opps.needs_of(db, opp)], "solution_ids": [s.id for s in opp.solutions]}
    return _form_page(request, db, form, opp)


@router.post("/{opportunity_id}")
def update(
    opportunity_id: int, request: Request,
    name: str = Form(""), customer_id: str = Form(""), bm_id: str = Form(""), memo: str = Form(""),
    need_ids: list[str] = Form([]), solution_ids: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    opp = get_or_404(db, Opportunity, opportunity_id)
    values, error = _parse(db, name, customer_id, bm_id, memo)
    form = _submitted(values, need_ids, solution_ids)
    if error is None:
        error = opps.customer_change_problem(db, opp, values["customer_id"])
    if error:
        return _form_page(request, db, form, opp, error)
    before = opps.dependents(db, opp)
    for field, value in values.items():
        setattr(opp, field, value)
    db.flush()
    if error := opps.apply_needs(db, opp, need_ids):
        db.rollback()
        return _form_page(request, db, form, opp, error)
    opp.solutions = pick(db, Solution, solution_ids)
    db.commit()
    db.refresh(opp)
    mirror.sync(opp)
    resync_objects(db, mirror, [*before, *opps.dependents(db, opp)])
    return RedirectResponse(f"/opportunities/{opp.id}", status_code=303)


@router.post("/{opportunity_id}/stage")
def change_stage(
    opportunity_id: int, stage: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    opp = get_or_404(db, Opportunity, opportunity_id)
    if error := opps.stage_problem(stage):
        raise HTTPException(status_code=400, detail=error)
    if opp.stage != stage:  # 어디서 어디로든 바꿀 수 있다
        opp.stage = stage
        db.commit()
        db.refresh(opp)
        mirror.sync(opp)
        resync_objects(db, mirror, [opp.customer, opp.bm])
    return RedirectResponse(f"/opportunities/{opp.id}", status_code=303)


def _convert_page(request: Request, db: Session, opp: Opportunity, form: dict, error: str | None = None):
    return render(
        request, "opportunities/convert.html", status_code=400 if error else 200,
        opp=opp, form=form, error=error, projects=opps.linkable_projects(db, opp),
    )


@router.get("/{opportunity_id}/convert")
def convert_form(opportunity_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    opp = get_or_404(db, Opportunity, opportunity_id)
    if opp.project_id is not None:  # 두 번째부터는 있던 프로젝트를 보여 준다
        return RedirectResponse(f"/projects/{opp.project_id}", status_code=303)
    if error := opps.convert_problem(opp):
        return _detail_page(request, db, user, opp, error)
    return _convert_page(request, db, opp, {"mode": "create", "name": opp.name, "project_id": ""})


def _lost_claim(request: Request, db: Session, user: User, opp: Opportunity, form: dict, error: str):
    """프로젝트 자리를 차지하지 못했을 때: 이미 이어진 프로젝트가 있으면 거기로, 없으면(다른 영업기회가 그 프로젝트를 먼저 이은 경우) 오류 화면."""
    db.refresh(opp)
    if opp.project_id is not None:
        return RedirectResponse(f"/projects/{opp.project_id}", status_code=303)
    return _convert_page(request, db, opp, form, error)


@router.post("/{opportunity_id}/convert")
def convert(
    opportunity_id: int, request: Request,
    mode: str = Form("create"), name: str = Form(""), project_id: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    """수주한 영업기회를 프로젝트로: 새 프로젝트를 만들거나 같은 고객의 기존 프로젝트를 잇는다. 한 번만."""
    opp = get_or_404(db, Opportunity, opportunity_id)
    if opp.project_id is not None:
        return RedirectResponse(f"/projects/{opp.project_id}", status_code=303)
    if error := opps.convert_problem(opp):
        return _detail_page(request, db, user, opp, error)
    form = {"mode": "link" if mode == "link" else "create", "name": name.strip(), "project_id": project_id.strip()}
    if form["mode"] == "link":
        pid = opps.parse_id(project_id)
        project = db.get(Project, pid) if pid is not None else None
        if error := opps.link_problem(db, opp, project):
            return _convert_page(request, db, opp, form, error)
        if error := opps.link_project(db, opp, project):
            return _lost_claim(request, db, user, opp, form, error)
    else:
        if not form["name"]:
            return _convert_page(request, db, opp, form, opps.NAME_REQUIRED)
        project = opps.convert_create(db, opp, form["name"], user)
        if project is None:  # 그 사이 다른 요청이 먼저 전환했다: 새로 만든 프로젝트는 되돌려졌고 있던 프로젝트를 보여 준다
            return _lost_claim(request, db, user, opp, form, "")
    db.commit()
    db.refresh(opp)
    mirror.sync(project)
    mirror.sync(opp)
    resync_objects(db, mirror, [opp.customer, opp.bm])
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.post("/{opportunity_id}/delete")
def delete(
    opportunity_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    opp = get_deletable_or_404(db, Opportunity, opportunity_id, user)
    related = opps.dependents(db, opp)  # 묶인 니즈의 활동 기록까지 (지우기 전에 알아 둔다)
    opps.release_opportunity(db, opp)
    mirror.remove(opp)
    db.delete(opp)
    db.commit()
    resync_objects(db, mirror, related)
    return RedirectResponse("/opportunities", status_code=303)

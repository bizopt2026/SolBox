"""솔루션 화면과 발굴 단계."""
from __future__ import annotations

import shutil
from collections import Counter

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, selectinload

from app.ai.gate import require_ai_on
from app.idparse import parse_id
from app import business_types as bts, labels, ontology, origin, relations, solution_fields as sfields, ui
from app.ai.queue import enqueue, queue_position
from app.ai.tasks import NO_READABLE_FILES, SolutionDraft
from app import guards, notices
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import AIJob, Activity, Partner, Solution, SolutionField, User
from app.routers._common import (
    list_filter, origin_filter,
    activities_linked_to, clear_file_targets, get_deletable_or_404, get_or_404, name_error, newest_first, duplicate_warning, paginate,
    resync_activities, resync_objects, resync_related, row_info, search_by_name, sort_rows,
)
from app.security import can_delete, require_user

router = APIRouter(prefix="/solutions")
FIELDS = ("name", "summary", "description", "partner_id", "stage", "usage_direction", "applicable_types", "tags")
BAD_STAGE = ui.FieldError("알 수 없는 단계입니다.", "stage")
BAD_PARTNER = ui.FieldError("존재하지 않는 파트너입니다.", "partner_id")


def _form_page(
    request: Request, db: Session, form: dict, solution: Solution | None = None, error: str | None = None,
    conflict=None, duplicate=None, bt: dict | None = None, **extra,
):
    return render(
        request, "solutions/form.html", status_code=409 if conflict else 400 if error else 200,
        form=form, solution=solution, partners=search_by_name(db, Partner), error=error, conflict=conflict, duplicate=duplicate,
        bt=bt if bt is not None else bts.form_value_of(solution),
        field_options=sfields.select_options(db, solution, form.get("field_id")), **extra,
    )


def detail_page(request: Request, db: Session, user: User, solution: Solution, notice: str | None = None):
    return render(
        request, "solutions/detail.html",
        solution=solution, notice=notice,
        can_delete_files={f.id: can_delete(user, f) for f in solution.files},
        **relations.page_related(db, solution, user),
        can_delete=can_delete(user, solution), origin=origin.view(db, user, solution), meta=ui.stamp_info(db, solution),
    )


def _normalize_tags(tags: str) -> str:
    return ",".join(t for t in (p.strip() for p in tags.split(",")) if t)


def _parse(db: Session, name, summary, description, partner_id, stage, usage_direction, applicable_types, tags):
    """폼 값을 다듬고 오류 문구를 돌려준다. 단계 코드와 파트너가 올바른지 확인한다."""
    values = dict(
        name=name.strip(), summary=summary.strip(), description=description.strip(),
        partner_id=partner_id.strip(), stage=stage.strip() or ontology.STAGE_DEFAULT,
        usage_direction=usage_direction.strip(), applicable_types=applicable_types.strip(),
        tags=_normalize_tags(tags),
    )
    if error := name_error(name):
        return values, error
    if values["stage"] not in labels.STAGES:
        return values, BAD_STAGE
    if values["partner_id"]:
        pid = values["partner_id"]
        if (partner_pk := parse_id(pid)) is None or db.get(Partner, partner_pk) is None:
            return values, BAD_PARTNER
        values["partner_id"] = partner_pk
    else:
        values["partner_id"] = None
    return values, None


@router.get("")
def list_solutions(
    request: Request, q: str = "", stage: str = "", page: str = "1",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    solutions = search_by_name(db, Solution, q, Solution.summary, Solution.tags, loaders=(selectinload(Solution.partner), selectinload(Solution.business_types)))
    solutions, flt = list_filter(db, request, "solution", sort_rows(request, "solution", solutions, db=db))
    solutions, origin_tabs, origin_now = origin_filter(request, solutions)
    solutions, type_tabs, type_now = bts.type_tabs(request, solutions, lambda s: s.business_type_codes)
    counts = Counter(s.stage for s in solutions)  # 구분 탭의 건수: 검색어·걸러 보기를 적용한 목록 기준 (탭을 누르면 나오는 건수와 같다)
    total = len(solutions)
    if stage:
        solutions = [s for s in solutions if s.stage == stage]
    tabs = ui.seg_tabs(request, "stage", list(labels.STAGES.items()), counts, stage, total)
    group, explicit = sfields.group_param(request)
    ctx = dict(q=q.strip(), stage=stage, flt=flt, tabs=tabs, origin_tabs=origin_tabs, origin_now=origin_now, type_tabs=type_tabs, type_now=type_now,
               group=group, group_choices=_group_choices(request, group))
    if group:  # 묶어 보기: 걸러진 목록 전부를 묶는다 (쪽 나누기 없음 — 한 묶음이 쪽에 걸려 잘리지 않게)
        groups = sfields.build_groups(db, group, solutions)
        info = row_info(db, "solution", solutions)
        response = render(request, "solutions/list.html", pg=paginate(solutions, 1, max(len(solutions), 1)), groups=groups, info=info, **ctx)
    else:
        pg = paginate(solutions, page)
        response = render(request, "solutions/list.html", pg=pg, groups=None, info=row_info(db, "solution", pg.items), **ctx)
    _remember_group(response, group, explicit, request)
    return response


def _group_choices(request: Request, current: str) -> list[dict]:
    """"묶어 보기" 선택 링크: 없음 + 기준들. 다른 조건(검색어·구분·걸러 보기·정렬)은 그대로 두고 쪽 번호만 버린다. 없음은 `group=none` (기억한 값을 지운다)."""
    from urllib.parse import urlencode

    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in (ontology.SOLUTION_GROUP_PARAM, "page", "fu_toggled", "notice", "nt")]

    def href(code: str) -> str:
        return request.url.path + "?" + urlencode(keep + [(ontology.SOLUTION_GROUP_PARAM, code or "none")])
    return [{"code": c, "label": label, "href": href(c), "active": c == current}
            for c, label in (("", "없음"), *ontology.SOLUTION_GROUPS.items())]


def _remember_group(response, group: str, explicit: bool, request: Request) -> None:
    """주소에 `group` 이 있으면 그 선택을 쿠키에 기억한다 (`none` 은 기억을 지운다). 없으면 쿠키를 건드리지 않는다. DB 에는 두지 않는다."""
    if not explicit:
        return
    if group:
        response.set_cookie(ontology.SOLUTION_GROUP_COOKIE, group, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax", path="/solutions")
    else:
        response.delete_cookie(ontology.SOLUTION_GROUP_COOKIE, path="/solutions")


@router.get("/new")
def new_form(request: Request, partner_id: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    """`?partner_id=` 는 파트너 화면의 "+ 솔루션 등록": 존재하는 파트너면 미리 골라 둔다."""
    pid = partner_id.strip()
    chosen = pid if (partner_pk := parse_id(pid)) is not None and db.get(Partner, partner_pk) is not None else ""
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "stage": ontology.STAGE_DEFAULT, "partner_id": chosen, "field_id": ""})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), summary: str = Form(""), description: str = Form(""),
    partner_id: str = Form(""), stage: str = Form(ontology.STAGE_DEFAULT), usage_direction: str = Form(""),
    applicable_types: str = Form(""), tags: str = Form(""), confirm_dup: str = Form(""), field_id: str | None = Form(None), field_present: str | None = Form(None),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once), bt: bts.BTForm = Depends(bts.bt_form),
):
    values, error = _parse(db, name, summary, description, partner_id, stage, usage_direction, applicable_types, tags)
    selection, bt_error = bts.solution_selection(bt) if bt.present else ([], None)
    new_field, field_error = sfields.resolve_form(db, field_id or "")
    values["field_id"] = field_id or ""  # 오류로 다시 그릴 때 고른 값을 그대로 (저장은 아래 `new_field`)
    error = error or bt_error or field_error
    if error:
        return _form_page(request, db, values, error=error, bt=bts.form_value(bt))
    if dup := duplicate_warning(db, Solution, name, confirm_dup):
        return _form_page(request, db, values, duplicate=dup, bt=bts.form_value(bt))
    values["field_id"] = new_field
    solution = Solution(**values, created_by_id=user.id, origin_type=ontology.ORIGIN_MANUAL)
    bts.apply_solution(solution, selection)
    db.add(solution)
    db.commit()
    mirror.sync(solution)
    if solution.partner:
        mirror.sync(solution.partner)
    return once.done(notices.redirect(f"/solutions/{solution.id}", "created"))


@router.get("/{solution_id}")
def detail(solution_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return detail_page(request, db, user, get_or_404(db, Solution, solution_id))


DRAFT_KIND = "solution_draft"
BAD_DRAFT_JOB = "이 솔루션의 AI 초안 작업이 아닙니다."


def _draft_job(db: Session, solution: Solution, raw: str, user: User) -> AIJob:
    job = db.get(AIJob, job_id) if (job_id := parse_id(raw)) is not None else None
    if (job is None or job.kind != DRAFT_KIND or (job.input or {}).get("solution_id") != solution.id
            or job.requester_id != user.id):
        raise HTTPException(status_code=400, detail=BAD_DRAFT_JOB)
    return job


@router.post("/{solution_id}/draft", dependencies=[Depends(require_ai_on)])
def start_draft(solution_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    solution = get_or_404(db, Solution, solution_id)
    job = enqueue(db, DRAFT_KIND, {"solution_id": solution.id}, user.id)
    return RedirectResponse(f"/solutions/{solution.id}/edit?draft_job={job.id}", status_code=303)


@router.get("/{solution_id}/edit")
def edit_form(
    solution_id: int, request: Request, draft_job: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    solution = get_or_404(db, Solution, solution_id)
    form = {f: getattr(solution, f) for f in FIELDS}
    form["partner_id"] = solution.partner_id or ""
    form["field_id"] = str(solution.field_id) if solution.field_id else ""
    if not draft_job:
        return _form_page(request, db, form, solution)
    job = _draft_job(db, solution, draft_job, user)
    next_url = f"/solutions/{solution.id}/edit?draft_job={job.id}"
    if job.status in ontology.JOB_ACTIVE:
        return _form_page(request, db, form, solution, job=job, position=queue_position(db, job), next=next_url)
    draft = None
    if job.status == "done":
        try:
            draft = SolutionDraft.model_validate_json(job.result)
        except ValueError:
            draft = None
    if draft is None:
        kind = job.shown_error_kind if job.status == "failed" else "bad_output"
        message = job.error if job.error == NO_READABLE_FILES else labels.ERROR_KINDS.get(kind, labels.ERROR_KINDS["unknown"])
        return _form_page(request, db, form, solution, draft_error=message)
    # AI 가 비워 보낸 칸은 기존 값을 그대로 둔다
    form.update(summary=draft.summary.strip() or form["summary"],
                description=draft.description.strip() or form["description"],
                tags=_normalize_tags(",".join(draft.tags)) or form["tags"])
    # 사업방식 제안: 저장된 선택에 더해 미리 체크해 보여 준다 (저장은 사용자가 한다. 이미 있는 선택·메모는 지우지 않는다)
    suggested = bts.clean_suggestions(draft.business_types)
    bt = bts.form_value_of(solution)
    bt["business_types"] = [c for c in ontology.BUSINESS_TYPES if c in bt["business_types"] or c in {s["code"] for s in suggested}]
    # 분야 제안: 사용 중인 분야 이름에서 고른 것만 (모르는 이름은 버린다). 이미 분야가 있으면 그대로 두고 제안은 보이기만 한다
    suggested_field = sfields.suggest_id(db, draft.field)
    if suggested_field is not None and not solution.field_id:
        form["field_id"] = str(suggested_field)
    return _form_page(request, db, form, solution, drafted=True, bt=bt, bt_suggest={s["code"]: s["reason"] for s in suggested},
                      field_suggest=suggested_field)


@router.post("/{solution_id}/stage")
def change_stage(
    solution_id: int, stage: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    solution = get_or_404(db, Solution, solution_id)
    if stage not in labels.STAGES:
        raise HTTPException(status_code=400, detail=BAD_STAGE)
    if solution.stage != stage:  # 같은 단계로 바꾸는 요청은 쓰지 않는다
        solution.stage = stage
        db.commit()
        mirror.sync(solution)
    return notices.redirect(f"/solutions/{solution.id}", f"stage.{stage}")


@router.post("/{solution_id}")
def update(
    solution_id: int, request: Request,
    name: str = Form(""), summary: str = Form(""), description: str = Form(""),
    partner_id: str = Form(""), stage: str = Form(ontology.STAGE_DEFAULT), usage_direction: str = Form(""),
    applicable_types: str = Form(""), tags: str = Form(""), confirm_dup: str = Form(""), field_id: str | None = Form(None), field_present: str | None = Form(None),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), rev: Rev = Depends(rev_form), bt: bts.BTForm = Depends(bts.bt_form),
):
    solution = get_or_404(db, Solution, solution_id)
    values, error = _parse(db, name, summary, description, partner_id, stage, usage_direction, applicable_types, tags)
    # 분야 칸이 폼에 왔는지는 숨은 칸 `field_present` 로 안다 (빈 선택 "" 은 폼 읽기에서 없는 값과 같아진다). 칸이 오지 않으면 그대로 둔다 (옛 화면·API)
    present = field_present is not None or field_id is not None
    new_field, field_error = sfields.resolve_form(db, field_id or "", solution) if present else (solution.field_id, None)
    values["field_id"] = (field_id or "") if present else str(solution.field_id or "")  # 오류로 다시 그릴 때 고른 값 (저장은 아래 `new_field`)
    shown_field = {"field": _field_text(db, new_field)} if present and not field_error else {}
    selection, bt_error = bts.solution_selection(bt) if bt.present else ([], None)  # 사업방식 칸이 오지 않으면 그대로 둔다 (옛 화면·API)
    shown = bts.form_value(bt) if bt.present else None
    typed = {**values, "business_types": bts.form_text(selection)} if bt.present and not bt_error else dict(values)
    typed.update(shown_field)
    stored = {**{f: getattr(solution, f) for f in values}, "business_types": bts.solution_text(solution), "field": sfields.name_of(solution)} if ("business_types" in typed or shown_field) else None
    if conflict := guards.check_rev(solution, rev, values=typed, stored=stored):
        return _form_page(request, db, values, solution, conflict=conflict, bt=shown)
    error = error or bt_error or field_error
    if error:
        return _form_page(request, db, values, solution, error, bt=shown)
    if dup := duplicate_warning(db, Solution, name, confirm_dup, exclude=solution):
        return _form_page(request, db, values, solution, duplicate=dup, bt=shown)
    old_partner = solution.partner
    values["field_id"] = new_field
    for field, value in values.items():
        setattr(solution, field, value)
    db.expire(solution, ["field"])  # 분야를 바꿨으면 관계가 낡은 값을 들고 있지 않게 (지식 사본·상세가 새 분야를 읽는다)
    if bt.present:
        bts.apply_solution(solution, selection)
    db.commit()
    mirror.sync(solution)
    db.refresh(solution)
    resync_related(db, mirror, solution)  # 새 파트너 사본과 활동 기록
    if old_partner is not None and old_partner.id != solution.partner_id:  # 옛 파트너 사본
        db.refresh(old_partner)
        mirror.sync(old_partner)
    return notices.redirect(f"/solutions/{solution.id}", "saved")


def _field_text(db: Session, field_id) -> str:
    found = db.get(SolutionField, field_id) if field_id else None
    return found.name if found is not None else ""


@router.post("/{solution_id}/delete")
def delete(
    solution_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    solution = get_deletable_or_404(db, Solution, solution_id, user)
    activities = activities_linked_to(db, Activity.solutions, solution)
    partner = solution.partner
    review_links = [*solution.bms, *solution.opportunities]  # 이 솔루션이 적힌 BM·영업기회 사본 (연결 행은 함께 지워진다)
    mirror.remove(solution)
    shutil.rmtree(request.app.state.settings.uploads_dir / "solutions" / str(solution.id), ignore_errors=True)
    clear_file_targets(db, "solution", solution.id)
    db.delete(solution)
    db.commit()
    resync_activities(db, mirror, activities)
    resync_objects(db, mirror, review_links)
    if partner is not None:
        mirror.sync(partner)
    return notices.redirect("/solutions", "deleted")

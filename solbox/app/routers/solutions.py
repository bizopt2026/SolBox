"""솔루션 화면과 발굴 단계."""
from __future__ import annotations

import shutil
from collections import Counter

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, selectinload

from app.idparse import parse_id
from app import labels, ontology, origin, relations, ui
from app.ai.queue import enqueue, queue_position
from app.ai.tasks import NO_READABLE_FILES, SolutionDraft
from app import notices
from app.db import get_db
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import AIJob, Activity, Partner, Solution, User
from app.routers._common import (
    list_filter,
    activities_linked_to, clear_file_targets, get_deletable_or_404, get_or_404, name_error, newest_first, paginate,
    resync_activities, resync_objects, resync_related, row_info, search_by_name, sort_rows,
)
from app.security import can_delete, require_user

router = APIRouter(prefix="/solutions")
FIELDS = ("name", "summary", "description", "partner_id", "stage", "usage_direction", "applicable_types", "tags")
BAD_STAGE = ui.FieldError("알 수 없는 단계입니다.", "stage")
BAD_PARTNER = ui.FieldError("존재하지 않는 파트너입니다.", "partner_id")


def _form_page(
    request: Request, db: Session, form: dict, solution: Solution | None = None, error: str | None = None,
    **extra,
):
    return render(
        request, "solutions/form.html", status_code=400 if error else 200,
        form=form, solution=solution, partners=search_by_name(db, Partner), error=error, **extra,
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
    solutions = search_by_name(db, Solution, q, Solution.summary, Solution.tags, loaders=(selectinload(Solution.partner),))
    solutions, flt = list_filter(db, request, "solution", sort_rows(request, "solution", solutions))
    counts = Counter(s.stage for s in solutions)  # 구분 탭의 건수: 검색어·걸러 보기를 적용한 목록 기준 (탭을 누르면 나오는 건수와 같다)
    total = len(solutions)
    if stage:
        solutions = [s for s in solutions if s.stage == stage]
    pg = paginate(solutions, page)
    tabs = ui.seg_tabs(request, "stage", list(labels.STAGES.items()), counts, stage, total)
    return render(request, "solutions/list.html", pg=pg, q=q.strip(), stage=stage, flt=flt, tabs=tabs, info=row_info(db, "solution", pg.items))


@router.get("/new")
def new_form(request: Request, partner_id: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    """`?partner_id=` 는 파트너 화면의 "+ 솔루션 등록": 존재하는 파트너면 미리 골라 둔다."""
    pid = partner_id.strip()
    chosen = pid if (partner_pk := parse_id(pid)) is not None and db.get(Partner, partner_pk) is not None else ""
    return _form_page(request, db, {**{f: "" for f in FIELDS}, "stage": ontology.STAGE_DEFAULT, "partner_id": chosen})


@router.post("")
def create(
    request: Request,
    name: str = Form(""), summary: str = Form(""), description: str = Form(""),
    partner_id: str = Form(""), stage: str = Form(ontology.STAGE_DEFAULT), usage_direction: str = Form(""),
    applicable_types: str = Form(""), tags: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    values, error = _parse(db, name, summary, description, partner_id, stage, usage_direction, applicable_types, tags)
    if error:
        return _form_page(request, db, values, error=error)
    solution = Solution(**values, created_by_id=user.id, origin_type=ontology.ORIGIN_MANUAL)
    db.add(solution)
    db.commit()
    mirror.sync(solution)
    if solution.partner:
        mirror.sync(solution.partner)
    return notices.redirect(f"/solutions/{solution.id}", "created")


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


@router.post("/{solution_id}/draft")
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
        kind = job.error_kind if job.status == "failed" else "bad_output"
        message = job.error if job.error == NO_READABLE_FILES else labels.ERROR_KINDS.get(kind, labels.ERROR_KINDS["unknown"])
        return _form_page(request, db, form, solution, draft_error=message)
    # AI 가 비워 보낸 칸은 기존 값을 그대로 둔다
    form.update(summary=draft.summary.strip() or form["summary"],
                description=draft.description.strip() or form["description"],
                tags=_normalize_tags(",".join(draft.tags)) or form["tags"])
    return _form_page(request, db, form, solution, drafted=True)


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
    applicable_types: str = Form(""), tags: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror),
):
    solution = get_or_404(db, Solution, solution_id)
    values, error = _parse(db, name, summary, description, partner_id, stage, usage_direction, applicable_types, tags)
    if error:
        return _form_page(request, db, values, solution, error)
    old_partner = solution.partner
    for field, value in values.items():
        setattr(solution, field, value)
    db.commit()
    mirror.sync(solution)
    db.refresh(solution)
    resync_related(db, mirror, solution)  # 새 파트너 사본과 활동 기록
    if old_partner is not None and old_partner.id != solution.partner_id:  # 옛 파트너 사본
        db.refresh(old_partner)
        mirror.sync(old_partner)
    return notices.redirect(f"/solutions/{solution.id}", "saved")


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

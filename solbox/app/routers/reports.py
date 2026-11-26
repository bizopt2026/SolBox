"""팀 업무 보고 화면: 목록·만들기·상세(단계 띠 4칸: 실적 → 지난 계획 점검 → 다음 계획 → 확정·파일)와 저장 POST.
숫자·점검·계획 넘김·확정 규칙은 `app/reports.py`, 기간은 `app/report_periods.py` 에 있고 여기서는 부르기만 한다.
팀원 누구나(관리자 구분 없음) 만들고·고치고·확정하고·지울 수 있다 (설계 §5.4b). 고치기 폼은 모두 보고 건의 고침 표시를 쓴다."""
from __future__ import annotations

import logging
import shutil
from collections import Counter
from datetime import date, datetime
from urllib.parse import quote
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import activity_rows, guards, notices, ontology, report_files, report_periods, reports, ui
from app.report_periods import period_text, short_period
from app.ai import gate
from app.ai.gate import keeps_text, require_ai_on
from app.ai.report_tasks import PLAN_OPEN
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.idparse import parse_id
from app.main import render
from app.models import Customer, Report, ReportMessage, ReportPlanItem, User
from app.records import rows as form_rows
from app.relations import report_label
from app.routers._common import get_or_404, paginate
from app.security import require_user

router = APIRouter(prefix="/reports")

STEP_KEYS = tuple(k for k, _ in ontology.REPORT_STEPS)
DEFAULT_STEP = {ontology.REPORT_STATUS_DEFAULT: "result", ontology.REPORT_STATUS_PLANNING: "plan", ontology.REPORT_STATUS_CONFIRMED: "files"}
NOTE_STEP = {"kpi": "result", "opps": "result", "proposals": "result", "issues": "result", "check": "check", "plan": "plan"}  # 구역 문구가 놓인 단계
ACTIVITY_PREVIEW = 5  # 실적 단계의 "주요 활동" 에 보이는 수 (나머지는 활동 기록 목록에서)
PLAN_LOCKED = "확정한 계획은 고칠 수 없습니다. 확정·파일 단계에서 확정을 먼저 풀어 주세요. 저장하지 않았습니다."
CHECK_LOCKED = "확정한 보고의 지난 계획 점검은 고칠 수 없습니다. 확정·파일 단계에서 확정을 먼저 풀어 주세요. 저장하지 않았습니다."
EMPTY_MESSAGE = "메시지를 입력하세요."
MAX_MESSAGE = 4000
TOO_LONG_MESSAGE = f"메시지는 {MAX_MESSAGE}자까지 보낼 수 있습니다."
CHAT_BUSY = "이미 AI 작업이 진행 중이라 보내지 못했습니다. 쓴 글은 아래에 남겨 두었으니 작업이 끝나면 다시 보내 주세요."
NO_DRAFT = "옮길 AI 계획 초안이 없습니다."
JOB_PLACE = {"report_summary": ("result", "summary"), "report_plan": ("plan", "plan"), "report_chat": ("plan", "chat"), "report_notes": ("files", "report-notes")}  # 작업 → (단계, 구역 앵커)


# ---- 기간 글 ----

def _preview(db: Session, unit: str, day: date) -> dict:
    """만들기 화면의 기간 미리보기: 기간·다음 기간·지난 계획을 가져올 보고·같은 기간의 기존 보고."""
    start, end = report_periods.period_of(unit, day)
    nxt = report_periods.next_period(unit, start, end)
    prev = reports.previous_report(db, SimpleNamespace(id=None, unit=unit, period_start=start))
    existing = db.scalars(select(Report).where(Report.unit == unit, Report.period_start == start)).first()
    return {"period": period_text(unit, start, end), "next": short_period(unit, *nxt), "prev": prev, "existing": existing}


def _parse_day(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip()) if text and text.strip() else None
    except ValueError:
        return None


# ---- 목록 ----

def _row_summary(r: Report) -> str:
    """목록 줄의 한 줄 요약: 확정한 보고는 그때 숫자(활동·수주), 그리고 계획 수 (+ 다음 보고가 점검한 완료 수)."""
    parts = []
    k = (r.snapshot or {}).get("kpis") or {}
    if k:
        parts += [f"활동 {k.get('activities', 0)}", f"수주 {k.get('won', 0)}"]
    plan = f"계획 {len(r.plan_items)}"
    done = sum(1 for i in r.plan_items if i.result == "done")
    if any(i.checked_in_report_id for i in r.plan_items):
        plan += f" (완료 {done})"
    parts.append(plan)
    return " · ".join(parts)


@router.get("")
def list_reports(request: Request, unit: str = "", page: str = "1", user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Report).options(selectinload(Report.plan_items)).order_by(Report.period_start.desc(), Report.id.desc())))
    counts = Counter(r.unit for r in rows)
    total = len(rows)
    unit = unit if unit in ontology.REPORT_UNITS else ""
    if unit:
        rows = [r for r in rows if r.unit == unit]
    tabs = ui.seg_tabs(request, "unit", list(ontology.REPORT_UNITS.items()), counts, unit, total)
    pg = paginate(rows, page)
    author_ids = {r.created_by_id for r in pg.items if r.created_by_id}
    authors = {u.id: (u.name or u.username) for u in db.scalars(select(User).where(User.id.in_(author_ids)))} if author_ids else {}
    return render(request, "reports/list.html", pg=pg, tabs=tabs, unit=unit, authors=authors, row_summary=_row_summary)


# ---- 만들기 ----

def _new_page(request: Request, db: Session, unit: str, day: date, error: str | None = None, status_code: int = 200):
    return render(request, "reports/new.html", status_code=status_code, unit=unit, day=day, error=error, preview=_preview(db, unit, day))


@router.get("/new")
def new_form(request: Request, unit: str = "", day: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    unit = unit if unit in ontology.REPORT_UNITS else ontology.REPORT_UNIT_DEFAULT
    return _new_page(request, db, unit, _parse_day(day) or date.today())


@router.post("")
def create(
    request: Request, unit: str = Form(""), day: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), once: Once = Depends(form_once),
):
    parsed = _parse_day(day)
    if unit not in ontology.REPORT_UNITS:
        return _new_page(request, db, ontology.REPORT_UNIT_DEFAULT, parsed or date.today(), ui.FieldError("보고 단위를 고르세요.", "unit"), 400)
    if parsed is None:
        return _new_page(request, db, unit, date.today(), ui.FieldError("기준 날짜를 YYYY-MM-DD 로 넣어 주세요.", "day"), 400)
    start, end = report_periods.period_of(unit, parsed)
    existing = db.scalars(select(Report).where(Report.unit == unit, Report.period_start == start)).first()
    if existing is not None:
        return once.done(notices.redirect(f"/reports/{existing.id}", "report_exists"))
    r = Report(unit=unit, period_start=start, period_end=end, created_by_id=user.id)
    db.add(r)
    try:
        db.flush()
    except IntegrityError:  # 같은 기간을 다른 사람이 막 만들었다: 그 보고로
        db.rollback()
        existing = db.scalars(select(Report).where(Report.unit == unit, Report.period_start == start)).first()
        if existing is None:
            raise
        return once.done(notices.redirect(f"/reports/{existing.id}", "report_exists"))
    reports.sync_carry(db, r)  # 지난 계획의 미완료(아직 점검 안 함 포함) 항목을 계획 맨 앞에
    db.commit()
    return once.done(notices.redirect(f"/reports/{r.id}", "report_created"))


# ---- 상세 ----

def _steps(r: Report, current: str) -> list[dict]:
    """단계 띠: 순서는 강제하지 않아 모두 누를 수 있다. 끝난 표시: 계획 작성 중이면 실적·점검, 확정이면 전부."""
    done = set(STEP_KEYS) if r.status == ontology.REPORT_STATUS_CONFIRMED else {"result", "check"} if r.status == ontology.REPORT_STATUS_PLANNING else set()
    return [
        {"key": key, "label": label, "href": f"?step={key}", "busy": False,
         "state": "current" if key == current else "done" if key in done else "todo"}
        for key, label in ontology.REPORT_STEPS
    ]


def _folder(request: Request, r: Report):
    return request.app.state.settings.reports_dir / str(r.id)


def _detail_page(request: Request, db: Session, r: Report, step: str, status_code: int = 200, form: dict | None = None, **extra):
    """상세 화면. `form` = 충돌로 다시 그릴 때 {"which": 폼 이름, "conflict": Conflict, "values": 쓴 값} (그 폼만 쓴 값으로 채운다)."""
    step = step if step in STEP_KEYS else DEFAULT_STEP.get(r.status, "result")
    data = reports.data_of(db, r)
    prev = reports.previous_report(db, r)
    check = reports.items_to_check(db, r)
    plan = list(r.plan_items)
    nxt = report_periods.next_period(r.unit, r.period_start, r.period_end)
    label = report_label(r)
    title = f"{ontology.REPORT_UNITS.get(r.unit, '')} 업무 보고"
    notes = reports.notes_of(r)
    author = db.get(User, r.created_by_id) if r.created_by_id else None
    sub = f"{period_text(r.unit, r.period_start, r.period_end)} · 다음 기간 {short_period(r.unit, *nxt)}"
    if author is not None:
        sub += f" · 만든 사람 {author.name or author.username}"
    return render(
        request, "reports/detail.html", status_code=status_code,
        report=r, title=title, page_title=label, crumbs=ui.crumbs("report", label), sub=sub, step=step, steps=_steps(r, step), stepbar_label="업무 보고 단계",
        data=data, prev=prev, check=check, plan=plan, next_text=short_period(r.unit, *nxt), period=period_text(r.unit, r.period_start, r.period_end),
        notes=notes, default_notes=reports.default_notes(data, check, plan), note_flags=reports.note_line_warnings(notes, data, check, plan, r.highlights_md or ""),
        summary_job=reports.last_summary_job(db, r) if step == "result" else None, draft_changes=reports.draft_changes(r) if r.plan_draft else None,
        plan_open=PLAN_OPEN,
        unconfirm_problem=reports.unconfirm_problem(db, r) if r.status == ontology.REPORT_STATUS_CONFIRMED else None,
        activity_preview=ACTIVITY_PREVIEW, form=form or {}, meta=ui.stamp_info(db, r),
        files=report_files.listing(_folder(request, r), r.file_names) if step == "files" and r.status == ontology.REPORT_STATUS_CONFIRMED else [], file_formats=report_files.FORMATS,
        customer_names=list(db.scalars(select(Customer.name).order_by(Customer.name))) if step == "plan" else [],  # 계획 표의 고객 칸 고르기 목록
        **extra,
    )


@router.get("/{report_id}")
def detail(report_id: int, request: Request, step: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _detail_page(request, db, get_or_404(db, Report, report_id), step)


def _back(r: Report, step: str, code: str, anchor: str = ""):
    return notices.redirect(f"/reports/{r.id}?step={step}" + (f"#{anchor}" if anchor else ""), code)


@router.post("/{report_id}/summary")
def save_summary(
    report_id: int, request: Request, summary_md: str = Form(""), highlights_md: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), rev: Rev = Depends(rev_form),
):
    r = get_or_404(db, Report, report_id)
    values = {"summary_md": summary_md, "highlights_md": highlights_md}
    if conflict := guards.check_rev(r, rev, values=values):
        return _detail_page(request, db, r, "result", 409, {"which": "summary", "conflict": conflict, "values": values})
    r.summary_md, r.highlights_md = summary_md.strip(), highlights_md.strip()
    db.commit()
    return _back(r, "result", "saved", "summary")


@router.post("/{report_id}/notes")
async def save_notes(
    report_id: int, request: Request, step: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), rev: Rev = Depends(rev_form),
):
    """구역별 보고 문구: 보낸 구역(`sn_<구역>`)만 바꾼다. 칸의 빈 줄은 빼고 줄마다 한 문장으로 저장한다 (비우면 확정 때 기본 문장이 들어간다)."""
    r = get_or_404(db, Report, report_id)
    form = await request.form()
    sent = {k: form.get(f"sn_{k}") for k in reports.NOTE_KEYS if isinstance(form.get(f"sn_{k}"), str)}
    step = step if step in STEP_KEYS else NOTE_STEP.get(next(iter(sent), ""), "result")
    if conflict := guards.check_rev(r, rev):
        return _detail_page(request, db, r, step, 409, {"which": "notes", "conflict": conflict, "values": sent})
    notes = reports.notes_of(r)
    for key, text in sent.items():
        lines = reports.note_lines(text)
        if lines:
            notes[key] = lines
        else:
            notes.pop(key, None)
    r.section_notes = notes  # JSON 칸은 새 값을 넣는다
    db.commit()
    return _back(r, step, "saved", f"notes-{next(iter(sent))}" if sent else "")


@router.post("/{report_id}/check")
async def save_check(
    report_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db), rev: Rev = Depends(rev_form),
):
    """지난 계획 점검 표: 숨은 칸 `item_id` 여러 개와 줄마다 `result_<번호>`·`note_<번호>`. 넘길 항목을 다시 맞춘다.
    확정한 보고는 고칠 수 없다 (계획과 같이 409: 확정 뒤 결과를 바꾸면 넘어온 줄·굳힌 점검 문구와 어긋난다)."""
    r = get_or_404(db, Report, report_id)
    if r.status == ontology.REPORT_STATUS_CONFIRMED:
        raise HTTPException(status_code=409, detail=CHECK_LOCKED)
    form = await request.form()
    results: dict[int, tuple[str, str]] = {}
    for raw in form.getlist("item_id"):
        item_id = parse_id(raw)
        if item_id is not None:
            results[item_id] = (str(form.get(f"result_{item_id}") or ""), str(form.get(f"note_{item_id}") or ""))
    if conflict := guards.check_rev(r, rev):
        return _detail_page(request, db, r, "check", 409, {"which": "check", "conflict": conflict, "values": results})
    reports.save_check(db, r, results)
    reports.start_planning(r)
    db.commit()
    return _back(r, "check", "report_checked")


@router.post("/{report_id}/plan")
def save_plan(
    report_id: int, request: Request,
    pi_id: list[str] = Form([]), pi_task: list[str] = Form([]), pi_owner: list[str] = Form([]), pi_due: list[str] = Form([]),
    pi_note: list[str] = Form([]), pi_customer: list[str] = Form([]), pi_drop: list[str] = Form([]),
    user: User = Depends(require_user), db: Session = Depends(get_db), rev: Rev = Depends(rev_form),
):
    """계획 항목 표: 줄 번호(`pi_id`)로 맞춘다 (같은 번호 고침 / 빠진 번호 지움 / 새 줄 만듦). "빼기"(`pi_drop`, 줄 위치)를 고른 줄과 할 일이 빈 줄은 지운다."""
    r = get_or_404(db, Report, report_id)
    if r.status == ontology.REPORT_STATUS_CONFIRMED:
        raise HTTPException(status_code=409, detail=PLAN_LOCKED)
    lines = form_rows(pi_task, pi_owner, pi_due, pi_note, pi_customer)
    existing = list(r.plan_items)  # 번호 칸이 없거나 개수가 안 맞는 제출은 줄 위치로 기존 줄과 짝짓는다 (activity_rows 와 같다)
    ids = [parse_id(i) for i in pi_id] if len(pi_id) == len(lines) else [existing[n].id if n < len(existing) else None for n in range(len(lines))]
    dropped = set(pi_drop)
    keep = [(i, row) for n, (i, row) in enumerate(zip(ids, lines)) if str(n) not in dropped]
    if conflict := guards.check_rev(r, rev):
        typed = [{"id": i, "task": t, "owner": o, "due": d, "note": no, "customer": c} for i, (t, o, d, no, c) in keep]
        return _detail_page(request, db, r, "plan", 409, {"which": "plan", "conflict": conflict, "values": typed})
    unlinked = reports.save_plan(r, [i for i, _ in keep], [row for _, row in keep], activity_rows.load_index(db))
    reports.start_planning(r)
    db.commit()
    return _back(r, "plan", "report_planned_unlinked" if unlinked else "report_planned")


@router.post("/{report_id}/confirm")
def confirm(request: Request, report_id: int, user: User = Depends(require_user), db: Session = Depends(get_db), once: Once = Depends(form_once)):
    """계획 확정: 지금 숫자를 스냅숏으로 고정하고 비어 있는 구역 문구를 기본 문장으로 채운다. 이미 확정이면 그대로 둔다.
    AI 처리기가 켜져 있으면 기본 문장으로 채운 구역을 AI 보고 문구(`report_notes`)로 한 번 다듬는다 (그 사이 사람이 고친 구역은 덮지 않는다).
    꺼져 있으면 작업을 넣지 않고 기본 문장 그대로 둔다 (확정은 AI 없이 된다)."""
    r = get_or_404(db, Report, report_id)
    if r.status != ontology.REPORT_STATUS_CONFIRMED:
        before = reports.notes_of(r)
        reports.confirm(db, r, user)
        db.commit()
        filled = {k: v for k, v in reports.notes_of(r).items() if not before.get(k)}
        if filled and not gate.ai_is_off(request):
            reports.start_job(db, r, "report_notes", user, {"replace": filled})  # 다른 AI 작업 중이면 넣지 않는다 (기본 문장 그대로)
    return once.done(_back(r, "files", "report_confirmed"))


@router.post("/{report_id}/unconfirm")
def unconfirm(report_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """확정 풀기: 다음 보고가 이 계획을 이미 점검했거나 확정이 아니면 바꾸지 않는다 (이유는 확정·파일 단계에 보인다)."""
    r = get_or_404(db, Report, report_id)
    if reports.unconfirm(db, r):
        return _back(r, "files", "report_unconfirm_blocked")
    # 확정을 풀면 만든 파일은 옛 숫자라 지운다 (다시 확정한 뒤 새로 만든다)
    r.file_names, r.files_built_at = {}, None
    db.commit()
    shutil.rmtree(_folder(request, r), ignore_errors=True)
    return _back(r, "files", "report_unconfirmed")


# ---- 보고서 파일 (설계 §5.6): 확정된 보고에서만 만들고 내려받는다 ----

@router.post("/{report_id}/files")
def build_file(report_id: int, request: Request, fmt: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db)):
    """`fmt`=pptx|docx|md 파일을 만든다 (같은 형식은 덮어쓴다). 확정이 아니면 만들지 않는다."""
    r = get_or_404(db, Report, report_id)
    if r.status != ontology.REPORT_STATUS_CONFIRMED:
        return _back(r, "files", "report_not_confirmed")
    if fmt not in report_files.FORMATS:
        return _detail_page(request, db, r, "files", status_code=400, file_error="만들 파일 형식을 골라 주세요.")
    stem = report_periods.file_stem(r.unit, r.period_start, r.period_end)
    try:
        path = report_files.write(reports.report_content(db, r), fmt, _folder(request, r), stem)
    except Exception as exc:  # noqa: BLE001 - 파일 만들기 실패는 화면에 알리고 로그에 남긴다
        logging.getLogger(__name__).exception("업무 보고 파일 만들기 실패: %s", exc)
        return _detail_page(request, db, r, "files", status_code=500, file_error="파일을 만들지 못했습니다. 잠시 뒤 다시 시도해 주세요.")
    r.file_names = {**(r.file_names or {}), fmt: path.name}
    r.files_built_at = datetime.now()
    db.commit()
    return _back(r, "files", "report_file_built", "report-files")


@router.get("/{report_id}/files/{fmt}")
def download_file(report_id: int, fmt: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    r = get_or_404(db, Report, report_id)
    if r.status != ontology.REPORT_STATUS_CONFIRMED:
        return _back(r, "files", "report_not_confirmed")
    name = (r.file_names or {}).get(fmt) if fmt in report_files.FORMATS else None
    path = _folder(request, r) / name if name else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="만든 보고서 파일이 없습니다.")
    disposition = f"attachment; filename=\"report-{r.id}.{fmt}\"; filename*=UTF-8''" + quote(name)
    return FileResponse(path, media_type=report_files.MEDIA_TYPES[fmt], headers={"Content-Disposition": disposition})


@router.post("/{report_id}/delete")
def delete(report_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """보고 건 지우기 (팀원 누구나): 계획 항목·대화는 함께, 이 보고가 점검한 지난 항목은 남고 점검 표시만 비워진다 (FK SET NULL).
    만든 보고서 파일 폴더도 지운다."""
    r = get_or_404(db, Report, report_id)
    for item in db.scalars(select(ReportPlanItem).where(ReportPlanItem.checked_in_report_id == r.id)):
        item.checked_in_report_id = None  # SQLite 외래 키 설정과 상관없이 표시를 비운다
    folder = request.app.state.settings.reports_dir / str(r.id)
    db.delete(r)
    db.commit()
    shutil.rmtree(folder, ignore_errors=True)
    return notices.redirect("/reports", "deleted")


# ---- AI 작업 (설계 §5.5): 요약 글·계획 초안·계획 대화·보고 문구. 결과는 초안이고 화면에서 고친다 ----

def _busy(r: Report) -> bool:
    return r.active_job is not None and r.active_job.status in ontology.JOB_ACTIVE


def _start(db: Session, r: Report, kind: str, user: User, extra: dict | None = None):
    step, anchor = JOB_PLACE[kind]
    if reports.start_job(db, r, kind, user, extra) is None:
        return _back(r, step, "busy", anchor)
    return RedirectResponse(f"/reports/{r.id}?step={step}#{anchor}", status_code=303)  # 작업이 시작됨: 진행 상자가 알림을 대신한다


def _unlocked(r: Report) -> None:
    if r.status == ontology.REPORT_STATUS_CONFIRMED:
        raise HTTPException(status_code=409, detail=PLAN_LOCKED)


@router.post("/{report_id}/ai/summary", dependencies=[Depends(require_ai_on)])
def ai_summary(report_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """AI 요약 쓰기: 집계를 보고 실적 요약 글(마크다운 5~10줄)을 쓴다. 쓴 글은 "직접 고치기"로 고친다."""
    return _start(db, get_or_404(db, Report, report_id), "report_summary", user)


@router.post("/{report_id}/ai/plan", dependencies=[Depends(require_ai_on)])
def ai_plan(report_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """AI 계획 초안: 결과는 표가 아니라 초안(`plan_draft`)에 둔다 ("이 초안 쓰기"로 옮긴다). 확정한 계획에는 만들지 않는다."""
    r = get_or_404(db, Report, report_id)
    _unlocked(r)
    return _start(db, r, "report_plan", user)


@router.post("/{report_id}/ai/notes", dependencies=[Depends(require_ai_on)])
def ai_notes(report_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """보고 문구 다시 쓰기: 모든 구역의 문구를 AI 가 다시 쓴다 (숫자는 화면이 "숫자 확인"으로 검사한다)."""
    return _start(db, get_or_404(db, Report, report_id), "report_notes", user)


def _chat_ai_off(request: Request, exc):
    """AI 꺼짐: 쓴 글을 입력칸에 채운 계획 단계를 409 로 다시 그린다 (안내는 화면의 AI 꺼짐 줄 하나). 글이 없거나 보고가 없으면 보통의 돌려보내기."""
    if not exc.first("message").strip():
        return None
    with request.app.state.session_factory() as db:
        r = db.get(Report, request.path_params.get("report_id"))
        if r is None:
            return None
        return _detail_page(request, db, r, "plan", 409, chat_text=exc.first("message"))


@router.post("/{report_id}/chat", dependencies=[Depends(require_ai_on)])
@keeps_text(_chat_ai_off)
def chat(report_id: int, request: Request, message: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db)):
    """계획 대화: 내 글은 작업과 한 트랜잭션에 저장하고, AI 답과 고친 계획(초안)은 작업이 쓴다."""
    r = get_or_404(db, Report, report_id)
    _unlocked(r)
    text = message.strip()
    if not text:
        return _detail_page(request, db, r, "plan", 400, chat_error=EMPTY_MESSAGE)
    if len(text) > MAX_MESSAGE:
        return _detail_page(request, db, r, "plan", 400, chat_error=TOO_LONG_MESSAGE, chat_text=text)
    if _busy(r):
        return _detail_page(request, db, r, "plan", 409, chat_error=CHAT_BUSY, chat_text=text)
    rid = r.id
    if reports.start_job(db, r, "report_chat", user, rows=[ReportMessage(report_id=rid, role="user", content=text)]) is None:
        if not db.scalar(select(func.count()).select_from(Report).where(Report.id == rid)):
            return RedirectResponse("/reports", status_code=303)
        return _detail_page(request, db, r, "plan", 409, chat_error=CHAT_BUSY, chat_text=text)
    return RedirectResponse(f"/reports/{rid}?step=plan#chat", status_code=303)


@router.post("/{report_id}/chat/retry", dependencies=[Depends(require_ai_on)])
def chat_retry(report_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    r = get_or_404(db, Report, report_id)
    _unlocked(r)  # 확정한 계획은 대화로 고치지 않는다 (/chat·/ai/plan 과 같다)
    job = r.active_job
    last = r.messages[-1] if r.messages else None
    if job is None or job.kind != "report_chat" or job.status != ontology.JOB_FAILED or last is None or last.role != "user":
        raise HTTPException(status_code=400, detail="다시 시도할 수 있는 대화 작업이 없습니다.")
    return _start(db, r, "report_chat", user)


@router.get("/{report_id}/chat/live")
def chat_live(report_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """대화 구역 조각 (제안 대화와 같은 방식): 대기·진행 중에는 맨 끝 말풍선만 스스로 다시 부르고, 끝나면 대화 구역 전체로 바꾼다."""
    r = db.get(Report, report_id)
    if r is None:
        return Response("", headers={"HX-Redirect": "/reports"})
    ctx = dict(report=r, confirmed=r.status == ontology.REPORT_STATUS_CONFIRMED, plan_open=PLAN_OPEN)
    if _busy(r):
        return render(request, "reports/_chat_tail.html", **ctx)
    # 끝났다: 대화 구역 전체(입력칸 켜짐·최종 말풍선)로 바꾸고, 위 계획 카드의 "AI 가 고친 초안" 띠도 같은 응답으로 바꾼다 (hx-swap-oob)
    response = render(request, "reports/_chat_live.html", form={}, draft_changes=reports.draft_changes(r) if r.plan_draft else None, **ctx)
    response.headers["HX-Retarget"] = "#chat-live"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


@router.get("/{report_id}/progress")
def progress(report_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """구역 안 진행 상자 조각: 작업이 끝나면 쪽을 새로 고쳐(HX-Refresh) 결과를 보인다."""
    r = db.get(Report, report_id)
    if r is None:
        return Response("", headers={"HX-Redirect": "/reports"})
    if _busy(r):
        return render(request, "reports/_job_box.html", report=r, job=r.active_job)
    return Response("작업이 끝났습니다.", headers={"HX-Refresh": "true"})


@router.post("/{report_id}/plan/apply-draft")
def apply_draft(report_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db), rev: Rev = Depends(rev_form)):
    """"이 초안 쓰기": AI 초안을 계획 항목 표로 옮긴다 (AI 문 아님). 넘어온 줄은 넘어온 표시를 지킨다. 표가 그 사이 바뀌었으면 409."""
    r = get_or_404(db, Report, report_id)
    _unlocked(r)
    if not r.plan_draft:
        raise HTTPException(status_code=400, detail=NO_DRAFT)
    if conflict := guards.check_rev(r, rev):
        return _detail_page(request, db, r, "plan", 409, {"which": "draft", "conflict": conflict, "values": {}})
    unlinked = reports.apply_plan_draft(db, r, activity_rows.load_index(db))
    reports.start_planning(r)
    db.commit()
    return _back(r, "plan", "report_planned_unlinked" if unlinked else "report_draft_applied", "plan")

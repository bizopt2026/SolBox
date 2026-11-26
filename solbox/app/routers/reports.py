"""팀 업무 보고 화면: 목록·만들기·상세(단계 띠 4칸: 실적 → 지난 계획 점검 → 다음 계획 → 확정·파일)와 저장 POST.
숫자·점검·계획 넘김·확정 규칙은 `app/reports.py`, 기간은 `app/report_periods.py` 에 있고 여기서는 부르기만 한다.
팀원 누구나(관리자 구분 없음) 만들고·고치고·확정하고·지울 수 있다 (설계 §5.4b). 고치기 폼은 모두 보고 건의 고침 표시를 쓴다."""
from __future__ import annotations

import shutil
from collections import Counter
from datetime import date
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import activity_rows, guards, notices, ontology, report_periods, reports, ui
from app.db import get_db
from app.guards import Once, Rev, form_once, rev_form
from app.idparse import parse_id
from app.main import render
from app.models import Customer, Report, ReportPlanItem, User
from app.records import rows as form_rows
from app.relations import report_label
from app.routers._common import get_or_404, paginate
from app.security import require_user

router = APIRouter(prefix="/reports")

STEP_KEYS = tuple(k for k, _ in ontology.REPORT_STEPS)
DEFAULT_STEP = {ontology.REPORT_STATUS_DEFAULT: "result", ontology.REPORT_STATUS_PLANNING: "plan", ontology.REPORT_STATUS_CONFIRMED: "files"}
NOTE_STEP = {"kpi": "result", "opps": "result", "proposals": "result", "issues": "result", "check": "check", "plan": "plan"}  # 구역 문구가 놓인 단계
ACTIVITY_PREVIEW = 5  # 실적 단계의 "주요 활동" 에 보이는 수 (나머지는 활동 기록 목록에서)
_WD = "월화수목금토일"
PLAN_LOCKED = "확정한 계획은 고칠 수 없습니다. 확정·파일 단계에서 확정을 먼저 풀어 주세요. 저장하지 않았습니다."


# ---- 기간 글 ----

def _d(day: date, with_year: bool = True) -> str:
    return f"{day.isoformat() if with_year else day.strftime('%m-%d')}({_WD[day.weekday()]})"


def period_text(unit: str, start: date, end: date) -> str:
    """"2026-11-23(월) ~ 11-29(일)", 일간은 "2026-11-24(화)"."""
    if unit == "day" or start == end:
        return _d(start)
    return f"{_d(start)} ~ {_d(end, with_year=end.year != start.year)}"


def short_period(unit: str, start: date, end: date) -> str:
    """"11-30 ~ 12-06", 일간 "11-25(수)", 월간 "2026-12"."""
    if unit == "month":
        return f"{start:%Y-%m}"
    if unit == "day" or start == end:
        return _d(start, with_year=False)
    return f"{start:%m-%d} ~ {end:%m-%d}"


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


def _detail_page(request: Request, db: Session, r: Report, step: str, status_code: int = 200, form: dict | None = None):
    """상세 화면. `form` = 충돌로 다시 그릴 때 {"which": 폼 이름, "conflict": Conflict, "values": 쓴 값} (그 폼만 쓴 값으로 채운다)."""
    step = step if step in STEP_KEYS else DEFAULT_STEP.get(r.status, "result")
    data = reports.data_of(db, r)
    prev = reports.previous_report(db, r)
    check = reports.items_to_check(db, r)
    plan = list(r.plan_items)
    nxt = report_periods.next_period(r.unit, r.period_start, r.period_end)
    title = report_label(r)
    author = db.get(User, r.created_by_id) if r.created_by_id else None
    sub = f"{period_text(r.unit, r.period_start, r.period_end)} · 다음 기간 {short_period(r.unit, *nxt)}"
    if author is not None:
        sub += f" · 만든 사람 {author.name or author.username}"
    return render(
        request, "reports/detail.html", status_code=status_code,
        report=r, title=title, crumbs=ui.crumbs("report", title), sub=sub, step=step, steps=_steps(r, step), stepbar_label="업무 보고 단계",
        data=data, prev=prev, check=check, plan=plan, next_text=short_period(r.unit, *nxt), period=period_text(r.unit, r.period_start, r.period_end),
        notes=reports.notes_of(r), default_notes=reports.default_notes(data, check, plan),
        unconfirm_problem=reports.unconfirm_problem(db, r) if r.status == ontology.REPORT_STATUS_CONFIRMED else None,
        activity_preview=ACTIVITY_PREVIEW, form=form or {}, meta=ui.stamp_info(db, r),
        customer_names=list(db.scalars(select(Customer.name).order_by(Customer.name))) if step == "plan" else [],  # 계획 표의 고객 칸 고르기 목록
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
    """지난 계획 점검 표: 숨은 칸 `item_id` 여러 개와 줄마다 `result_<번호>`·`note_<번호>`. 확정 전이면 넘길 항목을 다시 맞춘다."""
    r = get_or_404(db, Report, report_id)
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
def confirm(report_id: int, user: User = Depends(require_user), db: Session = Depends(get_db), once: Once = Depends(form_once)):
    """계획 확정: 지금 숫자를 스냅숏으로 고정하고 비어 있는 구역 문구를 기본 문장으로 채운다. 이미 확정이면 그대로 둔다."""
    r = get_or_404(db, Report, report_id)
    if r.status != ontology.REPORT_STATUS_CONFIRMED:
        reports.confirm(db, r, user)
        db.commit()
    return once.done(_back(r, "files", "report_confirmed"))


@router.post("/{report_id}/unconfirm")
def unconfirm(report_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """확정 풀기: 다음 보고가 이 계획을 이미 점검했거나 확정이 아니면 바꾸지 않는다 (이유는 확정·파일 단계에 보인다)."""
    r = get_or_404(db, Report, report_id)
    if reports.unconfirm(db, r):
        return _back(r, "files", "report_unconfirm_blocked")
    db.commit()
    return _back(r, "files", "report_unconfirmed")


@router.post("/{report_id}/delete")
def delete(report_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """보고 건 지우기 (팀원 누구나): 계획 항목·대화는 함께, 이 보고가 점검한 지난 항목은 남고 점검 표시만 비워진다 (FK SET NULL).
    만든 보고서 파일 폴더도 지운다."""
    r = get_or_404(db, Report, report_id)
    for item in db.scalars(select(ReportPlanItem).where(ReportPlanItem.checked_in_report_id == r.id)):
        item.checked_in_report_id = None  # SQLite 외래 키 설정과 상관없이 표시를 비운다
    folder = request.app.state.settings.data_dir / "reports" / str(r.id)
    db.delete(r)
    db.commit()
    shutil.rmtree(folder, ignore_errors=True)
    return notices.redirect("/reports", "deleted")

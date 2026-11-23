"""사용자 관리 (관리자 전용)."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import backup, ontology, scheduler, transcribe, ui, update
from app.ai.gate import require_ai_on
from app.ai.queue import enqueue
from app import notices
from app.db import get_db
from app.main import render
from app.labels import ERROR_KINDS, RESEARCH_AREAS, WEEKDAYS
from app.models import AIJob, ResearchRun, ResearchTopic, User
from app.research import Schedule, get_schedule, set_schedule
from app import guards
from app.guards import Once, Rev, form_once, rev_form
from app.security import PASSWORD_RULE, hash_password, password_problem, password_version, require_admin
from app.system import app_version, describe, folder_size, human_size, run_checks

router = APIRouter(prefix="/admin")

ROLES = ontology.USER_ROLES  # 정의는 ontology
USER_CHANGED = "이 사용자 정보가 다른 곳에서 바뀌었거나 다른 계정으로 바뀌었습니다. 저장하지 않았습니다. 아래 목록에서 현재 내용을 확인한 뒤 다시 고쳐 주세요."


def _page(request: Request, db: Session, error: str | None = None, status_code: int = 200):
    role = request.query_params.get("role", "")
    counts = dict(db.execute(select(User.role, func.count()).group_by(User.role)).all())
    stmt = db.query(User).order_by(User.id)
    if role in ROLES:
        stmt = stmt.filter(User.role == role)
    else:
        role = ""
    tabs = ui.seg_tabs(request, "role", list(ROLES.items()), counts, role, total=sum(counts.values()))
    return render(request, "admin/users.html", status_code=status_code, users=stmt.all(), roles=ROLES, error=error, tabs=tabs, role=role,
                  password_rule=PASSWORD_RULE)


@router.get("/users")
def users_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _page(request, db)


@router.post("/users")
def create_user(
    request: Request,
    username: str = Form(""),
    name: str = Form(""),
    password: str = Form(""),
    role: str = Form("user"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    once: Once = Depends(form_once),
):
    username = username.strip()
    if not username or not password:
        return _page(request, db, "아이디와 비밀번호를 입력해 주세요.", 400)
    if role not in ROLES:
        return _page(request, db, "권한 값이 올바르지 않습니다.", 400)
    if db.query(User).filter_by(username=username).first():
        return _page(request, db, "이미 사용 중인 아이디입니다.", 400)
    if problem := password_problem(password, username):
        return _page(request, db, problem, 400)
    # 관리자가 정해 준 비밀번호: 이 사용자는 처음 로그인한 뒤 새 비밀번호로 바꿔야 다른 화면을 쓸 수 있다
    db.add(User(username=username, name=name.strip(), password_hash=hash_password(password), role=role, must_change_password=True))
    db.commit()
    return once.done(notices.redirect("/admin/users", "created"))


@router.post("/users/{user_id}")
def update_user(
    user_id: int,
    request: Request,
    name: str = Form(""),
    role: str = Form("user"),
    is_active: str | None = Form(None),
    password: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    rev: Rev = Depends(rev_form),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if guards.check_rev(target, rev) is not None:  # 사용자 줄은 "그래도 저장" 없이 새로 고쳐 다시 고치게 한다
        return _page(request, db, USER_CHANGED, 409)
    if role not in ROLES:
        return _page(request, db, "권한 값이 올바르지 않습니다.", 400)
    active = is_active is not None
    if target.id == admin.id and (not active or role != "admin"):
        return _page(request, db, "자기 자신을 비활성화하거나 관리자 권한을 내릴 수 없습니다.", 400)
    target.name = name.strip()
    target.role = role
    target.is_active = active
    if password:
        if problem := password_problem(password, target.username):
            db.rollback()
            return _page(request, db, f"{target.username}: {problem}", 400)
        target.password_hash = hash_password(password)
        if target.id == admin.id:  # 내가 내 비밀번호를 정했다: 이 로그인은 그대로, 바꿔야 하는 표시는 달지 않는다
            request.session["pv"] = password_version(target)
        else:  # 정해 준 비밀번호: 이 사용자는 다음 로그인 뒤 바꿔야 한다
            target.must_change_password = True
    db.commit()
    return notices.redirect("/admin/users", "saved")


def _duration(seconds: float | None) -> str:
    """"1시간 3분" / "2분 5초" / "8초" (끝나지 않았거나 시각이 없으면 빈 글)."""
    if seconds is None or seconds < 0:
        return ""
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    if h:
        return f"{h}시간 {m}분" if m else f"{h}시간"
    if m:
        return f"{m}분 {sec}초" if sec else f"{m}분"
    return f"{sec}초"


def _retry_target(admin: User, job: AIJob, proposals: dict, intakes: dict, reports: dict) -> str | None:
    """실패한 작업을 다시 시도하는 기존 POST 경로 (새 작업 규칙을 만들지 않고 각 화면의 "다시 시도" 와 같은 주소를 쓴다). 지금 그 대상의 현재 작업이 아니면 None."""
    inp = job.input or {}
    pid, iid, rid = inp.get("proposal_id"), inp.get("intake_id"), inp.get("report_id")
    if job.kind == "activity_structure":  # 활동 기록 초안은 요청한 본인만 다시 시도할 수 있다 (메모 내용이 든 작업이라 남의 것은 열지 않는다)
        return f"/activities/draft/{job.id}/retry" if job.requester_id == admin.id else None
    if job.kind == "solution_draft" and isinstance(inp.get("solution_id"), int):
        return f"/solutions/{inp['solution_id']}/draft"
    if job.kind == "proposal_directions" and proposals.get(pid) == job.id:
        return f"/proposals/{pid}/directions"
    if job.kind == "proposal_chat" and proposals.get(pid) == job.id:
        return f"/proposals/{pid}/chat/retry"
    if job.kind == "proposal_outline" and proposals.get(pid) == job.id:
        return f"/proposals/{pid}/outline"
    if job.kind in ("research_area", "research_suggest") and reports.get(rid) == job.id:
        return f"/research/reports/{rid}/retry"
    if job.kind == "intake_analyze" and intakes.get(iid) == job.id:
        return f"/intake/{iid}/analyze"
    if job.kind == PING_KIND:
        return "/admin/system/ai-check"
    return None


@router.get("/jobs")
def jobs_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from app.models import Intake, Proposal, ResearchReport

    status = request.query_params.get("status", "")
    kind = request.query_params.get("kind", "")
    counts = dict(db.execute(select(AIJob.status, func.count()).group_by(AIJob.status)).all())  # 구분 탭의 건수: 전체 작업 기준
    stmt = db.query(AIJob).order_by(AIJob.id.desc())
    if status in ontology.JOB_STATUSES:
        stmt = stmt.filter(AIJob.status == status)
    else:
        status = ""
    if kind in ontology.JOB_KINDS:
        stmt = stmt.filter(AIJob.kind == kind)
    else:
        kind = ""
    jobs = stmt.limit(100).all()
    requesters = {u.id: u.name or u.username for u in db.query(User).all()}
    failed = [j for j in jobs if j.status == "failed"]
    ids = lambda key: [j.input.get(key) for j in failed if isinstance((j.input or {}).get(key), int)]  # noqa: E731
    proposals = dict(tuple(r) for r in db.execute(select(Proposal.id, Proposal.active_job_id).where(Proposal.id.in_(ids("proposal_id"))))) if failed else {}
    intakes = dict(tuple(r) for r in db.execute(select(Intake.id, Intake.job_id).where(Intake.id.in_(ids("intake_id"))))) if failed else {}
    reports = dict(tuple(r) for r in db.execute(select(ResearchReport.id, ResearchReport.job_id).where(ResearchReport.id.in_(ids("report_id"))))) if failed else {}
    rows = []
    for j in jobs:
        waited = (j.started_at - j.created_at).total_seconds() if j.started_at and j.created_at else None
        took = (j.finished_at - j.started_at).total_seconds() if j.finished_at and j.started_at else None
        rows.append({"job": j, "wait": _duration(waited), "took": _duration(took),
                     "retry": _retry_target(admin, j, proposals, intakes, reports) if j.status == "failed" else None})
    tabs = ui.seg_tabs(request, "status", list(ontology.JOB_STATUSES.items()), counts, status, total=sum(counts.values()))
    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in ("kind", "page", "notice", "nt")]
    return render(request, "admin/jobs.html", rows=rows, requesters=requesters, tabs=tabs, status=status, kind=kind, kinds=ontology.JOB_KINDS,
                  keep=keep, total=sum(counts.values()), shown=len(jobs))


NAME_MAX = 100
KEYWORDS_MAX = 500


def _next_run(schedule) -> datetime | None:
    """다음 자동 조사 시각 (자동 실행이 꺼져 있으면 None)."""
    return scheduler.latest_slot(schedule, datetime.now()) + timedelta(days=7) if schedule.enabled else None


def _research_page(request: Request, db: Session, error: str | None = None, status_code: int = 200):
    topics = db.query(ResearchTopic).order_by(ResearchTopic.id).all()
    by_area = {code: [t for t in topics if t.area == code] for code in RESEARCH_AREAS}
    runs = db.query(ResearchRun).order_by(ResearchRun.id.desc()).limit(5).all()
    return render(
        request,
        "admin/research.html",
        status_code=status_code,
        by_area=by_area,
        areas=RESEARCH_AREAS,
        weekdays=WEEKDAYS,
        schedule=get_schedule(db),
        next_run=_next_run(get_schedule(db)),
        runs=runs,
        error=error,
    )


def _topic_error(name: str, keywords: str, area: str) -> str | None:
    if not name:
        return "주제 이름을 입력해 주세요."
    if len(name) > NAME_MAX:
        return f"주제 이름은 {NAME_MAX}자 이내로 입력해 주세요."
    if len(keywords) > KEYWORDS_MAX:
        return f"키워드는 {KEYWORDS_MAX}자 이내로 입력해 주세요."
    if area not in RESEARCH_AREAS:
        return "분야 값이 올바르지 않습니다."
    return None


@router.get("/research")
def research_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _research_page(request, db)


@router.post("/research/topics")
def create_topic(
    request: Request,
    name: str = Form(""),
    keywords: str = Form(""),
    area: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    once: Once = Depends(form_once),
):
    name, keywords = name.strip(), keywords.strip()
    error = _topic_error(name, keywords, area)
    if error:
        return _research_page(request, db, error, 400)
    db.add(ResearchTopic(name=name, keywords=keywords, area=area, created_by_id=admin.id))
    db.commit()
    return once.done(notices.redirect("/admin/research", "created"))


@router.post("/research/topics/{topic_id}")
def update_topic(
    topic_id: int,
    request: Request,
    name: str = Form(""),
    keywords: str = Form(""),
    area: str = Form(""),
    is_active: str | None = Form(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    topic = db.get(ResearchTopic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="주제를 찾을 수 없습니다.")
    name, keywords = name.strip(), keywords.strip()
    error = _topic_error(name, keywords, area)
    if error:
        return _research_page(request, db, error, 400)
    topic.name = name
    topic.keywords = keywords
    topic.area = area
    topic.is_active = is_active is not None
    db.commit()
    return notices.redirect("/admin/research", "saved")


@router.post("/research/topics/{topic_id}/delete")
def delete_topic(topic_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    topic = db.get(ResearchTopic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="주제를 찾을 수 없습니다.")
    db.delete(topic)
    db.commit()
    return notices.redirect("/admin/research", "deleted")


def _parse_int(text: str, low: int, high: int) -> int | None:
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return value if low <= value <= high else None


@router.post("/research/schedule")
def save_schedule(
    request: Request,
    enabled: str | None = Form(None),
    weekday: str = Form(""),
    hour: str = Form(""),
    minute: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    day, hh, mm = _parse_int(weekday, 0, 6), _parse_int(hour, 0, 23), _parse_int(minute, 0, 59)
    if day is None or hh is None or mm is None:
        return _research_page(
            request, db, "요일은 0~6, 시는 0~23, 분은 0~59 사이의 숫자로 입력해 주세요.", 400
        )
    set_schedule(db, Schedule(enabled=enabled is not None, weekday=day, hour=hh, minute=mm))
    return notices.redirect("/admin/research", "saved")


PING_KIND = "ai_ping"


def _ping_state(db: Session) -> dict:
    job = db.query(AIJob).filter_by(kind=PING_KIND).order_by(AIJob.id.desc()).first()
    if job is None:
        return {"state": "none"}
    if job.status in ontology.JOB_ACTIVE:
        return {"state": "waiting"}
    if job.status == "done":
        return {"state": "ok", "at": job.finished_at}
    text = ERROR_KINDS.get(job.shown_error_kind, ERROR_KINDS["unknown"])
    if job.error_kind == "bad_output" and job.error:
        text = job.error
    return {"state": "failed", "text": text, "at": job.finished_at}


_CHECK_CACHE: dict = {}
_CHECK_LOCK = threading.Lock()
WEB_CLAUDE_TIMEOUT_S = 5  # 화면(GET)에서 도는 `claude --version` 의 제한: 넘으면 "확인 못 함" (명령줄 `check` 는 30초)


def _check_rows(settings) -> list[dict]:
    """명령줄 `check` 와 같은 점검 항목(`app.system.run_checks`)을 뜻·할 일과 함께. Claude CLI 점검은 느릴 수 있어 1분 동안 결과를 다시 쓴다 (포트는 이 서버가 쓰는 중이라 새로 열어 보지 않는다)."""
    import time

    key = id(settings)
    cached = _CHECK_CACHE.get(key)
    if cached is None or time.monotonic() - cached[0] > 60:
        with _CHECK_LOCK:  # 캐시가 빈 채 동시에 열린 화면들이 점검 명령을 각각 띄우지 않게: 한 요청이 채우는 동안 나머지는 기다렸다가 그 결과를 쓴다
            cached = _CHECK_CACHE.get(key)
            if cached is None or time.monotonic() - cached[0] > 60:
                _CHECK_CACHE[key] = cached = (time.monotonic(), run_checks(lambda: settings, skip_port=True, claude_timeout=WEB_CLAUDE_TIMEOUT_S))
    rows = []
    for name, problem, detail in cached[1]:
        meaning, todo = describe(name)
        rows.append({"name": name, "problem": problem, "detail": detail, "meaning": meaning, "todo": todo,
                     "id": "transcriber" if name == "음성 받아쓰기" else None})
    return rows


def _update_ctx(request: Request) -> dict:
    """'시스템 업데이트' 카드에 보일 것: 돌고 있는 커밋, 마지막 확인(메모리), 마지막 업데이트 기록(app_settings), 한 번만 보일 결과 알림."""
    sf = request.app.state.session_factory
    record = update.read_record(sf)
    notice = update.take_notice(sf)  # 화면을 그리기 전에 한 번 읽고 끈다
    at = None
    if record and record.get("at"):
        try:
            at = datetime.fromisoformat(record["at"])
        except ValueError:
            at = None
    return {"boot": update.boot_commit(), "check": update.last_check(), "record": record, "record_at": at, "notice": notice,
            "running": update.state()["state"] == "running", "supervised": update.is_supervised()}


def _system_page(request: Request, db: Session, error: str | None = None, status_code: int = 200):
    s = request.app.state.settings
    now = datetime.now()
    schedule = get_schedule(db)
    next_run = None
    if schedule.enabled:
        next_run = scheduler.latest_slot(schedule, now) + timedelta(days=7)
    last_failed = (
        db.query(AIJob).filter(AIJob.status == "failed", AIJob.kind != PING_KIND).order_by(AIJob.id.desc()).first()
    )
    return render(
        request,
        "admin/system.html",
        status_code=status_code,
        version=app_version(),
        data_dir=s.data_dir,
        data_size=human_size(folder_size(s.data_dir)),
        last_backup=backup.latest(s.backup_dir),
        backup_dir=s.backup_dir,
        pending=db.query(AIJob).filter_by(status="pending").count(),
        running=db.query(AIJob).filter_by(status="running").count(),
        schedule=schedule,
        next_run=next_run,
        last_failed=last_failed,
        failed_text=ERROR_KINDS.get(last_failed.shown_error_kind, ERROR_KINDS["unknown"]) if last_failed else None,
        ping=_ping_state(db),
        checks=_check_rows(s),
        upd=_update_ctx(request),
        fresh_backup=request.query_params.get("notice") == "backup_done",
        error=error,
    )


@router.get("/system")
def system_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _system_page(request, db)


@router.get("/system/ai-check/status")
def ai_check_status(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return render(request, "admin/_ai_check.html", ping=_ping_state(db))


@router.post("/system/ai-check", dependencies=[Depends(require_admin), Depends(require_ai_on)])
def ai_check(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    waiting = db.query(AIJob.id).filter(AIJob.kind == PING_KIND, AIJob.status.in_(ontology.JOB_ACTIVE)).first()
    if waiting is None:  # 한 번에 하나만 확인한다
        enqueue(db, PING_KIND, {}, admin.id)
    return notices.redirect("/admin/system", "ai_check")


@router.post("/system/backup")
def manual_backup(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        backup.run_backup(request.app.state.settings)
    except (OSError, ValueError, sqlite3.Error) as e:
        return _system_page(request, db, f"백업하지 못했습니다: {e}")
    return notices.redirect("/admin/system", "backup_done")


@router.post("/system/update/check")
def update_check(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """업데이트 확인(읽기만): git fetch 로 새 커밋을 보고 결과를 메모리에 둔다."""
    result = update.run_check(request.app.state.session_factory)
    if result.get("busy"):
        return _system_page(request, db, result["busy"])
    return notices.redirect("/admin/system", "update_checked")


@router.post("/system/update/apply")
def update_apply(request: Request, admin: User = Depends(require_admin), once: Once = Depends(form_once)):
    """지금 업데이트: 백그라운드에서 다시 확인 → 백업 → pull → (pip) → 다시 시작. 두 번 눌러도 한 번만 돈다 (폼 표 + 모듈 잠금)."""
    update.start_apply(request.app, admin.name or admin.username)
    return once.done(RedirectResponse("/admin/system/update/progress", status_code=303))


@router.get("/system/update/progress")
def update_progress(request: Request, admin: User = Depends(require_admin)):
    if update.state()["state"] == "idle":
        return RedirectResponse("/admin/system", status_code=303)
    return render(request, "admin/update_progress.html")


@router.get("/system/update/status")
def update_status(admin: User = Depends(require_admin)):
    st = update.state()
    return JSONResponse({"state": st["state"], "steps": [{"text": t, "status": s} for t, s in st["steps"]], "error": st["error"], "to": st["to"], "from": st["from"]},
                        headers={"Cache-Control": "no-store"})

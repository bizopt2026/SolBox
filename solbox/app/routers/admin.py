"""사용자 관리 (관리자 전용)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import backup, ontology, scheduler, transcribe, ui
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
from app.system import app_version, folder_size, human_size

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


@router.get("/jobs")
def jobs_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    status = request.query_params.get("status", "")
    counts = dict(db.execute(select(AIJob.status, func.count()).group_by(AIJob.status)).all())  # 구분 탭의 건수: 전체 작업 기준
    stmt = db.query(AIJob).order_by(AIJob.id.desc())
    if status in ontology.JOB_STATUSES:
        stmt = stmt.filter(AIJob.status == status)
    else:
        status = ""
    jobs = stmt.limit(100).all()
    requesters = {u.id: u.name or u.username for u in db.query(User).all()}
    tabs = ui.seg_tabs(request, "status", list(ontology.JOB_STATUSES.items()), counts, status, total=sum(counts.values()))
    return render(request, "admin/jobs.html", jobs=jobs, requesters=requesters, tabs=tabs, status=status, total=sum(counts.values()))


NAME_MAX = 100
KEYWORDS_MAX = 500


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
        transcriber=transcribe.label(s),
        error=error,
    )


@router.get("/system")
def system_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _system_page(request, db)


@router.get("/system/ai-check/status")
def ai_check_status(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return render(request, "admin/_ai_check.html", ping=_ping_state(db))


@router.post("/system/ai-check")
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

"""앱 안의 스케줄러: 주간 리서치(다운타임 뒤 한 번 따라잡기)와 일일 작업."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from sqlalchemy import func

from app.models import AppSetting, ResearchRun, ResearchTopic
from app.research import LAST_SLOT_KEY, Schedule, get_schedule, start_run

log = logging.getLogger(__name__)

CHECK_INTERVAL_S = 60
MAX_SLOT_AGE = timedelta(days=6)  # 이보다 오래된 예정 시각은 건너뛴다 (다음 주 예정이 하루도 안 남았다)
DAILY_HOUR = 2
DAILY_KEY = "daily_last_run"
ATTEMPT_KEY = "daily_last_attempt"
RETRY_AFTER = timedelta(hours=1)  # 실패한 일일 작업은 이 간격 이상 두고 다시 한다

# 하루에 한 번(02:00 이후) 차례로 부르는 작업. 백업 등이 여기에 들어간다.
_WARNED_SLOTS: set[datetime] = set()  # 주제가 없어 건너뛴 예정 시각 (프로세스마다 한 번만 기록한다)

DAILY_JOBS: list[Callable[[object], None]] = []


def _backup_job(app) -> None:
    from app import backup

    backup.run_backup(app.state.settings)


def register_default_jobs() -> None:
    """운영에서 쓰는 일일 작업(백업)을 등록한다. 여러 번 불러도 한 번만 등록된다."""
    if _backup_job not in DAILY_JOBS:
        DAILY_JOBS.append(_backup_job)


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def latest_slot(s: Schedule, now: datetime) -> datetime:
    """now 이전(같은 시각 포함)의 가장 최근 예정 시각."""
    slot = (now - timedelta(days=(now.weekday() - s.weekday) % 7)).replace(
        hour=s.hour, minute=s.minute, second=0, microsecond=0
    )
    return slot - timedelta(days=7) if slot > now else slot


def research_due(db: Session, now: datetime) -> datetime | None:
    s = get_schedule(db)
    if not s.enabled:
        return None
    slot = latest_slot(s, now)
    if now - slot > MAX_SLOT_AGE:
        return None
    if db.query(ResearchRun.id).filter(ResearchRun.slot == slot).first() is not None:
        return None
    last = db.get(AppSetting, LAST_SLOT_KEY)  # 지운 실행의 예정 시각도 다시 돌지 않게 기억해 둔다
    if last is not None:
        try:
            if slot <= datetime.fromisoformat(last.value):
                return None
        except ValueError:
            pass
    earliest = db.query(func.min(ResearchTopic.created_at)).filter(ResearchTopic.is_active.is_(True)).scalar()
    if earliest is not None and earliest > slot:
        return None  # 예정 시각에 사용 중인 주제가 없었다: 이번 주는 건너뛰고 다음 예정 시각이나 수동 실행을 기다린다
    return slot


def _run_research(app, now: datetime) -> bool:
    with app.state.session_factory() as db:
        slot = research_due(db, now)
        if slot is None:
            return False
        try:
            run = start_run(db, "schedule", None, slot=slot)
        except ValueError:
            # 주제가 없다: 조용히 건너뛴다. 예정 시각 전부터 있던 주제가 다시 사용 중이 되면 다음 점검 때 시작한다.
            db.rollback()
            if slot not in _WARNED_SLOTS:
                _WARNED_SLOTS.add(slot)
                log.warning("예약 리서치를 건너뜁니다: 사용 중인 주제가 없습니다 (예정 %s).", slot)
            return False
        return run is not None


def _run_daily(app, now: datetime) -> bool:
    if now.hour < DAILY_HOUR or not DAILY_JOBS:  # 할 일이 없으면 한 일도 없다
        return False
    today = now.date().isoformat()
    with app.state.session_factory() as db:
        last_run = db.get(AppSetting, DAILY_KEY)
        if last_run is not None and last_run.value == today:
            return False
        attempt = db.get(AppSetting, ATTEMPT_KEY)
        if attempt is not None:
            try:
                age = now - datetime.fromisoformat(attempt.value)
            except ValueError:
                age = RETRY_AFTER
            if timedelta(0) <= age < RETRY_AFTER:
                return False
        _set_setting(db, ATTEMPT_KEY, now.isoformat())
    failed = False
    for job in list(DAILY_JOBS):
        try:
            job(app)
        except Exception:
            failed = True
            log.exception("일일 작업 실패: %s", getattr(job, "__name__", job))
    if not failed:  # 실패한 날은 표시를 남기지 않아 한 시간 뒤 다시 한다
        with app.state.session_factory() as db:
            _set_setting(db, DAILY_KEY, today)
    return True


def tick(app, now: datetime) -> list[str]:
    """한 번 점검해 한 일을 돌려준다. 어떤 예외도 밖으로 내보내지 않는다."""
    done: list[str] = []
    for name, step in (("research", _run_research), ("daily", _run_daily)):
        try:
            if step(app, now):
                done.append(name)
        except Exception:
            log.exception("스케줄러 점검 실패: %s", name)
    return done


def _loop(app, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            tick(app, datetime.now())
        except Exception:  # tick 이 이미 막지만 스레드는 어떤 경우에도 죽지 않는다.
            log.exception("스케줄러 오류")
        stop.wait(CHECK_INTERVAL_S)


def start(app) -> None:
    app.state.scheduler_thread = None
    app.state.scheduler_stop = threading.Event()
    if not app.state.settings.scheduler_enabled:
        return
    register_default_jobs()
    t = threading.Thread(target=_loop, args=(app, app.state.scheduler_stop), name="scheduler", daemon=True)
    t.start()
    app.state.scheduler_thread = t


def stop(app, join_timeout: float = 5.0) -> None:
    ev = getattr(app.state, "scheduler_stop", None)
    if ev is None:
        return
    ev.set()
    t = getattr(app.state, "scheduler_thread", None)
    if t is not None:
        t.join(timeout=join_timeout)

"""웹 리서치: 주제와 실행 주기 설정."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.labels import RESEARCH_AREAS
from app.models import AppSetting, ResearchTopic

SCHEDULE_KEY = "research_schedule"


@dataclass
class Schedule:
    enabled: bool = True
    weekday: int = 0
    hour: int = 6
    minute: int = 0


def _int_in(value, low: int, high: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return default
    return value


def get_schedule(db: Session) -> Schedule:
    default = Schedule()
    row = db.get(AppSetting, SCHEDULE_KEY)
    raw = row.value if row is not None and isinstance(row.value, dict) else {}
    enabled = raw.get("enabled", default.enabled)
    return Schedule(
        enabled=enabled if isinstance(enabled, bool) else default.enabled,
        weekday=_int_in(raw.get("weekday"), 0, 6, default.weekday),
        hour=_int_in(raw.get("hour"), 0, 23, default.hour),
        minute=_int_in(raw.get("minute"), 0, 59, default.minute),
    )


def set_schedule(db: Session, s: Schedule) -> None:
    value = asdict(s)
    row = db.get(AppSetting, SCHEDULE_KEY)
    if row is None:
        db.add(AppSetting(key=SCHEDULE_KEY, value=value))
    else:
        row.value = value
    db.commit()


def active_topics(db: Session) -> dict[str, list[ResearchTopic]]:
    """분야별 사용 중인 주제 (주제가 없는 분야는 키가 없다)."""
    topics = db.query(ResearchTopic).filter_by(is_active=True).order_by(ResearchTopic.id).all()
    result: dict[str, list[ResearchTopic]] = {}
    for area in RESEARCH_AREAS:
        items = [t for t in topics if t.area == area]
        if items:
            result[area] = items
    return result

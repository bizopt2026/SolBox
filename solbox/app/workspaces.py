"""사업 방향·과제 공간의 공용 규칙 (AI 코워크 ①). 라우터·보고서가 이것만 부른다."""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import ontology, ui
from app.models import Initiative, Strategy, User, WorkspaceChange
from app.search import like_pattern

TITLE_REQUIRED = ui.FieldError("제목을 입력하세요.", "title")
BAD_STATUS = ui.FieldError("알 수 없는 상태입니다.", "status")
BAD_KIND = ui.FieldError("알 수 없는 종류입니다.", "kind")
BAD_DATE = ui.FieldError("날짜는 YYYY-MM-DD 형식으로 입력하세요.", "due")
BAD_PERIOD = ui.FieldError("기간의 끝이 시작보다 빠릅니다.", "period_end")
RECENT_CHANGES = 8  # 상세 화면 옆 카드의 최근 이력 줄 수


def search(db: Session, model, q: str = "", *extra_columns, loaders=()) -> list:
    """제목(과 추가 칸)에 `q` 가 부분 일치하는 공간. 기본 순서는 최근 수정순."""
    stmt = select(model).options(*loaders)
    q = q.strip()
    if q:
        like = like_pattern(q)
        stmt = stmt.where(or_(*(c.like(like, escape="\\") for c in (model.title, *extra_columns))))
    return list(db.scalars(stmt.order_by(model.updated_at.desc(), model.id.desc())).unique())


def parse_day(text: str) -> date | None | str:
    """폼의 날짜 글: 비면 None, 틀리면 "bad"."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return "bad"


def change_rows(db: Session, obj, limit: int = RECENT_CHANGES) -> list[dict]:
    """최근 칸 이력 (최신이 위): [{"when", "field", "old", "new", "by"}]. 값은 화면 글로 (코드는 이름, 사람·방향은 이름)."""
    col = WorkspaceChange.strategy_id if isinstance(obj, Strategy) else WorkspaceChange.initiative_id
    rows = db.scalars(select(WorkspaceChange).where(col == obj.id).order_by(WorkspaceChange.id.desc()).limit(limit)).all()
    users = {u.id: (u.name or u.username) for u in db.scalars(select(User))}
    out = []
    for ch in rows:
        out.append({"when": ch.changed_at, "field": ontology.WORKSPACE_FIELD_LABELS.get(ch.field, ch.field),
                    "old": value_text(db, ch.field, ch.old_value, users), "new": value_text(db, ch.field, ch.new_value, users),
                    "by": users.get(ch.changed_by_id, "")})
    return out


def value_text(db: Session, field: str, value: str, users: dict | None = None) -> str:
    if not value:
        return ""
    if field == "status":
        return ontology.STRATEGY_STATUSES.get(value) or ontology.INITIATIVE_STATUSES.get(value, value)
    if field == "kind":
        return ontology.INITIATIVE_KINDS.get(value, value)
    if field == "owner_user_id" and value.isdigit():
        return (users or {}).get(int(value), "") or f"사용자 {value}"
    if field == "strategy_id" and value.isdigit():
        s = db.get(Strategy, int(value))
        return s.title if s else f"(지운 방향 {value})"
    if field == "consensus":
        first = value.strip().splitlines()[0] if value.strip() else ""
        return first[:60] + ("…" if len(first) > 60 or len(value.strip().splitlines()) > 1 else "")
    return value


def open_initiatives(items) -> list[Initiative]:
    return [i for i in items if i.status in ontology.INITIATIVE_OPEN]


def active_strategies(db: Session, today: date) -> list[Strategy]:
    """잣대로 쓰는 사업 방향 (합의 상태이고 기간 안)."""
    rows = db.scalars(select(Strategy).where(Strategy.status == ontology.STRATEGY_STATUS_AGREED).order_by(Strategy.id)).all()
    return [s for s in rows if ontology.strategy_is_active(s, today)]

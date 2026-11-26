"""팀 (영업 A 단계): 관리자가 만들고 사람을 배정한다. 영업기회의 팀은 담당의 팀에서 읽는다 (따로 저장하지 않는다).
규칙은 여기 한 곳이고 라우터·템플릿은 이 함수만 부른다. 여기 함수는 커밋하지 않는다."""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Team, User
from app.ui import FieldError

NAME_MAX = 30
NAME_REQUIRED = FieldError("팀 이름을 입력하세요.", "name")
NAME_TOO_LONG = FieldError(f"팀 이름은 {NAME_MAX}자까지 쓸 수 있습니다.", "name")
DUPLICATE = FieldError("같은 이름의 팀이 이미 있습니다.", "name")
BAD_TEAM = FieldError("존재하지 않는 팀입니다.", "team_id")
NO_TEAM = "팀 없음"


def clean_name(name: str) -> str:
    return " ".join((name or "").split())


def ordered(db: Session) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.position, Team.name, Team.id)))


def member_counts(db: Session) -> dict[int, int]:
    return dict(db.execute(select(User.team_id, func.count()).where(User.team_id.is_not(None)).group_by(User.team_id)).all())


def name_problem(db: Session, name: str, exclude: Team | None = None) -> FieldError | None:
    name = clean_name(name)
    if not name:
        return NAME_REQUIRED
    if len(name) > NAME_MAX:
        return NAME_TOO_LONG
    stmt = select(Team.id).where(Team.name == name)
    if exclude is not None:
        stmt = stmt.where(Team.id != exclude.id)
    return DUPLICATE if db.scalar(stmt) is not None else None


def add(db: Session, name: str) -> Team:
    top = db.scalar(select(func.max(Team.position))) or 0
    team = Team(name=clean_name(name), position=top + 1)
    db.add(team)
    return team


def remove(db: Session, team: Team) -> None:
    """팀을 지운다. 그 팀 사람의 팀은 비워진다 (영업기회는 그대로)."""
    db.execute(update(User).where(User.team_id == team.id).values(team_id=None).execution_options(synchronize_session="fetch"))
    db.delete(team)


def parse_team_id(db: Session, raw: str) -> tuple[int | None, FieldError | None]:
    """폼의 팀 칸: 빈 값 = 팀 없음, 등록된 팀 번호만."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if not (raw.isascii() and raw.isdecimal() and len(raw) <= 18) or db.get(Team, int(raw)) is None:
        return None, BAD_TEAM
    return int(raw), None


def owned_opportunities(db: Session, user_ids) -> list:
    """이 사람들이 담당인 영업기회 (지식 사본에 "담당: 이름 (팀)" 이 적혀 있어, 이름·팀이 바뀌면 사본을 다시 쓴다)."""
    from app.models import Opportunity

    ids = [i for i in user_ids if i is not None]
    return list(db.scalars(select(Opportunity).where(Opportunity.owner_user_id.in_(ids)))) if ids else []


def team_name(user: User | None) -> str:
    return user.team.name if user is not None and user.team is not None else ""


def grouped_users(db: Session, keep: User | None = None) -> list[tuple[str, list[User]]]:
    """담당 고르기 칸: 사용 중인 계정을 팀 순서대로 묶고(팀 없음은 맨 끝), 묶음 안은 이름순. `keep`(지금 담당)은 사용 중지여도 넣는다."""
    users = list(db.scalars(select(User).where(User.is_active.is_(True))))
    if keep is not None and all(u.id != keep.id for u in users):
        users.append(keep)
    by_team: dict[int | None, list[User]] = {}
    for u in users:
        by_team.setdefault(u.team_id, []).append(u)
    out = []
    for team in ordered(db):
        if team.id in by_team:
            out.append((team.name, sorted(by_team[team.id], key=lambda u: (u.name or u.username, u.id))))
    if None in by_team:
        out.append((NO_TEAM, sorted(by_team[None], key=lambda u: (u.name or u.username, u.id))))
    return out

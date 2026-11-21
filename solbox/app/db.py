"""SQLAlchemy 엔진, 세션, 의존성."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(db_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


SQLITE_INT_MAX = 2**63 - 1


class AppSession(Session):
    """번호가 SQLite 정수 범위를 넘으면 (주소에 19자리 숫자가 온 경우 등) 질의 없이 "없음"으로 답한다 (OverflowError 로 500 이 되지 않게)."""

    def get(self, entity, ident, *args, **kwargs):
        if isinstance(ident, int) and not -SQLITE_INT_MAX - 1 <= ident <= SQLITE_INT_MAX:
            return None
        return super().get(entity, ident, *args, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=AppSession, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    from app.ai import gate

    session.info["ai_off"] = gate.ai_is_off(request)  # 꺼진 서버에서는 AI 작업 줄을 넣지 못한다 (app/ai/gate.py)
    try:
        yield session
    finally:
        session.close()

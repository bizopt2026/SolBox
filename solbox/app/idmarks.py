"""지운 번호를 다시 쓰지 않게 하는 장치.

SQLite 는 `AUTOINCREMENT` 없이 만든 표에서 가장 큰 번호의 줄을 지우면 그 번호를 새 줄에 다시 준다 (그래서 옛 주소가 다른 기록을 열 수 있다).
기존 표는 다시 만들지 않는다 (표 이름·열·저장된 값을 바꾸지 않는다). 대신 표마다 "지금까지 쓴 가장 큰 번호"를 작은 표 `id_marks` 에 두고,
ORM 으로 새 줄을 넣을 때(`before_flush`) 번호를 `max(표시, 그 표의 MAX(id)) + 1` 로 직접 정한 뒤 표시를 올린다. 줄을 지울 때도 표시가 그 번호 이상이 되게 한다.

- 대상: 메타데이터에서 기본키가 정수 한 열인 표 가운데, 실제 DB 의 표 정의에 `AUTOINCREMENT` 가 없는 표 (손으로 적은 목록이 아니다).
- 동시 쓰기: 먼저 표시 줄을 갱신해 SQLite 쓰기 잠금을 잡고(`UPDATE id_marks SET last_id = last_id`), 그 잠금 안에서 MAX(id) 를 읽고 번호를 정한다.
  쓰기 잠금은 한 번에 하나라 두 요청이 같은 번호를 받지 못한다 (기다린 쪽은 먼저 쓴 쪽이 커밋한 뒤 새로 읽는다).
- 처음 쓰는 표는 그때의 MAX(id) 로 표시를 만든다 (시작할 때 모든 표에 한 번 해 둔다: `ensure_marks`). 그 전에 지워진 번호는 알 방법이 없어 받아들인다.
- ORM 을 거치지 않는 INSERT 는 `migrate.copy_activity_json`(시작 때 한 번, 번호를 정하지 않고 넣는 후속 조치·고객 니즈 복사)뿐이다.
  그 뒤 `ensure_marks` 가 돌므로 표시가 그 줄들을 덮는다. 새 코어 INSERT 를 더하면 `reserve_ids` 로 번호를 받거나 이 장치를 거친 ORM 으로 넣는다.
- 코어 DELETE 는 쓰지 않는다 (일괄 지우기는 번호가 연결되지 않은 `form_tokens` 뿐).
"""
from __future__ import annotations

import threading
import weakref

from sqlalchemy import Connection, Engine, event, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import Base

_LOCK = threading.Lock()
_CACHE: "weakref.WeakKeyDictionary[Engine, frozenset[str]]" = weakref.WeakKeyDictionary()


def candidate_tables() -> dict[str, str]:
    """메타데이터에서 기본키가 정수 한 열인 표 -> 그 열 이름 (표시 표 자신은 뺀다)."""
    found = {}
    for table in Base.metadata.sorted_tables:
        if table.name == "id_marks":
            continue
        cols = list(table.primary_key.columns)
        if len(cols) == 1 and cols[0].type.python_type is int and cols[0].autoincrement is not False:
            found[table.name] = cols[0].name
    return found


def tracked_tables(conn: Connection) -> dict[str, str]:
    """번호를 직접 정할 표 -> 기본키 열: 후보 가운데 실제 DB 의 정의에 AUTOINCREMENT 가 없는 표 (있으면 SQLite 가 이미 번호를 다시 쓰지 않는다)."""
    cands = candidate_tables()
    engine = conn.engine
    with _LOCK:
        cached = _CACHE.get(engine)
    if cached is not None:
        return {t: c for t, c in cands.items() if t in cached}
    rows = dict(conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type = 'table'")).all())
    tracked = frozenset(t for t in cands if t in rows and "AUTOINCREMENT" not in (rows[t] or "").upper())
    if all(t in rows for t in cands):  # 표가 아직 다 안 만들어졌으면 저장하지 않는다 (나중에 다시 본다)
        with _LOCK:
            _CACHE[engine] = tracked
    return {t: c for t, c in cands.items() if t in tracked}


def _lock_and_mark(conn: Connection, table: str, col: str) -> tuple[int, int]:
    """표시 줄을 (없으면 지금 MAX 로) 만들고 쓰기 잠금을 잡은 뒤 (표시, MAX) 를 돌려준다."""
    conn.execute(text(f'INSERT OR IGNORE INTO id_marks (table_name, last_id) SELECT :t, COALESCE(MAX("{col}"), 0) FROM "{table}"'), {"t": table})
    conn.execute(text("UPDATE id_marks SET last_id = last_id WHERE table_name = :t"), {"t": table})  # 쓰기 잠금: 이 뒤로 다른 쓰기는 기다린다
    mark = conn.execute(text("SELECT last_id FROM id_marks WHERE table_name = :t"), {"t": table}).scalar_one()
    top = conn.execute(text(f'SELECT COALESCE(MAX("{col}"), 0) FROM "{table}"')).scalar_one()
    return mark, top


def reserve_ids(conn: Connection, table: str, count: int = 1) -> list[int]:
    """`table` 에 코어로 직접 넣을 줄을 위한 번호 `count` 개를 받는다 (연속, 이미 쓴 번호보다 크다). 쓰는 트랜잭션 안에서 부른다."""
    col = tracked_tables(conn).get(table)
    if col is None:
        raise ValueError(f"번호를 직접 정하는 표가 아닙니다: {table}")
    mark, top = _lock_and_mark(conn, table, col)
    base = max(mark, top)
    conn.execute(text("UPDATE id_marks SET last_id = :n WHERE table_name = :t"), {"n": base + count, "t": table})
    return list(range(base + 1, base + count + 1))


def ensure_marks(engine: Engine) -> int:
    """시작할 때: 번호를 직접 정할 모든 표에 표시 줄이 있게 한다 (없으면 그때의 MAX(id)). 만든 줄 수."""
    made = 0
    with engine.begin() as conn:
        for table, col in tracked_tables(conn).items():
            made += conn.execute(text(f'INSERT OR IGNORE INTO id_marks (table_name, last_id) SELECT :t, COALESCE(MAX("{col}"), 0) FROM "{table}"'), {"t": table}).rowcount
            top = f'(SELECT COALESCE(MAX("{col}"), 0) FROM "{table}")'
            conn.execute(text(f"UPDATE id_marks SET last_id = {top} WHERE table_name = :t AND last_id < {top}"), {"t": table})  # 코어로 넣은 줄이 있었으면 표시를 올린다 (내리지는 않는다)
    return made


@event.listens_for(Session, "before_flush")
def _assign_ids(session: Session, flush_context, instances) -> None:
    cands = candidate_tables()
    per_table: dict[str, dict] = {}
    for obj in session.new:
        table = getattr(type(obj), "__table__", None)
        if table is not None and table.name in cands:
            per_table.setdefault(table.name, {"new": [], "deleted": []})["new"].append(obj)
    for obj in session.deleted:
        table = getattr(type(obj), "__table__", None)
        if table is not None and table.name in cands:
            per_table.setdefault(table.name, {"new": [], "deleted": []})["deleted"].append(obj)
    if not per_table:
        return
    conn = session.connection()
    tracked = tracked_tables(conn)
    for table, group in per_table.items():
        col = tracked.get(table)
        if col is None:
            continue
        mapper = sa_inspect(type(group["new"][0] if group["new"] else group["deleted"][0]))
        key = mapper.get_property_by_column(mapper.local_table.c[col]).key
        try:
            mark, top = _lock_and_mark(conn, table, col)
        except OperationalError:  # 쓰기 잠금을 제때 못 잡았다: 저장하지 않고 "잠시 후 다시". 새 기록은 "다른 곳에서 바뀐" 일이 있을 수 없으니 항상 WriteBusy
            from app import guards

            raise guards.WriteBusy() from None
        base = max(mark, top)
        fresh = []
        for obj in group["new"]:
            value = getattr(obj, key, None)
            if value is None:
                fresh.append(obj)
            else:
                base = max(base, value)  # 번호를 직접 정한 줄(옮겨 심기 등): 표시가 그 번호 이상이 되게
        for obj in group["deleted"]:
            value = getattr(obj, key, None)
            if value is not None:
                base = max(base, value)
        for obj in fresh:
            base += 1
            setattr(obj, key, base)
        if base != mark:
            conn.execute(text("UPDATE id_marks SET last_id = :n WHERE table_name = :t"), {"n": base, "t": table})

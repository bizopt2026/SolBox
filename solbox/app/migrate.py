"""옛 DB 에 모델에는 있는데 없는 칸을 시작할 때 자동으로 추가한다.

칸 삭제·이름 변경·형식 변경은 하지 않는다. 칸을 추가하기 전에 DB 파일을 복사해 둔다.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import JSON, Column, Engine, MetaData, inspect
from sqlalchemy.dialects import sqlite as sqlite_dialect

from app.db import Base

log = logging.getLogger(__name__)
_DIALECT = sqlite_dialect.dialect()


def missing_columns(engine: Engine, metadata: MetaData | None = None) -> list[tuple[str, Column]]:
    """모델에는 있는데 DB 테이블에는 없는 (테이블명, 칸). 테이블 자체가 없으면 넣지 않는다."""
    metadata = Base.metadata if metadata is None else metadata
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    result: list[tuple[str, Column]] = []
    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        result.extend((table.name, col) for col in table.columns if col.name not in have)
    return result


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(value: object, column: Column) -> str | None:
    """파이썬 기본값을 SQL 리터럴로 바꾼다. 쓸 수 없으면 None."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return None
    if isinstance(column.type, JSON) or isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _default_sql(column: Column) -> str | None:
    default = column.default
    if default is None:
        return None
    if default.is_scalar:
        value = default.arg
    elif default.is_callable:
        try:
            value = default.arg(None)
        except Exception:  # noqa: BLE001  (기본값을 계산할 수 없으면 기본값 없음으로 본다)
            return None
    else:
        return None
    return _literal(value, column)


def _column_ddl(table: str, column: Column) -> str:
    default_sql = _default_sql(column)
    if default_sql is None and not column.nullable:
        if column.default is not None and not column.default.is_scalar:
            # 시각 같은 호출형 기본값: 추가하는 시점의 값으로 채운다.
            try:
                value = column.default.arg(None)
            except Exception:  # noqa: BLE001
                value = None
            if isinstance(value, datetime):
                default_sql = "'" + value.strftime("%Y-%m-%d %H:%M:%S.%f") + "'"
        if default_sql is None:
            raise RuntimeError(f"자동으로 추가할 수 없는 칸입니다: {table}.{column.name}")
    # 외래 키 제약은 붙이지 않는다 (ALTER 로 기본값과 함께 추가할 수 없는 경우가 있다).
    parts = [_quote(column.name), column.type.compile(dialect=_DIALECT)]
    if not column.nullable:
        parts.append("NOT NULL")
    if default_sql is not None:
        parts.append(f"DEFAULT {default_sql}")
    return " ".join(parts)


def _backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = db_path.with_name(f"{db_path.name}.before-migrate-{stamp}")
    n = 1
    while target.exists():
        n += 1
        target = db_path.with_name(f"{db_path.name}.before-migrate-{stamp}-{n}")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)  # WAL 에 남은 내용까지 포함한다
    finally:
        dst.close()
        src.close()
    return target


def add_missing_columns(engine: Engine, db_path: Path, metadata: MetaData | None = None) -> list[str]:
    """빠진 칸을 추가하고 `"테이블.칸"` 목록을 돌려준다. 빠진 칸이 없으면 아무것도 하지 않는다."""
    missing = missing_columns(engine, metadata)
    if not missing:
        return []
    statements = [
        f"ALTER TABLE {_quote(table)} ADD COLUMN {_column_ddl(table, column)}"
        for table, column in missing
    ]  # 추가할 수 없는 칸이 있으면 복사·변경 전에 여기서 멈춘다
    backup = _backup(db_path)
    log.info("DB 구조 변경 전 복사본: %s", backup)
    added: list[str] = []
    with engine.begin() as conn:
        for (table, column), sql in zip(missing, statements):
            conn.exec_driver_sql(sql)
            added.append(f"{table}.{column.name}")
            log.info("칸 추가: %s.%s", table, column.name)
    return added


# ---- 후속 조치·고객 니즈: 옛 JSON 열 -> 표 (한 번의 복사) ----

ROWS_COPIED_KEY = "activity_rows_copied"  # app_settings 표시: 있으면 복사를 다시 하지 않는다


def _json_rows(raw: object, activity_id: int, column: str, stats: dict) -> list:
    """JSON 열 값을 목록으로. 비었거나 목록이 아니면 빈 목록 (목록이 아니면 로그를 남긴다)."""
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            log.warning("활동 기록 %s 의 %s 를 읽을 수 없어 건너뜁니다 (JSON 형식 오류)", activity_id, column)
            stats["skipped"] += 1
            return []
    if raw in (None, [], ""):
        return []
    if not isinstance(raw, list):
        log.warning("활동 기록 %s 의 %s 가 목록이 아니어서 건너뜁니다", activity_id, column)
        stats["skipped"] += 1
        return []
    return raw


def copy_activity_json(engine: Engine, db_path: Path) -> dict | None:
    """모든 활동 기록의 옛 JSON(`follow_ups`, `demand_signals`)을 `follow_ups`·`customer_needs` 표로 복사한다.

    복사만 한다: JSON 값은 고치지도 지우지도 않는다. `app_settings` 의 표시로 한 번만 하고, 복사할 내용이 있으면 하기 전에
    DB 파일을 복사해 둔다. 행 삽입과 표시는 한 트랜잭션이라 중간에 멈추면 아무것도 남지 않는다.
    이미 새 표에 행이 있는 활동 기록은 건너뛴다 (표시를 잃은 경우에도 중복되지 않게).
    복사했으면 요약(dict)을, 이미 했으면 None 을 돌려준다."""
    from app.activity_rows import load_index  # 늦게 불러온다 (models 가 필요하다)
    from app import models  # noqa: F401  (표를 등록한다)

    tables = Base.metadata.tables
    settings_t, fu_t, need_t = tables["app_settings"], tables["follow_ups"], tables["customer_needs"]
    with engine.connect() as conn:
        if conn.execute(settings_t.select().where(settings_t.c.key == ROWS_COPIED_KEY)).first() is not None:
            return None
        legacy = conn.exec_driver_sql("SELECT id, follow_ups, demand_signals FROM activities ORDER BY id").all()
        have = {r[0] for r in conn.exec_driver_sql("SELECT activity_id FROM follow_ups UNION SELECT activity_id FROM customer_needs")}
        index = load_index(conn)

    stats = {"activities": 0, "follow_ups": 0, "needs": 0, "skipped": 0, "bad_due": 0,
             "customer_linked": 0, "customer_unlinked": 0, "owner_linked": 0, "owner_unlinked": 0}
    fu_rows: list[dict] = []
    need_rows: list[dict] = []
    for activity_id, raw_fu, raw_ds in legacy:
        if activity_id in have:
            continue
        pos = 0
        for entry in _json_rows(raw_fu, activity_id, "follow_ups", stats):
            task = entry.get("task") if isinstance(entry, dict) else None
            if not isinstance(task, str) or not task.strip():
                log.warning("활동 기록 %s 의 후속 조치 한 줄을 건너뜁니다 (형식 오류 또는 할 일 없음): %r", activity_id, entry)
                stats["skipped"] += 1
                continue
            owner = entry.get("owner") if isinstance(entry.get("owner"), str) else ""
            due = None
            if isinstance(entry.get("due"), str) and entry["due"].strip():
                try:
                    due = date.fromisoformat(entry["due"].strip())
                except ValueError:
                    stats["bad_due"] += 1
            user_id = index.user_id(owner)
            if owner.strip():
                stats["owner_linked" if user_id else "owner_unlinked"] += 1
            fu_rows.append(dict(activity_id=activity_id, position=pos, task=task, owner=owner, owner_user_id=user_id,
                                due=due, done=bool(entry.get("done"))))
            pos += 1
        pos = 0
        for entry in _json_rows(raw_ds, activity_id, "demand_signals", stats):
            content = entry.get("content") if isinstance(entry, dict) else None
            if not isinstance(content, str) or not content.strip():
                log.warning("활동 기록 %s 의 고객 니즈 한 줄을 건너뜁니다 (형식 오류 또는 내용 없음): %r", activity_id, entry)
                stats["skipped"] += 1
                continue
            name = entry.get("customer") if isinstance(entry.get("customer"), str) else ""
            customer_id = index.customer_id(name)
            if name.strip():
                stats["customer_linked" if customer_id else "customer_unlinked"] += 1
            need_rows.append(dict(activity_id=activity_id, position=pos, customer_name=name, customer_id=customer_id,
                                  content=content))
            pos += 1
    stats["activities"] = len({r["activity_id"] for r in fu_rows} | {r["activity_id"] for r in need_rows})
    stats["follow_ups"], stats["needs"] = len(fu_rows), len(need_rows)

    if fu_rows or need_rows:
        backup = _backup(db_path)
        log.info("후속 조치·고객 니즈 복사 전 DB 복사본: %s", backup)
    with engine.begin() as conn:  # 행과 표시를 한 트랜잭션으로: 중간에 멈추면 모두 취소된다
        if fu_rows:
            conn.execute(fu_t.insert(), fu_rows)
        if need_rows:
            conn.execute(need_t.insert(), need_rows)
        conn.execute(settings_t.insert().values(key=ROWS_COPIED_KEY, value=stats))
    log.info(
        "후속 조치·고객 니즈 복사: 활동 기록 %(activities)d건, 후속 조치 %(follow_ups)d줄(담당 연결 %(owner_linked)d, 미연결 %(owner_unlinked)d), "
        "고객 니즈 %(needs)d줄(고객 연결 %(customer_linked)d, 미연결 %(customer_unlinked)d), 건너뜀 %(skipped)d, 기한 형식 오류 %(bad_due)d",
        stats,
    )
    return stats

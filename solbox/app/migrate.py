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

"""옛 DB 에 모델에는 있는데 없는 칸을 시작할 때 자동으로 추가한다.

칸 삭제·이름 변경·형식 변경은 하지 않는다 (예외 하나: 베타 W 의 `proposals.customer_id` NOT NULL 풀기, 표 다시 만들기). 칸을 추가하기 전에 DB 파일을 복사해 둔다.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import JSON, Column, Engine, MetaData, inspect, select, update
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


class MigrationBackup:
    """한 번 시작할 때의 DB 복사본 하나를 모든 이관 단계가 나눠 쓴다. 바꿀 것이 있는 첫 단계가 `take()` 로 (바꾸기 전에) 한 번 만들고,
    뒤 단계는 같은 복사본을 다시 쓴다. 바꿀 것이 없으면 아무 단계도 부르지 않아 복사본이 생기지 않는다.
    되돌릴 때 쓰는 것은 이 복사본 하나다 (그 버전으로 처음 시작했을 때 만들어진 것)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.path: Path | None = None

    def take(self) -> Path:
        if self.path is None:
            self.path = _backup(self.db_path)
        return self.path


def add_missing_columns(engine: Engine, db_path: Path, metadata: MetaData | None = None, backup: MigrationBackup | None = None) -> list[str]:
    """빠진 칸을 추가하고 `"테이블.칸"` 목록을 돌려준다. 빠진 칸이 없으면 아무것도 하지 않는다."""
    missing = missing_columns(engine, metadata)
    if not missing:
        return []
    statements = [
        f"ALTER TABLE {_quote(table)} ADD COLUMN {_column_ddl(table, column)}"
        for table, column in missing
    ]  # 추가할 수 없는 칸이 있으면 복사·변경 전에 여기서 멈춘다
    copy = (backup or MigrationBackup(db_path)).take()
    log.info("DB 구조 변경 전 복사본: %s", copy)
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


_DONE_TRUE = {"true", "1", "yes", "y", "완료"}
_STRICT_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _legacy_done(value: object) -> bool:
    """옛 JSON 의 완료 값: 참거짓 값 true 와 글자 "true"·"1"·"yes"·"y"·"완료"(대소문자 무시)만 완료다. "false"·"0"·빈 글자·그 밖은 완료가 아니다."""
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in _DONE_TRUE


def _legacy_due(value: object) -> date | None:
    """옛 JSON 의 기한: 엄격한 `YYYY-MM-DD` 만 (파이썬 버전과 상관없이 `20261120`·`2026-W47-1` 같은 변형은 받지 않는다). 아니면 None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _STRICT_DAY.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def copy_activity_json(engine: Engine, db_path: Path, backup: MigrationBackup | None = None) -> dict | None:
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
            raw_due = entry.get("due")
            due = _legacy_due(raw_due)
            if due is None and raw_due is not None and str(raw_due).strip():
                stats["bad_due"] += 1  # 받지 않은 기한: 글은 옛 JSON 에 그대로 남는다
                log.warning("활동 기록 %s 의 후속 조치 기한을 옮기지 않습니다 (YYYY-MM-DD 형식이 아님, 옛 JSON 에는 남아 있음): %r", activity_id, raw_due)
            user_id = index.user_id(owner)
            if owner.strip():
                stats["owner_linked" if user_id else "owner_unlinked"] += 1
            fu_rows.append(dict(activity_id=activity_id, position=pos, task=task, owner=owner, owner_user_id=user_id,
                                due=due, done=_legacy_done(entry.get("done"))))
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
        copy = (backup or MigrationBackup(db_path)).take()
        log.info("후속 조치·고객 니즈 복사 전 DB 복사본: %s", copy)
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


# ---- 올리기로 만든 기록의 유래: `Intake.result` 로 되짚어 채우기 (한 번) ----

ORIGINS_FILLED_KEY = "origins_filled"  # app_settings 표시: 있으면 되짚지 않는다
ADDED_INDEXES = ("ix_ai_jobs_finished_at",)  # 옛 DB 에 모델의 이 보통 색인을 더한다 (값은 바뀌지 않는다)
UNIQUE_INDEXES = ("ux_partners_customer_id",)  # 옛 DB 에 모델의 이 유일 색인을 더한다 (열이 새로 생겨 모두 비어 있으므로 안전하다)
_LINKED_ONLY = re.compile(r" \((연락처 추가|자료 \d+개 추가|이미 등록된 솔루션에 자료 \d+개 추가)\)$")  # 새로 만들지 않고 연결·덧붙인 결과 줄의 꼬리표


def add_unique_indexes(engine: Engine) -> list[str]:
    """모델에 있는데 옛 DB 에 없는 색인(`UNIQUE_INDEXES`, `ADDED_INDEXES`)을 더한다. 유일 색인은 중복이 있어 못 만들면 로그만 남긴다 (응용 코드가 따로 지킨다)."""
    insp = inspect(engine)
    made: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in insp.get_table_names():
            continue
        have = {i["name"] for i in insp.get_indexes(table.name)}
        for index in table.indexes:
            if (index.name in UNIQUE_INDEXES or index.name in ADDED_INDEXES) and index.name not in have:
                try:
                    index.create(engine)
                    made.append(index.name)
                    log.info("색인 추가: %s", index.name)
                except Exception:  # noqa: BLE001
                    log.warning("유일 색인 %s: 만들지 못했습니다 (중복 값). 응용 코드가 중복을 막습니다.", index.name, exc_info=True)
    return made


def backfill_origins(engine: Engine, db_path: Path, backup: MigrationBackup | None = None) -> dict | None:
    """저장 완료된 모든 올리기 묶음의 `result` 를 읽어, 그 묶음이 만든 기록에 유래(`intake`/묶음 번호)를 채운다.

    채우는 조건 (모두): 기록이 아직 있다, 유래가 비어 있다, 결과 줄이 연결·덧붙임이 아니라 새로 만든 것이다(꼬리표),
    기록의 `created_at` 이 묶음을 올린 시각과 저장을 끝낸 시각(`applied_at`) 사이다 (SQLite 가 지워진 번호를 다시 써서 엉뚱한
    기록을 가리키거나, 묶음 뒤에 직접 만든 기록에 자료만 더한 줄을 잘못 채우는 일을 막는다). 근거가 없는 기록은 비워 둔다
    ("직접 입력" 으로 표시하지 않는다: 모른다). 리서치로 등록한 옛 솔루션은 되짚을 수 없어 그대로다.
    `copy_activity_json` 과 같은 방식: 표시로 한 번만, 고칠 것이 있으면 하기 전에 DB 복사, 고치기와 표시는 한 트랜잭션.
    했으면 요약(dict)을, 이미 했으면 None 을 돌려준다."""
    from app import models  # noqa: F401  (표를 등록한다)

    tables = Base.metadata.tables
    settings_t = tables["app_settings"]
    kinds = {"partner": tables["partners"], "customer": tables["customers"], "solution": tables["solutions"], "activity": tables["activities"]}
    with engine.connect() as conn:
        if conn.execute(settings_t.select().where(settings_t.c.key == ORIGINS_FILLED_KEY)).first() is not None:
            return None
        intakes = conn.exec_driver_sql(
            "SELECT id, created_at, applied_at, result FROM intakes WHERE status = 'applied' ORDER BY id").all()
        rows = {k: {r[0]: (r[1], r[2]) for r in conn.execute(select(t.c.id, t.c.created_at, t.c.origin_type))} for k, t in kinds.items()}

    def when(value):
        if isinstance(value, datetime) or value is None:
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    stats = {"intakes": len(intakes), "entries": 0, "total": 0, "set": {k: 0 for k in kinds},
             "missing": 0, "already": 0, "outside_window": 0, "linked_only": 0, "skipped_entries": 0}
    updates: list[tuple[str, int, int]] = []  # (종류, 기록 번호, 묶음 번호)
    claimed: set[tuple[str, int]] = set()
    for intake_id, created, applied, raw in intakes:
        created, applied = when(created), when(applied)
        try:
            result = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except ValueError:
            result = None
        if not isinstance(result, list):
            log.warning("올리기 묶음 %s 의 결과를 읽을 수 없어 건너뜁니다", intake_id)
            continue
        for entry in result:
            kind = entry.get("type") if isinstance(entry, dict) else None
            number = entry.get("id") if isinstance(entry, dict) else None
            if kind not in kinds or not isinstance(number, int) or isinstance(number, bool):
                stats["skipped_entries"] += 1  # 저장하지 못한 항목·저장하지 않은 파일·깨진 줄
                continue
            stats["entries"] += 1
            if _LINKED_ONLY.search(str(entry.get("label") or "")):
                stats["linked_only"] += 1
                continue
            row = rows[kind].get(number)
            if row is None:
                stats["missing"] += 1
                continue
            record_created, origin_type = when(row[0]), row[1]
            if origin_type is not None:
                stats["already"] += 1
                continue
            if created is None or applied is None or record_created is None or not created <= record_created <= applied:
                stats["outside_window"] += 1
                continue
            if (kind, number) in claimed:
                continue
            claimed.add((kind, number))
            updates.append((kind, number, intake_id))
            stats["set"][kind] += 1
    stats["total"] = len(updates)

    if updates:
        copy = (backup or MigrationBackup(db_path)).take()
        log.info("유래 채우기 전 DB 복사본: %s", copy)
    with engine.begin() as conn:  # 고치기와 표시를 한 트랜잭션으로: 중간에 멈추면 모두 취소된다
        for kind, number, intake_id in updates:
            t = kinds[kind]  # origin_type 이 아직 비어 있을 때만 쓴다
            conn.execute(update(t).where(t.c.id == number, t.c.origin_type.is_(None)).values(origin_type="intake", origin_id=intake_id))
        conn.execute(settings_t.insert().values(key=ORIGINS_FILLED_KEY, value=stats))
    log.info(
        "유래 채우기: 올리기 묶음 %(intakes)d건, 결과 줄 %(entries)d, 유래를 채운 기록 %(total)d건 %(set)s, 연결·덧붙임 줄 %(linked_only)d, "
        "기록 없음 %(missing)d, 이미 유래 있음 %(already)d, 시각이 맞지 않아 건너뜀 %(outside_window)d", stats,
    )
    return stats


# ---- 베타 W: `proposals.customer_id` 를 NOT NULL 에서 풀기 (표 다시 만들기, 한 번) ----
# 다수 고객 제안은 고객 한 곳이 없다. SQLite 는 ALTER 로 NOT NULL 을 풀 수 없어 SQLite 문서의 12단계 방법(https://www.sqlite.org/lang_altertable.html#otheralter)으로 표를 다시 만든다.
# "이미 했는가" 는 표 구조로 가른다 (customer_id 열의 notnull 이 0 이면 한 것이다: 새로 만든 DB 도 처음부터 풀려 있다). 따로 표시를 두지 않는다.

PROPOSALS_TABLE = "proposals"
_CUSTOMER_NOT_NULL = re.compile(r'(?<![\w"])(?P<col>"?customer_id"?\s+[A-Za-z]+(?:\s*\([^)]*\))?)\s+NOT\s+NULL', re.I)
_CREATE_NAME = re.compile(r'^(\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)(?:"proposals"|proposals|`proposals`|\[proposals\])', re.I)


def proposals_need_rebuild(engine: Engine) -> bool:
    """`proposals.customer_id` 가 아직 NOT NULL 인가 (표가 없거나 이미 풀렸으면 False)."""
    with engine.connect() as conn:
        info = conn.exec_driver_sql(f"PRAGMA table_info({PROPOSALS_TABLE})").all()
    return any(r[1] == "customer_id" and r[3] for r in info)


def _nullable_customer_sql(create_sql: str) -> str:
    """지금 `proposals` 의 CREATE 글에서 표 이름만 `proposals_new` 로, `customer_id` 의 NOT NULL 만 뺀 글. 다른 열·제약·기본값은 글자 그대로다
    (이전 이관이 더한 열도 그대로 들어 있다). 고칠 자리가 하나로 가려지지 않으면 (모르는 모양이면) 멈춘다."""
    named, n_name = _CREATE_NAME.subn(lambda m: m.group(1) + PROPOSALS_TABLE + "_new", create_sql, count=1)
    relaxed, n_col = _CUSTOMER_NOT_NULL.subn(lambda m: m.group("col"), named)
    if n_name != 1 or n_col != 1:
        raise RuntimeError(f"proposals 표의 CREATE 글을 자동으로 고칠 수 없습니다 (표 이름 {n_name}곳, customer_id {n_col}곳): {create_sql!r}")
    return relaxed


def _proposal_fk_violations(con: sqlite3.Connection) -> set[tuple]:
    """`PRAGMA foreign_key_check` 중 proposals 표가 아래쪽(자식)이거나 위쪽(부모)인 줄."""
    return {tuple(r) for r in con.execute("PRAGMA foreign_key_check") if r[0] == PROPOSALS_TABLE or r[2] == PROPOSALS_TABLE}


def rebuild_proposals_nullable_customer(engine: Engine, db_path: Path, backup: MigrationBackup | None = None) -> dict | None:
    """`proposals` 표를 `customer_id` 가 비어도 되는 모양으로 다시 만든다 (SQLite 문서의 12단계 방법). 이미 풀려 있으면 아무것도 하지 않고 None.

    순서: DB 복사본(`app.db.before-migrate-<시각>`) → `PRAGMA foreign_keys=OFF` → 한 트랜잭션 안에서 `proposals_new` 만들기(열·형·기본값·제약은 지금 CREATE 글 그대로,
    customer_id 의 NOT NULL 만 뺀다) → 모든 열을 `INSERT … SELECT` 로 옮기기(번호·값 그대로) → 옛 표 지우기 → 이름 바꾸기 → 옛 표에 있던 색인·트리거를 `sqlite_master` 에서 읽은 글로 다시 만들기 →
    `PRAGMA foreign_key_check` 가 이 일로 새 위반을 내면 되돌리기, 아니면 커밋 → `foreign_keys` 다시 켜기. 중간에 실패하면 되돌리고 멈춘다 (DB 는 그대로, 복사본은 남는다).
    이 표를 가리키는 다른 표(proposal_messages·proposal_solutions·proposal_customers)의 외래 키는 표 이름으로 가리키므로 그대로 이어진다.
    이 일 전에 있던 위반(다른 표의 낡은 번호 따위)은 막지 않는다: 새로 생긴 위반만 본다."""
    if not proposals_need_rebuild(engine):
        return None
    copy = (backup or MigrationBackup(db_path)).take()
    log.info("proposals 표 다시 만들기 전 DB 복사본: %s", copy)
    con = sqlite3.connect(db_path, isolation_level=None, timeout=30)  # 트랜잭션을 직접 다룬다 (PRAGMA foreign_keys 는 트랜잭션 밖에서만 바뀐다)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        if con.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise RuntimeError("PRAGMA foreign_keys=OFF 가 듣지 않아 표를 다시 만들지 않습니다")
        create_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (PROPOSALS_TABLE,)).fetchone()[0]
        extras = con.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index', 'trigger') AND sql IS NOT NULL ORDER BY type DESC, name", (PROPOSALS_TABLE,)).fetchall()
        columns = ", ".join(_quote(r[1]) for r in con.execute(f"PRAGMA table_info({PROPOSALS_TABLE})"))
        statements = [_nullable_customer_sql(create_sql)]  # 모르는 모양이면 여기서(아무것도 건드리기 전에) 멈춘다
        before = _proposal_fk_violations(con)
        rows = con.execute(f"SELECT COUNT(*) FROM {PROPOSALS_TABLE}").fetchone()[0]
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(statements[0])
            con.execute(f"INSERT INTO {PROPOSALS_TABLE}_new ({columns}) SELECT {columns} FROM {PROPOSALS_TABLE}")
            if con.execute(f"SELECT COUNT(*) FROM {PROPOSALS_TABLE}_new").fetchone()[0] != rows:
                raise RuntimeError("옮긴 줄 수가 맞지 않습니다")
            con.execute(f"DROP TABLE {PROPOSALS_TABLE}")
            con.execute(f"ALTER TABLE {PROPOSALS_TABLE}_new RENAME TO {PROPOSALS_TABLE}")
            for _kind, _name, sql in extras:
                con.execute(sql)
            if new := _proposal_fk_violations(con) - before:
                raise RuntimeError(f"외래 키 검사가 새 위반을 냈습니다: {sorted(new)[:5]}")
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        try:
            con.execute("PRAGMA foreign_keys=ON")
        finally:
            con.close()
    stats = {"rows": rows, "indexes": [n for k, n, _ in extras if k == "index"], "triggers": [n for k, n, _ in extras if k == "trigger"], "backup": str(copy)}
    log.info("proposals 표를 customer_id 가 비어도 되는 모양으로 다시 만들었습니다: 줄 %(rows)d, 색인 %(indexes)s, 트리거 %(triggers)s", stats)
    return stats


def run_startup_migrations(engine: Engine, db_path: Path) -> MigrationBackup:
    """시작 때 이관 단계를 순서대로 한 번에 돌린다. DB 복사본은 모든 단계를 통틀어 (바꿀 것이 있을 때만) 하나다."""
    backup = MigrationBackup(db_path)
    rebuild_proposals_nullable_customer(engine, db_path, backup=backup)  # 먼저: 다수 고객 제안 칸을 더하는 단계가 풀린 표 위에서 일한다
    add_missing_columns(engine, db_path, backup=backup)
    copy_activity_json(engine, db_path, backup=backup)
    add_unique_indexes(engine)
    backfill_origins(engine, db_path, backup=backup)
    from app import idmarks  # 지운 번호를 다시 쓰지 않는 표시 (새 작은 표만 쓴다: 기존 표·값은 그대로)

    idmarks.ensure_marks(engine)
    from app import solution_fields  # 솔루션 분야 처음 채우기 (베타 AD): 표시가 없을 때만 한 번, 번호는 번호 표시 장치를 거쳐 정한다

    solution_fields.seed_once(engine)
    from app import opportunities  # 영업 A 단계: 옛 영업기회의 담당을 만든 사람으로 한 번만 채운다

    opportunities.backfill_owners_once(engine)
    return backup

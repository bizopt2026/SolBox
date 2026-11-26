"""매일 백업: app.db(SQLite 온라인 백업) + uploads/ + decks/ + reports/ 복사, 오래된 백업 정리, 점검."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.system import open_readonly

log = logging.getLogger(__name__)

STAMP_FORMAT = "%Y%m%d-%H%M%S"
_STAMP_RE = re.compile(r"\d{8}-\d{6}")
PARTIAL_MAX_AGE = timedelta(days=1)
_COPIED_FOLDERS = ("uploads", "decks", "reports")  # knowledge/ 는 다시 만들 수 있으므로 복사하지 않는다


def _stamp(name: str) -> datetime | None:
    if not _STAMP_RE.fullmatch(name):
        return None
    try:
        return datetime.strptime(name, STAMP_FORMAT)
    except ValueError:
        return None


def _is_real_dir(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink()


def _count_files(root: Path) -> int:
    """파일과 링크의 수. 링크는 따라가지 않는다."""
    n = 0
    for _dir, dirnames, filenames in os.walk(root, followlinks=False):
        n += len(filenames) + sum(1 for d in dirnames if os.path.islink(os.path.join(_dir, d)))
    return n


def _remove_stale_partials(backup_dir: Path, now: datetime) -> None:
    for p in backup_dir.iterdir():
        if not p.name.endswith(".partial") or not _is_real_dir(p):
            continue
        stamp = _stamp(p.name[: -len(".partial")])
        if stamp is not None and now - stamp > PARTIAL_MAX_AGE:
            shutil.rmtree(p, ignore_errors=True)


def _copy_database(src_path: Path, dst_path: Path) -> None:
    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)  # 앱이 쓰는 중이어도 일관된 사본
            dst.execute("PRAGMA journal_mode=DELETE")  # 사본은 파일 하나로 둔다
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def run_backup(settings: Settings, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    if not settings.db_path.is_file():
        raise FileNotFoundError(f"데이터베이스 파일이 없습니다: {settings.db_path}")
    backup_dir = settings.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_partials(backup_dir, now)
    name = now.strftime(STAMP_FORMAT)
    final = backup_dir / name
    if final.exists():
        raise FileExistsError(f"같은 시각의 백업이 이미 있습니다: {final}")
    partial = backup_dir / f"{name}.partial"
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir()
    try:
        _copy_database(settings.db_path, partial / "app.db")
        for folder in _COPIED_FOLDERS:
            source = settings.data_dir / folder
            if source.is_dir():
                shutil.copytree(source, partial / folder, symlinks=True, dirs_exist_ok=False)
            else:
                (partial / folder).mkdir()
        meta = {
            "created_at": now.isoformat(timespec="seconds"),
            "db_bytes": (partial / "app.db").stat().st_size,
            "files": sum(_count_files(partial / f) for f in _COPIED_FOLDERS),
        }
        (partial / "backup.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        partial.rename(final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    try:
        prune(backup_dir, settings.backup_keep_days, now)
    except OSError:
        log.exception("오래된 백업을 정리하지 못했습니다")  # 백업 자체는 끝났다
    return final


def prune(backup_dir: Path, keep_days: int, now: datetime) -> list[Path]:
    """keep_days 일보다 오래된 백업 폴더를 지운다. 가장 최근 백업 하나는 남긴다."""
    if not backup_dir.is_dir():
        return []
    found = sorted(
        (stamp, p)
        for p in backup_dir.iterdir()
        if _is_real_dir(p) and (stamp := _stamp(p.name)) is not None
    )
    removed: list[Path] = []
    for stamp, p in found[:-1]:  # 마지막(가장 최근)은 나이와 무관하게 남긴다
        if now - stamp > timedelta(days=keep_days):
            try:
                shutil.rmtree(p)
            except OSError:
                log.warning("오래된 백업을 지우지 못했습니다: %s", p, exc_info=True)  # 나머지는 계속 지운다
                continue
            removed.append(p)
    return removed


def verify(backup_path: Path) -> list[str]:
    """문제 목록. 없으면 []."""
    backup_path = Path(backup_path)
    if not backup_path.is_dir():
        return [f"백업 폴더가 없습니다: {backup_path}"]
    problems: list[str] = []
    db_file = backup_path / "app.db"
    if not db_file.is_file():
        problems.append("app.db 가 없습니다.")
    else:
        try:
            conn = open_readonly(db_file)
            try:
                result = [r[0] for r in conn.execute("PRAGMA integrity_check")]
            finally:
                conn.close()
            if result != ["ok"]:
                problems.append("app.db 점검 실패: " + "; ".join(map(str, result[:3])))
        except sqlite3.Error as e:
            problems.append(f"app.db 를 열 수 없습니다: {e}")
    meta_file = backup_path / "backup.json"
    if not meta_file.is_file():
        problems.append("backup.json 이 없습니다.")
    else:
        try:
            expected = json.loads(meta_file.read_text(encoding="utf-8"))["files"]
            actual = sum(_count_files(backup_path / f) for f in _COPIED_FOLDERS if (backup_path / f).is_dir())
            if actual != expected:
                problems.append(f"파일 수가 맞지 않습니다: 기록 {expected}개, 실제 {actual}개")
        except (OSError, ValueError, KeyError, TypeError):
            problems.append("backup.json 을 읽을 수 없습니다.")
    return problems


def latest(backup_dir: Path) -> dict | None:
    """가장 최근의 읽을 수 있는 백업: {"path", "created_at", "db_bytes"}."""
    if not backup_dir.is_dir():
        return None
    folders = sorted(
        (p for p in backup_dir.iterdir() if _is_real_dir(p) and _stamp(p.name) is not None),
        key=lambda p: p.name,
        reverse=True,
    )
    for p in folders:
        try:
            meta = json.loads((p / "backup.json").read_text(encoding="utf-8"))
            return {
                "path": p,
                "created_at": datetime.fromisoformat(meta["created_at"]),
                "db_bytes": int(meta["db_bytes"]),
            }
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None

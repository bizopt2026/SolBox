"""관리용 명령줄: python -m app.cli create-admin ..."""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from app import backup
from app.config import load_settings
from app.db import Base, make_engine, make_session_factory
from app.knowledge import KnowledgeMirror
from app.models import User
from app.security import hash_password


def create_admin(session_factory, username: str, name: str, password: str) -> None:
    with session_factory() as db:
        if db.query(User).filter_by(username=username).first():
            raise SystemExit(f"이미 있는 아이디입니다: {username}")
        db.add(User(username=username, name=name, password_hash=hash_password(password), role="admin"))
        db.commit()


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"  # pragma: no cover


def _folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def _backup_command(verify_folder: str | None) -> None:
    if verify_folder is not None:
        problems = backup.verify(Path(verify_folder))
        if problems:
            print("백업에 문제가 있습니다:")
            for p in problems:
                print(f"- {p}")
            raise SystemExit(1)
        print("이상 없음")
        return
    settings = load_settings()
    try:
        path = backup.run_backup(settings)
    except FileNotFoundError:
        raise SystemExit(f"데이터베이스 파일이 없습니다: {settings.db_path}") from None
    print(f"백업했습니다: {path} ({_human_size(_folder_size(path))})")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin", help="관리자 계정 만들기")
    p.add_argument("--username", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--password", help="생략하면 입력창에서 두 번 입력받는다")
    sub.add_parser("rebuild-knowledge", help="knowledge 폴더를 DB 기준으로 다시 만들기")
    p = sub.add_parser("backup", help="app.db, uploads, decks 를 백업 폴더에 복사하기")
    p.add_argument("--verify", metavar="폴더", help="백업을 만들지 않고 이 백업 폴더를 점검한다")
    args = parser.parse_args(argv)
    if args.cmd == "backup":
        _backup_command(args.verify)
        return

    password = None
    if args.cmd == "create-admin":
        password = args.password
        if password is None:
            password = getpass.getpass("비밀번호: ")
            if password != getpass.getpass("비밀번호 확인: "):
                raise SystemExit("비밀번호가 일치하지 않습니다.")

    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    try:
        if args.cmd == "rebuild-knowledge":
            with session_factory() as db:
                KnowledgeMirror(settings.knowledge_dir).rebuild_all(db, settings.uploads_dir)
            print(f"knowledge 폴더를 다시 만들었습니다: {settings.knowledge_dir}")
        else:
            create_admin(session_factory, args.username, args.name, password)
            print(f"관리자 계정을 만들었습니다: {args.username}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

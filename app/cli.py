"""관리용 명령줄: python -m app.cli create-admin ..."""
from __future__ import annotations

import argparse

from app.config import load_settings
from app.db import Base, make_engine, make_session_factory
from app.models import User
from app.security import hash_password


def create_admin(session_factory, username: str, name: str, password: str) -> None:
    with session_factory() as db:
        if db.query(User).filter_by(username=username).first():
            raise SystemExit(f"이미 있는 아이디입니다: {username}")
        db.add(User(username=username, name=name, password_hash=hash_password(password), role="admin"))
        db.commit()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin", help="관리자 계정 만들기")
    p.add_argument("--username", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--password", required=True)
    args = parser.parse_args(argv)

    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)
    create_admin(make_session_factory(engine), args.username, args.name, args.password)
    print(f"관리자 계정을 만들었습니다: {args.username}")


if __name__ == "__main__":
    main()

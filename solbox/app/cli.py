"""관리용 명령줄: python -m app.cli create-admin ..."""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from app import backup, certs
from app.config import load_settings
from app.db import Base, make_engine, make_session_factory
from app.knowledge import KnowledgeMirror
from app.migrate import add_missing_columns
from app.models import User
from app.security import hash_password
from app.system import folder_size, human_size, run_checks


def create_admin(session_factory, username: str, name: str, password: str) -> None:
    with session_factory() as db:
        if db.query(User).filter_by(username=username).first():
            raise SystemExit(f"이미 있는 아이디입니다: {username}")
        db.add(User(username=username, name=name, password_hash=hash_password(password), role="admin"))
        db.commit()


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
    print(f"백업했습니다: {path} ({human_size(folder_size(path))})")


def _make_cert_command(hosts: list[str], out: str, overwrite: bool) -> None:
    try:
        crt, key = certs.make_self_signed(Path(out), hosts, overwrite=overwrite)
    except FileExistsError as e:
        raise SystemExit(f"이미 있는 파일입니다: {e}\n덮어쓰려면 --overwrite 를 붙이세요.") from None
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print(f"인증서를 만들었습니다: {crt}")
    print("config.toml 에 아래 두 줄을 넣으세요.")
    print(f'ssl_certfile = "{crt.resolve().as_posix()}"')
    print(f'ssl_keyfile = "{key.resolve().as_posix()}"')


def _check_command() -> None:
    problems = 0
    for name, problem, detail in run_checks(load_settings):
        if problem is None:
            print(f"{name}: 정상" + (f" ({detail})" if detail else ""))
        else:
            problems += 1
            print(f"{name}: 문제: {problem}")
    if problems:
        print(f"문제가 {problems}개 있습니다.")
        raise SystemExit(1)
    print("모두 정상입니다.")


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
    p = sub.add_parser("make-cert", help="자체 서명 HTTPS 인증서 만들기")
    p.add_argument("--host", action="append", required=True, help="접속 주소(IP 또는 이름). 여러 번 쓸 수 있고 첫 항목이 대표 이름이 된다")
    p.add_argument("--out", default="certs", help="저장할 폴더 (기본 certs)")
    p.add_argument("--overwrite", action="store_true", help="이미 있는 인증서를 덮어쓴다")
    sub.add_parser("check", help="설치 상태 점검 (서버를 띄우거나 데이터를 바꾸지 않는다)")
    args = parser.parse_args(argv)
    if args.cmd == "make-cert":
        _make_cert_command(args.host, args.out, args.overwrite)
        return
    if args.cmd == "check":
        _check_command()
        return
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
    add_missing_columns(engine, settings.db_path)
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

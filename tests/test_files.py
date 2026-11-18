import dataclasses
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Solution, SolutionFile
from tests.conftest import make_user


def _solution(db, **kw):
    s = Solution(name="A", **kw)
    db.add(s)
    db.commit()
    return s


def test_upload_multiple_files_at_once(user_client, db, settings):
    s = _solution(db)
    files = [("files", ("소개서.txt", "솔루션 소개".encode(), "text/plain")),
             ("files", ("spec.txt", b"220V", "text/plain"))]
    r = user_client.post(f"/solutions/{s.id}/files", files=files, data={"kind": "intro"}, follow_redirects=False)
    assert r.status_code == 303
    rows = db.query(SolutionFile).order_by(SolutionFile.id).all()
    assert [f.original_name for f in rows] == ["소개서.txt", "spec.txt"]
    assert all(f.kind == "intro" and f.extract_status == "ok" for f in rows)
    assert rows[1].size == 4
    kdir = settings.knowledge_dir / "solutions" / str(s.id) / "files"
    text = (kdir / f"{rows[0].id}-소개서.txt.md").read_text(encoding="utf-8")
    assert text.startswith("# 소개서.txt (소개)")
    assert text.strip().endswith("솔루션 소개")
    card = (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")
    assert "소개서.txt" in card and "spec.txt" in card


def test_default_kind_is_related_and_bad_kind_rejected(user_client, db):
    s = _solution(db)
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("a.txt", b"x", "text/plain"))])
    assert db.query(SolutionFile).one().kind == "related"
    r = user_client.post(f"/solutions/{s.id}/files", files=[("files", ("b.txt", b"x", "text/plain"))], data={"kind": "nope"})
    assert r.status_code == 400
    assert db.query(SolutionFile).count() == 1


def test_empty_file_selection_is_ignored(user_client, db):
    s = _solution(db)
    r = user_client.post(f"/solutions/{s.id}/files", files=[("files", ("", b"", "application/octet-stream"))],
                         follow_redirects=False)
    assert r.status_code == 303
    assert db.query(SolutionFile).count() == 0


def test_same_name_files_do_not_overwrite(user_client, db, settings):
    s = _solution(db)
    files = [("files", ("a.txt", b"one", "text/plain")), ("files", ("a.txt", b"two", "text/plain"))]
    user_client.post(f"/solutions/{s.id}/files", files=files)
    f1, f2 = db.query(SolutionFile).order_by(SolutionFile.id).all()
    up = settings.uploads_dir / "solutions" / str(s.id)
    assert f1.stored_name == f"{f1.id}.txt"
    assert (up / f1.stored_name).read_bytes() == b"one" and (up / f2.stored_name).read_bytes() == b"two"


def test_download_keeps_korean_special_name(user_client, db):
    s = _solution(db)
    name = '제안서: "최종"?.txt'
    user_client.post(f"/solutions/{s.id}/files", files=[("files", (name, "본문".encode(), "text/plain"))])
    f = db.query(SolutionFile).one()
    r = user_client.get(f"/files/{f.id}/download")
    assert r.status_code == 200 and r.content == "본문".encode()
    assert r.headers["content-disposition"] == "attachment; filename*=UTF-8''" + quote(name)


def test_download_missing_is_404(user_client):
    assert user_client.get("/files/999/download").status_code == 404


def test_unsupported_file_is_kept_with_status(user_client, db, settings):
    s = _solution(db)
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("a.hwp", b"xx", "application/octet-stream"))])
    f = db.query(SolutionFile).one()
    assert f.extract_status == "unsupported"
    assert (settings.uploads_dir / "solutions" / str(s.id) / f.stored_name).exists()
    assert not (settings.knowledge_dir / "solutions" / str(s.id) / "files" / f"{f.id}-a.hwp.md").exists()
    assert "텍스트 추출 미지원" in user_client.get(f"/solutions/{s.id}").text


def test_failed_extraction_is_shown(user_client, db):
    s = _solution(db)
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("bad.pptx", b"not a zip", "application/octet-stream"))])
    assert db.query(SolutionFile).one().extract_status == "failed"
    assert "텍스트 추출 실패" in user_client.get(f"/solutions/{s.id}").text


def _oversize_client(settings):
    """max_upload_mb=0 인 앱. 같은 data_dir(DB)을 쓰므로 이미 만든 사용자 kim 으로 로그인한다."""
    other = create_app(dataclasses.replace(settings, max_upload_mb=0))
    c = TestClient(other)
    assert c.post("/login", data={"username": "kim", "password": "pw1234"}, follow_redirects=False).status_code == 303
    return other, c


def test_oversize_file_skipped_others_saved(user_client, db, settings):
    s = _solution(db)
    other, c = _oversize_client(settings)
    try:
        r = c.post(f"/solutions/{s.id}/files",
                   files=[("files", ("큰파일.txt", b"x", "text/plain")), ("files", ("빈.txt", b"", "text/plain"))],
                   follow_redirects=False)
    finally:
        other.state.engine.dispose()
    assert r.status_code == 200
    assert "큰파일.txt" in r.text and "빈.txt" not in r.text.split("건너뛴")[1].split("</")[0]
    assert [f.original_name for f in db.query(SolutionFile).all()] == ["빈.txt"]


def test_oversize_leaves_no_row_or_file(user_client, db, settings):
    s = _solution(db)
    other, c = _oversize_client(settings)
    try:
        r = c.post(f"/solutions/{s.id}/files", files=[("files", ("큰.txt", b"x", "text/plain"))])
    finally:
        other.state.engine.dispose()
    assert r.status_code == 200 and "큰.txt" in r.text
    assert db.query(SolutionFile).count() == 0
    up = settings.uploads_dir / "solutions" / str(s.id)
    assert not up.exists() or not list(up.iterdir())


def test_change_kind_resyncs_card_and_text(user_client, db, settings):
    s = _solution(db)
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("a.txt", b"hi", "text/plain"))])
    f = db.query(SolutionFile).one()
    r = user_client.post(f"/files/{f.id}/kind", data={"kind": "spec"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/solutions/{s.id}"
    db.refresh(f)
    assert f.kind == "spec"
    base = settings.knowledge_dir / "solutions" / str(s.id)
    assert "(스펙)" in (base / "files" / f"{f.id}-a.txt.md").read_text(encoding="utf-8").splitlines()[0]
    assert "스펙: a.txt" in (base / "card.md").read_text(encoding="utf-8")
    assert user_client.post(f"/files/{f.id}/kind", data={"kind": "nope"}).status_code == 400
    assert user_client.post("/files/999/kind", data={"kind": "spec"}).status_code == 404


def test_delete_file_removes_original_and_mirror(user_client, db, settings):
    s = _solution(db)
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("a.txt", b"hi", "text/plain"))])
    f = db.query(SolutionFile).one()
    fid, stored = f.id, f.stored_name
    r = user_client.post(f"/files/{fid}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/solutions/{s.id}"
    db.expire_all()
    assert db.query(SolutionFile).count() == 0
    assert not (settings.uploads_dir / "solutions" / str(s.id) / stored).exists()
    base = settings.knowledge_dir / "solutions" / str(s.id)
    assert not (base / "files" / f"{fid}-a.txt.md").exists()
    assert "a.txt" not in (base / "card.md").read_text(encoding="utf-8")


def test_delete_file_permission(app, db, user_client, admin_client):
    s = _solution(db)
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("a.txt", b"hi", "text/plain"))])
    f = db.query(SolutionFile).one()
    make_user(db, "lee")
    other = TestClient(app)
    other.post("/login", data={"username": "lee", "password": "pw1234"})
    assert other.post(f"/files/{f.id}/delete").status_code == 403
    assert db.query(SolutionFile).count() == 1
    assert admin_client.post(f"/files/{f.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(SolutionFile).count() == 0


def test_delete_solution_removes_upload_folder(admin_client, db, settings):
    s = _solution(db)
    admin_client.post(f"/solutions/{s.id}/files", files=[("files", ("a.txt", b"hi", "text/plain"))])
    up = settings.uploads_dir / "solutions" / str(s.id)
    assert up.exists()
    r = admin_client.post(f"/solutions/{s.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert not up.exists()
    assert not (settings.knowledge_dir / "solutions" / str(s.id)).exists()


def test_upload_to_missing_solution_is_404(user_client):
    r = user_client.post("/solutions/999/files", files=[("files", ("a.txt", b"x", "text/plain"))])
    assert r.status_code == 404


def test_requires_login(client):
    assert client.post("/solutions/1/files", files=[("files", ("a.txt", b"x", "text/plain"))],
                       follow_redirects=False).status_code == 303
    assert client.get("/files/1/download", follow_redirects=False).status_code == 303

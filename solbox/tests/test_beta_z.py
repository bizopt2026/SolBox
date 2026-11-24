"""베타 Z: 서버가 도는 중에 git pull 로 파일만 바뀌어도 화면이 깨지지 않게.

- 화면 파일을 시작할 때 모두 읽어 둔다 (`main.snapshot_loader`): 다시 시작할 때까지 시작 때의 프로그램과 화면이 짝을 이룬다.
- 올리기 칸의 한도 값은 화면에도 기본값이 있다: 옛 프로그램이 새 화면 파일을 읽어도 500 이 나지 않는다 (2026-11-23 로컬 PC 에서 실제로 난 오류).
- 폴더의 코드가 실행 중인 코드와 다르면 관리자 화면 위에 알린다 (`update.code_changed_on_disk`).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import DictLoader, Environment, FileSystemLoader

from app import update
from app.main import APP_DIR, snapshot_loader

SHA_A = "a" * 40
SHA_B = "b" * 40


def test_z_snapshot_keeps_the_startup_version_of_a_template(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "page.html").write_text("옛 화면 {{ x }}", encoding="utf-8")
    env = Environment(loader=snapshot_loader(tmp_path), auto_reload=False)
    (tmp_path / "sub" / "page.html").write_text("새 화면 {{ x.missing.format(1) }}", encoding="utf-8")
    (tmp_path / "late.html").write_text("나중에 생긴 파일", encoding="utf-8")
    assert env.get_template("sub/page.html").render(x=1) == "옛 화면 1"
    with pytest.raises(Exception):
        env.get_template("late.html")


def test_z_app_uses_a_snapshot_of_every_template(client):
    env = client.app.state.templates.env
    assert isinstance(env.loader, DictLoader)
    assert env.auto_reload is False
    on_disk = {p.relative_to(APP_DIR / "templates").as_posix() for p in (APP_DIR / "templates").rglob("*.html")}
    assert on_disk and on_disk <= set(env.loader.mapping)


def _dropzone_env():
    env = Environment(loader=FileSystemLoader(str(APP_DIR / "templates")))
    env.globals["ai_gate"] = lambda: None
    return env


@pytest.mark.parametrize("zone", ["home", "intake"])
def test_z_upload_box_renders_without_paste_limits_in_context(zone):
    """옛 프로그램(한도 값을 넘기지 않음) + 새 화면 파일: 예전에는 TypeError(Undefined.__format__)로 500 이었다."""
    html = _dropzone_env().get_template("intake/_dropzone.html").render(zone_id=zone, max_files=20, max_mb=100, max_note=500)
    assert "200,000자" in html
    if zone == "intake":
        assert "60자까지" in html


def test_z_upload_box_still_uses_the_values_it_is_given():
    html = _dropzone_env().get_template("intake/_dropzone.html").render(zone_id="intake", max_files=20, max_mb=100, max_note=500,
                                                                        max_paste=1234, max_paste_title=7)
    assert "1,234자" in html and "7자까지" in html


def _repo(tmp_path: Path, head: str, refs: dict[str, str] | None = None, packed: str = "") -> Path:
    root = tmp_path / "repo"
    (root / "solbox").mkdir(parents=True)
    (root / "solbox" / "pyproject.toml").write_text("", encoding="utf-8")
    git = root / ".git"
    git.mkdir()
    (git / "HEAD").write_text(head + "\n", encoding="utf-8")
    for ref, sha in (refs or {}).items():
        (git / ref).parent.mkdir(parents=True, exist_ok=True)
        (git / ref).write_text(sha + "\n", encoding="utf-8")
    if packed:
        (git / "packed-refs").write_text(packed, encoding="utf-8")
    return root


@pytest.fixture
def boot(monkeypatch):
    monkeypatch.setattr(update, "_BOOT", {"sha": SHA_A, "subject": "s"})
    monkeypatch.setattr(update, "_DISK_CACHE", {"at": 0.0, "sha": ""})


def _point(monkeypatch, root: Path):
    monkeypatch.setattr(update, "find_repo_root", lambda start=None: root)


def test_z_reads_head_from_ref_file(tmp_path, monkeypatch, boot):
    _point(monkeypatch, _repo(tmp_path, "ref: refs/heads/main", {"refs/heads/main": SHA_B}))
    assert update.code_changed_on_disk() == {"running": "aaaaaaa", "disk": "bbbbbbb"}


def test_z_reads_head_from_packed_refs_and_detached(tmp_path, monkeypatch, boot):
    _point(monkeypatch, _repo(tmp_path, "ref: refs/heads/main", packed=f"# pack-refs\n{SHA_B} refs/heads/main\n"))
    assert update.code_changed_on_disk()["disk"] == "bbbbbbb"
    update._DISK_CACHE.update(at=0.0, sha="")
    _point(monkeypatch, _repo(tmp_path / "d", SHA_B))
    assert update.code_changed_on_disk()["disk"] == "bbbbbbb"


def test_z_same_commit_or_unknown_is_not_reported(tmp_path, monkeypatch, boot):
    _point(monkeypatch, _repo(tmp_path, "ref: refs/heads/main", {"refs/heads/main": SHA_A}))
    assert update.code_changed_on_disk() is None
    update._DISK_CACHE.update(at=0.0, sha="")
    _point(monkeypatch, None)
    assert update.code_changed_on_disk() is None
    monkeypatch.setattr(update, "_BOOT", {})
    assert update.code_changed_on_disk() is None


def test_z_broken_git_folder_never_breaks_the_page(tmp_path, monkeypatch, boot):
    root = _repo(tmp_path, "ref: refs/heads/main")
    (root / ".git" / "HEAD").unlink()
    _point(monkeypatch, root)
    assert update.code_changed_on_disk() is None


def test_z_worktree_gitdir_file_is_followed(tmp_path, monkeypatch, boot):
    main = _repo(tmp_path, "ref: refs/heads/main", {"refs/heads/main": SHA_A})
    wt_git = main / ".git" / "worktrees" / "w"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/topic\n", encoding="utf-8")
    (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
    (main / ".git" / "refs" / "heads" / "topic").write_text(SHA_B + "\n", encoding="utf-8")
    wt = tmp_path / "wt"
    (wt / "solbox").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {wt_git}\n", encoding="utf-8")
    _point(monkeypatch, wt)
    assert update.code_changed_on_disk()["disk"] == "bbbbbbb"


def test_z_result_is_cached_for_a_while(tmp_path, monkeypatch, boot):
    root = _repo(tmp_path, "ref: refs/heads/main", {"refs/heads/main": SHA_B})
    _point(monkeypatch, root)
    assert update.code_changed_on_disk()
    (root / ".git" / "refs" / "heads" / "main").write_text(SHA_A + "\n", encoding="utf-8")
    assert update.code_changed_on_disk()  # 30초 안에는 다시 읽지 않는다


def test_z_banner_only_for_admins(admin_client, user_client, monkeypatch):
    monkeypatch.setattr(update, "code_changed_on_disk", lambda: {"running": "aaaaaaa", "disk": "bbbbbbb"})
    for c in (admin_client, user_client):
        c.app.state.templates.env.globals["code_changed_on_disk"] = update.code_changed_on_disk
    page = admin_client.get("/").text
    assert "서버를 다시 시작해야" in page and "aaaaaaa" in page and "bbbbbbb" in page
    assert "서버를 다시 시작해야" not in user_client.get("/").text


def test_z_no_banner_when_code_matches(admin_client, monkeypatch):
    admin_client.app.state.templates.env.globals["code_changed_on_disk"] = lambda: None
    assert "서버를 다시 시작해야" not in admin_client.get("/").text

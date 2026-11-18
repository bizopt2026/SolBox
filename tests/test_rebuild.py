import shutil
from datetime import date

from app.models import Activity, Customer, Solution


def test_rebuild_all_recreates_everything(user_client, db, settings, app):
    s = Solution(name="AI 솔루션"); db.add(s); db.commit()
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("소개.txt", "본문 내용".encode(), "text/plain"))])
    c = Customer(name="고객사"); db.add(c)
    a = Activity(raw_text="메모", title="회의", date=date(2026, 3, 2)); db.add(a)
    db.commit()
    app.state.mirror.sync(s); app.state.mirror.sync(c); app.state.mirror.sync(a)
    shutil.rmtree(settings.knowledge_dir)
    settings.knowledge_dir.mkdir()
    (settings.knowledge_dir / "stale.md").write_text("old", encoding="utf-8")

    app.state.mirror.rebuild_all(db, settings.uploads_dir)

    k = settings.knowledge_dir
    assert (k / f"solutions/{s.id}/card.md").is_file()
    file_md = next((k / f"solutions/{s.id}/files").glob("*-소개.txt.md"))
    assert "본문 내용" in file_md.read_text(encoding="utf-8")
    assert (k / f"customers/{c.id}.md").is_file()
    assert (k / f"activities/2026/{a.id}.md").is_file()
    assert not (k / "stale.md").exists()


def test_rebuild_all_refreshes_extract_status(user_client, db, settings, app):
    s = Solution(name="A"); db.add(s); db.commit()
    user_client.post(f"/solutions/{s.id}/files", files=[("files", ("a.txt", "내용".encode(), "text/plain"))])
    f = s.files[0]
    f.extract_status = "failed"; db.commit()
    app.state.mirror.rebuild_all(db, settings.uploads_dir)
    db.refresh(f)
    assert f.extract_status == "ok"


def test_rebuild_cli(settings, tmp_path, monkeypatch, capsys):
    from app import cli
    from app.db import Base, make_engine, make_session_factory

    settings.data_dir.mkdir(parents=True)
    engine = make_engine(settings.db_path); Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    with sf() as db:
        db.add(Customer(name="고객사")); db.commit()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'data_dir = "{settings.data_dir.as_posix()}"\nsecret_key = "x"\n', encoding="utf-8")
    monkeypatch.setenv("SOLBOX_CONFIG", str(cfg))
    cli.main(["rebuild-knowledge"])
    engine.dispose()
    assert (settings.knowledge_dir / "customers/1.md").is_file()
    assert "다시 만들었습니다" in capsys.readouterr().out

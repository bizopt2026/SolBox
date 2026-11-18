"""FastAPI 앱 생성."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import models  # noqa: F401  (모델을 메타데이터에 등록)
from app.config import Settings
from app.db import Base, make_engine, make_session_factory

APP_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings) -> FastAPI:
    for d in (settings.data_dir, settings.uploads_dir, settings.knowledge_dir, settings.decks_dir):
        d.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)

    app = FastAPI(title="솔루션 박스")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    static_dir = APP_DIR / "static"
    templates_dir = APP_DIR / "templates"
    static_dir.mkdir(exist_ok=True)
    templates_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.state.templates = Jinja2Templates(directory=templates_dir)
    return app

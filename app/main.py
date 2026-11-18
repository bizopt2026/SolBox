"""FastAPI 앱 생성."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import labels, models  # noqa: F401  (모델을 메타데이터에 등록)
from app.ai.queue import fail_stale_running, start_workers, stop_workers
from app.ai.runner import ClaudeRunner
from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.knowledge import KnowledgeMirror
from app.security import LoginRequired

APP_DIR = Path(__file__).resolve().parent


def render(request: Request, template: str, status_code: int = 200, **ctx) -> HTMLResponse:
    """템플릿을 그린다. `user`, `labels` 는 항상 넣어 준다."""
    ctx.setdefault("user", getattr(request.state, "user", None))
    ctx.setdefault("labels", labels)
    return request.app.state.templates.TemplateResponse(request, template, ctx, status_code=status_code)


def _login_required_handler(request: Request, exc: LoginRequired) -> Response:
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


def create_app(settings: Settings) -> FastAPI:
    for d in (settings.data_dir, settings.uploads_dir, settings.knowledge_dir, settings.decks_dir):
        d.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with app.state.session_factory() as db:
            fail_stale_running(db)
        start_workers(app)
        try:
            yield
        finally:
            stop_workers(app)

    app = FastAPI(title="솔루션 박스", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.mirror = KnowledgeMirror(settings.knowledge_dir)
    app.state.runner = ClaudeRunner(settings.claude_cmd, settings.knowledge_dir)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="strict",
        https_only=bool(settings.ssl_certfile),
    )
    app.add_exception_handler(LoginRequired, _login_required_handler)

    static_dir = APP_DIR / "static"
    templates_dir = APP_DIR / "templates"
    static_dir.mkdir(exist_ok=True)
    templates_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.state.templates = Jinja2Templates(directory=templates_dir)

    from app.routers import activities, admin, auth, businesses, customers, files, jobs, partners, solutions  # 지연 임포트: 라우터가 render 를 쓴다

    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(partners.router)
    app.include_router(customers.router)
    app.include_router(businesses.router)
    app.include_router(solutions.router)
    app.include_router(files.router)
    app.include_router(jobs.router)
    app.include_router(activities.router)
    return app

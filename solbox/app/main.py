"""FastAPI 앱 생성."""
from __future__ import annotations

import functools
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from jinja2 import pass_context
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.ai.gate import AIOff
from app import guards, labels, models, notices, ontology, scheduler, ui  # noqa: F401  (모델을 메타데이터에 등록, guards 는 flush 처리기를 단다)
from app.ai.queue import fail_stale_running, jobs_ahead, start_workers, stop_workers
from app.ai.runner import ClaudeRunner
from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.knowledge import KnowledgeMirror, rebuild_if_stale
from app.models import AIJob, Intake
from app.intake import recover as recover_intakes
from app.intake import recover_applying
from app.research import recover_runs
from app.migrate import run_startup_migrations
from app.security import MustChangePassword, LoginRequired

APP_DIR = Path(__file__).resolve().parent
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5


def _open_log_file(settings: Settings) -> tuple[logging.Logger, RotatingFileHandler, int]:
    """앱·대기열·스케줄러 로그(`app.*`)를 data_dir/logs/app.log 에 쓰는 처리기를 붙인다."""
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("app")
    previous_level = logger.level
    logger.addHandler(handler)
    if logger.getEffectiveLevel() > logging.INFO:
        logger.setLevel(logging.INFO)
    return logger, handler, previous_level


def _intake_ready_count(session_factory, user) -> int:
    """메뉴의 "올리기" 배지: 내 묶음 중 확인 대기 수. 요청 세션과 따로 짧은 세션을 열어 COUNT 하나만 세고 닫는다.
    로그인 화면(사용자 없음)이거나 어떤 이유로든 읽지 못하면 0 (배지 때문에 화면이 깨지면 안 된다)."""
    if user is None:
        return 0
    try:
        with session_factory() as db:
            return db.scalar(select(func.count()).select_from(Intake).where(Intake.status == "ready", Intake.created_by_id == user.id)) or 0
    except Exception:  # noqa: BLE001
        return 0


AI_HARD_PROBLEMS = ("not_installed", "auth")  # CLI 가 없거나 로그인이 안 됨: 이때만 "사용 불가" (한도·네트워크·시간 초과·형식은 그 작업의 실패일 뿐이다)


def _ai_status_data(session_factory, settings) -> dict:
    """상단 줄의 AI 상태: {"state": "ok"|"busy"|"warn"|"down"|"off", "text"}. 작은 조회 두 번 (대기·실행 수, 마지막으로 끝난 작업).
    꺼짐(처리기 꺼짐) → 사용 불가(CLI 없음·로그인 안 됨) → 작업 중 → 마지막 작업 실패(주의 색, 성공하는 작업이 나올 때까지) → 사용 가능."""
    if not settings.ai_worker_enabled:
        return {"state": "off", "text": labels.AI_OFF_STATUS}
    with session_factory() as db:
        counts = dict(db.execute(select(AIJob.status, func.count()).where(AIJob.status.in_(("pending", "running"))).group_by(AIJob.status)).all())
        last = db.scalars(select(AIJob).where(AIJob.finished_at.is_not(None)).order_by(AIJob.finished_at.desc(), AIJob.id.desc()).limit(1)).first()
    failed = last if last is not None and last.status == "failed" else None
    pending, running = counts.get("pending", 0), counts.get("running", 0)
    if failed is not None and failed.error_kind in AI_HARD_PROBLEMS:
        return {"state": "down", "text": "AI 사용 불가"}
    if running:
        return {"state": "busy", "text": f"AI 작업 중 {running}" + (f" · 대기 {pending}" if pending else "")}
    if failed is not None:
        return {"state": "warn", "text": f"마지막 AI 작업 실패 ({labels.JOB_KINDS.get(failed.kind, 'AI 작업')})"}
    return {"state": "ok", "text": f"AI 사용 가능 · 대기 {pending}"}


def _ai_status(session_factory, settings, user) -> dict | None:
    """로그인하지 않았거나 어떤 이유로든 읽지 못하면 None (상태 표시 때문에 화면이 깨지면 안 된다)."""
    if user is None:
        return None
    try:
        return _ai_status_data(session_factory, settings)
    except Exception:  # noqa: BLE001
        return None


def ai_gate(status: dict | None, user=None) -> dict | None:
    """AI 를 쓰는 동작 곁 안내용: 상단 줄과 같은 상태(`_ai_status`)에 더한 것. 읽지 못했으면 None.
    `blocked`: 처리기가 꺼진 "off" 일 때만 참 (버튼을 막는다). "down"(지난 AI 작업이 설치·로그인 문제로 실패)은 막지 않는다:
    성공하는 작업이 하나 나오면 저절로 풀리므로 다시 눌러 볼 수 있어야 한다. `down` 일 때 안내만 보인다.
    `reason`, `upload`: 안내 글(ontology). `admin`: 관리자면 안내에 "AI 연결 확인" 링크를 건다."""
    if status is None:
        return None
    state = status.get("state")
    return {**status, "blocked": state == "off", "down": state == "down",
            "reason": ontology.AI_BLOCK_REASONS.get(state, ""), "upload": ontology.AI_BLOCK_UPLOAD.get(state, ""),
            "admin": getattr(user, "role", "") == "admin"}


GENERIC_AHEAD_AFTER_S = 5  # 이만큼 기다리게 되면 일반 대기 안내에도 앞선 작업을 적는다


def _queue_ahead(session_factory, job, min_wait_s: float = 0) -> dict:
    """대기 안내용: 대기 중인 `job` 앞에 무엇이 있는지 {"count", "lines"}. 기다린 지 `min_wait_s` 초가 안 됐으면 비어 있다."""
    empty = {"count": 0, "lines": []}
    if job is None or job.status != "pending":
        return empty
    if min_wait_s and (datetime.now() - job.created_at).total_seconds() < min_wait_s:
        return empty
    with session_factory() as db:
        ahead = jobs_ahead(db, job)
    return {"count": len(ahead), "lines": labels.ahead_lines(ahead)} if ahead else empty


def render(request: Request, template: str, status_code: int = 200, **ctx) -> HTMLResponse:
    """템플릿을 그린다. `user`, `labels` 는 항상 넣어 준다."""
    ctx.setdefault("user", getattr(request.state, "user", None))
    ctx.setdefault("labels", labels)
    ctx.setdefault("queue_ahead", functools.partial(_queue_ahead, request.app.state.session_factory))
    ctx.setdefault("nav_ready_count", functools.partial(_intake_ready_count, request.app.state.session_factory, ctx["user"]))
    ctx.setdefault("ai_off", not request.app.state.settings.ai_worker_enabled)  # 대기 작업이 시작되지 않는다: 화면은 계속 다시 불러오지 않는다
    if request.method == "GET" and status_code < 400:
        ctx.setdefault("flash", notices.from_request(request))  # 결과 알림 (app/notices.py): 맨 위 한 줄
    ai_cache: dict = {}

    def cached_ai_status():
        """상단 줄과 동작 곁 안내가 한 번만 조회하도록 요청마다 한 번 저장한다."""
        if "v" not in ai_cache:
            ai_cache["v"] = _ai_status(request.app.state.session_factory, request.app.state.settings, ctx["user"])
        return ai_cache["v"]

    ctx.setdefault("ai_status", cached_ai_status)
    ctx.setdefault("ai_gate", lambda: ai_gate(cached_ai_status(), ctx["user"]))  # 템플릿: {% set aig = ai_gate() %}, 매크로 ai_note(aig)
    return request.app.state.templates.TemplateResponse(request, template, ctx, status_code=status_code)


def _same_screen(request: Request) -> str:
    """AI 를 시작하지 못하고 돌려보낼 "같은 화면": 보낸 화면(Referer)의 경로, 없거나 다른 서버면 홈."""
    ref = request.headers.get("referer", "")
    parts = urlsplit(ref)
    if ref and parts.netloc == request.url.netloc and parts.path.startswith("/") and not parts.path.startswith("//"):
        return urlunsplit(("", "", parts.path, parts.query, parts.fragment))
    return "/"


def _ai_off_handler(request: Request, exc: AIOff) -> Response:
    view = getattr(request.scope.get("endpoint"), "ai_off_view", None)  # 쓴 글이 있는 라우터는 글을 채운 같은 폼을 돌려준다 (gate.keeps_text)
    if view is not None and exc.items and not request.headers.get("HX-Request"):
        try:
            response = view(request, exc)
        except Exception:  # noqa: BLE001  (글을 되돌리지 못해도 보통의 돌려보내기로)
            logging.getLogger("app").exception("AI 꺼짐 화면을 그리지 못했습니다 %s", request.url.path)
            response = None
        if response is not None:
            return response
    target = notices.with_notice(_same_screen(request), "ai_off")
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


def _login_required_handler(request: Request, exc: LoginRequired) -> Response:
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


def _must_change_handler(request: Request, exc: MustChangePassword) -> Response:
    """관리자가 정해 준 비밀번호를 아직 안 바꿨다: 비밀번호 바꾸기 화면으로 (다른 화면은 바꾼 뒤에)."""
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": "/account"})
    return RedirectResponse("/account", status_code=303)


def _duplicate_submit_handler(request: Request, exc: guards.DuplicateSubmit) -> Response:
    """같은 폼 표가 다시 제출됐다: 새로 만들지 않고 처음 결과로 (알림 표시는 새로 붙여 성공 알림이 보이게)."""
    return RedirectResponse(notices.refresh(exc.url), status_code=303)


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
    """주소 번호 자리에 글자·소수가 온 GET 화면(/customers/abc)은 다른 없는 주소처럼 "찾을 수 없습니다" 화면으로.
    그 밖의 검증 오류(POST 폼, HTMX 요청)는 FastAPI 기본 응답 그대로다."""
    path_only = bool(exc.errors()) and all(e.get("loc", ())[:1] == ("path",) for e in exc.errors())
    if request.method == "GET" and path_only and not request.headers.get("HX-Request"):
        if request.url.path in GET_FALLBACKS:  # POST 전용 주소가 다른 상세 주소(/activities/{번호})에 걸려 들어왔다: 404 가 아니라 폼으로
            return RedirectResponse(GET_FALLBACKS[request.url.path], status_code=303)
        return _http_error_handler(request, StarletteHTTPException(404, NOT_FOUND_TEXT))
    return await request_validation_exception_handler(request, exc)


NOT_FOUND_TEXT = "항목을 찾을 수 없습니다."
GET_FALLBACKS = {"/activities/raw": "/activities/new", "/activities/draft": "/activities/new"}


def _get_fallback(request: Request) -> str | None:
    """POST 전용 주소를 GET 으로 열었을 때(오류 화면이 그 주소에 떠 있다가 새로 고침·다시 열기) 405 대신 보낼 화면: 같은 일을 하는 폼·목록·상세 화면.
    정해 둔 것(`GET_FALLBACKS`)이 없으면 주소의 뒤쪽을 하나씩 떼며 GET 으로 열리는 첫 화면 (/admin/users/1 → /admin/users, /solutions/5/stage → /solutions/5)."""
    path = request.url.path
    routes = []
    for r in request.app.routes:
        inner = getattr(r, "original_router", None)
        routes.extend(x for x in (inner.routes if inner is not None else [r]) if getattr(x, "methods", None))

    def opens(candidate: str) -> bool:
        return any("GET" in x.methods and x.path_regex.match(candidate) for x in routes if hasattr(x, "path_regex"))

    if GET_FALLBACKS.get(path):
        return GET_FALLBACKS[path]
    parts = path.rstrip("/").split("/")
    while len(parts) > 1:
        parts.pop()
        candidate = "/".join(parts) or "/"
        if opens(candidate):
            return candidate
    return "/"


ERROR_TITLES = {404: "항목을 찾을 수 없습니다", 403: "권한이 없습니다", 400: "요청을 확인해 주세요", 500: "문제가 생겼어요", 409: "저장하지 않았습니다", 503: "잠시 후 다시 시도해 주세요"}


def error_title(status_code: int) -> str:
    """오류 화면 제목. 상태 코드에 맞추고, 모르는 코드는 일반 문구."""
    return ERROR_TITLES.get(status_code, "요청을 처리하지 못했습니다")


def _server_error_handler(request: Request, exc: Exception) -> Response:
    """처리하지 못한 오류: 예외 내용은 로그에만 남기고, 화면에는 친절한 문구만 보인다 (HTMX 요청에는 짧은 글)."""
    logging.getLogger("app").error("처리하지 못한 오류 %s %s", request.method, request.url.path, exc_info=exc)
    message = "서버에서 문제가 생겨 요청을 마치지 못했습니다."
    if request.headers.get("HX-Request"):
        return PlainTextResponse(message, status_code=500)
    try:
        return render(request, "error.html", status_code=500, message=error_title(500), title=error_title(500), code=500)
    except Exception:  # 화면을 그리다 또 실패해도 예외 글은 내보내지 않는다
        return PlainTextResponse(message, status_code=500)


def _operational_error_handler(request: Request, exc: OperationalError) -> Response:
    """쓰기 잠금을 제때 잡지 못한 SQLite 오류(어느 쓰기 단계에서든)는 500 이 아니라 "잠시 후 다시 시도"(503)다. 다른 운영 오류는 500 화면."""
    if guards.is_busy_error(exc):
        logging.getLogger("app").warning("쓰기 잠금 대기 초과 %s %s", request.method, request.url.path)
        return _http_error_handler(request, guards.WriteBusy())
    return _server_error_handler(request, exc)


def _http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """HTTPException 을 브라우저에는 한국어 오류 화면으로, HTMX 요청에는 짧은 글로 돌려준다."""
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else "요청을 처리하지 못했습니다."
    if exc.status_code == 405 and request.method in ("GET", "HEAD") and not request.headers.get("HX-Request"):
        return RedirectResponse(_get_fallback(request) or "/", status_code=303)
    if request.headers.get("HX-Request"):
        return PlainTextResponse(message, status_code=exc.status_code, headers=exc.headers)
    response = render(request, "error.html", status_code=exc.status_code, message=message, title=error_title(exc.status_code), code=exc.status_code)
    for key, value in (exc.headers or {}).items():
        response.headers[key] = value
    return response



def snapshot_loader(templates_dir: Path):
    """templates 폴더의 모든 파일을 지금 읽어 메모리에 둔 Jinja 로더 (실행 중에 폴더가 바뀌어도 그대로)."""
    from jinja2 import DictLoader

    files = {}
    for path in sorted(templates_dir.rglob("*")):
        if path.is_file() and path.suffix in (".html", ".txt", ".xml", ".md", ".j2"):
            files[path.relative_to(templates_dir).as_posix()] = path.read_text(encoding="utf-8")
    return DictLoader(files)

def create_app(settings: Settings) -> FastAPI:
    for d in (settings.data_dir, settings.uploads_dir, settings.knowledge_dir, settings.decks_dir):
        d.mkdir(parents=True, exist_ok=True)

    from app import update as update_lib

    update_lib.remember_boot_commit()  # 지금 돌고 있는 코드의 커밋 (업데이트 뒤 다시 시작했는지 /healthz 가 알린다)
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)
    run_startup_migrations(engine, settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 로그 파일은 서버가 도는 동안만 연다 (끝나면 처리기를 떼고 파일을 닫는다).
        logger, handler, previous_level = _open_log_file(settings)
        with app.state.session_factory() as db:
            rebuild_if_stale(app.state.mirror, db, settings.uploads_dir)
            fail_stale_running(db)
            recover_runs(db, worker_on=settings.ai_worker_enabled)
            recover_intakes(db)
            recover_applying(db)
        start_workers(app)
        scheduler.start(app)
        try:
            yield
        finally:
            scheduler.stop(app)
            stop_workers(app)
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(previous_level)

    app = FastAPI(title="솔박스", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.mirror = KnowledgeMirror(settings.knowledge_dir)
    app.state.runner = ClaudeRunner(settings.claude_cmd, settings.knowledge_dir)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=bool(settings.ssl_certfile),
    )
    app.add_exception_handler(AIOff, _ai_off_handler)
    app.add_exception_handler(LoginRequired, _login_required_handler)
    app.add_exception_handler(MustChangePassword, _must_change_handler)
    app.add_exception_handler(guards.DuplicateSubmit, _duplicate_submit_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(OperationalError, _operational_error_handler)
    app.add_exception_handler(Exception, _server_error_handler)

    static_dir = APP_DIR / "static"
    templates_dir = APP_DIR / "templates"
    static_dir.mkdir(exist_ok=True)
    templates_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.state.templates = Jinja2Templates(directory=templates_dir)
    # 화면 파일을 시작할 때 한 번 모두 읽어 둔다 (베타 Z): 서버가 도는 중에 git pull 로 파일이 바뀌어도
    # 다시 시작할 때까지는 시작 때의 프로그램과 화면이 짝을 이룬다 (옛 코드 + 새 화면 = 500 을 막는다).
    app.state.templates.env.loader = snapshot_loader(templates_dir)
    app.state.templates.env.auto_reload = False
    from app.update import code_changed_on_disk

    app.state.templates.env.globals["code_changed_on_disk"] = code_changed_on_disk
    from app.markdown_lite import render_markdown
    from app.proposals import check_items, check_summary_text, number_sources, deck_is_old, deck_is_stale, display_title, is_group, outline_check_summary, target_name, unresolved_check_count

    app.state.templates.env.globals["display_title"] = display_title
    app.state.templates.env.globals["target_name"] = target_name  # 제안 건의 대상 이름 (특정 고객은 고객 이름, 다수 고객은 대상 그룹 이름)
    app.state.templates.env.globals["is_group"] = is_group
    app.state.templates.env.globals["deck_is_stale"] = deck_is_stale
    app.state.templates.env.globals["deck_is_old"] = deck_is_old
    from app import pdf_export

    def _deck_pdf(p) -> dict:
        """제안서 상자의 PDF 줄: 이 PC 에서 만들 수 있는지, 지금 고객용 파일의 PDF 가 있는지 (베타 AJ)."""
        exists = bool(p.deck_name) and pdf_export.pdf_path(settings.decks_dir / str(p.id) / p.deck_name).is_file()
        return {"can": bool(pdf_export.methods(settings)), "exists": exists}

    app.state.templates.env.globals["deck_pdf"] = _deck_pdf
    from app.deck_gates import counts as _deck_quality_counts

    app.state.templates.env.globals["deck_quality_counts"] = _deck_quality_counts  # 제안서 상자의 "품질 검사: 경고 N건"
    app.state.templates.env.globals["check_items"] = check_items
    app.state.templates.env.globals["number_sources"] = number_sources
    app.state.templates.env.globals["outline_check_summary"] = outline_check_summary
    app.state.templates.env.globals["unresolved_check_count"] = unresolved_check_count
    from app.korean import josa as _josa

    app.state.templates.env.globals["josa"] = _josa
    from app.relations import report_label as _report_label

    app.state.templates.env.globals["report_label"] = _report_label  # 업무 보고 건 이름 ("주간 2026-11-23 ~ 11-29")
    app.state.templates.env.globals["current_sort"] = ui.current_sort
    from app.checks import is_unopened as _is_unopened

    app.state.templates.env.globals["is_unopened"] = _is_unopened
    from app import impact as _impact

    app.state.templates.env.globals["delete_impact"] = _impact.text_for  # 상세 화면 삭제 버튼의 "연결이 끊깁니다" 안내
    from app.intake import is_archived as _intake_archived

    app.state.templates.env.globals["intake_archived"] = _intake_archived
    app.state.templates.env.globals["check_summary_text"] = check_summary_text
    app.state.templates.env.globals["labels"] = labels  # 매크로에서도 쓴다
    app.state.templates.env.globals["ontology"] = ontology  # 대상 이름·속성 이름표·목록은 템플릿도 여기서 읽는다
    app.state.templates.env.globals["ont"] = ontology
    app.state.templates.env.globals["wait_detail"] = labels.wait_detail
    from app.proposals import open_check_slides
    app.state.templates.env.globals["open_check_slides"] = open_check_slides
    app.state.templates.env.globals["kdate"] = lambda d: ui.short_date(d, date.today())  # "11월 24일" (올해가 아니면 연도도)
    app.state.templates.env.globals["kdatetime"] = lambda d: ui.short_datetime(d, date.today())  # "11월 24일 14:30"
    app.state.templates.env.globals["now_ts"] = lambda: int(time.time())  # "새로 고침" 링크가 늘 새 주소가 되게
    app.state.templates.env.globals["ui"] = ui
    app.state.templates.env.globals["rev_of"] = guards.rev_of  # 고치기 폼의 숨은 고침 표시 (app/guards.py)
    app.state.templates.env.globals["new_token"] = guards.new_token  # 만들기 폼의 한 번 쓰는 표
    versions = ui.asset_versions(static_dir)  # 시작할 때 한 번: 파일이 바뀌면 주소가 바뀐다
    app.state.templates.env.globals["asset"] = lambda name: f"/static/{name}?v={versions.get(name, '0')}"
    app.state.templates.env.globals["error_field"] = ui.error_field
    app.state.templates.env.filters["first_line"] = ui.first_line
    app.state.templates.env.filters["kdate_text"] = lambda t: ui.iso_date_text(t, date.today())  # ISO 날짜 글 -> "11월 17일"
    @pass_context
    def md_filter(ctx, text):
        """Markdown 조각. 화면이 출처 조회(`src`)를 가졌으면 지식 사본 경로를 출처 칩으로 바꾼다 (제안 대화 답변)."""
        return render_markdown(text, ctx.get("src"))

    app.state.templates.env.filters["md"] = md_filter

    from app.routers import (
        activities, admin, auth, bms, customers, files, home, intake, jobs, opportunities, partners, projects, proposal_outline,
        follow_ups, proposals, research, search, solutions, solution_fields as solution_fields_router, confirm, help as help_router, account, health, duplicates, reports, strategies, initiatives, workspace_files,
    )  # 지연 임포트: 라우터가 render 를 쓴다

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(home.router)
    app.include_router(admin.router)
    app.include_router(solution_fields_router.router)
    app.include_router(duplicates.admin)
    app.include_router(duplicates.router)  # 고객·파트너 합치기 (/partners/{id}/merge, /customers/{id}/merge)
    app.include_router(partners.router)
    app.include_router(customers.router)
    app.include_router(opportunities.router)
    app.include_router(bms.router)
    app.include_router(strategies.router)
    app.include_router(initiatives.router)
    app.include_router(workspace_files.router)
    app.include_router(projects.router)
    app.include_router(projects.legacy_router)
    app.include_router(solutions.router)
    app.include_router(files.router)
    app.include_router(intake.router)
    app.include_router(jobs.router)
    app.include_router(activities.router)
    app.include_router(activities.follow_up_router)
    app.include_router(follow_ups.router)
    app.include_router(search.router)
    app.include_router(proposals.router)
    app.include_router(proposal_outline.router)
    app.include_router(research.router)
    app.include_router(reports.router)
    app.include_router(help_router.router)
    app.include_router(confirm.router)  # 삭제 확인 화면 (GET …/delete): 기존 POST 라우터와 같은 주소
    return app

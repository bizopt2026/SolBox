"""올리기 화면: 파일 여러 개를 한 번에 올리고, 묶음 목록·상세·원본 내려받기."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy import update
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import FormData

from app import cardfile, cardplan
from app import intake as intake_lib
from app import labels, origin, transcribe, ui
from app.ai import gate
from app.ai.gate import keeps_text, require_ai_on
from app.ai.queue import queue_position
from app import notices
from app.db import get_db
from app.guards import Once, form_once
from app.knowledge import KnowledgeMirror, get_mirror
from app.main import render
from app.models import Activity, Customer, Intake, IntakeFile, Partner, Project, Solution, User
from app.routers._common import get_or_404, paginate
from app.routers.home import ai_problem
from app.security import require_user

RECENT_APPLY_S = 60  # 방금 저장이 끝난 묶음으로 보는 시간 (표 없이 연타한 진 쪽 요청을 성공으로 보여 준다)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/intake")

NOT_YOURS = "올린 사람과 관리자만 볼 수 있습니다."
NOTE_TOO_LONG = f"메모는 {intake_lib.MAX_NOTE}자까지 쓸 수 있습니다."
CANNOT_DISCARD = "저장 중이거나 저장이 끝난 묶음은 버릴 수 없습니다."
_ASCII_UNSAFE = re.compile(r'[^\x20-\x7e]|["\\%]')


def _is_owner(user: User, intake: Intake) -> bool:
    return origin.can_open_intake(user, intake)


def _ensure_owner(user: User, intake: Intake) -> None:
    if not _is_owner(user, intake):
        raise HTTPException(status_code=403, detail=NOT_YOURS)


async def read_form(request: Request) -> FormData:
    """저장 폼 전체 (칸 이름이 정리안 항목마다 달라 고정된 매개변수로 받을 수 없다)."""
    return await request.form()


def get_intake(db: Session, user: User, intake_id: int) -> Intake:
    intake = get_or_404(db, Intake, intake_id)
    _ensure_owner(user, intake)
    return intake


def _position(db: Session, intake: Intake) -> int:
    return queue_position(db, intake.job) if intake.job is not None else 0


def _result_of(intake: Intake, wanted) -> list[dict]:
    """저장 완료 묶음의 결과 중 종류가 맞는 것 (옛 묶음에는 저장하지 않은 파일 기록이 없다)."""
    if intake.status != "applied":
        return []
    return [e for e in intake.result or [] if wanted(e.get("type"))]


def detail_page(request: Request, user: User, intake: Intake, db: Session, notice: str = "",
                skipped: list[str] | None = None, status_code: int = 200, form: FormData | None = None, note_value: str | None = None):
    ready = intake.status == "ready"
    # 엔진이 꺼져 있을 때 올린 음성이 있고 지금은 받아쓸 수 있으면, 다시 정리를 누르라고 안내한다
    can_transcribe = (intake.status not in ("analyzing", "applied", "discarded")
                      and any(f.kind == "audio" and f.read_status == "needs_transcript" for f in intake.files)
                      and transcribe.get(request.app.state.settings).available())
    ctx = {}
    settings = request.app.state.settings
    if cardplan.is_cards(intake):
        ctx["cards_info"] = _cards_info(settings, db, user, intake)
    if ready:
        ctx = dict(
            cards=intake_lib.review_cards(db, intake, form, request.app.state.settings, user),
            choices={
                "solution": list(db.scalars(select(Solution).order_by(Solution.name))),
                "customer": list(db.scalars(select(Customer).order_by(Customer.name))),
                "project": list(db.scalars(select(Project).order_by(Project.name))),
                "partner": list(db.scalars(select(Partner).order_by(Partner.name))),
            },
        )
    return render(
        request, "intake/detail.html", status_code=status_code, user=user, intake=intake, notice=notice, can_transcribe=can_transcribe,
        skipped=skipped or [], note_value=note_value, intake_files_transcribing=intake_lib.transcribing_file(intake),
        position=_position(db, intake), stale_applying=intake_lib.is_stale_applying(intake), max_note=intake_lib.MAX_NOTE,
        plan_summary=intake_lib.plan_summary(intake), outcome=intake_lib.outcome_text(intake),
        saved=_result_of(intake, lambda t: t not in ("skipped", "unsaved")),
        not_saved=_result_of(intake, lambda t: t == "skipped"),
        unsaved_files=_result_of(intake, lambda t: t == "unsaved"),
        **ctx,
    )


def _cards_info(settings, db: Session, user: User, intake: Intake) -> dict:
    """명함 파일 묶음 화면의 덧붙임: 열 지정 단계(`mapping`)이면 머리글·미리보기, 확인 단계면 읽은 열 요약(`plan` 에 적혀 있다), 같은 파일을 이미 가져왔는지."""
    info: dict = {"mapping": None, "earlier": None}
    f = intake.files[0] if intake.files else None
    if intake.status == "uploaded" and f is not None:
        try:
            path = intake_lib.stored_path(settings, f)
            data = path.read_bytes()
            parsed = cardfile.parse(f.original_name, data)
            rows = cardfile.read_rows(f.original_name, data)
            saved = (intake.plan or {}).get("mapping_raw")
            current = {k: int(v) for k, v in saved.items()} if isinstance(saved, dict) else parsed.mapping
            hdr = int((intake.plan or {}).get("header_row", parsed.header_row + 1)) - 1 if saved else parsed.header_row
            if saved is not None:
                parsed.headers = rows[hdr] if 0 <= hdr < len(rows) else parsed.headers
            info["mapping"] = {
                "headers": parsed.headers, "current": current, "header_row": hdr + 1, "fields": cardfile.FIELDS, "required": cardfile.REQUIRED,
                "preview": rows[:cardfile.HEADER_SCAN_ROWS], "scan": cardfile.HEADER_SCAN_ROWS,
                "default_kind": (intake.plan or {}).get("default_kind", "customer"),
            }
        except (OSError, cardfile.CardFileError) as e:
            info["mapping_error"] = str(e)
    if intake.status in ("ready", "uploaded"):
        copies = intake_lib.earlier_copies(db, settings, intake, user)
        info["earlier"] = next(iter(copies.values()), None)
    return info


def upload_context(request: Request, db: Session) -> dict:
    """올리기 입력 영역(`_dropzone.html`)이 쓰는 값: 한도, 음성 받아쓰기 가능 여부, AI 사용 불가 안내."""
    settings = request.app.state.settings
    return dict(max_files=intake_lib.MAX_FILES, max_mb=settings.max_upload_mb, max_note=intake_lib.MAX_NOTE,
                max_paste=intake_lib.MAX_PASTE, max_paste_title=intake_lib.MAX_PASTE_TITLE, transcriber_ready=transcribe.get(settings).available(), ai_problem=ai_problem(db))


def _recent(db: Session, user: User, limit: int = 5) -> list[Intake]:
    """올리기 화면 옆에 보이는 내가 최근 올린 묶음."""
    return list(db.scalars(select(Intake).where(Intake.created_by_id == user.id).order_by(Intake.id.desc()).limit(limit)))


@router.get("/new")
def new_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render(request, "intake/new.html", user=user, error="", note="", recent=_recent(db, user), **upload_context(request, db))


def cards_context(request: Request) -> dict:
    return dict(max_mb=request.app.state.settings.max_upload_mb, max_rows=cardfile.MAX_ROWS)


@router.get("/cards")
def cards_page(request: Request, user: User = Depends(require_user)):
    return render(request, "intake/cards.html", user=user, error="", default_kind="customer", **cards_context(request))


@router.post("/cards")
def cards_create(
    request: Request, file: list[UploadFile | str] = [], default_kind: str = Form("customer"),
    user: User = Depends(require_user), db: Session = Depends(get_db), once: Once = Depends(form_once),
):
    """명함 앱 내보내기 파일(.vcf·.csv·.xlsx) 하나를 읽어 정리안이 있는 묶음을 만든다. AI 를 쓰지 않는다 (작업을 넣지 않는다)."""
    settings = request.app.state.settings
    default_kind = default_kind if default_kind in cardplan.KIND_CHOICES else "customer"

    def again(error: str):
        return render(request, "intake/cards.html", status_code=400, user=user, error=error, default_kind=default_kind, **cards_context(request))

    uploads = [u for u in file if not isinstance(u, str) and (u.filename or "").strip()]
    if not uploads:
        return again("파일을 골라 주세요.")
    if len(uploads) > 1:
        return again("명함 파일은 한 번에 하나만 가져올 수 있어요.")
    name = intake_lib.clean_name(uploads[0].filename or "")
    if not name.lower().endswith(cardfile.EXTENSIONS):
        return again("명함 파일은 .vcf, .csv, .xlsx 만 가져올 수 있어요.")
    limit_mb = settings.max_upload_mb
    try:
        intake, _skipped = intake_lib.create_intake(db, settings, user, f"명함 파일: {name}"[:intake_lib.MAX_NOTE], uploads, source=cardplan.SOURCE)
    except ValueError as e:
        if str(e) == intake_lib.NO_FILES:
            return again(f"파일을 올리지 못했어요. 크기 제한({limit_mb}MB)을 넘었거나 비어 있는 파일일 수 있어요.")
        return again(str(e))
    f = intake.files[0]
    try:
        parsed = cardfile.parse(f.original_name, intake_lib.stored_path(settings, f).read_bytes())
    except (cardfile.CardFileError, OSError) as e:
        intake_lib.purge_new(db, settings, intake)
        return again(str(e) if isinstance(e, cardfile.CardFileError) else "파일을 읽지 못했어요.")
    f.read_status = "ok"
    if parsed.needs_mapping:
        intake.plan = {"source": cardplan.SOURCE, "default_kind": default_kind}
        db.commit()
        return once.done(notices.redirect(f"/intake/{intake.id}", "cards_need_map"))
    intake.plan = cardplan.build_plan(db, intake, parsed, default_kind)
    intake.status = "ready"
    db.commit()
    return once.done(notices.redirect(f"/intake/{intake.id}", "cards_read"))


@router.post("/{intake_id}/cards-map")
def cards_map(
    intake_id: int, request: Request, form: FormData = Depends(read_form),
    user: User = Depends(require_user), db: Session = Depends(get_db), once: Once = Depends(form_once),
):
    """열 지정 단계: 사용자가 고른 열로 명함 파일을 다시 읽어 정리안을 만든다. 이미 정리안이 있으면(연타) 그대로 확인 화면으로."""
    intake = get_intake(db, user, intake_id)
    if not cardplan.is_cards(intake) or intake.status != "uploaded" or not intake.files:
        return once.done(notices.redirect(f"/intake/{intake.id}", "not_ready"))
    settings = request.app.state.settings
    f = intake.files[0]
    default_kind = form.get("default_kind", "customer")
    default_kind = default_kind if default_kind in cardplan.KIND_CHOICES else "customer"

    def again(message: str):
        return detail_page(request, user, intake, db, notice=message, status_code=400)

    try:
        data = intake_lib.stored_path(settings, f).read_bytes()
        rows = cardfile.read_rows(f.original_name, data)
        header = form.get("header_row", "")
        hdr = int(header) - 1 if str(header).isascii() and str(header).isdecimal() else 0
        if not 0 <= hdr < min(len(rows), cardfile.HEADER_SCAN_ROWS):
            return again("머리글 줄 번호가 올바르지 않습니다.")
        mapping = cardfile.build_mapping(rows[hdr], {k[len("map_"):]: v for k, v in form.items() if k.startswith("map_") and isinstance(v, str)})
        missing = [cardfile.FIELDS[k] for k in cardfile.REQUIRED if k not in mapping]
        if missing:
            return again(f"{', '.join(missing)} 열을 골라 주세요.")
        used = list(mapping.values())
        if len(set(used)) != len(used):
            return again("같은 열을 두 칸에 고를 수 없어요.")
        parsed = cardfile.parse(f.original_name, data, mapping=mapping, header_row=hdr)
    except (cardfile.CardFileError, OSError) as e:
        return again(str(e) if isinstance(e, cardfile.CardFileError) else "파일을 읽지 못했어요.")
    plan = cardplan.build_plan(db, intake, parsed, default_kind)
    done = db.execute(update(Intake).where(Intake.id == intake.id, Intake.status == "uploaded", Intake.source == cardplan.SOURCE)
                      .values(status="ready", plan=plan))
    db.commit()
    db.refresh(intake)
    if done.rowcount != 1:
        return once.done(notices.redirect(f"/intake/{intake.id}", "not_ready"))
    return once.done(notices.redirect(f"/intake/{intake.id}", "cards_read"))


@router.post("/{intake_id}/cards-remap")
def cards_remap(intake_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """확인 대기 중인 명함 묶음을 열 지정 단계로 되돌린다 (읽은 열이 틀렸을 때). 지금의 열 지정은 기본값으로 남는다."""
    intake = get_intake(db, user, intake_id)
    plan = intake.plan or {}
    if cardplan.is_cards(intake) and intake.status == "ready":
        mapping_raw = {m["field"]: m["index"] for m in plan.get("mapping", []) if "field" in m and m.get("index") is not None}
        db.execute(update(Intake).where(Intake.id == intake.id, Intake.status == "ready", Intake.source == cardplan.SOURCE)
                   .values(status="uploaded", plan={"source": cardplan.SOURCE, "default_kind": plan.get("default_kind", "customer"),
                                                    "mapping_raw": mapping_raw, "header_row": plan.get("header_row", 1)}))
        db.commit()
        db.refresh(intake)
    return notices.redirect(f"/intake/{intake.id}", "cards_remap")


@router.post("")
def create(
    request: Request, files: list[UploadFile | str] = [], note: str = Form(""),
    pasted_text: str = Form(""), pasted_title: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db), once: Once = Depends(form_once),
):
    settings = request.app.state.settings

    def again(error: str):
        return render(request, "intake/new.html", status_code=400, user=user, error=error, note=note,
                      pasted_text=pasted_text, pasted_title=pasted_title,
                      recent=_recent(db, user), **upload_context(request, db))

    if len(note.strip()) > intake_lib.MAX_NOTE:
        return again(NOTE_TOO_LONG)
    if len(pasted_text.replace("\r\n", "\n").replace("\r", "\n")) > intake_lib.MAX_PASTE:
        return again(intake_lib.PASTE_TOO_LONG)
    try:
        intake, skipped = intake_lib.create_intake(db, settings, user, note, files, pasted_text, pasted_title)
    except ValueError as e:
        return again(str(e))
    try:
        intake_lib.prepare_workdir(settings, intake)
    except Exception:  # noqa: BLE001 - 묶음은 이미 저장되었다. 정리를 시작할 때 작업 폴더를 다시 준비한다
        db.rollback()
        log.exception("올리기 묶음 %s 작업 폴더 준비 실패", intake.id)
    ai_off = gate.ai_is_off(request)  # 처리기가 꺼져 있으면 정리 작업을 넣지 않는다 (끝나지 않는 대기 줄이 생기지 않게). 파일은 보관되고 "올림" 상태로 남는다
    if not ai_off:
        try:
            intake_lib.start_analysis(db, intake, user)
        except Exception:  # noqa: BLE001 - 묶음은 이미 저장되었다. 상세 화면의 "정리 시작" 으로 다시 할 수 있다
            db.rollback()
            log.exception("올리기 묶음 %s 정리 시작 실패", intake.id)
    if skipped:
        return once.done(detail_page(request, user, intake, db, skipped=skipped), url=f"/intake/{intake.id}")
    return once.done(notices.redirect(f"/intake/{intake.id}", "intake_kept_ai_off" if ai_off else "intake_uploaded"))


@router.get("")
def list_page(request: Request, status: str = "", page: str = "1",
              user: User = Depends(require_user), db: Session = Depends(get_db)):
    scope = [] if user.role == "admin" else [Intake.created_by_id == user.id]
    # 구분 탭의 건수: 내가 볼 수 있는 묶음 전체를 상태별로 한 번에 센다
    counts = dict(db.execute(select(Intake.status, func.count()).where(*scope).group_by(Intake.status)).all())
    stmt = select(Intake).where(*scope).options(selectinload(Intake.files))
    if status in labels.INTAKE_STATUSES:
        stmt = stmt.where(Intake.status == status)
    else:
        status = ""
    items = list(db.scalars(stmt.order_by(Intake.id.desc())))
    owners = {u.id: (u.name or u.username) for u in db.scalars(select(User))} if user.role == "admin" else {}
    tabs = ui.seg_tabs(request, "status", list(labels.INTAKE_STATUSES.items()), counts, status, total=sum(counts.values()))
    return render(request, "intake/list.html", user=user, pg=paginate(items, page), status=status, owners=owners, tabs=tabs)


@router.get("/files/{file_id}/download")
def download(file_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    f = get_or_404(db, IntakeFile, file_id)
    if not _is_owner(user, f.intake):  # 활동 기록에 연결된 원본은 그 기록을 볼 수 있는 로그인 사용자 누구나 내려받는다
        if not (f.target_type == "activity" and f.target_id is not None and db.get(Activity, f.target_id) is not None):
            raise HTTPException(status_code=403, detail=NOT_YOURS)
    path = intake_lib.stored_path(request.app.state.settings, f)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    fallback = _ASCII_UNSAFE.sub("_", f.original_name) or "file"
    return FileResponse(
        path, media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(f.original_name, safe='')}"},
    )


@router.get("/{intake_id}")
def detail(intake_id: int, request: Request,
           user: User = Depends(require_user), db: Session = Depends(get_db)):
    return detail_page(request, user, get_intake(db, user, intake_id), db)


@router.get("/{intake_id}/status")
def status(intake_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """정리 중 안내 조각 (HTMX). 끝나면(정리 중이 아니게 되면) 상세 화면으로 보낸다."""
    intake = db.get(Intake, intake_id)
    if intake is None:  # 그 사이 지워졌다. 조회를 멈추고 목록으로 보낸다.
        if user.role != "admin":  # 남의 묶음과 없는 묶음을 구별할 수 없게 같은 응답을 준다
            raise HTTPException(status_code=403, detail=NOT_YOURS)
        return Response("올리기 묶음이 없습니다.", headers={"HX-Redirect": "/intake"})
    _ensure_owner(user, intake)
    if intake.status == "analyzing" and intake_lib.finish_failed_analysis(db, intake.id):
        db.refresh(intake)  # 작업이 끝났는데 기록되지 않은 채 남은 묶음을 바로잡았다
    if intake.status != "analyzing":
        return Response("정리가 끝났습니다.", headers={"HX-Redirect": f"/intake/{intake.id}"})
    return render(request, "intake/_status.html", intake=intake, job=intake.job, position=_position(db, intake),
                  intake_files_transcribing=intake_lib.transcribing_file(intake))


def _analyze_ai_off(request: Request, exc):
    """AI 꺼짐: 고친 메모는 (정리를 시작하지 않고) 묶음에 저장해 둔다 — 일은 하되 작업만 안 넣는다. 알림 `ai_off` 와 함께 같은 묶음 화면으로."""
    note = exc.first("note", None)
    if note is None:
        return None
    with request.app.state.session_factory() as db:
        intake = db.get(Intake, request.path_params.get("intake_id"))
        user = request.state.user
        if intake is not None and _is_owner(user, intake) and intake.status not in ("analyzing", "applied", "discarded"):
            if len(note.strip()) > intake_lib.MAX_NOTE:  # 보통 경로와 같은 응답 (메모를 말없이 버리지 않는다)
                return detail_page(request, user, intake, db, notice=NOTE_TOO_LONG, status_code=400, note_value=note)
            intake.note = note.strip()
            db.commit()
    return notices.redirect(f"/intake/{request.path_params.get('intake_id')}", "ai_off")


@router.post("/{intake_id}/analyze", dependencies=[Depends(require_ai_on)])
@keeps_text(_analyze_ai_off)
def analyze(
    intake_id: int, request: Request, note: str | None = Form(None),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """메모를 고치고 다시 정리한다."""
    intake = get_intake(db, user, intake_id)
    if cardplan.is_cards(intake):
        raise HTTPException(status_code=400, detail="명함 파일 묶음은 AI 로 정리하지 않습니다. 열을 다시 고르려면 화면의 \"열 다시 고르기\"를 쓰세요.")
    if intake.status in ("applied", "discarded"):
        raise HTTPException(status_code=400, detail="이미 끝난 묶음은 다시 정리할 수 없습니다.")
    if intake.status != "analyzing":
        if note is not None and len(note.strip()) > intake_lib.MAX_NOTE:
            return detail_page(request, user, intake, db, notice=NOTE_TOO_LONG, status_code=400, note_value=note)
        intake_lib.ensure_workdir(request.app.state.settings, intake)  # 준비가 덜 끝난 묶음은 여기서 다시 준비한다
        intake_lib.start_analysis(db, intake, user, note=note.strip() if note is not None else None)  # 메모는 작업과 한 트랜잭션
    return notices.redirect(f"/intake/{intake.id}", "intake_analyzing")


@router.post("/{intake_id}/apply")
def apply(
    intake_id: int, request: Request, form: FormData = Depends(read_form),
    user: User = Depends(require_user), db: Session = Depends(get_db),
    mirror: KnowledgeMirror = Depends(get_mirror), once: Once = Depends(form_once),
):
    """정리안을 확인한 대로 저장한다. 한 묶음은 한 번만 저장된다. 같은 폼 표로 다시 온 제출(연타)은 처음 저장의 성공 결과로 보낸다."""
    once.sticky = False  # 저장 상태(`applying`→실패하면 `ready`)를 이 라우터가 스스로 되돌린다: 선점 커밋 뒤 실패해도 같은 표로 다시 저장할 수 있어야 한다
    intake = get_intake(db, user, intake_id)
    try:
        intake_lib.apply(db, request.app.state.settings, mirror, intake, user, form)
    except intake_lib.Superseded:
        return notices.redirect(f"/intake/{intake.id}", "superseded")
    except intake_lib.NotReady:
        # 표 없이 온 연타·옛 화면의 진 쪽 요청: 방금(1분 안에) 저장이 끝난 묶음이면 성공으로 보여 준다 (실패처럼 읽히는 문구로 덮지 않는다)
        if intake.status == "applied":
            recent = intake.applied_at is not None and (datetime.now() - intake.applied_at).total_seconds() <= RECENT_APPLY_S
            return notices.redirect(f"/intake/{intake.id}", "intake_applied" if recent else "already_applied")
        return notices.redirect(f"/intake/{intake.id}", "not_ready")
    except ValueError as e:  # 입력 오류: 입력값을 그대로 둔 채 화면을 다시 보여 준다
        return detail_page(request, user, intake, db, notice=str(e), status_code=400, form=form)
    except intake_lib.ApplyFailed:  # 오류 문구는 묶음에 적혀 확인 화면에 보인다
        return detail_page(request, user, intake, db, status_code=500, form=form)
    return once.done(notices.redirect(f"/intake/{intake.id}", "intake_applied"))


@router.post("/{intake_id}/discard")
def discard(intake_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """묶음을 버린다. 올린 원본은 지우지 않는다."""
    intake = get_intake(db, user, intake_id)
    if not intake_lib.discard(db, request.app.state.settings, intake):
        raise HTTPException(status_code=400, detail=CANNOT_DISCARD)
    return notices.redirect(f"/intake/{intake.id}", "intake_discarded")


@router.post("/{intake_id}/recheck")
def recheck(intake_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """오래 저장 중으로 멈춘 묶음을 확인 대기로 되돌린다 (아직 저장 중인 묶음은 그대로 둔다)."""
    intake = get_intake(db, user, intake_id)
    intake_lib.recheck_applying(db, intake)
    return notices.redirect(f"/intake/{intake.id}", "intake_rechecked")

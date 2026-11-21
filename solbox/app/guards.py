"""동시 작업에서 기록을 지키는 두 장치와 "누가 고쳤나" 기록.

1. 고침 표시(revision stamp): 고치기 폼은 숨은 칸 `_rev` 로 "이 기록의 신원과 마지막 변경" 을 가져온다. 저장할 때 지금 기록과 다르면
   저장하지 않고 폼을 다시 보여 준다 (`Conflict`).
   - 앞부분은 신원(표 이름·번호·만든 때): 다르면 "삭제되고 같은 번호로 새 기록이 만들어졌다" (번호는 다시 쓰인다) → 강제 저장 없음.
   - 뒷부분은 마지막 변경(`updated_at`): 다르면 "다른 곳에서 바뀌었다" → 사용자가 "그래도 내 내용으로 저장" 을 누르면 `_force` 로 저장한다.
   - 표시가 아예 없으면(업그레이드 전에 열어 둔 화면, 브라우저가 아닌 클라이언트) 지금처럼 한 번 받아 준다. 값을 바꿔 보내도 바꾼 사람만 불리하다.
   모든 쓰기가 `updated_at` 을 올려야 표시가 믿을 만하다: ORM 변경은 아래 flush 처리기가 한곳에서 올리고(연결 표만 바뀐 경우, 후속 조치·고객 니즈 줄이
   바뀐 경우의 활동 기록 포함), 코어 UPDATE 는 열의 `onupdate` 가 올린다.
2. 한 번 쓰는 폼 표(`_tok`): 만들기 폼은 그릴 때 새 표를 숨은 칸에 싣고, 처음 제출이 표를 차지한다 (`form_tokens` 표). 같은 표가 다시 오면
   (연타·동시 제출·"뒤로 가서 다시") 새로 만들지 않고 처음 결과로 돌려보낸다. 표는 사용자마다 따로고 오래되면 지운다.
3. 고친 사람: 요청의 DB 세션에 `info["user_id"]` 를 두고(`require_user`), flush 처리기가 바뀐 기록의 `updated_by_id` 를 채운다."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import NamedTuple

from fastapi import Depends, Form, HTTPException, Request
from sqlalchemy import event, select, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, object_session

from app import idmarks  # noqa: F401  (지운 번호를 다시 쓰지 않는 번호 처리기를 단다)
from app.models import Activity, Customer, CustomerNeed, FollowUp, FormToken, Partner, Proposal, Stamped, User
from app.db import get_db
from app.security import require_user

log = logging.getLogger(__name__)

REV_FIELD = "_rev"
FORCE_FIELD = "_force"
TOKEN_FIELD = "_tok"

CHANGED_ELSEWHERE = "다른 곳에서 이 기록이 바뀌었습니다. 아래 내용은 저장하지 않았고, 쓰신 내용은 그대로 남겨 두었습니다."
REPLACED = ("이 기록은 삭제되고 같은 번호로 새 기록이 만들어졌습니다. 다른 기록이므로 저장하지 않았고, 쓰신 내용은 그대로 남겨 두었습니다. "
            "필요한 내용은 복사해 두고 현재 기록을 확인해 주세요.")


# ---- 고침 표시 ----

def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _iso(value) -> str:
    return value.isoformat() if value is not None else ""


def _change_of(obj, model=None) -> str:
    """마지막 변경을 나타내는 글. 보통 `updated_at`. 제안 건은 AI 작업이 `updated_at` 을 자주 올리므로 제안 대상 폼이 다루는 값 자체로 본다.
    사용자 계정은 `updated_at` 이 없어 값으로 본다. (`model`: 기록이 아니라 표에서 읽은 줄을 볼 때 그 줄의 모델)"""
    model = model or type(obj)
    if issubclass(model, Proposal):
        return json.dumps([obj.customer_id, obj.project_id, obj.opportunity_id, obj.request_memo, list(obj.extra_solution_ids or [])], ensure_ascii=False)
    if issubclass(model, User):
        return json.dumps([obj.name, obj.role, bool(obj.is_active), obj.password_hash], ensure_ascii=False)
    return _iso(getattr(obj, "updated_at", None))


def rev_of(obj, model=None) -> str:
    """기록의 고침 표시: "<신원>.<마지막 변경>" (뜻을 알 수 없는 짧은 글). `model` 은 `_change_of` 와 같다."""
    model = model or type(obj)
    ident = f"{model.__tablename__}:{obj.id}:{_iso(getattr(obj, 'created_at', None))}"
    return f"{_h(ident)}.{_h(_change_of(obj, model))}"


def slide_rev(proposal: Proposal, slide: dict) -> str:
    """구성안 슬라이드 하나의 고침 표시: 제안 건의 신원 + 폼이 다루는 칸의 내용 (확인 표시 `checked` 는 빼고, 순서 번호도 넣지 않는다:
    다른 탭이 장을 옮기거나 지워서 그 번호의 장이 달라졌으면 내용이 달라져 걸린다)."""
    from app.proposals import slide_to_form

    ident = f"proposals:{proposal.id}:{_iso(proposal.created_at)}"
    return f"{_h(ident)}.{_h(json.dumps([slide.get('kind'), slide_to_form(slide)], ensure_ascii=False, sort_keys=True))}"


class Conflict(NamedTuple):
    kind: str  # "changed" 다른 곳에서 바뀜 / "replaced" 같은 번호의 다른 기록
    rev: str  # 사용자가 가져온(옛) 표시: 다시 그리는 폼이 그대로 다시 보낸다
    message: str
    diff: tuple = ()  # 저장된 내용과 쓴 내용이 다른 칸 [(칸 이름표, 저장된 값 발췌)] (`diff_fields`). 비교할 수 없는 폼은 빈 목록

    @property
    def can_force(self) -> bool:
        return self.kind == "changed"


class Rev(NamedTuple):
    value: str
    force: bool


async def rev_form(request: Request) -> Rev:
    """고치기 POST 의 의존성: 폼의 `_rev`·`_force` (없으면 빈 글자·거짓). 다른 칸 읽기와 같은 요청 본문을 쓴다."""
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001  (폼이 아니면 표시 없음)
        return Rev("", False)
    value = form.get(REV_FIELD)
    return Rev(value if isinstance(value, str) else "", bool(form.get(FORCE_FIELD)))


class StaleWrite(HTTPException):
    """검사를 통과한 뒤 저장하는 사이에 다른 곳에서 같은 기록이 바뀌었다: 저장하지 않는다."""

    def __init__(self):
        super().__init__(status_code=409, detail="저장하는 사이에 다른 곳에서 이 기록이 바뀌었습니다. 저장하지 않았으니 화면을 새로 열어 다시 고쳐 주세요.")


LOCK_BUSY_TEXT = "다른 사람이 저장하는 중이라 지금은 저장하지 못했습니다. 잠시 후 다시 시도해 주세요. 저장하지 않았으니 입력한 내용은 브라우저의 뒤로 가기로 다시 볼 수 있습니다."


class WriteBusy(HTTPException):
    """쓰기 잠금을 제때 잡지 못했다 (다른 쓰기가 오래 걸린다): 저장하지 않았다. 충돌이 아니므로 잠시 뒤 다시 하면 된다 (503)."""

    def __init__(self):
        super().__init__(status_code=503, detail=LOCK_BUSY_TEXT, headers={"Retry-After": "5"})


_SQLITE_BUSY_SNAPSHOT = 517  # SQLITE_BUSY_SNAPSHOT: 읽기 시작 뒤 다른 쓰기가 커밋됐다 (읽은 내용이 더는 최신이 아니다)


_SQLITE_BUSY, _SQLITE_LOCKED = 5, 6


def is_busy_error(exc: BaseException) -> bool:
    """SQLite 의 "잠겨 있음"(BUSY·LOCKED 와 그 하위 코드, 스냅숏이 낡은 경우 포함)인 `OperationalError` 인가. 다른 운영 오류(깨진 DB, 문법 …)는 아니다."""
    if not isinstance(exc, OperationalError):
        return False
    code = getattr(exc.orig, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF in (_SQLITE_BUSY, _SQLITE_LOCKED)
    return any(w in str(exc.orig).lower() for w in ("database is locked", "database table is locked", "database is busy"))


def lock_failure(exc: OperationalError) -> HTTPException:
    """쓰기 잠금 단계에서 난 `OperationalError` 를 사용자에게 줄 오류로. 읽은 뒤 다른 쓰기가 커밋된 경우(스냅숏이 낡음)만 바뀐 것(`StaleWrite`),
    잠금 대기 시간 초과 같은 나머지는 충돌이 아니므로 "잠시 후 다시"(`WriteBusy`)."""
    if getattr(exc.orig, "sqlite_errorcode", None) == _SQLITE_BUSY_SNAPSHOT:
        return StaleWrite()
    return WriteBusy()


EXCERPT_LEN = 60


def _text_of(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v) for v in value)
    return str(value).replace("\r\n", "\n").strip()


def _excerpt(text: str) -> str:
    """저장된 값의 짧은 발췌: 첫 줄 앞부분 (길면 줄임표, 줄이 더 있으면 줄임표)."""
    first, _, rest = text.partition("\n")
    out = first[:EXCERPT_LEN].rstrip()
    return out + ("…" if len(first) > EXCERPT_LEN or rest.strip() else "")


def diff_fields(obj, typed: dict | None, stored: dict | None = None, labels: dict | None = None) -> tuple:
    """저장된 기록과 사용자가 쓴 값이 다른 칸: [(칸 이름표, 저장된 값 발췌)]. `typed` = {칸 이름: 쓴 값}. 칸 이름표는 온톨로지 속성 이름표(`labels` 로 바꿀 수 있다).
    저장된 값은 기록의 같은 이름 속성(`stored` 가 주어지면 그 {칸 이름: 값}). 이름표가 없는 칸(연결 번호 `…_id` 따위)은 비교하지 않는다.
    코드 칸(발굴 단계 …)은 코드 이름으로 보인다. 비교는 앞뒤 공백·줄바꿈 모양을 무시한다."""
    from app import ontology

    if not typed:
        return ()
    if labels is None:
        code = ontology.type_for_model(type(obj))
        labels = ontology.TYPES[code].props
    else:
        code = None
    out = []
    for field, value in typed.items():
        label = labels.get(field)
        if label is None:
            continue
        current = stored.get(field) if stored is not None else getattr(obj, field, None)
        a, b = _text_of(current), _text_of(value)
        if a == b:
            continue
        key = ontology.CONFLICT_CODE_FIELDS.get((code, field))
        shown = ontology.CODE_LISTS[key].items.get(a, a) if key and a else a
        out.append((label, _excerpt(shown) if shown else "(비어 있음)"))
    return tuple(out)


def check_rev(obj, rev: Rev, current: str | None = None, fresh=None, values: dict | None = None, stored: dict | None = None, labels: dict | None = None) -> Conflict | None:
    """제출된 표시가 지금 기록과 맞는지: 맞거나 표시가 없으면 None, 아니면 충돌. (`current` 는 기록 말고 다른 단위의 표시를 비교할 때: 슬라이드,
    그때 `fresh(줄)` 은 DB 에서 막 읽은 줄로 같은 표시를 다시 계산하는 함수. `values` 는 사용자가 쓴 {칸 이름: 값}: 충돌일 때 저장된 내용과 다른 칸을 `Conflict.diff` 에 싣는다.)
    통과하면 "저장 직전 다시 확인" 을 세션에 걸어 둔다: 첫 쓰기 flush 때 `_verify_checks` 가 쓰기 잠금을 잡은 뒤 DB 의 표시를 다시 읽어, 검사와 저장 사이에
    다른 곳이 바꿨으면 `StaleWrite`(409)로 저장을 막는다. 모든 고치기 라우터가 이 함수를 쓰므로 한곳에서 지켜진다."""
    submitted = rev.value.strip()
    if not submitted:
        return None
    current = current or rev_of(obj)
    if submitted == current:
        _arm(obj, submitted, fresh, identity_only=False)
        return None
    same_record = submitted.partition(".")[0] == current.partition(".")[0]
    if not same_record:
        return Conflict("replaced", submitted, REPLACED)
    if rev.force:
        _arm(obj, submitted.partition(".")[0], fresh, identity_only=True)  # 강제 저장: 같은 기록이기만 하면 된다
        return None
    return Conflict("changed", submitted, CHANGED_ELSEWHERE, diff_fields(obj, values, stored, labels))


def _arm(obj, expected: str, fresh, identity_only: bool) -> None:
    session = object_session(obj)
    if session is None or not sa_inspect(obj).persistent:
        return
    session.info.setdefault("rev_checks", []).append((obj, expected, fresh, identity_only))


def _row_view(model, row) -> SimpleNamespace:
    """표에서 읽은 줄을 기록처럼 읽을 수 있게 한다 (열 이름 -> 모델 속성 이름: 프로젝트의 `business_id` 가 `project_id` 인 식)."""
    mapper = sa_inspect(model)
    return SimpleNamespace(**{mapper.get_property_by_column(col).key: row._mapping[col] for col in model.__table__.columns})


def _verify_checks(session: Session) -> None:
    """첫 쓰기 flush 직전: 걸어 둔 검사마다 쓰기 잠금을 잡고(내용을 바꾸지 않는 UPDATE) 그 트랜잭션 안에서 줄을 다시 읽어 표시를 견준다."""
    checks = session.info.pop("rev_checks", None)
    if not checks:
        return
    conn = session.connection()
    for obj, expected, fresh, identity_only in checks:
        model = type(obj)
        table = model.__table__
        try:
            conn.execute(text(f'UPDATE "{table.name}" SET id = id WHERE id = :id'), {"id": obj.id})  # 쓰기 잠금: 이 뒤로는 다른 쓰기가 끼어들지 못한다
            row = conn.execute(select(table).where(table.c.id == obj.id)).one_or_none()
        except OperationalError as e:  # 읽은 뒤 다른 쓰기가 커밋됐으면 바뀐 것, 잠금 대기 초과면 "잠시 후 다시"
            raise lock_failure(e) from None
        if row is None:
            raise StaleWrite()
        try:
            view = _row_view(model, row)
            now = fresh(view) if fresh is not None else rev_of(view, model)
        except Exception:  # noqa: BLE001  (줄이 달라져 표시를 못 만들면 바뀐 것)
            raise StaleWrite() from None
        if identity_only:
            now = now.partition(".")[0]
        if now != expected:
            raise StaleWrite()


def touch(obj, user: User | None = None) -> None:
    """이 기록이 바뀌었다고 표시한다 (칸 값이 그대로여도 연결·줄이 바뀐 경우). 고친 사람도 적는다."""
    obj.updated_at = datetime.now()
    if user is not None and hasattr(obj, "updated_by_id"):
        obj.updated_by_id = user.id


# ---- flush 처리기: 고친 때·고친 사람 ----

@event.listens_for(Session, "before_flush")
def _stamp_changes(session: Session, flush_context, instances) -> None:
    _verify_checks(session)
    uid = session.info.get("user_id")
    now = datetime.now()
    parents: set[int] = set()

    def mark(obj) -> None:
        obj.updated_at = now
        if uid is not None:
            obj.updated_by_id = uid

    for obj in list(session.dirty):
        if isinstance(obj, Stamped):
            if session.is_modified(obj):
                mark(obj)
                if isinstance(obj, Partner):  # 같은 회사 연결은 파트너 쪽에 저장되지만 고객 화면에도 나온다: 옛·새 고객의 고친 표시도 올린다
                    hist = sa_inspect(obj).attrs.customer_id.history
                    for customer_id in {*hist.added, *hist.deleted} - {None}:
                        customer = session.get(Customer, customer_id)
                        if customer is not None and customer not in session.deleted:
                            mark(customer)
        elif isinstance(obj, (FollowUp, CustomerNeed)) and session.is_modified(obj):
            parents.add(obj.activity_id)
    for obj in [*session.new, *session.deleted]:
        if isinstance(obj, (FollowUp, CustomerNeed)) and obj.activity_id is not None:
            parents.add(obj.activity_id)
        elif isinstance(obj, (FollowUp, CustomerNeed)) and obj.activity is not None and obj.activity.id is not None:
            parents.add(obj.activity.id)
    for activity_id in parents:
        activity = session.get(Activity, activity_id)
        if activity is not None and activity not in session.deleted and activity not in session.new:
            mark(activity)


# ---- 한 번 쓰는 폼 표 ----

TOKEN_KEEP = timedelta(hours=1)  # 이 안에 같은 표가 다시 오면 처음 결과로 보낸다
TOKEN_WAIT_S = 30.0  # 처음 제출이 아직 끝나지 않았을 때 기다리는 시간


class DuplicateSubmit(Exception):
    """이미 처리한 제출: 처음 결과 주소로 돌려보낸다."""

    def __init__(self, url: str):
        super().__init__(url)
        self.url = url


def new_token() -> str:
    return secrets.token_urlsafe(12)


def purge_tokens(db: Session, now: datetime | None = None) -> int:
    """오래된 폼 표를 지운다 (매일 작업과 표를 차지할 때). 지운 수."""
    cutoff = (now or datetime.now()) - TOKEN_KEEP
    deleted = db.query(FormToken).filter(FormToken.created_at < cutoff).delete()
    db.commit()
    return deleted


def _fallback_url(path: str) -> str:
    """결과 주소를 모를 때(저장은 끝났는데 뒤 처리에서 예외가 난 경우) 같은 표로 다시 오면 보내는 곳: 만들기 주소의 목록 화면."""
    parts = [p for p in path.split("/") if p]
    while len(parts) > 1 and parts[-1] in {"raw", "draft", "topics", "apply"}:
        parts.pop()
    return "/" + "/".join(parts)


_ABANDONED: set[tuple[int, str]] = set()  # 잠금 때문에 풀지 못한 표 (사용자, 표): 같은 표로 다시 오면 이어받는다 (멈춘 표가 남지 않게)


class Once:
    """한 번 제출된 폼 하나. 처음 제출이 `claim` 으로 차지하고, 끝나면 `done(응답)` 으로 결과 주소를 남긴다 (끝내지 못하면 표를 풀어 다시 낼 수 있게).
    같은 표라도 폼 내용(`digest`)이 다르면 새 제출이다 (뒤로 가서 고쳐 낸 경우). 기록을 커밋한 뒤에는 표를 풀지 않는다 (`keep`)."""

    def __init__(self, factory, user_id: int, token: str, digest: str = "", fallback: str = "/"):
        self.factory, self.user_id, self.token, self.digest, self.fallback = factory, user_id, token, digest, fallback
        self.claimed = False
        self.finished = False
        self.committed = False
        self.sticky = True  # 커밋한 뒤 예외가 나도 표를 풀지 않는다. 저장 상태를 스스로 되돌리는 라우터(올리기 저장)만 False 로 둔다

    def claim(self) -> None:
        if not self.token or len(self.token) > 120:
            self.token = ""  # 표가 없는 제출(옛 화면·브라우저가 아닌 클라이언트)은 지금처럼 받는다
            return
        deadline = time.monotonic() + TOKEN_WAIT_S
        while True:
            with self.factory() as db:
                purge_tokens(db)
                try:
                    db.add(FormToken(user_id=self.user_id, token=self.token, body_hash=self.digest))
                    db.commit()
                    self.claimed = True
                    return
                except IntegrityError:
                    db.rollback()
                row = db.get(FormToken, (self.user_id, self.token))
                url = row.result_url if row is not None else None
                old_hash = row.body_hash if row is not None else None
                if row is not None and not url and (self.user_id, self.token) in _ABANDONED:
                    # 잠금 때문에 풀지 못하고 끝난 제출이 남긴 표: 이 제출이 이어받는다 (처음 제출은 이미 끝났고 저장하지 못했다)
                    taken = db.query(FormToken).filter(
                        FormToken.user_id == self.user_id, FormToken.token == self.token, FormToken.result_url.is_(None),
                    ).update({"body_hash": self.digest, "created_at": datetime.now()})
                    db.commit()
                    _ABANDONED.discard((self.user_id, self.token))
                    if taken:
                        self.claimed = True
                        return
                    continue
                if row is not None and url and old_hash and old_hash != self.digest:
                    # 같은 표인데 내용이 바뀌었다: 새 제출이다. 끝난 줄을 이 제출이 이어받는다 (그 사이 다른 제출이 먼저 이어받았으면 다시 돈다)
                    taken = db.query(FormToken).filter(
                        FormToken.user_id == self.user_id, FormToken.token == self.token,
                        FormToken.result_url == url, FormToken.body_hash == old_hash,
                    ).update({"result_url": None, "body_hash": self.digest, "created_at": datetime.now()})
                    db.commit()
                    if taken:
                        self.claimed = True
                        return
                    continue
            if row is not None and url:
                raise DuplicateSubmit(url)
            if row is not None and time.monotonic() > deadline:
                raise HTTPException(status_code=409, detail="같은 내용을 처리하는 중입니다. 잠시 뒤 목록에서 확인해 주세요.")
            time.sleep(0.05)  # 처음 제출이 끝나길 기다린다 (실패해 표를 풀면 다음 바퀴에서 내가 차지한다)

    def _mine(self, db):
        return db.query(FormToken).filter(FormToken.user_id == self.user_id, FormToken.token == self.token, FormToken.body_hash == self.digest)

    def done(self, response, url: str | None = None):
        """성공한 응답(보통 303)의 이동 주소를 표에 적는다 (이동이 아닌 성공 화면이면 `url` 로 따로 준다). 응답을 그대로 돌려준다."""
        url = url or response.headers.get("location")
        if self.claimed and url:
            try:
                with self.factory() as db:
                    self._mine(db).update({"result_url": url})
                    db.commit()
                self.finished = True
            except OperationalError as e:  # 기록은 저장됐다: 표 글이 잠겨 못 적어도 응답은 보낸다 (표는 `keep` 이 다시 적어 본다)
                if not is_busy_error(e):
                    raise
                log.warning("폼 표 결과를 적지 못했습니다 (잠금)")
        return response

    def keep(self) -> None:
        """기록을 이미 커밋했는데 뒤 처리에서 예외가 났다: 표를 풀면 다시 낼 때 같은 기록이 또 생기므로 목록 주소로 끝낸 것으로 남긴다."""
        if self.claimed and not self.finished:
            try:
                with self.factory() as db:
                    self._mine(db).filter(FormToken.result_url.is_(None)).update({"result_url": self.fallback})
                    db.commit()
                self.finished = True
            except OperationalError as e:  # 잠겨 있으면 표는 그대로 둔다 (기록이 이미 있으므로 풀지 않는 쪽이 안전하다: 같은 표는 기다린 뒤 409)
                if not is_busy_error(e):
                    raise
                log.warning("폼 표를 끝내지 못했습니다 (잠금)")

    def release(self) -> None:
        if self.claimed and not self.finished:
            try:
                with self.factory() as db:
                    self._mine(db).filter(FormToken.result_url.is_(None)).delete()
                    db.commit()
            except OperationalError as e:  # 잠금 때문에 못 풀었다: 표가 멈춰 남지 않게 기억해 두고, 같은 표로 다시 오면 이어받는다 (`claim`)
                if not is_busy_error(e):
                    raise
                _ABANDONED.add((self.user_id, self.token))
                log.warning("폼 표를 풀지 못했습니다 (잠금): 같은 표로 다시 내면 이어받습니다")
            self.claimed = False


async def form_digest(request: Request) -> str:
    """폼 내용의 해시 (표 칸 제외). 올린 파일은 이름과 크기만 넣어 내용을 다시 읽지 않는다."""
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001  (폼이 아니면 내용 없음)
        return ""
    parts = []
    for key, value in form.multi_items():
        if key == TOKEN_FIELD:
            continue
        parts.append([key, value] if isinstance(value, str) else [key, "file", getattr(value, "filename", "") or "", getattr(value, "size", 0) or 0])
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


def form_once(request: Request, tok: str = Form("", alias=TOKEN_FIELD), digest: str = Depends(form_digest),
              user: User = Depends(require_user), db: Session = Depends(get_db)) -> Iterator[Once]:
    """만들기 POST 의 의존성: 같은 표·같은 내용이 이미 처리됐으면 처음 결과로 돌려보내고(`DuplicateSubmit`), 아니면 이 제출이 표를 차지한다
    (같은 표라도 내용이 바뀌었으면 새 제출). 라우터는 성공했을 때 `return once.done(응답)` 한다. 검증 오류로 폼을 다시 그리거나 예외가 나면 표를 풀어
    같은 폼으로 다시 낼 수 있다. 단 요청의 세션이 커밋한 뒤에는 풀지 않는다 (이미 기록이 있다: 뒤 처리 예외로 중복이 생기지 않게)."""
    tok = tok.strip()
    once = Once(request.app.state.session_factory, user.id, f"{request.url.path}|{tok}"[:120] if tok else "", digest, _fallback_url(request.url.path))  # 같은 폼 표를 다른 주소(원문 저장 / AI 정리)에 쓰면 서로 막지 않는다
    once.claim()

    def _on_commit(_session) -> None:
        once.committed = True

    wrote = []

    def _on_flush(_session, _ctx) -> None:
        wrote.append(True)

    event.listen(db, "after_commit", _on_commit)
    event.listen(db, "after_flush", _on_flush)
    try:
        yield once
    finally:
        event.remove(db, "after_commit", _on_commit)
        event.remove(db, "after_flush", _on_flush)
        if once.committed and once.sticky:
            once.keep()
        else:
            if wrote:  # 쓰다가 멈춘 요청이 쓰기 잠금을 쥔 채면 표를 풀 때 잠긴다: 먼저 되돌린다 (쓰지 않았으면 기록을 만료시키지 않는다: 오류 화면이 사용자를 읽는다)
                db.rollback()
                try:
                    db.refresh(user)  # 되돌리면 만료된다: 오류 화면이 이 사용자를 읽을 수 있게 다시 읽어 둔다
                except Exception:  # noqa: BLE001
                    pass
            once.release()

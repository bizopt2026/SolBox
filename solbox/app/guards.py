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
import secrets
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import NamedTuple

from fastapi import Depends, Form, HTTPException, Request
from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Activity, Customer, CustomerNeed, FollowUp, FormToken, Partner, Proposal, Stamped, User
from app.security import require_user

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


def _change_of(obj) -> str:
    """마지막 변경을 나타내는 글. 보통 `updated_at`. 제안 건은 AI 작업이 `updated_at` 을 자주 올리므로 제안 대상 폼이 다루는 값 자체로 본다.
    사용자 계정은 `updated_at` 이 없어 값으로 본다."""
    if isinstance(obj, Proposal):
        return json.dumps([obj.customer_id, obj.project_id, obj.opportunity_id, obj.request_memo, list(obj.extra_solution_ids or [])], ensure_ascii=False)
    if isinstance(obj, User):
        return json.dumps([obj.name, obj.role, bool(obj.is_active), obj.password_hash], ensure_ascii=False)
    return _iso(getattr(obj, "updated_at", None))


def rev_of(obj) -> str:
    """기록의 고침 표시: "<신원>.<마지막 변경>" (뜻을 알 수 없는 짧은 글)."""
    ident = f"{type(obj).__tablename__}:{obj.id}:{_iso(getattr(obj, 'created_at', None))}"
    return f"{_h(ident)}.{_h(_change_of(obj))}"


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


def check_rev(obj, rev: Rev, current: str | None = None) -> Conflict | None:
    """제출된 표시가 지금 기록과 맞는지: 맞거나 표시가 없으면 None, 아니면 충돌. (`current` 는 기록 말고 다른 단위의 표시를 비교할 때: 슬라이드)"""
    submitted = rev.value.strip()
    if not submitted:
        return None
    current = current or rev_of(obj)
    if submitted == current:
        return None
    same_record = submitted.partition(".")[0] == current.partition(".")[0]
    if not same_record:
        return Conflict("replaced", submitted, REPLACED)
    if rev.force:
        return None
    return Conflict("changed", submitted, CHANGED_ELSEWHERE)


def touch(obj, user: User | None = None) -> None:
    """이 기록이 바뀌었다고 표시한다 (칸 값이 그대로여도 연결·줄이 바뀐 경우). 고친 사람도 적는다."""
    obj.updated_at = datetime.now()
    if user is not None and hasattr(obj, "updated_by_id"):
        obj.updated_by_id = user.id


# ---- flush 처리기: 고친 때·고친 사람 ----

@event.listens_for(Session, "before_flush")
def _stamp_changes(session: Session, flush_context, instances) -> None:
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


class Once:
    """한 번 제출된 폼 하나. 처음 제출이 `claim` 으로 차지하고, 끝나면 `done(응답)` 으로 결과 주소를 남긴다 (끝내지 못하면 표를 풀어 다시 낼 수 있게)."""

    def __init__(self, factory, user_id: int, token: str):
        self.factory, self.user_id, self.token = factory, user_id, token
        self.claimed = False
        self.finished = False

    def claim(self) -> None:
        if not self.token or len(self.token) > 120:
            self.token = ""  # 표가 없는 제출(옛 화면·브라우저가 아닌 클라이언트)은 지금처럼 받는다
            return
        deadline = time.monotonic() + TOKEN_WAIT_S
        while True:
            with self.factory() as db:
                purge_tokens(db)
                try:
                    db.add(FormToken(user_id=self.user_id, token=self.token))
                    db.commit()
                    self.claimed = True
                    return
                except IntegrityError:
                    db.rollback()
                row = db.get(FormToken, (self.user_id, self.token))
                url = row.result_url if row is not None else None
            if row is not None and url:
                raise DuplicateSubmit(url)
            if row is not None and time.monotonic() > deadline:
                raise HTTPException(status_code=409, detail="같은 내용을 처리하는 중입니다. 잠시 뒤 목록에서 확인해 주세요.")
            time.sleep(0.05)  # 처음 제출이 끝나길 기다린다 (실패해 표를 풀면 다음 바퀴에서 내가 차지한다)

    def done(self, response, url: str | None = None):
        """성공한 응답(보통 303)의 이동 주소를 표에 적는다 (이동이 아닌 성공 화면이면 `url` 로 따로 준다). 응답을 그대로 돌려준다."""
        url = url or response.headers.get("location")
        if self.claimed and url:
            with self.factory() as db:
                row = db.get(FormToken, (self.user_id, self.token))
                if row is not None:
                    row.result_url = url
                    db.commit()
            self.finished = True
        return response

    def release(self) -> None:
        if self.claimed and not self.finished:
            with self.factory() as db:
                db.query(FormToken).filter(FormToken.user_id == self.user_id, FormToken.token == self.token,
                                           FormToken.result_url.is_(None)).delete()
                db.commit()
            self.claimed = False


def form_once(request: Request, tok: str = Form("", alias=TOKEN_FIELD), user: User = Depends(require_user)) -> Iterator[Once]:
    """만들기 POST 의 의존성: 같은 표가 이미 처리됐으면 처음 결과로 돌려보내고(`DuplicateSubmit`), 아니면 이 제출이 표를 차지한다.
    라우터는 성공했을 때 `return once.done(응답)` 한다. 검증 오류로 폼을 다시 그리거나 예외가 나면 표를 풀어 같은 폼으로 다시 낼 수 있다."""
    tok = tok.strip()
    once = Once(request.app.state.session_factory, user.id, f"{request.url.path}|{tok}"[:120] if tok else "")  # 같은 폼 표를 다른 주소(원문 저장 / AI 정리)에 쓰면 서로 막지 않는다
    once.claim()
    try:
        yield once
    finally:
        once.release()

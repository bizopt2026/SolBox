"""AI 처리기가 꺼진 서버에서 AI 작업을 새로 만들지 못하게 하는 한 곳.

대기 작업은 처리기가 돌아야 시작된다. 꺼진 서버에 작업을 넣으면 끝나지 않는 대기 줄만 생기므로:
- `require_ai_on` : AI 작업을 시작하는 POST 라우터가 받는 의존성. 아무것도 쓰기 전에 거절한다 (`AIOff`).
- `before_flush` 처리기 : 요청 세션(`db.info["ai_off"]`, `db.get_db` 가 단다)에서 새 `AIJob` 이 들어오려 하면 거절한다 (빠뜨린 곳이 있어도 대기 줄은 생기지 않는다).
- `main._ai_off_handler` 가 `AIOff` 를 같은 화면으로 돌려보내며 알림 `ai_off` 를 붙인다.
스케줄러·작업자의 세션은 요청 세션이 아니므로 이 처리기에 걸리지 않는다 (스케줄러는 꺼져 있으면 스스로 시작하지 않는다)."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import AIJob, User
from app.security import require_user


class AIOff(Exception):
    """AI 작업 처리기가 꺼져 있어 작업을 시작할 수 없다. `items` 는 그때 제출된 폼의 글 칸 (이름, 값) — 쓴 글을 잃지 않게 되돌려 줄 때 쓴다."""

    def __init__(self, items: list[tuple[str, str]] | None = None):
        super().__init__()
        self.items = items or []

    def first(self, name: str, default: str = "") -> str:
        return next((v for k, v in self.items if k == name), default)


def ai_is_off(source) -> bool:
    """처리기가 꺼져 있나. `source` 는 요청(`request.app`)이거나 앱 자신 (스케줄러). 시험은 이 한 함수를 바꿔 끼워 기본으로 열어 둔다."""
    app = getattr(source, "app", source)
    return not app.state.settings.ai_worker_enabled


async def require_ai_on(request: Request, user: User = Depends(require_user)) -> None:
    """로그인(`require_user`)을 먼저 본다: 로그인하지 않은 요청은 이 문 앞에서 로그인 화면으로 간다 (AI 꺼짐 알림이 아니라).
    관리자만 쓰는 라우터는 `dependencies=[Depends(require_admin), Depends(require_ai_on)]` 로 권한 검사를 이 문보다 앞에 둔다."""
    if ai_is_off(request):  # 모듈 이름으로 부른다 (시험이 이 한 함수를 바꿔 끼운다)
        items: list[tuple[str, str]] = []
        try:
            form = await request.form()
            items = [(k, v) for k, v in form.multi_items() if isinstance(v, str)]
        except Exception:  # noqa: BLE001  (폼이 아니면 글 없음)
            pass
        raise AIOff(items)


def keeps_text(view: Callable) -> Callable:
    """쓴 글이 있는 AI 라우터 표시: 꺼짐일 때 알림만 붙여 돌려보내면 입력한 글을 잃으므로, 이 라우터는 `view(request, AIOff)` 가
    돌려주는 응답(보통 쓴 글을 채운 같은 폼)을 보낸다 (`main._ai_off_handler`). `view` 가 None 을 주면 보통의 돌려보내기."""
    def deco(fn: Callable) -> Callable:
        fn.ai_off_view = view
        return fn
    return deco


@event.listens_for(Session, "before_flush")
def _refuse_jobs_when_off(session: Session, flush_context, instances) -> None:
    if session.info.get("ai_off") and any(isinstance(o, AIJob) for o in session.new):
        raise AIOff()

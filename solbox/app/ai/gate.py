"""AI 처리기가 꺼진 서버에서 AI 작업을 새로 만들지 못하게 하는 한 곳.

대기 작업은 처리기가 돌아야 시작된다. 꺼진 서버에 작업을 넣으면 끝나지 않는 대기 줄만 생기므로:
- `require_ai_on` : AI 작업을 시작하는 POST 라우터가 받는 의존성. 아무것도 쓰기 전에 거절한다 (`AIOff`).
- `before_flush` 처리기 : 요청 세션(`db.info["ai_off"]`, `db.get_db` 가 단다)에서 새 `AIJob` 이 들어오려 하면 거절한다 (빠뜨린 곳이 있어도 대기 줄은 생기지 않는다).
- `main._ai_off_handler` 가 `AIOff` 를 같은 화면으로 돌려보내며 알림 `ai_off` 를 붙인다.
스케줄러·작업자의 세션은 요청 세션이 아니므로 이 처리기에 걸리지 않는다 (스케줄러는 꺼져 있으면 스스로 시작하지 않는다)."""
from __future__ import annotations

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import AIJob


class AIOff(Exception):
    """AI 작업 처리기가 꺼져 있어 작업을 시작할 수 없다."""


def ai_is_off(source) -> bool:
    """처리기가 꺼져 있나. `source` 는 요청(`request.app`)이거나 앱 자신 (스케줄러). 시험은 이 한 함수를 바꿔 끼워 기본으로 열어 둔다."""
    app = getattr(source, "app", source)
    return not app.state.settings.ai_worker_enabled


def require_ai_on(request: Request) -> None:
    if ai_is_off(request):  # 모듈 이름으로 부른다 (시험이 이 한 함수를 바꿔 끼운다)
        raise AIOff()


@event.listens_for(Session, "before_flush")
def _refuse_jobs_when_off(session: Session, flush_context, instances) -> None:
    if session.info.get("ai_off") and any(isinstance(o, AIJob) for o in session.new):
        raise AIOff()

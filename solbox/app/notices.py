"""결과 알림 (한 곳): 저장·삭제·단계 변경 같은 POST 가 끝나면 다음 화면 맨 위에 보이는 한 줄.
라우터는 `redirect(주소, 코드)` 로 돌려보내기만 하고, 화면은 `main.render` 가 `?notice=<코드>` 를 이 표에서 찾아 그린다.
- 글은 여기 표에만 있다 (한국어). 주소로 오는 것은 코드뿐이라 사용자가 쓴 글은 비치지 않고, 모르는 코드는 무시한다.
- 값이 붙는 코드는 `종류.값` (예: `stage.tapping`): 값은 온톨로지의 코드 목록에 있을 때만 이름으로 바뀐다 (없으면 무시).
- 오류 색의 알림(`error`)은 같은 표에서 `tone` 만 다르다."""
from __future__ import annotations

from typing import Callable, NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi.responses import RedirectResponse

PARAM = "notice"


class Notice(NamedTuple):
    tone: str  # "ok" 성공 / "warn" 주의 / "error" 막힘
    text: str


def _ro(word: str) -> str:
    """"'(단계 이름)'으로" / "'(단계 이름)'로": 받침이 있으면 으로, 없거나 ㄹ 받침이면 로."""
    last = word[-1:] if word else ""
    if "가" <= last <= "힣":
        final = (ord(last) - 0xAC00) % 28
        return "로" if final in (0, 8) else "으로"
    return "으로"


NOTICES: dict[str, Notice] = {c: Notice(t, m) for c, (t, m) in {
    # 만들기·고치기·지우기
    "created": ("ok", "등록했습니다."),
    "saved": ("ok", "저장했습니다."),
    "deleted": ("ok", "삭제했습니다."),
    # 연결·자료
    "same_company": ("ok", "같은 회사로 연결했습니다."),
    "files_uploaded": ("ok", "자료를 올렸습니다."),
    "file_kind": ("ok", "자료 종류를 바꿨습니다."),
    "file_deleted": ("ok", "자료를 삭제했습니다."),
    "linked_project": ("ok", "기존 프로젝트에 연결했습니다."),
    "converted": ("ok", "프로젝트로 전환했습니다."),
    # 올리기
    "intake_uploaded": ("ok", "파일을 올렸습니다."),
    "intake_analyzing": ("ok", "정리를 시작했습니다."),
    "intake_applied": ("ok", "정리안을 저장했습니다."),
    "intake_discarded": ("ok", "이 묶음을 버렸습니다. 올린 원본 파일은 남아 있습니다."),
    "intake_rechecked": ("ok", "다시 확인할 수 있게 풀었습니다."),
    "not_ready": ("error", "이미 저장했거나 저장할 수 없는 상태입니다."),
    # 제안
    "direction_selected": ("ok", "방향을 골랐습니다."),
    "deck_built": ("ok", "제안서 파일을 만들었습니다."),
    "target_saved": ("ok", "제안 대상을 저장했습니다."),
    "slide_saved": ("ok", "슬라이드를 저장했습니다."),
    "slide_added": ("ok", "슬라이드를 추가했습니다."),
    "slide_moved": ("ok", "슬라이드 순서를 바꿨습니다."),
    "slide_deleted": ("ok", "슬라이드를 삭제했습니다."),
    "busy": ("warn", "이미 AI 작업이 진행 중입니다."),
    # 리서치·관리
    "research_started": ("ok", "조사를 시작했습니다. 끝나면 목록에 나타납니다."),
    "research_retry": ("ok", "다시 조사를 시작했습니다."),
    "research_busy": ("warn", "이미 조사가 진행 중입니다."),
    "no_topics": ("warn", "사용 중인 리서치 주제가 없습니다."),
    "ai_check": ("ok", "AI 점검을 시작했습니다."),
    "backup_done": ("ok", "백업을 만들었습니다."),
    "candidate_registered": ("ok", "솔루션 후보로 등록했습니다. 내용을 확인해 저장하세요."),
    "already_registered": ("warn", "이미 등록된 솔루션이라 그 화면으로 왔습니다."),
}.items()}


def _stage_notice(label_key: str, lead: str) -> Callable[[str], Notice | None]:
    def make(value: str) -> Notice | None:
        from app import ontology

        name = ontology.CODE_LISTS[label_key].items.get(value)
        return Notice("ok", f"{lead} '{name}'{_ro(name)} 바꿨습니다.") if name else None
    return make


PARAM_NOTICES: dict[str, Callable[[str], Notice | None]] = {
    "stage": _stage_notice("stage", "발굴 단계를"),
    "opportunity_stage": _stage_notice("opportunity_stage", "영업기회 단계를"),
}


def lookup(code: str) -> Notice | None:
    """주소의 코드 하나를 알림으로: 모르거나 값이 맞지 않으면 None (그냥 무시)."""
    if not isinstance(code, str) or not code or len(code) > 60:
        return None
    if code == "superseded":
        from app import intake

        return Notice("error", intake.SAVE_SUPERSEDED)
    if "." in code:
        kind, _, value = code.partition(".")
        make = PARAM_NOTICES.get(kind)
        return make(value) if make else None
    return NOTICES.get(code)


def dismiss_url(request) -> str:
    """알림 닫기 링크: 지금 화면에서 `notice` 만 뺀 주소 (스크립트 없이 동작)."""
    rest = [(k, v) for k, v in request.query_params.multi_items() if k != PARAM]
    return request.url.path + (f"?{urlencode(rest)}" if rest else "")


def from_request(request) -> dict | None:
    """화면에 그릴 알림 {"tone", "text", "dismiss"} 또는 None. GET 화면에서만 쓴다."""
    found = lookup(request.query_params.get(PARAM, ""))
    if found is None or not found.text:
        return None
    return {"tone": found.tone, "text": found.text, "dismiss": dismiss_url(request)}


def with_notice(url: str, code: str) -> str:
    """주소에 `notice` 를 덧붙인다 (이미 있는 질의와 `#조각` 은 그대로 둔다)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != PARAM]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query + [(PARAM, code)]), parts.fragment))


def redirect(url: str, code: str) -> RedirectResponse:
    """POST 가 끝난 뒤 `url` 로 303 으로 돌려보내며 알림 코드를 붙인다."""
    return RedirectResponse(with_notice(url, code), status_code=303)

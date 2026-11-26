"""결과 알림 (한 곳): 저장·삭제·단계 변경 같은 POST 가 끝나면 다음 화면 맨 위에 보이는 한 줄.
라우터는 `redirect(주소, 코드)` 로 돌려보내기만 하고, 화면은 `main.render` 가 `?notice=<코드>` 를 이 표에서 찾아 그린다.
- 글은 여기 표에만 있다 (한국어). 주소로 오는 것은 코드뿐이라 사용자가 쓴 글은 비치지 않고, 모르는 코드는 무시한다.
- 값이 붙는 코드는 `종류.값` (예: `stage.tapping`): 값은 온톨로지의 코드 목록에 있을 때만 이름으로 바뀐다 (없으면 무시).
- 오류 색의 알림(`error`)은 같은 표에서 `tone` 만 다르다.
- 알림은 한 번만 보인다 (스크립트 없이): 돌려보내는 주소에 짧은 시각 표시 `&nt=<초>` 를 함께 붙이고, 화면은 30초 안의 표시가 있을 때만 알림을 그린다.
  옛 주소(북마크·히스토리)를 다시 열면 일어나지 않은 일의 알림이 뜨지 않는다. 비밀이 필요 없다: 알림은 해로울 것 없는 글이고, 틀린 표시는 알림을 못 보게 할 뿐이다."""
from __future__ import annotations

import time
from typing import Callable, NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi.responses import RedirectResponse

PARAM = "notice"
STAMP_PARAM = "nt"
TTL_S = 30  # 알림이 붙은 주소가 이만큼 지나면 알림을 그리지 않는다
SKEW_S = 5  # 서버 시계가 조금 어긋나도 받아 주는 폭


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
    "merged": ("ok", "두 기록을 하나로 합쳤습니다. 합치기 전 백업을 만들어 두었고, 사라진 이름은 아래 '다른 이름' 에 남았습니다."),
    "merge_gone": ("warn", "합칠 기록 중 하나가 이미 없습니다. 다른 사람이 먼저 합쳤거나 지웠을 수 있어요. 아무것도 바꾸지 않았습니다."),
    "dup_dismissed": ("ok", "다른 회사로 표시했습니다. 이 쌍은 더 이상 후보로 보이지 않습니다 (아래 '다른 회사로 표시한 쌍' 에서 되돌릴 수 있어요)."),
    "dup_undismissed": ("ok", "다시 후보로 볼 수 있게 되돌렸습니다."),
    "dup_ai_started": ("ok", "AI 확인을 시작했습니다. 이름 목록만 보내며, 끝나면 후보에 'AI' 로 표시되어 나옵니다."),
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
    "cards_read": ("ok", "명함 파일을 읽었습니다. AI 는 쓰지 않았어요. 아래 정리안을 확인한 뒤 저장하세요."),
    "cards_need_map": ("warn", "이름이나 회사 열을 찾지 못했습니다. 어느 열인지 직접 골라 주세요."),
    "cards_remap": ("ok", "열을 다시 고를 수 있습니다."),
    "already_applied": ("warn", "이미 저장된 묶음입니다."),
    "not_ready": ("error", "이 묶음은 지금 저장할 수 없는 상태입니다. 화면을 새로 고쳐 상태를 확인해 주세요."),
    # 제안
    "direction_selected": ("ok", "방향을 골랐습니다."),
    "deck_built": ("ok", "제안서 파일을 만들었습니다."),
    "deck_old": ("warn", "이 제안서 파일은 고객용·내부 검토용으로 나누기 전에 만든 것이라 내려받을 수 없습니다. 아래에서 '다시 만들기'를 눌러 주세요."),
    "target_saved": ("ok", "제안 대상을 저장했습니다."),
    "slide_saved": ("ok", "슬라이드를 저장했습니다."),
    "slide_added": ("ok", "슬라이드를 추가했습니다."),
    "slide_moved": ("ok", "슬라이드 순서를 바꿨습니다."),
    "slide_deleted": ("ok", "슬라이드를 삭제했습니다."),
    "fu_mine": ("ok", "내 할 일로 바꿨습니다."),
    "busy": ("warn", "이미 AI 작업이 진행 중입니다."),
    # 업무 보고
    "report_created": ("ok", "보고를 만들었습니다. 숫자는 지금 기록에서 바로 센 값입니다."),
    "report_exists": ("warn", "같은 기간의 보고가 이미 있어 그 보고로 왔습니다."),
    "report_checked": ("ok", "점검 결과를 저장했습니다. 미완료 항목은 다음 계획 맨 앞에 넘어가 있습니다."),
    "report_planned": ("ok", "계획을 저장했습니다."),
    "report_planned_unlinked": ("warn", "계획을 저장했습니다. 등록된 고객 하나와 정확히 맞지 않는 고객 이름은 연결하지 않았습니다."),
    "report_confirmed": ("ok", "계획을 확정했습니다. 보고서 숫자가 지금 값으로 고정됐습니다."),
    "report_unconfirmed": ("ok", "확정을 풀었습니다. 숫자는 다시 지금 기록에서 셉니다."),
    "report_unconfirm_blocked": ("warn", "확정을 풀지 못했습니다. 아래 안내를 확인해 주세요."),
    # 리서치·관리
    "research_started": ("ok", "조사를 시작했습니다. 끝나면 목록에 나타납니다."),
    "research_retry": ("ok", "다시 조사를 시작했습니다."),
    "research_busy": ("warn", "이미 조사가 진행 중입니다."),
    "no_topics": ("warn", "사용 중인 리서치 주제가 없습니다."),
    "field_created": ("ok", "분야를 더했습니다."),
    "field_renamed": ("ok", "분야 이름을 저장했습니다."),
    "field_moved": ("ok", "분야 순서를 바꿨습니다."),
    "field_on": ("ok", "분야를 다시 쓰도록 했습니다."),
    "field_off": ("ok", "분야 사용을 껐습니다. 이미 붙은 솔루션은 그대로이고 새로 고를 수는 없습니다."),
    "field_deleted": ("ok", "분야를 지웠습니다."),
    "ai_check": ("ok", "AI 점검을 시작했습니다."),
    "keywords_started": ("ok", "AI 추천을 시작했습니다. 끝나면 아래 카드에 나옵니다."),
    "ai_off": ("error", "AI 작업 처리기가 꺼져 있어 시작하지 못했습니다. 관리자에게 문의해 주세요. 직접 입력하고 고치는 일은 그대로 할 수 있습니다."),
    "created_ai_off": ("warn", "등록했습니다. AI 작업 처리기가 꺼져 있어 방향 만들기는 시작하지 않았습니다. 처리기가 켜진 뒤 \"방향 만들기\"를 눌러 주세요."),
    "intake_kept_ai_off": ("warn", "파일을 보관했습니다. AI 작업 처리기가 꺼져 있어 정리는 시작하지 않았습니다. 처리기가 켜진 뒤 \"정리 시작\"을 눌러 주세요."),
    "password_changed": ("ok", "비밀번호를 바꿨습니다. 다른 곳에서 로그인한 상태는 끝났습니다."),
    "weak_password": ("warn", "지금 쓰는 비밀번호가 새 규칙(8자 이상 등)에 맞지 않습니다. 아래에서 새 비밀번호로 바꿔 주세요."),
    "backup_done": ("ok", "백업을 만들었습니다."),
    "update_checked": ("ok", "업데이트를 확인했어요. 결과는 '시스템 업데이트' 카드에 있어요."),
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
    rest = [(k, v) for k, v in request.query_params.multi_items() if k not in (PARAM, STAMP_PARAM)]
    return request.url.path + (f"?{urlencode(rest)}" if rest else "")


def fresh(stamp, now: float | None = None) -> bool:
    """`nt` 표시가 방금(30초 안) 붙은 것인가. 없거나 숫자가 아니거나 너무 오래됐으면 False."""
    if not isinstance(stamp, str) or not stamp.isascii() or not stamp.isdecimal() or len(stamp) > 12:
        return False
    age = (time.time() if now is None else now) - int(stamp)
    return -SKEW_S <= age <= TTL_S


def from_request(request) -> dict | None:
    """화면에 그릴 알림 {"tone", "text", "dismiss"} 또는 None. GET 화면에서만 쓴다. 시각 표시가 없거나 오래됐으면 그리지 않는다."""
    if not fresh(request.query_params.get(STAMP_PARAM)):
        return None
    found = lookup(request.query_params.get(PARAM, ""))
    if found is None or not found.text:
        return None
    return {"tone": found.tone, "text": found.text, "dismiss": dismiss_url(request)}


def inline(code: str, dismiss: str) -> dict | None:
    """다시 그리는 화면(POST 응답)에 바로 보일 알림: `from_request` 와 같은 모양. 코드를 모르면 None."""
    found = lookup(code)
    return {"tone": found.tone, "text": found.text, "dismiss": dismiss} if found is not None else None


def with_notice(url: str, code: str) -> str:
    """주소에 `notice` 와 시각 표시 `nt` 를 덧붙인다 (이미 있는 질의와 `#조각` 은 그대로 둔다)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in (PARAM, STAMP_PARAM)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query + [(PARAM, code), (STAMP_PARAM, str(int(time.time())))]), parts.fragment))


def refresh(url: str) -> str:
    """이미 알림이 붙은 주소의 시각 표시를 지금으로 새로 한다 (같은 제출이 다시 와서 처음 결과로 보낼 때). 알림이 없으면 그대로."""
    parts = urlsplit(url)
    code = dict(parse_qsl(parts.query, keep_blank_values=True)).get(PARAM)
    return with_notice(url, code) if code else url


def redirect(url: str, code: str) -> RedirectResponse:
    """POST 가 끝난 뒤 `url` 로 303 으로 돌려보내며 알림 코드를 붙인다."""
    return RedirectResponse(with_notice(url, code), status_code=303)

"""솔박스 온톨로지의 단일 정의. 대상 유형, 코드 값 목록, 관계, 지식 사본 경로 해석을 여기 한 곳에 둔다.

다른 코드는 이 모듈을 읽기만 한다 (`labels.py` 는 호환을 위해 이름을 다시 내보낼 뿐이다). 모델 클래스는 늦게 불러오므로
`app.models` 가 이 모듈의 상수를 써도 순환 참조가 생기지 않는다.

이름 공간: 같은 코드가 여러 곳에 쓰이므로 목록마다 이름을 붙였다.
- 발굴 단계 `stage` 의 "intake"(아이템 접수) 와 대상 유형 `TYPES["intake"]`(올리기 묶음)
- 활동 종류 `activity_kind` 의 "partner"(파트너 미팅) 와 대상 유형 `TYPES["partner"]`
- 발굴 단계 "tapping"(사전 태핑) 과 활동 종류 "tapping"(고객 태핑)
저장된 값은 바꾸지 않는다. 물리 표·열 이름도 그대로이고, 코드의 이름과 다르면 `ObjectType.physical` 에 적는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple

# ======================================================================================
# 코드 값 목록 (각각 한 번만 정의한다)
# ======================================================================================


@dataclass(frozen=True)
class CodeList:
    key: str
    label: str  # 이 목록의 한국어 이름
    items: dict[str, str]  # 코드 -> 화면 이름
    default: str | None = None
    descriptions: dict[str, str] = field(default_factory=dict)  # 코드 -> 한 문장 뜻 (툴팁·범례·도움말이 같은 글을 쓴다. 앱이 하는 일만 적는다)


STAGES = {  # 발굴 단계
    "intake": "아이템 접수",
    "tapping": "사전 태핑",
    "survey": "수요조사",
    "boxed": "박스 등록",
    "hold": "보류",
}
# 발굴 단계의 뜻: 이 앱이 단계로 무엇을 하는지만 적는다 (설계 문서 docs/superpowers/specs/2026-11-18-solution-box-design.md 의 말을 따른다)
STAGE_DESCRIPTIONS = {
    "intake": "파트너에게서 받은 아이템을 처음 등록한 상태입니다.",
    "tapping": "기존 고객에게 이 솔루션에 관심이 있는지 미리 알아보는 단계입니다.",
    "survey": "고객들이 실제로 필요로 하는지 조사하는 단계입니다.",
    "boxed": "검토를 마쳐 제안에 바로 쓸 수 있는 상태입니다.",
    "hold": "지금은 진행하지 않는 상태입니다. 제안 후보에는 자동으로 들어가지 않고, 제안 건에서 직접 더할 때만 후보가 됩니다.",
}
STAGE_DEFAULT = "intake"
STAGE_BOXED = "boxed"
STAGE_HOLD = "hold"
STAGE_FLOW = ("intake", "tapping", "survey", "boxed")  # 순서대로 나아가는 단계 (보류는 따로)

ACTIVITY_KINDS = {  # 활동 종류
    "meeting": "회의",
    "partner": "파트너 미팅",
    "tapping": "고객 태핑",
    "survey": "수요조사",
    "other": "기타",
}
ACTIVITY_KIND_DEFAULT = "meeting"
ACTIVITY_KIND_OTHER = "other"

FILE_KINDS = {"intro": "소개", "spec": "스펙", "related": "관련"}  # 자료 종류
FILE_KIND_DEFAULT = "related"

PROPOSAL_STATUSES = {  # 제안 진행 단계
    "new": "대상 지정",
    "directions": "방향 검토",
    "refining": "구체화",
    "outlined": "구성안",
    "deck": "제안서 완료",
}
PROPOSAL_STATUS_DEFAULT = "new"
PROPOSAL_TARGETS = {"customer": "특정 고객", "group": "다수 고객"}  # 제안 건의 대상 종류 (베타 W). 만든 뒤 바꾸지 않는다
PROPOSAL_TARGET_DEFAULT = "customer"
PROPOSAL_TARGET_GROUP = "group"
PROPOSAL_GROUP_NAME_MAX = 60
PROPOSAL_GROUP_BADGE = "다수 고객"  # 목록·검색 결과의 작은 배지 글
PROPOSAL_STATUS_DECK = "deck"
PROPOSAL_STEPS = {  # 제안 화면의 단계 표시줄 (진행 단계에서 매긴다)
    "target": "대상", "directions": "방향", "chat": "구체화", "outline": "구성안", "deck": "제안서",
}
PROPOSAL_STATUS_STEP = {"new": "directions", "directions": "directions", "refining": "chat", "outlined": "outline", "deck": "deck"}
JOB_KIND_STEP = {"proposal_directions": "directions", "proposal_chat": "chat", "proposal_outline": "outline"}

INTAKE_STATUSES = {  # 올리기 상태
    "uploaded": "올림",
    "analyzing": "정리 중",
    "ready": "확인 대기",
    "applying": "저장 중",
    "applied": "저장 완료",
    "failed": "실패",
    "discarded": "버림",
}
INTAKE_STATUS_DEFAULT = "uploaded"
# 보여 주는 모양만 다른 올림 상태: 읽을 수 있는 파일이 없고 음성이 받아쓰기를 기다리는 묶음 (실패가 아니다). 상태 코드는 늘리지 않는다 (`intake.is_archived`).
INTAKE_ARCHIVED_LABEL = "보관됨"
INTAKE_ARCHIVED_TONE = "slate"

INTAKE_FILE_KINDS = {"document": "문서", "image": "사진", "audio": "음성", "other": "기타"}  # 올린 파일 종류
INTAKE_FILE_KIND_DEFAULT = "other"

READ_STATUSES = {  # 읽기 상태
    "ok": "읽음",
    "failed": "읽지 못함",
    "unsupported": "보관만 함",
    "needs_transcript": "받아쓰기 필요",
    "pending_transcript": "받아쓰기 대기",
}

INTAKE_ITEM_KINDS = {  # 정리안 항목 종류
    "new_solution": "새 솔루션",
    "add_to_solution": "솔루션에 자료 추가",
    "add_contact": "연락처 추가",
    "activity": "활동 기록",
    "new_customer": "새 고객",
    "new_partner": "새 파트너",
    "unplaced": "정리하지 못한 파일",
}
INTAKE_ITEM_UNPLACED = "unplaced"
INTAKE_SAVEABLE_KINDS = tuple(k for k in INTAKE_ITEM_KINDS if k != INTAKE_ITEM_UNPLACED)

SLIDE_KINDS = {  # 장 종류
    "cover": "표지",
    "bullets": "글머리",
    "two_column": "2단 비교",
    "table": "표",
    "steps": "단계",
    "closing": "마무리",
}

RESEARCH_AREAS = {"market": "시장", "tech": "기술", "policy": "정책"}  # 리서치 분야
SUGGESTION_KINDS = {"new": "새로 찾을 솔루션", "push": "기존 솔루션 밀기"}  # 리서치 제안 종류
REPORT_KINDS = {"area": "분야 동향", "suggest": "발굴 방향 제안"}  # 리서치 보고서 종류
RUN_TRIGGERS = {"schedule": "예약", "manual": "수동"}  # 리서치 실행을 시작한 방식
RUN_STATUSES = {"running": "진행", "done": "완료", "partial": "일부 실패", "failed": "실패"}  # 실행 상태

USER_ROLES = {"admin": "관리자", "user": "일반 사용자"}  # 사용자 역할
USER_ROLE_DEFAULT = "user"
USER_ROLE_ADMIN = "admin"

JOB_STATUSES = {"pending": "대기", "running": "진행", "done": "완료", "failed": "실패"}  # AI 작업 상태
JOB_PENDING, JOB_RUNNING, JOB_DONE, JOB_FAILED = "pending", "running", "done", "failed"
JOB_ACTIVE = (JOB_PENDING, JOB_RUNNING)  # 아직 끝나지 않은 상태

JOB_KINDS = {
    "activity_structure": "활동 기록 AI 정리",
    "solution_draft": "솔루션 소개 초안",
    "proposal_directions": "제안 방향 만들기",
    "proposal_chat": "제안 채팅 답변",
    "proposal_outline": "제안서 구성안 만들기",
    "research_area": "리서치 분야 조사",
    "research_suggest": "리서치 발굴 방향 제안",
    "research_keywords": "리서치 주제·키워드 추천",
    "intake_analyze": "올린 파일 정리",
    "ai_ping": "AI 연결 확인",
    "duplicate_names": "중복 후보 AI 확인",
}

# AI 작업 종류별 예상 시간 (대기 안내에 쓴다)
JOB_EXPECTED = {
    "activity_structure": "보통 20초 안에",
    "solution_draft": "보통 30초 안에",
    "proposal_directions": "보통 1분 안에",
    "proposal_chat": "보통 1분 안에",
    "proposal_outline": "보통 1분 안에",
    "research_area": "분야마다 1~2분",
    "research_suggest": "보통 1분 안에",
    "research_keywords": "보통 1~2분 안에",
    "intake_analyze": "보통 1분 안에",
    "ai_ping": "보통 10초 안에",
    "duplicate_names": "보통 30초 안에",
}

ERROR_KINDS = {
    "not_installed": "Claude CLI가 설치되어 있지 않습니다. 서버에 설치되어 있는지 확인해 주세요.",
    "auth": "Claude 로그인이 필요합니다. 서버에서 Claude에 다시 로그인해 주세요.",
    "limit": "Claude 사용 한도에 도달했습니다. 잠시 뒤에 다시 시도해 주세요.",
    "network": "네트워크 연결에 문제가 있습니다. 연결을 확인한 뒤 다시 시도해 주세요.",
    "timeout": "제한 시간 안에 작업이 끝나지 않았습니다. 잠시 뒤에 다시 시도해 주세요.",
    "bad_output": "AI가 예상하지 못한 형식으로 답했습니다. 다시 시도해 주세요.",
    "restart": "서버가 다시 시작되어 작업이 중단되었습니다. 다시 시도해 주세요.",
    "unknown": "알 수 없는 오류가 발생했습니다. 잠시 뒤에 다시 시도해 주세요.",
}
RESTART_ERROR_TEXT = "서버가 재시작되어 중단되었습니다."  # 옛 중단 작업은 오류 종류 unknown 에 이 글이 적혀 있다 (행은 고쳐 쓰지 않고 화면에서 같은 안내로 읽는다)

# AI 작업 처리기가 꺼진 서버: 대기 중인 작업은 시작되지 않는다. 화면은 계속 다시 불러오지 않고 이 안내를 보인다.
AI_OFF_STATUS = "AI 꺼짐"
AI_OFF_NOTICE = "AI 작업 처리기가 꺼져 있어 작업이 시작되지 않습니다. 관리자에게 문의하세요."
# AI 를 쓰는 동작 곁에 보이는 한 줄 (상단 줄의 AI 상태와 같은 값에서 정한다: main.ai_gate). 상태 코드 → (이유, 올리기에서 일어나는 일)
AI_BLOCK_REASONS = {
    "off": "AI 작업 처리기가 꺼져 있어요",
    "down": "지난번 AI 작업이 Claude 를 쓰지 못해 실패했어요 (설치되지 않았거나 로그인이 필요해요)",
}
# 사용 불가("down")에서는 버튼을 막지 않는다: 다시 시도하면 연결을 다시 확인하고, 성공하면 이 안내가 사라진다.
AI_DOWN_RETRY = "버튼은 그대로 누를 수 있어요. 다시 시도하면 연결을 다시 확인하고, 성공하면 이 안내가 사라져요."
AI_BLOCK_UPLOAD = {
    "off": "파일은 올려 두면 그대로 보관돼요. 정리는 시작되지 않아요. 처리기가 켜진 뒤 올리기 묶음에서 '정리 시작'을 눌러 주세요.",
    "down": "파일은 올리면 보관되고 정리를 바로 시도해요. 지난번 AI 작업이 실패했기 때문에 또 실패할 수 있어요. 실패하면 올리기 묶음에서 '다시 정리'를 눌러 주세요.",
}

# 확인 항목의 종류: 근거 문제 / 확인할 점 (저장된 값이 아니라 문구에서 가려낸다. app/checks.py)
CHECK_KINDS = {"evidence": "근거 문제", "decide": "확인할 점"}
CHECK_KIND_DESCRIPTIONS = {
    "evidence": "출처 본문을 열어 확인하지 못했거나 출처가 없는 항목입니다. 링크를 열어 직접 확인하세요.",
    "decide": "수치·범위처럼 사람이 정하거나 맞는지 확인해야 하는 항목입니다.",
}

OPPORTUNITY_STAGES = {  # 영업기회 단계 (순서 강제 없음: 어디서 어디로든 바꿀 수 있다)
    "discovery": "발굴",
    "proposing": "제안 중",
    "negotiating": "협상",
    "won": "수주",
    "lost": "실패",
    "hold": "보류",
}
OPPORTUNITY_STAGE_DESCRIPTIONS = {
    "discovery": "고객 니즈를 확인하며 영업기회를 찾는 단계입니다.",
    "proposing": "고객에게 제안을 내고 답을 기다리는 단계입니다.",
    "negotiating": "금액·범위 같은 조건을 고객과 조율하는 단계입니다.",
    "won": "수주한 상태입니다. 이어서 프로젝트를 만들거나 기존 프로젝트에 이을 수 있습니다.",
    "lost": "수주하지 못하고 끝난 상태입니다.",
    "hold": "지금은 진행하지 않고 멈춘 상태입니다.",
}
OPPORTUNITY_STAGE_DEFAULT = "discovery"
OPPORTUNITY_STAGE_WON = "won"
OPPORTUNITY_FLOW = ("discovery", "proposing", "negotiating", "won")  # 단계 막대에 차례로 보이는 단계
OPPORTUNITY_SIDE_STAGES = ("lost", "hold")  # 막대 옆에 따로 보이는 단계
OPPORTUNITY_OPEN_STAGES = ("discovery", "proposing", "negotiating")  # 진행 중
OPPORTUNITY_FILTER_OPEN = "open"  # 목록 거르기: 진행 중인 단계 모두

# 사업방식 (사용자 결정 2026-11-23, 베타 Y): 정확히 세 값이고 이 순서다. 하위 유형은 두지 않는다. 솔루션=가능한 방식, BM=대표 방식(하나), 영업기회·제안 건=검토하는 방식.
# 화면 이름·한 줄 뜻·AI 에 주는 설명은 모두 이 목록 하나에서 읽는다 (다른 곳에 다시 적지 않는다).
BUSINESS_TYPES = {"build": "구축형", "lease": "임대형", "invest": "투자형"}
# 한 줄 뜻: **초안이다 (사용자가 아직 확인하지 않았다).** 고치려면 여기 이 글만 고친다. 폼·도움말·배지 툴팁·AI 프롬프트가 모두 이것을 쓴다.
BUSINESS_TYPE_DESCRIPTIONS = {
    "build": "고객이 설비·시스템을 구매해 소유하는 방식",
    "lease": "고객이 소유하지 않고 이용료(렌탈·구독)를 내고 쓰는 방식",
    "invest": "당사(또는 투자자)가 투자·소유하고 절감액·전력 판매 등 수익으로 회수하는 방식 (IPP 등)",
}
BUSINESS_TYPE_INVEST = "invest"  # 투자형: 제안서 구성안에 투자·회수 구조 장을 넣게 하는 값
BUSINESS_TYPE_MEMO_MAX = 300  # 솔루션의 사업방식별 메모 길이 (예: "투자형은 100kW 이상만")
BUSINESS_TYPE_FILTER_PARAM = "business_type"  # 목록 `?business_type=build|lease|invest` (화이트리스트, 그 사업방식을 가진 것만)
BUSINESS_TYPE_LISTS = ("solution", "bm", "opportunity")  # 위 걸러 보기가 있는 목록

# 솔루션 분야 (베타 AD, 사용자 결정 2026-11-24): 값 목록은 코드가 아니라 데이터(표 `solution_fields`, 관리자가 고친다). 코드에는 처음 한 번 채울 값과 규칙만 둔다.
SOLUTION_FIELD_SEED = ("에너지 효율", "신재생·발전", "안전·방재", "통신·네트워크", "시설 관리", "환경")  # 표가 처음 만들어질 때 이 순서로 한 번만 채운다 (관리자가 지운 값을 다시 넣지 않는다)
SOLUTION_FIELD_NAME_MAX = 30  # 분야 이름 길이
SOLUTION_FIELD_NONE = "분야 미지정"  # 분야가 없는 솔루션의 묶음 이름
SOLUTION_FIELD_INACTIVE = "(사용 안 함)"  # 사용을 끈 분야의 표시
SOLUTION_GROUP_PARAM = "group"  # 솔루션 목록 `?group=field|stage|business_type|partner` (없음은 `none`, 값이 없으면 기억한 값)
SOLUTION_GROUP_COOKIE = "solution_group"  # 사용자가 고른 묶어 보기를 기억하는 쿠키 (DB 에 두지 않는다)
SOLUTION_GROUPS = {"field": "분야", "stage": "발굴 단계", "business_type": "사업방식", "partner": "파트너"}  # 묶어 볼 기준 (이 순서로 보인다)
SOLUTION_GROUP_NONE_LABELS = {"field": SOLUTION_FIELD_NONE, "business_type": "사업방식 미지정", "partner": "파트너 없음"}


def clean_business_types(raw) -> list[str]:
    """사업방식 코드 목록을 다듬는다: 목록에 있는 값만, 중복 없이, 정의된 순서(구축형·임대형·투자형)로. 문자열(쉼표 구분)·목록·None 을 받는다."""
    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else [x for x in raw if isinstance(x, str)]
    picked = {i.strip() for i in items}
    return [c for c in BUSINESS_TYPES if c in picked]


def business_types_text(codes) -> str:
    """사업방식 이름들을 쉼표로: `구축형, 투자형` (없으면 빈 글)."""
    return ", ".join(BUSINESS_TYPES[c] for c in clean_business_types(codes))


def business_type_guide() -> str:
    """프롬프트·도움말에 쓰는 사업방식 풀이 줄들: `- 구축형: 고객이 …`"""
    return "\n".join(f"- {name}: {BUSINESS_TYPE_DESCRIPTIONS[code]}" for code, name in BUSINESS_TYPES.items())


# 프로젝트 상태 (고르는 목록, 순서가 곧 선택 칸의 순서). 저장되는 값은 한글 글자 그대로라서 코드 = 이름이다 (옛 자유 글 "진행" 같은 값이 그대로 목록 값과 맞는다).
# 목록에 없는 옛 자유 글은 고치지 않고 남긴다: 화면에는 뒤에 PROJECT_STATUS_LEGACY_MARK 를 붙여 보이고 (`project_status_text`), 저장할 때는 지금 값 그대로만 다시 받는다.
PROJECT_STATUSES = {"준비": "준비", "진행": "진행", "완료": "완료", "중단": "중단", "유지보수": "유지보수"}
PROJECT_STATUS_LEGACY_MARK = " (이전 입력)"


def project_status_text(value: str | None) -> str:
    """프로젝트 상태를 화면에 보이는 글로: 비면 빈 글, 목록 값은 그대로, 목록에 없는 옛 글은 뒤에 " (이전 입력)"."""
    value = value or ""
    return value if not value or value in PROJECT_STATUSES else value + PROJECT_STATUS_LEGACY_MARK


ORIGINS = {"manual": "직접 입력", "intake": "올리기", "import": "파일 가져오기", "research": "리서치"}  # 기록의 유래 (비어 있으면 모름)
ORIGIN_MANUAL, ORIGIN_INTAKE, ORIGIN_RESEARCH = "manual", "intake", "research"
# 베타 AA: 명함 파일 가져오기(`/intake/cards`)로 만든 기록. 번호(`origin_id`)는 올리기 묶음 번호라서 올리기 묶음과 이어지지만, AI 가 만들지 않았으므로
# "AI 생성" 표기 대상이 아니다 (`AI_GENERATED_ORIGINS` 에 넣지 않는다). 목록의 "올리기" 구분에는 함께 센다 (`_common.origin_bucket`).
ORIGIN_IMPORT = "import"
ORIGINS_SHOWN = (ORIGIN_INTAKE, ORIGIN_IMPORT, ORIGIN_RESEARCH)  # 목록 "유래" 칸에 이름을 보이는 유래
ORIGINS_OF_INTAKE = (ORIGIN_INTAKE, ORIGIN_IMPORT)  # 번호가 올리기 묶음 번호인 유래
ORIGIN_FILTER_PARAM = "origin"  # 목록 `?origin=intake|research|manual` (화이트리스트). 유래가 비어 있는(모름) 기록은 "직접 입력"에 센다: 올리기·리서치는 근거가 있을 때만 채우기 때문

# "AI 생성" 표기 (사용자 결정 2026-11-22): 유래가 올리기 묶음이나 리서치 보고서인 기록(= AI 가 정리·제안해 만든 기록)에만 붙인다.
# 직접 입력·유래 모름은 붙이지 않는다. 유래 열이 있는 유형(솔루션·활동 기록·고객·파트너)에만 해당한다. 사람이 직접 만든 기록을 "AI 초안 받기"·"AI 정리"
# 로 채운 것은 따라가지 않으므로 표기되지 않는다 (알려진 한계). 화면·지식 사본·도움말은 이 이름과 함수 하나만 쓴다.
AI_GENERATED_LABEL = "AI 생성"
AI_GENERATED_TONE = "violet"
AI_GENERATED_HELP = "올리기로 AI 가 정리했거나 리서치 보고서에서 AI 가 제안해 만든 기록입니다. 사람이 확인하기 전의 내용일 수 있습니다"
AI_GENERATED_ORIGINS = (ORIGIN_INTAKE, ORIGIN_RESEARCH)


def is_ai_generated(obj) -> bool:
    """기록의 유래(`origin_type`)가 올리기 또는 리서치이면 True. 유래 열이 없는 객체·직접 입력·모름은 False."""
    return getattr(obj, "origin_type", None) in AI_GENERATED_ORIGINS


def ai_generated_note(obj) -> str | None:
    """지식 사본 한 줄의 값: `예 (올리기 묶음 #3 에서 AI 가 정리)` / `예 (리서치 보고서 #5 에서 AI 가 제안)`. AI 생성이 아니면 None.
    묶음·보고서 번호가 지워져 비었으면 번호를 뺀다."""
    if not is_ai_generated(obj):
        return None
    number = getattr(obj, "origin_id", None)
    tag = f" #{number}" if number is not None else ""
    if obj.origin_type == ORIGIN_INTAKE:
        return f"예 ({label('intake')}{tag} 에서 AI 가 정리)"
    return f"예 ({label('research_report')}{tag} 에서 AI 가 제안)"


WEEKDAYS = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}

CODE_LISTS: dict[str, CodeList] = {c.key: c for c in (
    CodeList("stage", "발굴 단계", STAGES, STAGE_DEFAULT, STAGE_DESCRIPTIONS),
    CodeList("activity_kind", "활동 종류", ACTIVITY_KINDS, ACTIVITY_KIND_DEFAULT),
    CodeList("file_kind", "자료 종류", FILE_KINDS, FILE_KIND_DEFAULT),
    CodeList("proposal_status", "제안 진행 단계", PROPOSAL_STATUSES, PROPOSAL_STATUS_DEFAULT),
    CodeList("proposal_step", "제안 단계 표시줄", PROPOSAL_STEPS),
    CodeList("proposal_target", "제안 대상 종류", PROPOSAL_TARGETS, PROPOSAL_TARGET_DEFAULT),
    CodeList("intake_status", "올리기 상태", INTAKE_STATUSES, INTAKE_STATUS_DEFAULT),
    CodeList("intake_file_kind", "올린 파일 종류", INTAKE_FILE_KINDS, INTAKE_FILE_KIND_DEFAULT),
    CodeList("read_status", "읽기 상태", READ_STATUSES),
    CodeList("plan_item_kind", "정리안 항목 종류", INTAKE_ITEM_KINDS),
    CodeList("slide_kind", "장 종류", SLIDE_KINDS),
    CodeList("research_area", "리서치 분야", RESEARCH_AREAS),
    CodeList("suggestion_kind", "리서치 제안 종류", SUGGESTION_KINDS),
    CodeList("report_kind", "리서치 보고서 종류", REPORT_KINDS),
    CodeList("run_trigger", "리서치 실행 방식", RUN_TRIGGERS),
    CodeList("run_status", "실행 상태", RUN_STATUSES, "running"),
    CodeList("user_role", "사용자 역할", USER_ROLES, USER_ROLE_DEFAULT),
    CodeList("job_status", "AI 작업 상태", JOB_STATUSES, JOB_PENDING),
    CodeList("job_kind", "AI 작업 종류", JOB_KINDS),
    CodeList("check_kind", "확인 항목 종류", CHECK_KINDS, None, CHECK_KIND_DESCRIPTIONS),
    CodeList("origin_type", "기록의 유래", ORIGINS),
    CodeList("project_status", "프로젝트 상태", PROJECT_STATUSES),
    CodeList("opportunity_stage", "영업기회 단계", OPPORTUNITY_STAGES, OPPORTUNITY_STAGE_DEFAULT, OPPORTUNITY_STAGE_DESCRIPTIONS),
    CodeList("business_type", "사업방식", BUSINESS_TYPES, None, BUSINESS_TYPE_DESCRIPTIONS),
)}

# 배지 색: 코드 값마다 한 가지 색조 (app.css 의 `.badge.tone-*`). 모든 코드 목록의 배지가 이 표 하나를 따른다 (`pill` 매크로, `tone_class`).
# 표에 없는 값은 slate (중립). 의미: ok 완료·좋음, warn 진행 중·확인 필요, bad 실패·문제, brand 현재 흐름, violet 새로 들어옴·검토, slate 중립·보류.
TONES = ("brand", "violet", "ok", "warn", "bad", "slate")
CODE_TONES: dict[str, dict[str, str]] = {
    "stage": {"intake": "violet", "tapping": "warn", "survey": "brand", "boxed": "ok", "hold": "slate"},
    "opportunity_stage": {"discovery": "brand", "proposing": "warn", "negotiating": "violet", "won": "ok", "lost": "bad", "hold": "slate"},
    "activity_kind": {"meeting": "slate", "partner": "violet", "tapping": "warn", "survey": "brand", "other": "slate"},
    "proposal_status": {"new": "slate", "directions": "brand", "refining": "violet", "outlined": "warn", "deck": "ok"},
    "intake_status": {"uploaded": "brand", "analyzing": "warn", "ready": "violet", "applying": "warn", "applied": "ok", "failed": "bad", "discarded": "slate"},
    "read_status": {"ok": "ok", "failed": "bad", "unsupported": "slate", "needs_transcript": "warn", "pending_transcript": "brand"},
    "plan_item_kind": {"new_solution": "violet", "add_to_solution": "violet", "add_contact": "ok", "activity": "brand", "new_customer": "ok",
                       "new_partner": "ok", "unplaced": "slate"},
    "run_status": {"running": "warn", "done": "ok", "partial": "warn", "failed": "bad"},
    "job_status": {"pending": "slate", "running": "brand", "done": "ok", "failed": "bad"},
    "check_kind": {"evidence": "bad", "decide": "warn"},
    "user_role": {"admin": "violet", "user": "slate"},
    "file_kind": {"intro": "brand", "spec": "violet", "related": "slate"},
    "suggestion_kind": {"new": "violet", "push": "brand"},
    "intake_file_kind": {"document": "brand", "image": "violet", "audio": "warn", "other": "slate"},
    "origin_type": {"manual": "slate", "intake": "brand", "import": "brand", "research": "violet"},
    "business_type": {"build": "brand", "lease": "violet", "invest": "warn"},
}


def tone_class(list_key: str, code: str) -> str:
    """배지 CSS 클래스 (`tone-ok` …). 정해지지 않은 값은 중립."""
    return "tone-" + CODE_TONES.get(list_key, {}).get(code, "slate")


OPPORTUNITY_STAGE_CLASS = {c: tone_class("opportunity_stage", c) for c in OPPORTUNITY_STAGES}  # 단계 -> 배지 CSS 클래스

# 모델 열이 어느 코드 목록의 값을 담는지 (물리 표 이름, 열 이름). 일관성 검사가 기본값을 대조한다.
COLUMN_CODES = {
    ("solutions", "stage"): "stage",
    ("activities", "kind"): "activity_kind",
    ("solution_files", "kind"): "file_kind",
    ("proposals", "status"): "proposal_status",
    ("proposals", "target_kind"): "proposal_target",
    ("intakes", "status"): "intake_status",
    ("intake_files", "kind"): "intake_file_kind",
    ("intake_files", "read_status"): "read_status",
    ("research_topics", "area"): "research_area",
    ("research_runs", "status"): "run_status",
    ("research_runs", "trigger"): "run_trigger",
    ("research_reports", "kind"): "report_kind",
    ("research_reports", "area"): "research_area",
    ("research_reports", "status"): "job_status",
    ("solutions", "origin_type"): "origin_type",
    ("activities", "origin_type"): "origin_type",
    ("customers", "origin_type"): "origin_type",
    ("partners", "origin_type"): "origin_type",
    ("opportunities", "stage"): "opportunity_stage",
    ("businesses", "status"): "project_status",
    ("bms", "business_type"): "business_type",
    ("users", "role"): "user_role",
    ("ai_jobs", "status"): "job_status",
    ("ai_jobs", "kind"): "job_kind",
}

# ======================================================================================
# 용어: 여러 화면·지식 사본·프롬프트가 같은 낱말을 쓰도록 한 곳에 둔다
# ======================================================================================

TERMS = {
    "source": "출처",
    "reason": "이유",
    "check_item": "확인 항목",
    "evidence_problem": CHECK_KINDS["evidence"],
    "to_check": CHECK_KINDS["decide"],
    "proposal_deck": "제안서",
    "proposal_nav": "제안",
    "intake_nav": "올리기 묶음",
    "intake_short": "묶음",
    "customer_need": "고객 니즈",
    "same_company": "같은 회사",
}

# ======================================================================================
# 대상 유형
# ======================================================================================

GROUPS = ("핵심", "부속", "작업", "시스템")


@dataclass(frozen=True)
class ObjectType:
    code: str
    label: str
    group: str  # 핵심 / 부속 / 작업 / 시스템
    model_name: str | None = None  # app.models 의 클래스 이름. 다른 기록 안에 들어 있는 대상은 None
    table: str | None = None  # 물리 표 이름 (코드 이름과 다를 수 있다)
    list_url: str | None = None
    detail_url: str | None = None  # "/solutions/{id}"
    mirror_path: str | None = None  # 지식 사본 경로 형식
    legacy_mirror_paths: tuple[str, ...] = ()  # 저장된 옛 경로 형식 (읽기만 한다)
    mirror_dir_format: str | None = None  # 이 대상의 사본 폴더 (프롬프트에서 폴더로 가리킬 때)
    source_kind: str | None = None  # 출처 칩에 보일 종류 이름 (없으면 label)
    parent: tuple[str, str] | None = None  # 화면 주소를 부모 대상에서 빌린다: (부모 유형, 모델의 부모 번호 속성)
    nav: str | None = None  # 메뉴 이름 (없으면 메뉴에 없음)
    nav_group: str | None = None  # 메뉴 묶음 (NAV_GROUPS 의 코드). 메뉴에 있으면 반드시 있다
    embedded_in: str | None = None  # 다른 기록 안에 들어 있다면 "표.열" 또는 설명
    props: dict[str, str] = field(default_factory=dict)  # 속성 이름 -> 화면 이름표
    physical: dict[str, str] = field(default_factory=dict)  # 물리 이름이 코드 이름과 다른 곳
    description: str = ""  # 목록 화면 제목 밑에 보이는 한 문장 (docs/ontology.md 3장의 뜻)

    @property
    def model(self):
        if self.model_name is None:
            return None
        from app import models  # 늦게 불러온다 (models 가 이 모듈의 상수를 쓴다)

        return getattr(models, self.model_name)

    @property
    def kind_label(self) -> str:
        return self.source_kind or self.label

    def url(self, obj_id) -> str | None:
        return self.detail_url.format(id=obj_id) if self.detail_url else None


_TYPE_LIST = [
    ObjectType(
        "solution", "솔루션", "핵심", "Solution", "solutions", "/solutions", "/solutions/{id}",
        "solutions/{id}/card.md", mirror_dir_format="solutions/{id}/", nav="솔루션", nav_group="discover",
        props={"name": "이름", "summary": "한 줄 소개", "description": "상세 설명", "partner": "파트너", "stage": "발굴 단계",
               "usage_direction": "활용 방향", "applicable_types": "적용 가능 유형", "business_types": "가능한 사업방식", "field": "분야", "tags": "태그", "files": "자료", "origin": "유래"},
        description="파트너에게서 발굴해 고객에게 제안할 수 있는 제품과 서비스",
    ),
    ObjectType(
        "partner", "파트너", "핵심", "Partner", "partners", "/partners", "/partners/{id}", "partners/{id}.md", nav="파트너", nav_group="discover",
        props={"name": "이름", "aliases": "다른 이름", "contact": "연락처", "description": "소개", "memo": "메모", "solutions": "솔루션", "origin": "유래",
               "customer": "같은 회사인 고객"},
        description="솔루션을 공급하는 회사",
    ),
    ObjectType(
        "customer", "고객", "핵심", "Customer", "customers", "/customers", "/customers/{id}", "customers/{id}.md", nav="고객", nav_group="sales",
        props={"name": "이름", "aliases": "다른 이름", "industry": "업종", "contact": "연락처", "memo": "메모", "projects": "프로젝트", "origin": "유래",
               "partner": "같은 회사인 파트너", "opportunities": "영업기회"},
        description="솔루션을 제안받는 회사",
    ),
    ObjectType(
        "project", "프로젝트", "핵심", "Project", "businesses", "/projects", "/projects/{id}", "projects/{id}.md",
        legacy_mirror_paths=("businesses/{id}.md",), nav="프로젝트", nav_group="sales",
        props={"name": "프로젝트명", "customer": "고객", "description": "설명", "period": "기간", "status": "상태", "memo": "메모",
               "bm": "BM", "opportunity": "영업기회"},
        physical={"fk_column": "business_id", "link_table": "activity_businesses"},
        description="특정 고객과 하는(하게 된) 사업 한 건. 제안의 발판이 됩니다",
    ),
    ObjectType(
        "opportunity", "영업기회", "핵심", "Opportunity", "opportunities", "/opportunities", "/opportunities/{id}",
        "opportunities/{id}.md", mirror_dir_format="opportunities/", nav="영업기회", nav_group="sales",
        props={"name": "이름", "customer": "고객", "stage": "단계", "bm": "BM", "project": "프로젝트", "memo": "메모",
               "needs": "고객 니즈", "solutions": "검토 솔루션", "activities": "활동 기록", "proposals": "제안 건", "business_types": "검토 사업방식"},
        description="한 고객의 니즈를 묶어 사업으로 만들어 가는 건. 수주하면 프로젝트가 됩니다",
    ),
    ObjectType(
        "bm", "BM", "핵심", "BM", "bms", "/bms", "/bms/{id}", "bms/{id}.md", mirror_dir_format="bms/", nav="BM", nav_group="sales",
        props={"name": "이름", "summary": "한 줄 설명", "target_customers": "대상 고객 유형", "revenue_model": "수익 구조", "business_type": "대표 사업방식",
               "memo": "메모", "solutions": "솔루션", "projects": "프로젝트", "opportunities": "영업기회", "proposals": "다수 고객 제안 건"},
        description="사업 모델. 고객에 묶이지 않고, 특정 고객별로 나뉘는 것은 프로젝트입니다",
    ),
    ObjectType(
        "activity", "활동 기록", "핵심", "Activity", "activities", "/activities", "/activities/{id}",
        "activities/{year}/{id}.md", nav="활동 기록", nav_group="today",
        props={"kind": "종류", "date": "일자", "title": "제목", "raw_text": "원문", "summary_md": "정리본",
               "follow_ups": "후속 조치", "demand_signals": "고객 니즈", "solutions": "솔루션", "customers": "고객",
               "projects": "프로젝트", "partners": "파트너", "origin": "유래", "opportunities": "영업기회"},
        description="회의, 통화, 태핑, 수요조사 등 한 번의 활동",
    ),
    ObjectType(
        "proposal", "제안 건", "핵심", "Proposal", "proposals", "/proposals", "/proposals/{id}", nav="제안", nav_group="sales",
        props={"title": "제목", "request_memo": "요청 메모", "status": "진행 단계", "customer": "고객", "project": "프로젝트",
               "opportunity": "영업기회", "directions": "제안 방향", "outline": "구성안",
               "target_kind": "대상 종류", "group_name": "대상 그룹", "group_customers": "붙인 고객", "bm": "신규 사업 (BM)", "business_types": "검토 사업방식"},
        description="특정 고객(과 프로젝트·영업기회) 또는 다수 고객(대상 그룹)을 대상으로 한 제안 작업 한 건",
    ),
    ObjectType(
        "solution_file", "자료", "부속", "SolutionFile", "solution_files",
        mirror_path="solutions/{solution_id}/files/{id}-{name}.md", mirror_dir_format="solutions/{solution_id}/files/",
        parent=("solution", "solution_id"),
        props={"original_name": "파일 이름", "kind": "자료 종류", "extract_status": "글 추출 상태", "size": "크기"},
        description="솔루션에 붙여 올린 소개·스펙·관련 파일. 글을 뽑아 두어 AI 가 읽을 수 있습니다",
    ),
    ObjectType("solution_field", "솔루션 분야", "부속", "SolutionField", "solution_fields", "/admin/solution-fields",
               props={"name": "이름", "sort_order": "순서", "active": "사용 여부", "solutions": "솔루션"},
               description="솔루션을 묶어 보는 분야 목록 (관리자가 더하고 고치고 순서를 바꾼다)"),
    ObjectType("follow_up", "후속 조치", "부속", "FollowUp", "follow_ups", "/follow-ups",
               description="활동 기록에서 나온 할 일",
               props={"task": "할 일", "owner": "담당", "due": "기한", "done": "완료"}),
    ObjectType("customer_need", TERMS["customer_need"], "부속", "CustomerNeed", "customer_needs",
               props={"customer": "고객", "content": "내용", "opportunity": "영업기회"},
               description="활동 기록에서 드러난 고객의 필요. 같은 고객의 니즈를 묶어 영업기회로 만듭니다"),
    ObjectType("direction", "제안 방향", "부속", embedded_in="proposals.directions",
               props={"title": "제목", "summary": "한 줄 소개", "solutions": "솔루션", "rationale": "근거", "effects": "기대 효과", "business_type": "사업방식"}),
    ObjectType("slide", "구성안 장", "부속", embedded_in="proposals.outline",
               props={"kind": "장 종류", "title": "제목", "key_message": "핵심 메시지", "sources": TERMS["source"]}),
    ObjectType("check_item", TERMS["check_item"], "부속", embedded_in="proposals.directions / proposals.outline / research_reports.trends",
               props={"kind": "종류", "checked": "확인함"}),
    ObjectType(
        "intake", "올리기 묶음", "작업", "Intake", "intakes", "/intake", "/intake/{id}", nav=None,
        props={"note": "메모", "status": "상태", "files": "올린 파일"},
        description="한 번에 올린 파일들과 그 정리 상태",
    ),
    ObjectType(
        "intake_file", "올린 파일", "작업", "IntakeFile", "intake_files",
        props={"original_name": "이름", "kind": "종류", "read_status": "읽기 상태", "size": "크기"},
    ),
    ObjectType("plan_item", "정리안 항목", "작업", embedded_in="intakes.plan", props={"kind": "종류", "reason": TERMS["reason"]}),
    ObjectType("research_topic", "리서치 주제", "작업", "ResearchTopic", "research_topics",
               props={"name": "이름", "keywords": "검색어", "area": "분야"}),
    ObjectType("research_run", "리서치 실행", "작업", "ResearchRun", "research_runs", "/research", "/research/runs/{id}", nav="리서치", nav_group="discover",
               props={"status": "상태", "trigger": "실행 방식"},
               description="주간 웹 조사 한 회와 그 결과"),
    ObjectType(
        "research_report", "리서치 보고서", "작업", "ResearchReport", "research_reports",
        mirror_path="research/{day}-{tag}-{id}.md", source_kind="리서치", parent=("research_run", "run_id"),
        props={"kind": "종류", "area": "분야", "title": "제목", "trends": "동향", "insights": "시사점", "suggestions": "발굴 방향 제안"},
    ),
    ObjectType("suggestion", "리서치 제안", "작업", embedded_in="research_reports.suggestions",
               props={"title": "제목", "detail": "내용", "reason": TERMS["reason"], "sources": TERMS["source"], "kind": "종류",
                      "solution_ids": "대상 솔루션"}),
    ObjectType("user", "사용자", "시스템", "User", "users", "/admin/users", props={"username": "아이디", "name": "이름", "role": "역할"},
               description="로그인 계정"),
    ObjectType("ai_job", "AI 작업", "시스템", "AIJob", "ai_jobs", "/admin/jobs", props={"kind": "종류", "status": "상태"},
               description="AI 작업 대기열과 최근 결과"),
]
TYPES: dict[str, ObjectType] = {t.code: t for t in _TYPE_LIST}

# 메뉴: 묶음(NAV_GROUPS) 안의 항목. 대상 유형의 `nav`·`nav_group` 에서 만들고, 대상이 아닌 항목(홈·올리기 묶음)과 관리 항목만 여기서 적는다.
# 사이드 메뉴(base.html)는 이 정의만 그린다: 새 항목은 묶음이 없으면 안 된다 (tests/test_shell.py).
NAV_GROUPS = (("today", "오늘"), ("discover", "발굴"), ("sales", "영업"), ("admin", "관리"))
NAV_GROUP_LABELS = dict(NAV_GROUPS)  # 묶음 코드 -> 이름 (화면 제목 위 경로가 읽는다)
# 메뉴 항목이 아닌 화면이 어느 항목에 속하는지: 주소 앞 조각 -> 항목의 path (후속 조치 목록은 "오늘" 의 홈 구역)
NAV_ALIASES = {"follow-ups": ""}


class NavItem(NamedTuple):
    path: str  # 주소에서 앞 조각 ("" 은 홈, 관리는 "admin/users")
    label: str
    group: str
    badge: str | None = None  # 이름 옆 배지 종류 ("ready": 확인 대기 수)

    @property
    def href(self) -> str:
        return "/" + self.path


class NavGroup(NamedTuple):
    code: str
    label: str
    items: tuple[NavItem, ...]


def _type_nav_item(code: str) -> NavItem:
    t = TYPES[code]
    return NavItem(t.list_url.strip("/"), t.nav, t.nav_group)


# 묶음 안의 차례. 오늘 = 홈·올리기 묶음·활동 기록, 발굴 = 솔루션·파트너·리서치, 영업 = 고객·영업기회·제안·프로젝트·BM
NAV_ITEMS = (
    NavItem("", "홈", "today"),
    NavItem("intake", TERMS["intake_nav"], "today", badge="ready"),
    _type_nav_item("activity"),
    _type_nav_item("solution"), _type_nav_item("partner"), _type_nav_item("research_run"),
    _type_nav_item("customer"), _type_nav_item("opportunity"), _type_nav_item("proposal"), _type_nav_item("project"), _type_nav_item("bm"),
)
ADMIN_NAV_ITEMS = tuple(NavItem(f"admin/{path}", name, "admin") for path, name in
                        (("users", "사용자"), ("jobs", "AI 작업"), ("research", "리서치 설정"), ("solution-fields", "솔루션 분야"), ("duplicates", "중복 정리"), ("system", "시스템")))


# 메뉴 항목마다 다른 작은 선 그림 (24×24, 외부 파일 없이 SVG 조각 그대로). 키 = NavItem.path. 새 메뉴 항목은 여기에도 더한다 (tests/test_beta_d.py 가 빠짐·겹침을 본다).
NAV_ICONS = {
    "": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
    "intake": '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/>',
    "activities": '<path d="M6 3h9l4 4v14H6z"/><path d="M9 12h7M9 16h7"/>',
    "solutions": '<path d="M12 3l8 4v10l-8 4-8-4V7z"/><path d="M4 7l8 4 8-4M12 11v10"/>',
    "partners": '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.5 2.7-6 6-6s6 2.5 6 6"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14.2c3 .2 5 2.3 5 5.8"/>',
    "research": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "customers": '<path d="M5 21V5l8-2v18"/><path d="M13 9h6v12"/><path d="M8 8h2M8 12h2M8 16h2M3 21h18"/>',
    "opportunities": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/>',
    "proposals": '<rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="M7 14l3-3 3 2 4-5"/><path d="M8 21h8"/>',
    "projects": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "bms": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "admin/users": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/>',
    "admin/jobs": '<path d="M5 6h14M5 12h14M5 18h9"/>',
    "admin/research": '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    "admin/solution-fields": '<path d="M4 5h7v7H4zM13 5h7v7h-7zM4 14h7v5H4zM13 14h7v5h-7z"/>',
    "admin/duplicates": '<rect x="4" y="4" width="11" height="11" rx="1.5"/><path d="M9 20h9a2 2 0 0 0 2-2V9"/>',
    "admin/system": '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
}


def nav_icon(path: str):
    """사이드 메뉴 항목의 작은 그림 (장식이라 화면 읽기에서는 숨긴다). 안전한 SVG 조각이라 Markup 으로 돌려준다."""
    from markupsafe import Markup

    return Markup('<svg class="ni" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
                  + NAV_ICONS.get(path, "") + "</svg>")


def nav_groups(is_admin: bool = False) -> list[NavGroup]:
    """사이드 메뉴 묶음들. 관리 묶음은 관리자에게만."""
    out = []
    for code, label in NAV_GROUPS:
        items = ADMIN_NAV_ITEMS if code == "admin" else tuple(i for i in NAV_ITEMS if i.group == code)
        if code == "admin" and not is_admin:
            continue
        out.append(NavGroup(code, label, items))
    return out


def nav_group_label(first: str) -> str:
    """주소 앞 조각("intake", "follow-ups" …)이 속한 메뉴 묶음의 이름 (경로의 첫 마디)."""
    first = NAV_ALIASES.get(first, first)
    item = next(i for i in (*NAV_ITEMS, *ADMIN_NAV_ITEMS) if i.path == first)
    return NAV_GROUP_LABELS[item.group]


def nav_current(path: str) -> str | None:
    """이 주소가 속한 메뉴 항목의 `path` (상세·수정 화면은 그 구역의 항목). 메뉴에 없는 화면은 None."""
    parts = path.strip("/").split("/")
    first = parts[0]
    if first == "admin":
        key = "/".join(parts[:2])
        return key if any(i.path == key for i in ADMIN_NAV_ITEMS) else None
    first = NAV_ALIASES.get(first, first)
    return first if any(i.path == first for i in NAV_ITEMS) else None


# 옛 주소(GET) -> 새 주소. 영구 이동한다.
LEGACY_URL_PREFIXES = {"/businesses": "/projects"}


def label(code: str) -> str:
    return TYPES[code].label


def prop(code: str, name: str) -> str:
    return TYPES[code].props[name]


def code_help(key: str, code: str) -> str:
    """코드 값의 한 문장 뜻 (없으면 빈 글). 배지 툴팁·단계 범례·도움말이 이것 하나를 쓴다."""
    return CODE_LISTS[key].descriptions.get(code, "") if key in CODE_LISTS else ""


def term(key: str) -> str:
    return TERMS[key]


def model_for(code: str):
    return TYPES[code].model


def type_of(obj) -> str:
    """ORM 객체의 대상 유형 코드."""
    for t in _TYPE_LIST:
        if t.model_name == type(obj).__name__:
            return t.code
    raise TypeError(f"지원하지 않는 항목: {type(obj).__name__}")


def type_for_model(model) -> str:
    for t in _TYPE_LIST:
        if t.model_name == model.__name__:
            return t.code
    raise TypeError(model)


def detail_url(code: str, obj_id) -> str | None:
    return TYPES[code].url(obj_id)


# ---- 연결할 수 있는 대상 (활동 기록과의 N:M) ----
# 활동 기록에 붙일 수 있는 대상 유형 -> Activity 관계 이름. 화면의 순서와 같다.
ACTIVITY_LINKS = {"solution": "solutions", "customer": "customers", "project": "projects", "partner": "partners"}
def activity_link_fields() -> list[tuple[str, str, str]]:
    """활동 기록 폼의 연결 칸: (유형, 화면 이름, 폼 필드 이름). 순서는 ACTIVITY_LINKS 와 같다."""
    return [(code, TYPES[code].label, f"{code}_ids") for code in ACTIVITY_LINKS]


# 활동 기록 화면에서만 직접 고르는 연결 (AI 가 언급하지 않고, 이름만으로 새로 만들지도 않는다): 유형 -> Activity 관계 이름
ACTIVITY_MANUAL_LINKS = {"opportunity": "opportunities"}


def activity_form_link_fields() -> list[tuple[str, str, str]]:
    """활동 기록 폼의 연결 칸 전체: ACTIVITY_LINKS 다음에 직접 고르는 연결."""
    return [(code, TYPES[code].label, f"{code}_ids") for code in (*ACTIVITY_LINKS, *ACTIVITY_MANUAL_LINKS)]


# 활동 기록 등록 화면의 `?<파라미터>=<번호>` 미리 고르기 (다른 화면의 "+ 활동 기록 쓰기"): 파라미터 -> 유형. 있는 기록일 때만 쓰고 아니면 무시한다.
ACTIVITY_PREFILL = {"customer_id": "customer", "partner_id": "partner", "project_id": "project", "solution_id": "solution", "opportunity_id": "opportunity"}


# 이름만으로 새로 만들 수 있는 대상 (활동 기록 저장·올리기): 관계 이름 -> 유형
ACTIVITY_NEW_TYPES = {"solutions": "solution", "customers": "customer", "partners": "partner"}
# AI 가 본문에서 언급하는 이름의 묶음 키 (Mentions) -> 유형. 옛 키 "businesses" 는 읽을 때 "projects" 로 받는다.
MENTION_KEYS = {"solutions": "solution", "customers": "customer", "projects": "project", "partners": "partner"}
MENTION_ALIASES = {"businesses": "projects"}
# 이미 등록된 파트너·고객의 연락처에 덧붙일 수 있는 대상
CONTACT_TYPES = ("partner", "customer")

# ======================================================================================
# 지식 사본 경로 <-> (유형, 번호)
# ======================================================================================

_REL_FIELDS = {
    "id": r"[0-9]{1,18}", "solution_id": r"[0-9]{1,18}", "year": r"[0-9]{4}|undated", "day": r"[0-9]{4}-[0-9]{2}-[0-9]{2}",
    "tag": r"[^/-]+", "name": r"[^/]+",
}


def _rel_regex(fmt: str) -> re.Pattern:
    parts = re.split(r"\{(\w+)\}", fmt)
    body = "".join(re.escape(x) if i % 2 == 0 else f"(?P<{x}>{_REL_FIELDS[x]})" for i, x in enumerate(parts))
    return re.compile(body + r"\Z")


_REL_PATTERNS = [
    (t.code, _rel_regex(fmt)) for t in _TYPE_LIST for fmt in ((t.mirror_path, *t.legacy_mirror_paths) if t.mirror_path else ())
]


def rel_path(code: str, **fields) -> str:
    """대상의 지식 사본 경로 (지식 폴더 기준)."""
    fmt = TYPES[code].mirror_path
    if fmt is None:
        raise KeyError(f"{code} 는 지식 사본이 없다")
    return fmt.format(**fields)


def mirror_dir(code: str, **fields) -> str:
    """대상의 사본 폴더 경로 (끝에 `/`)."""
    return TYPES[code].mirror_dir_format.format(**fields)


def mirror_folder(code: str) -> str:
    """대상 유형 사본이 모이는 맨 위 폴더 이름."""
    return (TYPES[code].mirror_path or TYPES[code].mirror_dir_format).split("/")[0]


def legacy_mirror_folders() -> tuple[str, ...]:
    """옛 경로 형식이 쓰던 맨 위 폴더 이름 (지식 사본에서 더는 만들지 않는다). 예: businesses"""
    return tuple(dict.fromkeys(f.split("/")[0] for t in _TYPE_LIST for f in t.legacy_mirror_paths))


def parse_rel(path: str) -> tuple[str, dict[str, str]] | None:
    """사본 경로를 (대상 유형, 경로 안의 값들) 로 읽는다. 옛 형식(`businesses/3.md`)도 읽는다. 맞지 않으면 None."""
    path = str(path or "").strip().replace("\\", "/")
    for code, pattern in _REL_PATTERNS:
        m = pattern.match(path)
        if m:
            return code, m.groupdict()
    return None


# ======================================================================================
# 관계 (저장된 것만. 따라가기는 이후 단위에서 더한다)
# ======================================================================================

STORAGE_KINDS = ("fk", "link", "json", "text")


@dataclass(frozen=True)
class Relation:
    key: str
    label: str  # 한국어 동사
    source: tuple[str, ...]
    target: tuple[str, ...]
    cardinality: str  # 방향은 source -> target: 1:N, N:1, N:M
    storage: str  # fk 외래 키 / link 연결 표 / json JSON 안의 번호 / text 글(이름·경로·종류+번호)
    physical: str  # 저장 위치: "표.열" (여럿이면 공백으로 구분), 연결 표는 표 이름
    note: str = ""


def _r(key, label, source, target, cardinality, storage, physical, note=""):
    return Relation(key, label, (source,) if isinstance(source, str) else tuple(source),
                    (target,) if isinstance(target, str) else tuple(target), cardinality, storage, physical, note)


_CORE = ("solution", "partner", "customer", "project", "activity", "proposal", "opportunity", "bm")
RELATIONS: tuple[Relation, ...] = (
    _r("R1", "공급한다", "partner", "solution", "1:N", "fk", "solutions.partner_id", "솔루션은 파트너가 없을 수 있다"),
    _r("R2", "가진다", "customer", "project", "1:N", "fk", "businesses.customer_id", "프로젝트는 고객이 반드시 있다"),
    _r("R3", "붙는다", "solution", "solution_file", "1:N", "fk", "solution_files.solution_id"),
    _r("R4", "다룬다", "activity", "solution", "N:M", "link", "activity_solutions"),
    _r("R5", "관련된다", "activity", "customer", "N:M", "link", "activity_customers"),
    _r("R6", "관련된다", "activity", ("project", "opportunity"), "N:M", "link", "activity_businesses activity_opportunities",
       "R6': 프로젝트는 activity_businesses, 영업기회는 activity_opportunities. 영업기회는 활동 기록 화면에서 직접 고른다 (이름 언급으로 만들지 않는다)"),
    _r("R7", "관련된다", "activity", "partner", "N:M", "link", "activity_partners"),
    _r("R8", "나온다", "activity", "follow_up", "1:N", "fk", "follow_ups.activity_id", "활동 기록을 지우면 함께 지워진다. 옛 JSON 열은 읽지 않는다"),
    _r("R9", "나온다", "activity", "customer_need", "1:N", "fk", "customer_needs.activity_id", "활동 기록을 지우면 함께 지워진다. 옛 JSON 열은 읽지 않는다"),
    _r("R10", "의 것이다", "customer_need", "customer", "N:1", "fk", "customer_needs.customer_id customer_needs.customer_name",
       "선택. 쓴 이름 글자도 남기고, 고객을 지우면 연결만 풀린다. 이름이 등록된 고객과 정확히 하나로 맞을 때만 연결한다"),
    _r("R11", "맡는다", "follow_up", "user", "N:1", "fk", "follow_ups.owner_user_id follow_ups.owner",
       "선택. 쓴 담당 글자도 남긴다. 활성 사용자 이름과 정확히 같을 때만 연결한다"),
    _r("R12", "대상으로 한다", "proposal", "customer", "N:1", "fk", "proposals.customer_id", "특정 고객 제안은 필수, 다수 고객 제안은 비어 있다 (target_kind)"),
    _r("R13", "발판으로 한다", "proposal", "project", "N:1", "fk", "proposals.business_id", "선택. 그 고객의 프로젝트여야 한다"),
    _r("R14", "조합한다", "proposal", "solution", "N:M", "link", "proposal_solutions", "방향을 고른 뒤에만 채워진다"),
    _r("R15", "후보에 더한다", "proposal", "solution", "N:M", "json", "proposals.extra_solution_ids", "직접 추가한 후보"),
    _r("R16", "쓴다", "direction", "solution", "N:M", "json", "proposals.directions", "역할 글과 함께"),
    _r("R17", "근거로 삼는다", ("direction", "slide", "suggestion"),
       ("solution", "solution_file", "customer", "project", "partner", "activity", "research_report", "opportunity", "bm"), "N:M", "text",
       "proposals.directions proposals.outline research_reports.suggestions", "지식 사본 경로 글자. 해석은 ontology.parse_rel"),
    _r("R18", "민다", "suggestion", "solution", "N:M", "json", "research_reports.suggestions"),
    _r("R19", "등록된다", "research_report", "solution", "1:N", "fk", "solutions.origin_type solutions.origin_id solutions.origin_item",
       "origin_type='research', origin_id=보고서 번호, origin_item='suggestion:N'/'trend:N'. 보고서 번호는 조사를 지우면 비운다. 옛 등록은 채우지 못했다"),
    _r("R20", "담는다", "intake", "intake_file", "1:N", "fk", "intake_files.intake_id"),
    _r("R21", "원본이다", "intake_file", ("activity", "solution", "partner", "customer"), "N:1", "text",
       "intake_files.target_type intake_files.target_id", "종류+번호. 활동 기록만 읽는다. 대상을 지우면 함께 비운다"),
    _r("R22", "만들어진다", "intake", ("solution", "activity", "customer", "partner"), "1:N", "fk",
       "solutions.origin_type solutions.origin_id activities.origin_type activities.origin_id "
       "customers.origin_type customers.origin_id partners.origin_type partners.origin_id",
       "origin_type='intake', origin_id=묶음 번호. 새로 만든 기록만 (연결·덧붙임은 아님). 묶음 번호는 `Intake.result` 로 한 번 되짚어 채웠다"),
    _r("R23", "낸다", "research_run", "research_report", "1:N", "fk", "research_reports.run_id"),
    _r("R24", "작성한다", "user", _CORE, "1:N", "fk", "partners.created_by_id", "삭제 권한의 기준. 모든 핵심 대상의 created_by_id"),
    _r("R25", "같은 회사다", "partner", "customer", "1:1", "fk", "partners.customer_id", "선택. 한 파트너에 고객 하나, 한 고객에 파트너 하나"),
    # 단위 4 (13장): 활동 기록 → 고객 니즈 → 영업기회 → 프로젝트. BM 은 고객에 묶이지 않는다
    _r("R26", "모인다", "customer_need", "opportunity", "N:1", "fk", "customer_needs.opportunity_id",
       "선택. 한 니즈는 영업기회 하나에만. 같은 고객의 니즈만. 외래 키 제약 없는 번호 열이라 응용 수준에서 지킨다 (영업기회를 지우면 비운다)"),
    _r("R27", "의 것이다", "opportunity", "customer", "N:1", "fk", "opportunities.customer_id", "필수"),
    _r("R28", "검토한다", "opportunity", "solution", "N:M", "link", "opportunity_solutions", "제안 후보를 정렬할 때 앞에 둔다"),
    _r("R29", "위한 것이다", "proposal", "opportunity", "N:1", "fk", "proposals.opportunity_id",
       "선택. 그 고객의 영업기회여야 한다. 영업기회를 지우면 비운다"),
    _r("R30", "이어진다", "opportunity", "project", "1:1", "fk", "opportunities.project_id",
       "선택. 수주(won)일 때 프로젝트로 전환하거나 같은 고객의 기존 프로젝트를 잇는다. 한 영업기회에 프로젝트 하나, 한 프로젝트에 영업기회 하나. 프로젝트를 지우면 비운다"),
    _r("R31", "따른다", "project", "bm", "N:1", "fk", "businesses.bm_id", "선택"),
    _r("R32", "접근한다", "opportunity", "bm", "N:1", "fk", "opportunities.bm_id", "선택"),
    _r("R33", "구성된다", "bm", "solution", "N:M", "link", "bm_solutions", "제안 후보를 정렬할 때 영업기회 다음에 둔다"),
    # 베타 W: 다수 고객 제안 (2026-11-23 결정). 대상 그룹은 이름 글(group_name)이고, 등록된 고객은 0..n 개 붙일 수 있다. 고객별 진행 상태는 두지 않는다
    _r("R34", "고객을 붙인다", "proposal", "customer", "N:M", "link", "proposal_customers",
       "다수 고객 제안에만. 선택(0..n). 예시로 읽을 고객이다. 고객을 지우면 붙임만 풀린다"),
    _r("R35", "신규 사업으로 삼는다", "proposal", "bm", "N:1", "fk", "proposals.bm_id",
       "다수 고객 제안에만. 선택이지만 화면에서 눈에 띄게 보인다. 후보 솔루션은 그 BM 의 솔루션이 앞선다"),
    # 베타 AD: 솔루션 분야 (2026-11-24 결정). 분야 목록은 관리자가 고치는 데이터다
    _r("R36", "묶는다", "solution_field", "solution", "1:N", "fk", "solutions.field_id",
       "선택 (비면 분야 미지정). 분야를 지울 수 있는 것은 쓰는 솔루션이 없을 때뿐이고, 사용을 끈 분야는 새로 고를 수 없지만 붙은 솔루션은 그대로 가진다"),
)

# ======================================================================================
# 따라가기 정의 (저장된 관계의 보는 이름, 따라가지 않는 관계, 따라가서 얻는 관계, 목록 걸러 보기)
# 따라가는 엔진은 app/relations.py 이고, 여기에는 정의만 둔다. 저장된 관계(fk·link)는 위 RELATIONS 의
# 물리 위치(ORM 메타데이터)로 자동으로 따라간다. JSON·글에 든 관계만 아래 RESOLVERS 에 작은 해석기를 등록한다.
# ======================================================================================

# 다른 기록 안에 들어 있는 유형 -> 그것을 담은 기록 유형 (따라갈 때는 담은 기록을 대신 쓴다)
HOLDERS = {"suggestion": "research_report", "direction": "proposal"}

# 따라가지 않는 저장된 관계와 그 이유 (비어 있지 않아야 한다)
UNTRAVERSED = {
    "R16": "방향은 제안 건의 JSON 안에만 있어 따라갈 대상 기록이 없다 (선택한 방향의 솔루션은 R14 로 따라간다)",
    "R17": "근거는 방향·장·제안 안의 지식 사본 경로 글자라 대상 기록이 없다. 해석은 parse_rel / sources.py",
    "R36": "분야는 관리자가 고치는 분류 목록이라 상세 화면이 없다. 솔루션 화면의 속성 줄·목록의 묶어 보기로 보이고 연결 목록에는 올리지 않는다",
}
# 연결 목록에 올리지 않는 관계 (모든 대상에 붙어 소음이 된다). 따라가기(path_query)는 된다.
QUIET_RELATIONS = {"R24": "작성자는 모든 핵심 대상에 붙어 연결 목록에 올리지 않는다"}

# 유래 관계(R19·R22)의 짝 유형 -> origin_type 값
ORIGIN_PARENTS = {"intake": ORIGINS_OF_INTAKE, "research_report": (ORIGIN_RESEARCH,)}
# 올리기 묶음에 닿는 유형: 열 수 있는 사람만 보인다 (origin.can_open_intake)
INTAKE_BACKED_TYPES = ("intake", "intake_file")


def _vl(key, pairs):
    return {(key, a, b): label for (a, b), label in pairs.items()}


# (관계 키, 출발 유형, 도착 유형) -> 출발한 대상의 눈으로 본 한국어 이름
VIEW_LABELS: dict[tuple[str, str, str], str] = {}
for _key, _pairs in {
    "R1": {("partner", "solution"): "공급하는 솔루션", ("solution", "partner"): "공급 파트너"},
    "R2": {("customer", "project"): "프로젝트", ("project", "customer"): "고객"},
    "R3": {("solution", "solution_file"): "자료", ("solution_file", "solution"): "솔루션"},
    "R4": {("activity", "solution"): "이야기된 솔루션", ("solution", "activity"): "이 솔루션이 이야기된 활동 기록"},
    "R5": {("activity", "customer"): "고객", ("customer", "activity"): "활동 기록"},
    "R6": {("activity", "project"): "프로젝트", ("activity", "opportunity"): "영업기회",
           ("project", "activity"): "활동 기록", ("opportunity", "activity"): "활동 기록"},
    "R7": {("activity", "partner"): "파트너", ("partner", "activity"): "활동 기록"},
    "R8": {("activity", "follow_up"): "후속 조치", ("follow_up", "activity"): "나온 활동 기록"},
    "R9": {("activity", "customer_need"): "고객 니즈", ("customer_need", "activity"): "나온 활동 기록"},
    "R10": {("customer_need", "customer"): "고객", ("customer", "customer_need"): "고객 니즈"},
    "R11": {("follow_up", "user"): "담당 사용자", ("user", "follow_up"): "맡은 후속 조치"},
    "R12": {("proposal", "customer"): "대상 고객", ("customer", "proposal"): "제안 건"},
    "R13": {("proposal", "project"): "발판 프로젝트", ("project", "proposal"): "제안 건"},
    "R14": {("proposal", "solution"): "선택한 방향의 솔루션", ("solution", "proposal"): "이 솔루션이 들어간 제안 건"},
    "R15": {("proposal", "solution"): "후보에 더한 솔루션", ("solution", "proposal"): "후보로 더한 제안 건"},
    "R18": {("research_report", "solution"): "민 솔루션", ("solution", "research_report"): "언급한 리서치 제안"},
    "R19": {("research_report", "solution"): "등록된 솔루션", ("solution", "research_report"): "등록된 리서치 보고서"},
    "R20": {("intake", "intake_file"): "올린 파일", ("intake_file", "intake"): "올리기 묶음"},
    "R21": {**{("intake_file", t): "원본이 된 기록" for t in ("activity", "solution", "partner", "customer")},
            **{(t, "intake_file"): "원본 올린 파일" for t in ("activity", "solution", "partner", "customer")}},
    "R22": {**{("intake", t): "만들어진 기록" for t in ("activity", "solution", "partner", "customer")},
            **{(t, "intake"): "만들어진 올리기 묶음" for t in ("activity", "solution", "partner", "customer")}},
    "R23": {("research_run", "research_report"): "보고서", ("research_report", "research_run"): "조사 실행"},
    "R24": {**{("user", t): "작성한 기록" for t in _CORE}, **{(t, "user"): "작성자" for t in _CORE}},
    "R25": {("partner", "customer"): "같은 회사 고객", ("customer", "partner"): "같은 회사 파트너"},
    "R26": {("customer_need", "opportunity"): "영업기회", ("opportunity", "customer_need"): "고객 니즈"},
    "R27": {("opportunity", "customer"): "고객", ("customer", "opportunity"): "영업기회"},
    "R28": {("opportunity", "solution"): "검토한 솔루션", ("solution", "opportunity"): "이 솔루션을 검토한 영업기회"},
    "R29": {("proposal", "opportunity"): "영업기회", ("opportunity", "proposal"): "제안 건"},
    "R30": {("opportunity", "project"): "이어진 프로젝트", ("project", "opportunity"): "이 프로젝트가 된 영업기회"},
    "R31": {("project", "bm"): "BM", ("bm", "project"): "따르는 프로젝트"},
    "R32": {("opportunity", "bm"): "BM", ("bm", "opportunity"): "이 BM으로 진행하는 영업기회"},
    "R33": {("bm", "solution"): "솔루션", ("solution", "bm"): "BM"},
    "R34": {("proposal", "customer"): "붙인 고객", ("customer", "proposal"): "붙은 다수 고객 제안 건"},
    "R35": {("proposal", "bm"): "신규 사업 (BM)", ("bm", "proposal"): "신규 사업으로 삼은 다수 고객 제안 건"},
}.items():
    VIEW_LABELS.update(_vl(_key, _pairs))


@dataclass(frozen=True)
class Step:
    """경로의 한 걸음: 저장된 관계 `rel` 을 따라간다 (reverse 면 도착 쪽에서 출발 쪽으로). `to` 는 관계의 끝이 여럿일 때 도착 유형,
    `where` 는 도착한 기록의 속성이 같아야 한다는 조건 ((속성, 값) 묶음)."""
    rel: str
    reverse: bool = False
    to: str | None = None
    where: tuple[tuple[str, object], ...] = ()


def _f(rel, to=None, where=()):
    return Step(rel, False, to, tuple(where))


def _b(rel, to=None, where=()):
    return Step(rel, True, to, tuple(where))


@dataclass(frozen=True)
class Derived:
    """따라가서 얻는 관계. 저장하지 않고 경로로만 정의한다. `paths` 는 경로 후보들(합집합), 경로의 문자열 원소는 다른 Derived 키(펼쳐 넣는다)."""
    key: str
    label: str  # 출발한 대상의 눈으로 본 이름
    source: str
    target: str
    paths: tuple[tuple, ...]
    direct_note: str = ""  # 한 걸음짜리 경로로 닿았을 때 붙일 말
    minus: tuple[str, ...] = ()  # "A 빼기 B": 경로로 닿은 것에서, 같은 출발 기록이 다른 Derived 키들로 닿는 것을 뺀다 (차집합)


DERIVED: tuple[Derived, ...] = (
    Derived("D1", "이야기된 솔루션", "customer", "solution", ((_b("R5"), _f("R4")),)),
    Derived("D2", "제안된 솔루션", "customer", "solution", ((_b("R12"), _f("R14")),)),
    Derived("D3", "솔루션이 들어간 제안 건", "partner", "proposal", ((_f("R1"), _b("R14")),)),
    Derived("D4", "솔루션이 닿은 고객", "partner", "customer", ((_f("R1"), "D7"),)),
    Derived("D5", "이야기된 프로젝트", "solution", "project", ((_b("R4"), _f("R6", "project")),)),
    Derived("D5b", "활동에서 이야기된 영업기회", "solution", "opportunity", ((_b("R4"), _f("R6", "opportunity")),)),
    Derived("D6", "남은 후속 조치", "customer", "follow_up", ((_b("R5"), _f("R8", where=(("done", False),))),)),
    Derived("D7", "관심을 보인 고객", "solution", "customer",
            ((_b("R4"), _f("R5")), (_b("R14"), _f("R12")), (_b("R28"), _f("R27")))),
    Derived("D8", "따르는 프로젝트의 고객", "bm", "customer", ((_b("R31"), _b("R2")),)),
    Derived("D9", "니즈가 나온 활동 기록", "opportunity", "activity", ((_b("R26"), _b("R9")),)),
    Derived("D10", "같은 회사 고객의 프로젝트", "partner", "project", ((_f("R25"), _f("R2")),)),
    Derived("D11", "같은 회사 고객의 영업기회", "partner", "opportunity", ((_f("R25"), _b("R27")),)),
    Derived("D12", "같은 회사 고객에게 한 제안 건", "partner", "proposal", ((_f("R25"), _b("R12")),)),
    Derived("D13", "같은 회사 파트너가 공급하는 솔루션", "customer", "solution", ((_b("R25"), _f("R1")),)),
    # 이 고객과 이야기했거나(활동 기록 D1) 영업기회에서 검토했지만(R27→R28) 아직 제안 건에 들어가지 않은(D2) 솔루션: 만들 제안의 후보
    Derived("D14", "이야기했지만 아직 제안하지 않은 솔루션", "customer", "solution", (("D1",), (_b("R27"), _f("R28"))), minus=("D2",)),
    # 프로젝트 화면: 이 프로젝트가 된 영업기회의 제안 건 (제안 건이 프로젝트에 직접 이어지지 않았어도 영업기회를 통해 보인다), 이 프로젝트의 활동 기록에서 이야기된 솔루션과 그 솔루션의 파트너
    Derived("D15", "영업기회의 제안 건", "project", "proposal", ((_b("R30"), _b("R29")),)),
    Derived("D16", "이야기된 솔루션", "project", "solution", ((_b("R6"), _f("R4")),)),
    Derived("D17", "이야기된 솔루션의 파트너", "project", "partner", (("D16", _b("R1")),)),
)
DERIVED_BY_KEY = {d.key: d for d in DERIVED}

# 화면이 이미 속성 줄·특수 구역으로 보여 주어 연결 목록에서 뺄 그룹 (유형 -> 관계 키 또는 "관계 키:도착 유형")
RELATED_EXCLUDE: dict[str, tuple[str, ...]] = {
    "solution": ("R1", "R3", "R19", "R21", "R22"),
    "partner": ("R25", "R21", "R22"),
    "customer": ("R25", "R21", "R22"),
    "project": ("R2", "R31"),
    "opportunity": ("R27", "R30", "R32"),
    "bm": (),
    "activity": ("R8", "R9", "R21", "R22"),
    "proposal": ("R12", "R13", "R29", "R34", "R35"),
}
@dataclass(frozen=True)
class MainGroup:
    """상세 화면의 왼쪽 내용 칸으로 올라오는 연결 그룹: 그 대상이 "무엇으로 이루어졌는지"를 보이는 저장된 관계들.
    `key` 는 related() 그룹 키("R27:opportunity", 따라가서 얻는 관계는 "D6"), `action` 은 카드 머리의 "+ …" 동작 (글, 주소: `{id}` 는 이 기록 번호),
    `show_empty` 가 참이면 비어 있어도 카드를 보여 "+ …" 동작을 건넨다."""
    key: str
    action: tuple[str, str] | None = None
    show_empty: bool = False
    item_action: tuple[str, str] | None = None  # 항목마다 붙는 동작 (글, 주소): `{id}` 는 이 기록 번호, `{item}` 은 항목 번호


# 상세 화면 왼쪽 칸의 연결 카드 (순서대로). 여기 든 그룹은 옆 칸 "연결된 것" 에서는 빠지고, 옆 칸에는 건너서 연결된 것만 남는다. 화면마다 손으로 조인하지 않는다.
MAIN_GROUPS: dict[str, tuple[MainGroup, ...]] = {
    "customer": (
        MainGroup("R27:opportunity", ("+ 영업기회 만들기", "/opportunities/new?customer_id={id}"), True),
        MainGroup("R2:project", ("+ 프로젝트 등록", "/projects/new?customer_id={id}"), True),
        MainGroup("R12:proposal", ("+ 제안 만들기", "/proposals/new?customer_id={id}"), True),
        MainGroup("R34:proposal"),
        MainGroup("D14", None, False, ("제안 만들기", "/proposals/new?customer_id={id}&solution_id={item}")),
        MainGroup("R5:activity", ("+ 활동 기록 쓰기", "/activities/new?customer_id={id}"), True),
        MainGroup("R10:customer_need"),
        MainGroup("D6"),
    ),
    "partner": (
        MainGroup("R1:solution", ("+ 솔루션 등록", "/solutions/new?partner_id={id}"), True),
        MainGroup("R7:activity", ("+ 활동 기록 쓰기", "/activities/new?partner_id={id}"), True),
    ),
    "project": (
        MainGroup("R13:proposal", ("+ 제안 만들기", "/proposals/new?customer_id={customer_id}&project_id={id}"), True),
        MainGroup("R6:activity", ("+ 활동 기록 쓰기", "/activities/new?project_id={id}"), True),
        MainGroup("R30:opportunity"),
        MainGroup("D15"),
        MainGroup("D16"),
        MainGroup("D17"),
    ),
    "bm": (
        MainGroup("R33:solution"),
        MainGroup("R31:project"),
        MainGroup("R32:opportunity"),
        MainGroup("R35:proposal"),
    ),
    "opportunity": (
        MainGroup("R26:customer_need", ("고객 니즈 고르기", "/opportunities/{id}/edit")),
        MainGroup("R28:solution"),
        MainGroup("R29:proposal", ("+ 제안 만들기", "/proposals/new?customer_id={customer_id}&opportunity_id={id}")),
    ),
    "solution": (
        MainGroup("R14:proposal"),
        MainGroup("R4:activity", ("+ 활동 기록 쓰기", "/activities/new?solution_id={id}"), True),
    ),
}

# 연결 목록의 그룹 순서 (앞에 둘 그룹. 나머지는 관계 번호 순)
GROUP_ORDER: dict[str, tuple[str, ...]] = {
    "customer": ("R2", "R27", "R12", "R34", "R5", "R10"),
    "partner": ("R1", "R7"),
    "solution": ("R14", "R4", "R28", "R33"),
    "project": ("R13", "R6"),
    "opportunity": ("R26", "R28", "R29", "R6"),
    "bm": ("R33", "R31", "R32", "R35"),
    "activity": ("R4", "R5", "R6", "R7"),
}

# 목록 화면의 `?<파라미터>=<번호>` 걸러 보기: (목록 유형, 파라미터) -> (기준 유형, 그룹 키). 연결 목록의 "모두 보기" 도 같은 표에서 만든다.
LIST_FILTERS: dict[tuple[str, str], tuple[str, str]] = {
    ("activity", "customer_id"): ("customer", "R5"),
    ("activity", "solution_id"): ("solution", "R4"),
    ("activity", "partner_id"): ("partner", "R7"),
    ("activity", "project_id"): ("project", "R6"),
    ("activity", "opportunity_id"): ("opportunity", "R6"),
    ("proposal", "customer_id"): ("customer", "R12"),
    ("proposal", "solution_id"): ("solution", "R14"),
    ("proposal", "project_id"): ("project", "R13"),
    ("proposal", "opportunity_id"): ("opportunity", "R29"),
    ("proposal", "partner_id"): ("partner", "D3"),
    ("proposal", "member_customer_id"): ("customer", "R34"),
    ("proposal", "bm_id"): ("bm", "R35"),
    ("opportunity", "customer_id"): ("customer", "R27"),
    ("opportunity", "bm_id"): ("bm", "R32"),
    ("opportunity", "solution_id"): ("solution", "R28"),
    ("project", "customer_id"): ("customer", "R2"),
    ("project", "bm_id"): ("bm", "R31"),
    ("solution", "partner_id"): ("partner", "R1"),
    ("solution", "bm_id"): ("bm", "R33"),
    ("solution", "opportunity_id"): ("opportunity", "R28"),
}

# 목록의 `?sort=` 정렬: 유형 -> ((코드, 이름), …). 첫 항목이 지금의 기본 순서이고 (`?sort` 가 없거나 틀리면 이것), 나머지는 고르는 순서다.
# 코드 뜻: name 이름(제목)순, recent 최근 수정순, date 활동 일자순, active 최근 활동순 (표의 "최근 활동" 칸) (최근 것이 위). 표에 보이는 칸이 있는 정렬은 그 칸 머리에 방향 표시가 붙는다.
LIST_SORTS: dict[str, tuple[tuple[str, str], ...]] = {
    "solution": (("name", "이름순"), ("active", "최근 활동순"), ("recent", "최근 수정순")),
    "customer": (("name", "이름순"), ("active", "최근 활동순"), ("recent", "최근 수정순")),
    "partner": (("name", "이름순"), ("active", "최근 활동순"), ("recent", "최근 수정순")),
    "project": (("name", "이름순"), ("active", "최근 활동순"), ("recent", "최근 수정순")),
    "bm": (("name", "이름순"), ("recent", "최근 수정순")),
    "opportunity": (("recent", "최근 수정순"), ("active", "최근 활동순"), ("name", "이름순")),
    "proposal": (("recent", "최근 수정순"), ("name", "이름순")),
    "activity": (("date", "일자순"), ("recent", "최근 수정순"), ("name", "제목순")),
}

# 걸러 보기 파라미터가 아닌 "모두 보기" 목적지: (기준 유형, 그룹 키) -> 주소 ({id} 는 기준 기록 번호). 후속 조치 목록은 고객별로 모아 본다.
MORE_LINKS: dict[tuple[str, str], str] = {
    ("customer", "D6"): "/follow-ups?customer_id={id}",
}

# 목록의 "연결" 칸: 유형 -> [(보이는 이름, 관계 키 또는 따라가서 얻는 관계 키, 도착 유형)]. 건수는 relations.link_counts 가 쪽의 번호들만 묶어서 센다.
LIST_LINKS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "solution": (("고객", "D7", "customer"), ("제안", "R14", "proposal"), ("영업기회", "R28", "opportunity")),
    "customer": (("프로젝트", "R2", "project"), ("영업기회", "R27", "opportunity"), ("제안", "R12", "proposal"), ("다수 고객 제안", "R34", "proposal")),
    "partner": (("솔루션", "R1", "solution"),),
    "bm": (("솔루션", "R33", "solution"), ("프로젝트", "R31", "project"), ("영업기회", "R32", "opportunity"), ("다수 고객 제안", "R35", "proposal")),
    "project": (("제안", "R13", "proposal"), ("활동", "R6", "activity")),
    "opportunity": (("니즈", "R26", "customer_need"), ("제안", "R29", "proposal"), ("솔루션", "R28", "solution")),
    "activity": (("고객", "R5", "customer"), ("솔루션", "R4", "solution"), ("후속 조치", "R8", "follow_up")),
}
# 목록의 "최근 활동" 칸이 보는 관계 (연결된 활동 기록의 가장 늦은 일자). 여기 없는 유형은 "수정일" 을 보인다.
LIST_LATEST_ACTIVITY: dict[str, str] = {"solution": "R4", "customer": "R5", "partner": "R7", "project": "R6", "opportunity": "R6"}

# ---- JSON·글에 든 관계의 해석기 ----
# (관계 키, 출발 유형, 도착 유형) -> 함수(db, 출발 번호들) -> [(출발 번호, 도착 번호, 덧붙임|None)]. 모델은 늦게 불러온다.
RESOLVERS: dict[tuple[str, str, str], object] = {}


def _resolver(rel, src, dst):
    def deco(fn):
        RESOLVERS[(rel, src, dst)] = fn
        return fn
    return deco


def _scan(db, model, column, ids, reverse):
    """JSON 번호 목록 열을 훑어 (담은 기록 번호, 가리키는 번호) 쌍을 만든다. 거꾸로는 모든 행을 훑는다 (행이 적은 표에서만 쓴다)."""
    from sqlalchemy import select

    out = []
    for row_id, values in db.execute(select(model.id, column)):
        if not isinstance(values, list):  # 옛·깨진 값 (목록이 아닌 것)은 건너뛴다
            continue
        for v in values:
            if isinstance(v, int) and not isinstance(v, bool):
                out.append((v, row_id) if reverse else (row_id, v))
    wanted = set(ids)
    return [pair for pair in out if pair[0] in wanted]


@_resolver("R15", "proposal", "solution")
def _r15_forward(db, ids):  # proposals.extra_solution_ids (JSON, 직접 추가한 후보)
    from app.models import Proposal

    return [(a, b, None) for a, b in _scan(db, Proposal, Proposal.extra_solution_ids, ids, False)]


@_resolver("R15", "solution", "proposal")
def _r15_reverse(db, ids):
    from app.models import Proposal

    return [(a, b, None) for a, b in _scan(db, Proposal, Proposal.extra_solution_ids, ids, True)]


def _suggestion_hits(db, report_ids=None):
    from sqlalchemy import select

    from app.models import ResearchReport

    stmt = select(ResearchReport.id, ResearchReport.suggestions)
    for rid, suggestions in db.execute(stmt):
        if report_ids is not None and rid not in report_ids:
            continue
        if not isinstance(suggestions, list):
            continue
        for n, sug in enumerate(suggestions):
            if not isinstance(sug, dict):
                continue
            sids = sug.get("solution_ids")
            if not isinstance(sids, list):
                continue
            title, kind = sug.get("title"), sug.get("kind")
            for sid in sids:
                if isinstance(sid, int) and not isinstance(sid, bool):
                    yield rid, sid, {"title": title if isinstance(title, str) and title else "(제목 없음)",
                                     "kind": kind if isinstance(kind, str) else "", "n": n}


@_resolver("R18", "research_report", "solution")
def _r18_forward(db, ids):  # research_reports.suggestions[].solution_ids (JSON)
    return [(rid, sid, meta) for rid, sid, meta in _suggestion_hits(db, set(ids))]


@_resolver("R18", "solution", "research_report")
def _r18_reverse(db, ids):
    wanted = set(ids)
    return [(sid, rid, meta) for rid, sid, meta in _suggestion_hits(db) if sid in wanted]


def _r21_pairs(db, ids, target_type, reverse):
    from sqlalchemy import select

    from app.models import IntakeFile

    stmt = select(IntakeFile.id, IntakeFile.target_id).where(IntakeFile.target_type == target_type, IntakeFile.target_id.is_not(None))
    pairs = [(fid, tid) for fid, tid in db.execute(stmt)]
    wanted = set(ids)
    if reverse:
        return [(tid, fid, None) for fid, tid in pairs if tid in wanted]
    return [(fid, tid, None) for fid, tid in pairs if fid in wanted]


for _t in ("activity", "solution", "partner", "customer"):  # intake_files.target_type/target_id (글: 종류+번호)
    RESOLVERS[("R21", "intake_file", _t)] = (lambda db, ids, _t=_t: _r21_pairs(db, ids, _t, False))
    RESOLVERS[(("R21"), _t, "intake_file")] = (lambda db, ids, _t=_t: _r21_pairs(db, ids, _t, True))
# 해석기가 JSON·글 기반임을 표시한다 (화면과 문서가 "글에서 찾음" 으로 구분한다)
JSON_BACKED = {"R15", "R18", "R21"}


def related(db, obj, *, viewer=None, exclude=None, limit: int = 10):
    """`obj` 에서 연결된 것을 그룹별로: 저장된 관계(양방향) 다음에 따라가서 얻는 관계. app/relations.py 가 따라간다."""
    from app import relations

    return relations.related(db, obj, viewer=viewer, exclude=exclude, limit=limit)


def path_query(db, start, keys):
    """(앱 코드에서 부르는 곳은 없다. 문서의 확인 질문(`docs/ontology.md`)과 테스트가 이 함수로 따라가기를 검증한다.)
    `start` 에서 관계 키들(예: ["R1", "R14", "R12"], 끝 유형은 "R6:project", 따라가서 얻는 관계는 "D7")을 차례로 따라가 닿은 기록들."""
    from app import relations

    return relations.path_query(db, start, keys)


# ======================================================================================
# 규칙
# ======================================================================================


def proposal_candidate_rule_text() -> str:
    """제안 후보 규칙 설명 (화면·프롬프트가 같은 말을 쓴다)."""
    return f"{STAGES[STAGE_HOLD]} 단계를 뺀 모든 솔루션과, 제안 건에 직접 추가한 솔루션"


# ---- 영업기회·BM 행위 조건 (조건 코드는 app/opportunities.py, 화면 안내 글은 여기 한 곳) ----
OPPORTUNITY_RULES = {
    "bundle": "묶을 수 있는 니즈: 이 영업기회의 고객에 연결된 니즈 중 아직 다른 영업기회에 묶이지 않은 것",
    "unlinked_note": "고객에 연결되지 않은 니즈는 활동 기록에서 고객 이름을 등록된 이름으로 고치면 여기에 나옵니다",
    "convert": "프로젝트로 전환: 수주 단계이고 아직 프로젝트가 없을 때 한 번만. 같은 고객의 기존 프로젝트를 이을 수도 있습니다",
    "delete": "삭제하면 묶은 니즈는 풀리고(니즈는 남습니다) 제안 건의 연결은 비워집니다. 프로젝트는 지워지지 않습니다",
    "bm_delete": "프로젝트·영업기회가 따르거나 다수 고객 제안 건이 신규 사업으로 삼은 BM 은 삭제할 수 없습니다",
}


# ---- 삭제 확인 화면의 "지우면 이렇게 됩니다" (각 삭제 라우터의 규칙을 그대로 옮겨 적은 것. 확인 화면 `routers/confirm.py` 가 읽는다) ----
DELETE_EFFECTS: dict[str, tuple[str, ...]] = {
    "solution": ("올린 자료 파일과 지식 사본이 함께 지워집니다.",
                 "이 솔루션이 적힌 활동 기록·BM·영업기회·제안 건의 연결이 풀립니다 (그 기록들은 남습니다)."),
    "partner": ("이 파트너의 솔루션은 남고 파트너 칸만 비워집니다.",
                "같은 회사로 이은 고객 연결이 풀립니다 (고객 기록은 남습니다).",
                "활동 기록과의 연결이 풀립니다."),
    "customer": ("고객 니즈 줄은 남고 고객 연결만 풀립니다 (적힌 고객 이름은 그대로).",
                 "다수 고객 제안 건에 붙은 것이 빠집니다 (제안 건은 남습니다).",
                 "같은 회사로 이은 파트너 연결이 풀립니다 (파트너 기록은 남습니다)."),
    "project": ("이어진 영업기회는 남고 프로젝트 연결만 비워집니다.", "활동 기록과의 연결이 풀립니다 (활동 기록은 남습니다)."),
    "bm": ("솔루션과의 연결이 풀립니다 (솔루션은 남습니다).",),
    "opportunity": (OPPORTUNITY_RULES["delete"] + ".",),
    "activity": ("후속 조치와 고객 니즈 줄이 함께 지워집니다.",
                 "연결된 솔루션·고객·파트너·프로젝트는 남습니다.",
                 "이 기록을 만든 올린 원본 파일은 남습니다."),
    "proposal": ("대화·방향·구성안과 만든 제안서 파일이 함께 지워집니다.", "대상 고객(붙인 고객)·신규 사업(BM)·솔루션 기록은 남습니다."),
    "solution_file": ("올린 자료 파일이 지워지고 지식 사본에서도 빠집니다.", "솔루션 기록은 남습니다."),
    "intake_discard": ("정리안은 버려지고 저장되지 않습니다 (아무 기록도 만들어지지 않습니다).", "올린 원본 파일은 남습니다."),
    "research_run": ("이 조사의 보고서(동향·제안)가 모두 지워집니다.", "보고서에서 등록한 솔루션은 남고 유래 표시만 비워집니다."),
    "solution_field": ("이 분야는 목록에서 사라집니다 (쓰는 솔루션이 없을 때만 지울 수 있습니다).",),
    "research_topic": ("이 주제는 다음 조사부터 빠집니다. 이미 끝난 조사 결과는 남습니다.",),
    "outline_slide": ("이 슬라이드의 내용이 지워집니다.", "이미 만든 제안서 파일은 그대로이고, 다시 만들 때 반영됩니다."),
}


# 지우기 확인 화면의 "연결이 끊깁니다" 건수: 유형 -> (끊기는 연결 [(보이는 이름, 관계 키, 도착 유형)], 함께 지워지는 줄 [(이름, 관계 키, 도착 유형)],
# 제안 건 안에 들어 있는 경우의 문장 [(관계 키, 문장 틀 "{n}")]). 건수는 relations.link_counts 로 센다 (지우는 규칙은 바꾸지 않고 알려 주기만 한다).
DELETE_IMPACT: dict[str, dict] = {
    "solution": {"cut": (("영업기회", "R28", "opportunity"), ("BM", "R33", "bm"), ("활동 기록", "R4", "activity")),
                 "inside": (("R14", "제안 건 {n}건의 방향에 이 솔루션이 들어 있습니다."),)},
    "partner": {"cut": (("솔루션의 파트너 칸", "R1", "solution"), ("활동 기록", "R7", "activity"), ("같은 회사 고객", "R25", "customer"))},
    "customer": {"cut": (("활동 기록", "R5", "activity"), ("고객 니즈", "R10", "customer_need"), ("같은 회사 파트너", "R25", "partner"),
                         ("다수 고객 제안 건", "R34", "proposal"))},
    "project": {"cut": (("영업기회", "R30", "opportunity"), ("활동 기록", "R6", "activity"), ("제안 건", "R13", "proposal"), ("BM", "R31", "bm"))},
    "bm": {"cut": (("솔루션", "R33", "solution"), ("프로젝트", "R31", "project"), ("영업기회", "R32", "opportunity"))},
    "opportunity": {"cut": (("고객 니즈", "R26", "customer_need"), ("제안 건", "R29", "proposal"), ("프로젝트", "R30", "project"),
                            ("활동 기록", "R6", "activity"), ("솔루션", "R28", "solution"), ("BM", "R32", "bm"))},
    "activity": {"cut": (("솔루션", "R4", "solution"), ("고객", "R5", "customer"), ("파트너", "R7", "partner"), ("프로젝트", "R6", "project"), ("영업기회", "R6", "opportunity")),
                 "gone": (("후속 조치", "R8", "follow_up"), ("고객 니즈", "R9", "customer_need"))},
    "proposal": {"cut": (("고객", "R12", "customer"), ("붙인 고객", "R34", "customer"), ("프로젝트", "R13", "project"), ("영업기회", "R29", "opportunity"),
                         ("신규 사업(BM)", "R35", "bm"), ("솔루션", "R14", "solution"))},
}


# 제안 대화 입력칸 위의 시작 질문 (아직 이 방향의 대화가 없을 때만 화면에 칩으로 나온다)
PROPOSAL_CHAT_STARTERS: tuple[str, ...] = (
    "예산을 절반으로 줄이면 어떻게 구성할까요?",
    "1단계에 꼭 넣어야 할 범위만 남겨 주세요",
    "이 고객이 가장 걱정할 점과 답변을 정리해 주세요",
    "경쟁 방식과 비교한 강점을 알려 주세요",
    "도입 일정을 단계별로 제안해 주세요",
)


# 연결을 이르는 말 (화면의 "이야기된 / 검토한 / 제안된" 이름이 같은 뜻으로 쓰이도록 도움말이 풀어 준다)
RELATION_WORDS: tuple[tuple[str, str], ...] = (
    ("이야기된", "활동 기록(미팅·통화 메모)에서 언급된 것입니다."),
    ("검토한", "영업기회에서 고객 니즈에 맞춰 검토한 것입니다."),
    ("제안된", "제안 건의 방향에 들어가 고객에게 제안되는 것입니다 (제안 건 화면에서는 \"선택한 방향의 솔루션\"으로 보입니다)."),
)


# 도움말 화면(`/help`)의 용어 풀이: 대상 유형 코드 순서. 이름·뜻은 위 유형 정의(label·description)에서 읽는다 (여기에 다시 적지 않는다).
HELP_GLOSSARY: tuple[str, ...] = ("solution", "partner", "customer", "opportunity", "project", "bm", "activity", "customer_need",
                                  "follow_up", "proposal", "intake", "solution_file")


# 구성안 슬라이드 편집 폼의 칸 이름 (충돌 안내가 어느 칸이 다른지 보일 때 쓴다: 장 종류·제목·핵심 메시지·출처는 위 속성 이름표 그대로)
SLIDE_FORM_LABELS = {
    **TYPES["slide"].props,
    "subtitle": "부제", "bullets": "글머리", "left_title": "왼쪽 제목", "left": "왼쪽 내용", "right_title": "오른쪽 제목", "right": "오른쪽 내용",
    "columns": "표의 열", "rows": "표의 줄", "steps": "단계", "needs_check": "확인할 점",
}
# 충돌 비교에서 값을 코드 이름으로 바꿀 칸: (대상 유형, 칸) -> 코드 목록
CONFLICT_CODE_FIELDS = {("solution", "stage"): "stage", ("opportunity", "stage"): "opportunity_stage", ("activity", "kind"): "activity_kind"}

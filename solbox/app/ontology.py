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

# ======================================================================================
# 코드 값 목록 (각각 한 번만 정의한다)
# ======================================================================================


@dataclass(frozen=True)
class CodeList:
    key: str
    label: str  # 이 목록의 한국어 이름
    items: dict[str, str]  # 코드 -> 화면 이름
    default: str | None = None


STAGES = {  # 발굴 단계
    "intake": "아이템 접수",
    "tapping": "사전 태핑",
    "survey": "수요조사",
    "boxed": "박스 등록",
    "hold": "보류",
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
    "intake_analyze": "올린 파일 정리",
    "ai_ping": "AI 연결 확인",
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
    "intake_analyze": "보통 1분 안에",
    "ai_ping": "보통 10초 안에",
}

ERROR_KINDS = {
    "not_installed": "Claude CLI가 설치되어 있지 않습니다. 서버에 설치되어 있는지 확인해 주세요.",
    "auth": "Claude 로그인이 필요합니다. 서버에서 Claude에 다시 로그인해 주세요.",
    "limit": "Claude 사용 한도에 도달했습니다. 잠시 뒤에 다시 시도해 주세요.",
    "network": "네트워크 연결에 문제가 있습니다. 연결을 확인한 뒤 다시 시도해 주세요.",
    "timeout": "제한 시간 안에 작업이 끝나지 않았습니다. 잠시 뒤에 다시 시도해 주세요.",
    "bad_output": "AI가 예상하지 못한 형식으로 답했습니다. 다시 시도해 주세요.",
    "unknown": "알 수 없는 오류가 발생했습니다. 잠시 뒤에 다시 시도해 주세요.",
}

# 확인 항목의 종류: 근거 문제 / 확인할 점 (저장된 값이 아니라 문구에서 가려낸다. app/checks.py)
CHECK_KINDS = {"evidence": "근거 문제", "decide": "확인할 점"}

ORIGINS = {"manual": "직접 입력", "intake": "올리기", "research": "리서치"}  # 기록의 유래 (비어 있으면 모름)
ORIGIN_MANUAL, ORIGIN_INTAKE, ORIGIN_RESEARCH = "manual", "intake", "research"

WEEKDAYS = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}

CODE_LISTS: dict[str, CodeList] = {c.key: c for c in (
    CodeList("stage", "발굴 단계", STAGES, STAGE_DEFAULT),
    CodeList("activity_kind", "활동 종류", ACTIVITY_KINDS, ACTIVITY_KIND_DEFAULT),
    CodeList("file_kind", "자료 종류", FILE_KINDS, FILE_KIND_DEFAULT),
    CodeList("proposal_status", "제안 진행 단계", PROPOSAL_STATUSES, PROPOSAL_STATUS_DEFAULT),
    CodeList("proposal_step", "제안 단계 표시줄", PROPOSAL_STEPS),
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
    CodeList("check_kind", "확인 항목 종류", CHECK_KINDS),
    CodeList("origin_type", "기록의 유래", ORIGINS),
)}

# 모델 열이 어느 코드 목록의 값을 담는지 (물리 표 이름, 열 이름). 일관성 검사가 기본값을 대조한다.
COLUMN_CODES = {
    ("solutions", "stage"): "stage",
    ("activities", "kind"): "activity_kind",
    ("solution_files", "kind"): "file_kind",
    ("proposals", "status"): "proposal_status",
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
    "intake_nav": "올리기",
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
    embedded_in: str | None = None  # 다른 기록 안에 들어 있다면 "표.열" 또는 설명
    props: dict[str, str] = field(default_factory=dict)  # 속성 이름 -> 화면 이름표
    physical: dict[str, str] = field(default_factory=dict)  # 물리 이름이 코드 이름과 다른 곳

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
        "solutions/{id}/card.md", mirror_dir_format="solutions/{id}/", nav="솔루션",
        props={"name": "이름", "summary": "한 줄 소개", "description": "상세 설명", "partner": "파트너", "stage": "발굴 단계",
               "usage_direction": "활용 방향", "applicable_types": "적용 가능 유형", "tags": "태그", "files": "자료", "origin": "유래"},
    ),
    ObjectType(
        "partner", "파트너", "핵심", "Partner", "partners", "/partners", "/partners/{id}", "partners/{id}.md", nav="파트너",
        props={"name": "이름", "contact": "연락처", "description": "소개", "memo": "메모", "solutions": "솔루션", "origin": "유래",
               "customer": "같은 회사인 고객"},
    ),
    ObjectType(
        "customer", "고객", "핵심", "Customer", "customers", "/customers", "/customers/{id}", "customers/{id}.md", nav="고객",
        props={"name": "이름", "industry": "업종", "contact": "연락처", "memo": "메모", "projects": "프로젝트", "origin": "유래",
               "partner": "같은 회사인 파트너"},
    ),
    ObjectType(
        "project", "프로젝트", "핵심", "Project", "businesses", "/projects", "/projects/{id}", "projects/{id}.md",
        legacy_mirror_paths=("businesses/{id}.md",), nav="프로젝트",
        props={"name": "프로젝트명", "customer": "고객", "description": "설명", "period": "기간", "status": "상태", "memo": "메모"},
        physical={"fk_column": "business_id", "link_table": "activity_businesses"},
    ),
    ObjectType(
        "activity", "활동 기록", "핵심", "Activity", "activities", "/activities", "/activities/{id}",
        "activities/{year}/{id}.md", nav="활동 기록",
        props={"kind": "종류", "date": "일자", "title": "제목", "raw_text": "원문", "summary_md": "정리본",
               "follow_ups": "후속 조치", "demand_signals": "고객 니즈", "solutions": "솔루션", "customers": "고객",
               "projects": "프로젝트", "partners": "파트너", "origin": "유래"},
    ),
    ObjectType(
        "proposal", "제안 건", "핵심", "Proposal", "proposals", "/proposals", "/proposals/{id}", nav="제안",
        props={"title": "제목", "request_memo": "요청 메모", "status": "진행 단계", "customer": "고객", "project": "프로젝트",
               "directions": "제안 방향", "outline": "구성안"},
    ),
    ObjectType(
        "solution_file", "자료", "부속", "SolutionFile", "solution_files",
        mirror_path="solutions/{solution_id}/files/{id}-{name}.md", mirror_dir_format="solutions/{solution_id}/files/",
        parent=("solution", "solution_id"),
        props={"original_name": "파일 이름", "kind": "자료 종류", "extract_status": "글 추출 상태", "size": "크기"},
    ),
    ObjectType("follow_up", "후속 조치", "부속", "FollowUp", "follow_ups",
               props={"task": "할 일", "owner": "담당", "due": "기한", "done": "완료"}),
    ObjectType("customer_need", TERMS["customer_need"], "부속", "CustomerNeed", "customer_needs",
               props={"customer": "고객", "content": "내용"}),
    ObjectType("direction", "제안 방향", "부속", embedded_in="proposals.directions",
               props={"title": "제목", "summary": "한 줄 소개", "solutions": "솔루션", "rationale": "근거", "effects": "기대 효과"}),
    ObjectType("slide", "구성안 장", "부속", embedded_in="proposals.outline",
               props={"kind": "장 종류", "title": "제목", "key_message": "핵심 메시지", "sources": TERMS["source"]}),
    ObjectType("check_item", TERMS["check_item"], "부속", embedded_in="proposals.directions / proposals.outline / research_reports.trends",
               props={"kind": "종류", "checked": "확인함"}),
    ObjectType(
        "intake", "올리기 묶음", "작업", "Intake", "intakes", "/intake", "/intake/{id}", nav=None,
        props={"note": "메모", "status": "상태", "files": "올린 파일"},
    ),
    ObjectType(
        "intake_file", "올린 파일", "작업", "IntakeFile", "intake_files",
        props={"original_name": "이름", "kind": "종류", "read_status": "읽기 상태", "size": "크기"},
    ),
    ObjectType("plan_item", "정리안 항목", "작업", embedded_in="intakes.plan", props={"kind": "종류", "reason": TERMS["reason"]}),
    ObjectType("research_topic", "리서치 주제", "작업", "ResearchTopic", "research_topics",
               props={"name": "이름", "keywords": "검색어", "area": "분야"}),
    ObjectType("research_run", "리서치 실행", "작업", "ResearchRun", "research_runs", "/research", "/research/runs/{id}", nav="리서치",
               props={"status": "상태", "trigger": "실행 방식"}),
    ObjectType(
        "research_report", "리서치 보고서", "작업", "ResearchReport", "research_reports",
        mirror_path="research/{day}-{tag}-{id}.md", source_kind="리서치", parent=("research_run", "run_id"),
        props={"kind": "종류", "area": "분야", "title": "제목", "trends": "동향", "insights": "시사점", "suggestions": "발굴 방향 제안"},
    ),
    ObjectType("suggestion", "리서치 제안", "작업", embedded_in="research_reports.suggestions",
               props={"title": "제목", "detail": "내용", "reason": TERMS["reason"], "sources": TERMS["source"], "kind": "종류",
                      "solution_ids": "대상 솔루션"}),
    ObjectType("user", "사용자", "시스템", "User", "users", "/admin/users", props={"username": "아이디", "name": "이름", "role": "역할"}),
    ObjectType("ai_job", "AI 작업", "시스템", "AIJob", "ai_jobs", "/admin/jobs", props={"kind": "종류", "status": "상태"}),
]
TYPES: dict[str, ObjectType] = {t.code: t for t in _TYPE_LIST}

# 메뉴: (주소 앞 조각, 이름). "올리기" 는 대상이 아니라 동작이라 따로 둔다.
NAV_ITEMS = (
    ("intake", TERMS["intake_nav"]), ("", "홈"),
    *((t.list_url.strip("/"), t.nav) for t in _TYPE_LIST if t.nav and t.list_url and t.code != "research_run"),
    ("research", TYPES["research_run"].nav),
)
_NAV_ORDER = ("intake", "", "solutions", "customers", "projects", "partners", "proposals", "activities", "research")
NAV_ITEMS = tuple(sorted(NAV_ITEMS, key=lambda x: _NAV_ORDER.index(x[0])))
ADMIN_NAV_ITEMS = (("users", "사용자"), ("jobs", "AI 작업"), ("research", "리서치 설정"), ("system", "시스템"))

# 옛 주소(GET) -> 새 주소. 영구 이동한다.
LEGACY_URL_PREFIXES = {"/businesses": "/projects"}


def label(code: str) -> str:
    return TYPES[code].label


def prop(code: str, name: str) -> str:
    return TYPES[code].props[name]


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


_CORE = ("solution", "partner", "customer", "project", "activity", "proposal")
RELATIONS: tuple[Relation, ...] = (
    _r("R1", "공급한다", "partner", "solution", "1:N", "fk", "solutions.partner_id", "솔루션은 파트너가 없을 수 있다"),
    _r("R2", "가진다", "customer", "project", "1:N", "fk", "businesses.customer_id", "프로젝트는 고객이 반드시 있다"),
    _r("R3", "붙는다", "solution", "solution_file", "1:N", "fk", "solution_files.solution_id"),
    _r("R4", "다룬다", "activity", "solution", "N:M", "link", "activity_solutions"),
    _r("R5", "관련된다", "activity", "customer", "N:M", "link", "activity_customers"),
    _r("R6", "관련된다", "activity", "project", "N:M", "link", "activity_businesses"),
    _r("R7", "관련된다", "activity", "partner", "N:M", "link", "activity_partners"),
    _r("R8", "나온다", "activity", "follow_up", "1:N", "fk", "follow_ups.activity_id", "활동 기록을 지우면 함께 지워진다. 옛 JSON 열은 읽지 않는다"),
    _r("R9", "나온다", "activity", "customer_need", "1:N", "fk", "customer_needs.activity_id", "활동 기록을 지우면 함께 지워진다. 옛 JSON 열은 읽지 않는다"),
    _r("R10", "의 것이다", "customer_need", "customer", "N:1", "fk", "customer_needs.customer_id customer_needs.customer_name",
       "선택. 쓴 이름 글자도 남기고, 고객을 지우면 연결만 풀린다. 이름이 등록된 고객과 정확히 하나로 맞을 때만 연결한다"),
    _r("R11", "맡는다", "follow_up", "user", "N:1", "fk", "follow_ups.owner_user_id follow_ups.owner",
       "선택. 쓴 담당 글자도 남긴다. 활성 사용자 이름과 정확히 같을 때만 연결한다"),
    _r("R12", "대상으로 한다", "proposal", "customer", "N:1", "fk", "proposals.customer_id", "필수"),
    _r("R13", "발판으로 한다", "proposal", "project", "N:1", "fk", "proposals.business_id", "선택. 그 고객의 프로젝트여야 한다"),
    _r("R14", "조합한다", "proposal", "solution", "N:M", "link", "proposal_solutions", "방향을 고른 뒤에만 채워진다"),
    _r("R15", "후보에 더한다", "proposal", "solution", "N:M", "json", "proposals.extra_solution_ids", "직접 추가한 후보"),
    _r("R16", "쓴다", "direction", "solution", "N:M", "json", "proposals.directions", "역할 글과 함께"),
    _r("R17", "근거로 삼는다", ("direction", "slide", "suggestion"),
       ("solution", "solution_file", "customer", "project", "partner", "activity", "research_report"), "N:M", "text",
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
)

# ======================================================================================
# 규칙
# ======================================================================================


def proposal_candidate_rule_text() -> str:
    """제안 후보 규칙 설명 (화면·프롬프트가 같은 말을 쓴다)."""
    return f"{STAGES[STAGE_HOLD]} 단계를 뺀 모든 솔루션과, 제안 건에 직접 추가한 솔루션"

"""코드값과 화면에 보일 한국어 이름."""

STAGES = {
    "intake": "아이템 접수",
    "tapping": "사전 태핑",
    "survey": "수요조사",
    "boxed": "박스 등록",
    "hold": "보류",
}

ACTIVITY_KINDS = {
    "meeting": "회의",
    "partner": "파트너 미팅",
    "tapping": "고객 태핑",
    "survey": "수요조사",
    "other": "기타",
}

FILE_KINDS = {"intro": "소개", "spec": "스펙", "related": "관련"}

JOB_STATUSES = {
    "pending": "대기",
    "running": "진행",
    "done": "완료",
    "failed": "실패",
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

PROPOSAL_STATUSES = {
    "new": "대상 지정",
    "directions": "방향 검토",
    "refining": "구체화",
    "outlined": "구성안",
    "deck": "제안서 완료",
}

SLIDE_KINDS = {
    "cover": "표지",
    "bullets": "글머리",
    "two_column": "2단 비교",
    "table": "표",
    "steps": "단계",
    "closing": "마무리",
}

RESEARCH_AREAS = {"market": "시장", "tech": "기술", "policy": "정책"}

SUGGESTION_KINDS = {"new": "새로 찾을 솔루션", "push": "기존 솔루션 밀기"}

RUN_STATUSES = {"running": "진행", "done": "완료", "partial": "일부 실패", "failed": "실패"}

WEEKDAYS = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}

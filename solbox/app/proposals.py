"""제안 건 도우미."""
from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, selectinload

from app import business_types as bts, checks, ontology, ui
from app.checks import NO_EVIDENCE_NOTE as NO_SOURCE_NOTE, check_kind  # noqa: F401  (다른 곳에서 여기서 불러다 쓴다)
from app.models import AIJob, Proposal, Solution, User

if TYPE_CHECKING:
    from app.ai.proposal_tasks import DirectionsOut, Outline

log = logging.getLogger(__name__)

MAX_DIRECTIONS = 3
SLIDE_KINDS = tuple(ontology.SLIDE_KINDS)  # 정의는 ontology
CK = ontology.CHECK_KINDS
MAX_STEPS = 5
MAX_SLIDES = 14
NO_COLUMNS = "표의 열 이름을 입력하세요."
VISUAL_TEXT_FIELDS = ("chart_type", "unit", "note", "diagram_type", "center", "axis_x", "axis_y")  # 시각 자료 장의 한 줄 칸 (베타 AG)
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
_QUADRANT_ALIASES = {
    "왼쪽위": "왼쪽 위", "좌상": "왼쪽 위", "좌측상단": "왼쪽 위", "1": "왼쪽 위",
    "오른쪽위": "오른쪽 위", "우상": "오른쪽 위", "우측상단": "오른쪽 위", "2": "오른쪽 위",
    "왼쪽아래": "왼쪽 아래", "좌하": "왼쪽 아래", "좌측하단": "왼쪽 아래", "3": "왼쪽 아래",
    "오른쪽아래": "오른쪽 아래", "우하": "오른쪽 아래", "우측하단": "오른쪽 아래", "4": "오른쪽 아래",
}


def parse_number(value) -> int | float | None:
    """그래프 값 하나를 숫자로 읽는다 (`1,200`·`"2"`·`3.5`). 숫자가 아니면 None. 단위가 붙은 글(`2주`)은 숫자가 아니다: 단위는 `unit` 칸에 쓴다."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", "")
        if len(text) > 30 or not _NUMBER.fullmatch(text):
            return None
        number = float(text)
    if not math.isfinite(number) or abs(number) >= 1e15:
        return None
    return int(number) if number == int(number) else number


def number_text(value) -> str:
    """그래프 값을 편집 칸에 보일 글로 (`2.0` 이 아니라 `2`)."""
    number = parse_number(value)
    return "" if number is None else str(number)


def matrix_quadrant(text: str) -> str | None:
    """2×2 도식의 자리 이름을 맞춘다 (`우상`·`오른쪽위`·`2` → `오른쪽 위`). 빈 글은 빈 글, 모르는 글은 None."""
    key = "".join(str(text or "").split())
    if not key:
        return ""
    return _QUADRANT_ALIASES.get(key)


# ---- 대상 종류 (베타 W): 특정 고객 / 다수 고객(대상 그룹). 규칙은 여기 한 곳이고 라우터·프롬프트·제안서·목록이 이 함수들을 쓴다 ----

def is_group(p: Proposal) -> bool:
    """다수 고객 제안인가. (모르는 값은 특정 고객으로 본다: 옛 행·깨진 값)"""
    return p.target_kind == ontology.PROPOSAL_TARGET_GROUP


def normalize_group_name(raw: str) -> str:
    """대상 그룹 이름: 줄바꿈·연속 공백을 한 칸으로. 한 줄 글이라 AI 프롬프트에도 그대로 적을 수 있다."""
    return " ".join(str(raw or "").split())


def target_name(p: Proposal) -> str:
    """제안서 표지·제목·목록·AI 프롬프트에 쓰는 대상 이름: 특정 고객은 고객 이름, 다수 고객은 대상 그룹 이름. 비어 있거나 None 이 되는 일이 없다."""
    if is_group(p):
        return normalize_group_name(p.group_name) or "(대상 그룹 이름 없음)"
    return p.customer.name if p.customer is not None and p.customer.name else "(고객 없음)"


def target_search_names(p: Proposal) -> list[str]:
    """목록 검색이 보는 이름들: 특정 고객은 고객 이름, 다수 고객은 그룹 이름과 붙인 고객들의 이름."""
    if is_group(p):
        return [normalize_group_name(p.group_name), *(c.name for c in p.group_customers)]
    return [p.customer.name] if p.customer is not None else []


def candidate_condition(extra_ids):
    """제안 후보 규칙 (한 곳): 보류가 아닌 모든 단계의 솔루션 + 제안 건에 직접 추가한 솔루션 (`extra_solution_ids`)."""
    cond = Solution.stage != ontology.STAGE_HOLD
    ids = [i for i in (extra_ids or []) if isinstance(i, int)]
    return or_(cond, Solution.id.in_(ids)) if ids else cond


def _base_candidates(db: Session, p: Proposal) -> list[Solution]:
    ids = [i for i in (p.extra_solution_ids or []) if isinstance(i, int)]
    return list(db.scalars(select(Solution).where(candidate_condition(ids)).options(selectinload(Solution.business_types)).order_by(Solution.name, Solution.id)))


def priority_groups(db: Session, p: Proposal, candidates: list[Solution] | None = None) -> list[dict]:
    """제안 후보 중 앞에 둘 솔루션 묶음 (순서대로): 영업기회의 검토 솔루션, 그 영업기회의 BM 의 솔루션
    (영업기회가 없으면 프로젝트가 따르는 BM 의 솔루션). 다수 고객 제안은 신규 사업으로 삼은 BM 의 솔루션 한 묶음뿐이다. 각 묶음은 {"reason": 글, "solutions": [...]} (이름순, 앞 묶음에 든 것은 뺀다).
    후보가 아닌 솔루션(보류인데 직접 추가하지 않은 것)은 넣지 않는다: 후보의 집합은 `candidate_condition` 그대로다."""
    pool = {s.id: s for s in (candidates if candidates is not None else _base_candidates(db, p))}
    wanted = p.business_type_codes
    group = is_group(p)
    opp = None if group else p.opportunity  # 다수 고객 제안은 영업기회·프로젝트가 없고, 신규 사업(BM)의 솔루션이 앞선다
    bm = p.bm if group else (opp.bm if opp is not None else (p.project.bm if p.project is not None else None))
    sources = []
    if group:
        if bm is not None:
            sources.append((f"신규 사업 {ontology.label('bm')} '{bm.name}' 의 솔루션", bm.solutions))
        bm = None
    if opp is not None:
        sources.append((f"{ontology.label('opportunity')} '{opp.name}' 의 {ontology.prop('opportunity', 'solutions')}", opp.solutions))
    if bm is not None:
        owner = f"{ontology.label('opportunity')}의" if opp is not None else f"{ontology.label('project')}가 따르는"
        sources.append((f"{owner} {ontology.label('bm')} '{bm.name}' 의 솔루션", bm.solutions))
    groups, placed = [], set()
    for reason, solutions in sources:
        picked = [s for s in sorted(solutions, key=lambda s: (s.name, s.id)) if s.id in pool and s.id not in placed]
        if picked:
            placed.update(s.id for s in picked)
            groups.append({"reason": reason, "solutions": bts.prefer_supporting([pool[s.id] for s in picked], wanted)})  # 베타 Y: 묶음 안에서 고른 사업방식을 지원하는 솔루션이 먼저
    return groups


def candidate_solutions(db: Session, p: Proposal) -> list[Solution]:
    """제안에 조합할 수 있는 솔루션: 집합은 `candidate_condition` 이 정한다. 순서만 영업기회의 검토 솔루션 → BM 의 솔루션 → 나머지(이름순).
    베타 Y: 각 묶음 안에서(나머지 포함) 제안 건의 검토 사업방식을 하나라도 지원하는 솔루션이 먼저 온다 (안정 정렬, 아무것도 빼지 않는다)."""
    base = _base_candidates(db, p)
    front = [s for g in priority_groups(db, p, base) for s in g["solutions"]]
    first = {s.id for s in front}
    return front + bts.prefer_supporting([s for s in base if s.id not in first], p.business_type_codes)


def display_title(p: Proposal) -> str:
    if p.title:
        return p.title
    if is_group(p):
        return f"{target_name(p)} 제안"
    if p.project is not None:
        return f"{target_name(p)} · {p.project.name} 제안"
    return f"{target_name(p)} 제안"


def check_sources(knowledge_dir: Path, sources: list[str]) -> tuple[list[str], list[str]]:
    """출처 경로를 (존재하는 것, 존재하지 않는 것) 으로 나눈다. 지식 폴더 밖을 가리키는 경로는 없는 것으로 본다."""
    root = Path(knowledge_dir).resolve()
    found: list[str] = []
    missing: list[str] = []
    for source in sources:
        rel = source.strip().replace("\\", "/")
        parts = PurePosixPath(rel).parts  # `./` 와 중복 `/` 는 여기서 정리된다
        usable = (
            rel != "" and "\x00" not in rel
            and not rel.startswith("/") and not PureWindowsPath(rel).is_absolute() and not any(":" in part for part in parts)
            and ".." not in parts
        )
        if usable:
            try:
                current = rel  # 저장된 글자는 그대로 두고, 확인할 때만 지금의 사본 경로로 읽는다 (옛 `businesses/N.md` 는 `projects/N.md`)
                if (parsed := ontology.parse_rel(rel)) is not None:
                    current = ontology.rel_path(parsed[0], **parsed[1])
                target = (root / current).resolve()
                usable = target.is_relative_to(root) and target.is_file()
            except (OSError, ValueError, KeyError):
                usable = False
        (found if usable else missing).append(PurePosixPath(rel).as_posix() if usable else source)
    return found, missing


def clean_directions(db: Session, p: Proposal, out: "DirectionsOut", knowledge_dir: Path) -> list[dict]:
    """AI 가 만든 방향을 저장용 목록으로 바꾼다. 후보에 없는 솔루션과 확인되지 않은 출처는 빼고 `needs_check` 에 남긴다."""
    names = {s.id: s.name for s in candidate_solutions(db, p)}
    cleaned: list[dict] = []
    for d in out.directions:
        needs_check = [n for n in d.needs_check if n.strip()]
        solutions: list[dict] = []
        unknown = False
        for sol in d.solutions:
            if sol.solution_id not in names:
                unknown = True
            elif all(x["solution_id"] != sol.solution_id for x in solutions):
                solutions.append({"solution_id": sol.solution_id, "name": names[sol.solution_id], "role": sol.role})
        if unknown:
            needs_check.append(checks.UNKNOWN_SOLUTION_NOTE)
        if not solutions:
            continue
        rationale: list[dict] = []
        for ev in d.rationale:
            found, missing = check_sources(knowledge_dir, ev.sources)
            needs_check.extend(checks.source_unverified(m) for m in missing)
            if not found:
                needs_check.append(checks.no_evidence(ev.text))
            rationale.append({"text": ev.text, "sources": found})
        if not d.rationale:
            needs_check.append(checks.no_evidence())
        bt = d.business_type.strip() if isinstance(d.business_type, str) else ""
        cleaned.append({
            "title": d.title, "summary": d.summary, "solutions": solutions, "rationale": rationale,
            "effects": list(d.effects), "needs_check": needs_check,
            "business_type": bt if bt in ontology.BUSINESS_TYPES else "",  # 이 방향이 전제하는 사업방식 (목록에 없는 값은 버린다: 오류가 아니다)
        })
    return cleaned[:MAX_DIRECTIONS]


def number_sources(direction: dict) -> dict:
    """방향 카드의 출처에 번호를 붙인다. 같은 경로는 카드 안에서 같은 번호(처음 나온 순서).
    `{"rationale": [{"text", "refs": [1, 2]}], "sources": [경로, ...]}` — 출처 없는 근거는 refs 가 빈 목록."""
    order: list[str] = []
    rationale = []
    for ev in direction.get("rationale") or []:
        if not isinstance(ev, dict):
            continue
        refs: list[int] = []
        for path in ev.get("sources") or []:
            if not isinstance(path, str):
                continue
            if path not in order:
                order.append(path)
            n = order.index(path) + 1
            if n not in refs:
                refs.append(n)
        rationale.append({"text": ev.get("text") or "", "refs": refs})
    return {"rationale": rationale, "sources": order}


def slide_sources(slide: dict) -> list[str]:
    """한 장의 출처 경로. 같은 경로는 한 번만, 처음 나온 순서대로. 장 카드의 출처 번호가 이 순서를 따른다."""
    return list(dict.fromkeys(x for x in slide.get("sources") or [] if isinstance(x, str)))


def _strip_list(items: list[str]) -> list[str]:
    return [x for x in (i.strip() for i in items) if x]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# 고객에게 보이는 글에 섞인 "작성자에게 하는 말"을 알아보는 작고 보수적인 규칙: 줄에 분명한 지시 표지가 있을 때만 본다.
# "AI가 초안을 작성", "한계를 보완", "책임 범위를 명시", "○○시청 도입 사례" 같은 보통 문구는 동사만으로는 건드리지 않는다.
# 이 규칙은 어림이다. 한 줄을 분명한 규칙으로 가를 수 없으면 슬라이드에 둔다 (내부 검토용 PPT 와 확인할 점 상자가 안전망).
# 고객 쪽 일이 대상이면("네트워크 구성 확인 필요", "현장 여건 검토 요망", "제안 내용을 검토해 주시기 바랍니다") 고객 문장이다.
#
# 베타 I: `…필요`·`…요망`·`…예정`(`_TAIL`)은 줄 끝의 `<동사> 필요|요망|예정` 앞말(`lead`)로 가른다.
#  - 앞말이 없고 동사가 `확인|작성|기재 …` 뿐이면 작성자 말투("확인 필요", "작성 예정").
#  - 앞말이 한두 낱말이고 그 안에 문서 값 낱말(`_DOC_NEAR`: 금액·수치·일정·사례·출처 …)이나 그림 자리(`_VISUAL`: 로고·사진·도표 …) 또는 담당자 이름("담당자 이름 확인 필요")이 있으면 작성자 말투.
#    그림 자리 낱말이 동사 바로 앞이면 앞말이 넉 낱말까지("도입 사례 사진 추가 예정").
#  - 값을 바꾸라는 `교체|업데이트 필요` 는 앞말이 세 낱말까지("최신 통계 수치로 업데이트 필요").
#  - 앞말이 길면("납품 후 검수 기간 확인 필요", "설치 후 통신 품질 수치 검토 필요", "가격은 별도 견적서에 기재 예정") 고객에게 하는 약속·조건이라 슬라이드에 둔다.
#    길어도 옮기는 분명한 표지: `실제 … 로 교체`, `산출 … 추가|보강 …`, `추후 작성|기재|보완|업데이트 (예정)`, `<문서 값>은 … 후 기재|기입|작성|삽입 예정`("금액은 영업팀 확인 후 기재 예정"; `…에 기재 예정` 은 고객 약속).
#  - `담당자`·`규모` 는 고객 쪽 일에도 흔해("현장 담당자 확인 필요", "도입 규모 검토 필요") 앞말이 없을 때(`담당자 확인 필요`)나 `고객사 담당자`·`담당자 이름` 일 때만 센다.
#  - "이 슬라이드(장·페이지·쪽) 삭제" 는 `슬라이드|장|페이지|쪽` 바로 뒤에 조사만 오고 띄어 쓸 때만 본다 ("해당 장비는", "이 장치는" 은 고객 문장). 사이에 고객·관리자·사용자·직접·`…에서` 가 끼면 고객 문장이다.
# 줄이 아주 길어도 느려지지 않는다: 문서 값 낱말 뒤 `.*` 로 줄 끝을 찾는 규칙(`_DOC_THEN`)은 첫 문서 값 낱말 뒤에서 한 번만 찾고, 나머지는 낱말 단위로 센다.
_V_CORE = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|확인)"
_V_ASK = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|확인|추가|첨부)"  # "…바랍니다": 검토는 뺀다 ("귀사의 검토 바랍니다")
_V_WRITE = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|추가|첨부)"  # "…하세요"·"…할 것": 확인·검토는 뺀다 ("성과를 확인할 것", "앱에서 확인하세요")
_V_FILL = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|확인|추가|첨부|보강|업데이트|교체|포함|제시|검토)"  # 문서 값이 대상일 때
_V_FILL_NR = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|확인|추가|첨부|보강|업데이트|교체|포함|제시)"  # `_V_FILL` 에서 "검토" 를 뺀 것 (하세요·할 것 꼴)
_DOC = r"(?:총액|금액|수치|절감률|가격표?|단가|견적|예산|일정|기간|날짜|비율|건수|수량|통계|실적|사례|레퍼런스|근거|내용|출처|고객사명|고객명|담당자|시기|규모)"
_DOC_NEAR = r"(?:총액|금액|수치|절감률|가격표?|단가|견적|예산|일정|기간|날짜|비율|건수|수량|통계|실적|사례|레퍼런스|근거|내용|출처|고객사명|고객명|시기)"
_VISUAL = r"(?:로고|그림|도표|스크린샷|이미지|사진|차트|그래프|도면|아이콘)"
_NOTE_VERB = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|확인|재확인|검증|추가|첨부|보강|업데이트|교체|포함|제시|검토)"
_PLAN_VERBS = frozenset({"작성", "기재", "기입", "삽입", "추가", "업데이트", "보완", "교체", "수정"})
_TAIL = re.compile(rf"(?<!\S)(?P<verb>{_NOTE_VERB})\s*(?P<end>(?:이\s*)?필요(?:함|합니다)?|요망|요|예정)$")
_DOC_NEAR_WORD = re.compile(rf"{_DOC_NEAR}\S{{0,2}}$")
_VISUAL_WORD = re.compile(rf"{_VISUAL}\S{{0,2}}$")
# 도면·사진·이미지 같은 그림 낱말은 현장·설치·건물 같은 장소 낱말이 앞에 있으면("현장 도면 확인 필요", "CCTV 이미지 확인 필요") 고객 쪽 일이라 센 그림 자리로 보지 않는다.
_SITE_WORD = re.compile(r"(?:현장|설치|위치|건물|시설|매장|CCTV|카메라|공장|시공|실사|층|사무실|전산실|서버실|기존|장비|센서)")
# "모든 앞말이 문서 값 낱말" 규칙에 쓰는 낱말: 문서 값 낱말·ROI 같은 약어·수량("30%는", "2건")
_DOC_ONLY_WORD = re.compile(rf"(?:(?:{_DOC_NEAR}|ROI|KPI|TCO|BEP)\S{{0,2}}|[\d.,]+\s*(?:%|건|개|곳|원|만원|억원|억|천원|배)\S{{0,2}})$")
_DEICTIC_ONE = frozenset({"이", "위", "위의", "아래", "아래의", "여기", "여기에", "이곳", "이것"})  # `본 계약 기간 …`·`본 견적은 …` 의 `본` 은 고객 문장이 많아 뺐다 (베타 K)
# 지시어 규칙은 고객 쪽 조건·약속 낱말이 든 줄에는 쓰지 않는다 (`위 금액은 VAT 별도이며 계약 시 재확인 필요`, `위 일정은 귀사 사정에 따라 …`)
_DEICTIC_STAY = re.compile(r"귀사|고객사|협의|변동|계약|기준|별도")
_DEICTIC_DOC_PART = re.compile(r"(?:슬라이드|장|페이지|쪽|부분|항목|표)")
_TOPIC_WORD = re.compile(rf"\S*{_DOC_NEAR}(?:은|는)$")
_CONTACT_LEAD = re.compile(r"^(?:(?:고객사|거래처)\s+)?담당자(?:명|\s*(?:이름|성함|연락처))?$")
_LATER_FILL = re.compile(r"(?<!\S)추후\s+(?P<verb>작성|기재|기입|삽입|보완|업데이트|확인)(?:\s*예정)?$")
_REAL_VALUE = re.compile(r"(?<!\S)실제\s+(?P<mid>(?:\S+\s+){0,3}\S*?)(?:으로|로)\s*(?:교체|대체)(?:\s*(?:필요|요망|할\s*것|하세요))?$")
_COMPUTED = re.compile(r"(?<!\S)\S*산출\S*\s")
_SOURCE_NEED = re.compile(r"^(?:(?P<lead>\S+(?:\s+\S+)?)\s+)?(?P<core>출처|근거)(?:\s*자료)?\s*필요$")
_REAL_EXTRA = re.compile(r"(?:데이터|자료|화면|캡처)\S{0,2}$")
_SLIDE_WORD = re.compile(r"(?:슬라이드|장|페이지|쪽)(?:은|는|을|를|에)?$")
_DELETE_TAIL = re.compile(r"(?:삭제|제거)(?:\s*(?:할\s*것|하세요|해\s*주세요|바랍니다|요망|예정|요))?$")
_CUSTOMER_SIDE = re.compile(r"고객|관리자|사용자|직접|에서")
# 문서 값 낱말 뒤에 `.*` 로 줄 끝 꼴이 오는 규칙: 첫 문서 값 낱말이 끝난 자리부터 한 번만 찾는다 (선형).
_DOC_THEN = tuple(re.compile(p) for p in (
    r"(?:함께|반드시)\s*제시(?:\s*(?:하세요|해\s*주세요|할\s*것))?$",   # "총액과 견적 유효기간을 함께 제시" ("ROI 분석 결과를 함께 제시" 는 고객 문장)
    rf"{_V_FILL_NR}\s*할\s*것$",                                      # "근거를 제시할 것", "가격표를 포함할 것", "근거 자료 보강할 것"
    rf"(?:{_V_FILL_NR}\s*(?:하세요|해\s*주세요)|(?:넣으세요|넣어\s*주세요|포함하세요))$",  # "레퍼런스를 포함하세요", "여기에 도입 사례를 넣으세요"
))
_DOC_FIRST = re.compile(_DOC)
_PLACEHOLDER = r"(?:○{2,}|O{3,}|△{2,}|□{2,})"
_BLANK = r"(?:○{2,}|△{2,}|□{2,})"  # 칸에 든 자리표시 (라틴 O 는 "담당: OOO 팀" 처럼 이름일 수 있어 뺀다)
AUTHOR_INSTRUCTION_PATTERNS = tuple(re.compile(p) for p in (
    r"(?:함께|반드시)\s*(?:기재|명시|작성)(?:\s*(?:하세요|해\s*주세요|할\s*것))?$",   # "담당자 이름을 함께 기재", "보안 정책을 반드시 명시"
    rf"{_V_WRITE}\s*할\s*것$",                                          # "담당자 연락처를 추가할 것"
    r"^(?:확인|검토)\s*(?:하실\s*|할\s*)?것$",                           # 줄 전체가 "확인할 것"
    r"(?:적을|넣을|쓸)\s*것$",                                           # "도입 사례를 구체적으로 적을 것"
    rf"{_V_ASK}\s*(?:(?:하시기|해\s*주시기)\s*)?바랍니다$",               # "자료를 확인하시기 바랍니다"
    r"(?:확인|수정|보완|기재|작성)\s*바람$",                              # "확인 바람"
    rf"^{_V_ASK}\s*요망$|^(?:검토)\s*요망$",                             # "검토 요망" ("현장 여건 검토 요망" 은 고객 문장)
    rf"{_V_WRITE}\s*(?:하세요|해\s*주세요)$",                             # "수치를 입력하세요"
    rf"^{_V_CORE}\s*필요$",                                              # 줄 전체가 "확인 필요"
    rf"^(?:도입|구축|계약|착수)\s*전\b(?!.*귀사).*{_V_CORE}\s*필요$",         # "도입 전 네트워크 환경 확인 필요" (앞 묶음이 정한 것; `귀사` 가 든 줄은 고객 쪽 조건이다)
    rf"{_V_CORE}\s*필요\s*[-–—:：(（\[\]]|\[\s*{_V_CORE}\s*필요$",         # "확인 필요: 절감률", "금액 확인 필요 - 견적팀", "확인 필요(영업팀)", 끝의 "[확인 필요]"
    rf"^[\(\[]\s*{_V_CORE}\s*필요\s*[\)\]]",                           # 맨 앞 "[확인 필요]"
    r"^(?:작성|기재|기입|삽입)\s*예정$",                                    # 줄 전체가 "작성 예정"
    r"^(?:TBD|TODO)\s*[:：]",                                            # 맨 앞 "TODO: 수치 확인", "TBD: 일정"
    r"(?:금액|가격|단가|수치|절감률|비용|일정)\s*미정$",                    # "금액 미정"
    r"[\[\(<]\s*(?:TBD|TODO)\s*[\]\)>]|^(?:TBD|TODO)\s*[:：]?$|[:：]\s*(?:TBD|TODO)$|\s(?:TBD|TODO)$",  # 채우지 않은 자리표시 ("TODO 앱 연동", "서비스명: TBD Cloud" 는 고객 문장)
    rf"^[\(\[]?\s*{_PLACEHOLDER}\s*[\)\]]?$",                          # 줄 전체가 "○○" / "(OOO)"
    rf"[:：]\s*[\(\[]?{_BLANK}[\)\]]?\s*[^\s:：]{{0,3}}$",              # "담당자: ○○○", "비용: ○○원"
))
_LINE_TAIL_CHARS = ".。)]!?…"


def _strip_line(text: str) -> str:
    """앞뒤 공백을 떼고 줄 끝의 마침표·닫는 괄호·`!?…` 를 뗀다 (한 번 훑는다: 끝에 공백이 아주 길게 이어져도 선형)."""
    line = text.strip()
    i = len(line)
    while i > 0 and (line[i - 1].isspace() or line[i - 1] in _LINE_TAIL_CHARS):
        i -= 1
    return line[:i]


def _doc_value(word: str, site: bool) -> bool:
    """문서 값 낱말인가 (그림 낱말은 장소 낱말이 앞에 없을 때만)."""
    return _DOC_NEAR_WORD.search(word) is not None or (not site and _VISUAL_WORD.search(word) is not None)


def _real_word(w: str) -> bool:
    """`실제 … 로 교체` 사이의 낱말이 바뀔 값인가: 문서 값·그림 낱말·`데이터|자료|화면|캡처`·`값`."""
    return _DOC_NEAR_WORD.search(w) is not None or _VISUAL_WORD.search(w) is not None or _REAL_EXTRA.search(w) is not None or w.startswith("값")


def _is_real_value(line: str) -> bool:
    """"실제 고객사 수치로 교체", "아래 표는 예시이며 실제 값으로 교체": `실제` 와 `(으)로` 사이에 문서 값 낱말(또는 `값`)이 있을 때만.
    "노후 장비를 실제 운영 장비로 교체", "실제 운영 환경으로 교체" 는 고객 쪽 일이다."""
    m = _REAL_VALUE.search(line)
    return m is not None and any(_real_word(w) for w in m.group("mid").split())


def _is_later_fill(line: str) -> bool:
    """"추후 작성", "수치는 추후 업데이트", "내용 추후 보완": 앞말이 없거나 문서 값 낱말이 든 짧은 앞말일 때만. `추후 확인` 은 앞말이 없을 때만
    ("기능은 추후 업데이트 예정"·"상세 일정은 추후 확인" 은 고객 쪽 약속이다)."""
    m = _LATER_FILL.search(line)
    if m is None:
        return False
    words = line[:m.start()].split()
    if not words:
        return True
    return m.group("verb") != "확인" and len(words) <= 4 and any(_DOC_NEAR_WORD.search(w) for w in words)


def _is_source_need(line: str) -> bool:
    """"출처 필요" 는 앞말이 두 낱말까지면 언제나 작성자 메모다 ("데이터 출처 필요", "인용 출처 필요"). "근거 (자료) 필요" 는 앞말이 없거나 문서 값 낱말 하나일 때만
    ("법적 근거 필요", "증빙 자료 필요", "추가 자료 필요" 는 고객 쪽 일)."""
    m = _SOURCE_NEED.match(line)
    if m is None:
        return False
    lead = m.group("lead")
    return m.group("core") == "출처" or lead is None or _DOC_ONLY_WORD.match(lead) is not None


def _is_note_by_lead(line: str) -> bool:
    """줄 끝 `<동사> 필요|요망|예정` 의 앞말(`lead`)을 보고 작성자 말투인지 가른다 (위 설명)."""
    m = _TAIL.search(line)
    if m is None:
        return False
    verb, end = m.group("verb"), m.group("end")
    plan = end == "예정"
    if plan and verb not in _PLAN_VERBS:
        return False
    lead = line[:m.start()].strip()
    words = lead.split()
    n = len(words)
    if n == 0:
        return verb in ("기재", "작성", "명시", "기입", "입력", "삽입", "보완", "수정", "확인") and (not plan or verb in ("작성", "기재", "기입", "삽입"))
    if lead in ("담당자", "규모"):
        return not plan and verb in ("기재", "작성", "명시", "기입", "입력", "삽입", "보완", "수정", "확인", "추가", "첨부", "보강", "업데이트", "교체", "포함", "제시", "검토")
    if _CONTACT_LEAD.match(lead):
        return True
    if any(w.startswith("실제") for w in words) and verb in ("교체", "대체", "수정") and any(_real_word(w) for w in words):
        before = words[:next(i for i, w in enumerate(words) if w.startswith("실제"))]
        topics = [w for w in before if w.endswith(("은", "는"))]
        if plan and topics and not any(_TOPIC_WORD.match(w) for w in topics):
            return False  # "시범 데이터는 실제 운영 데이터로 교체 예정": 문서 값이 아닌 화제(`…는`)가 앞에 있는 예정은 고객 약속 (베타 L; `금액은 실제 값으로 교체 예정` 은 옮긴다)
        return True  # "실제 고객사 수치로 교체 필요" ("시범 장비는 실제 양산 장비로 교체 예정" 은 고객 쪽 일)
    if _COMPUTED.search(lead + " ") and verb in ("추가", "보강", "첨부", "확인", "보완", "기재") and not plan:
        return True  # "절감률 산출 근거 자료 추가 필요"
    site = verb in ("확인", "검토", "첨부") and any(_SITE_WORD.match(w) for w in words)  # 장소 낱말 + 그림은 고객 쪽 일 (`현장 도면 확인 필요`); 삽입·추가·교체는 작성자 메모 (`현장 사진 삽입 예정`)
    if n <= 2 and any(_doc_value(w, site) for w in words):
        return True
    if n <= 4 and not site and _VISUAL_WORD.search(words[-1]):
        return True
    if n <= 3 and verb in ("교체", "업데이트", "수정") and not plan and _doc_value(words[-1], site):
        return True  # "최신 통계 수치로 업데이트 필요", "고객사 맞춤 내용으로 수정 필요"
    if all(_DOC_ONLY_WORD.match(w) for w in words):
        return True  # "절감률 수치 근거 확인 필요", "ROI 수치 출처 확인 필요", "레퍼런스 사례 2건 추가 필요", "절감률 30%는 근거 확인 필요"
    first = words[0]
    reach = 3 if first == "해당" else 2  # 지시어 바로 뒤 두 낱말 안에 문서 값 낱말이 있어야 한다
    if (not plan and not _DEICTIC_STAY.search(lead) and (first in _DEICTIC_ONE or (first == "해당" and n > 1 and _DEICTIC_DOC_PART.match(words[1])))
            and any(_doc_value(w, site) for w in words[1:1 + reach])):
        return True  # "이 금액은 다시 확인 필요", "이 부분 사례 추가 필요", "여기에 고객 사례 추가 필요" (앞말이 길어도; `예정` 과 고객 쪽 낱말이 든 줄은 고객 약속이다)
    if plan and verb in ("기재", "기입", "작성", "삽입") and _TOPIC_WORD.match(words[0]) and (n == 1 or words[-1] == "후"):
        return True  # "금액은 영업팀 확인 후 기재 예정" ("…에 기재 예정" 은 고객 약속)
    return False


def _is_slide_delete(line: str) -> bool:
    words = line.split()
    if len(words) < 3 or words[0] not in ("이", "해당", "본") or not _SLIDE_WORD.match(words[1]):
        return False
    tail = " ".join(words[2:])
    m = _DELETE_TAIL.search(tail)
    return m is not None and not _CUSTOMER_SIDE.search(tail[:m.start()])


# 자리표시 모양 (베타 L 이 더했고 베타 Q 에서 남겼다): 채우지 않은 `XX%`·`○○`·`OO`·`XX` 같은 **모양**만 본다. 낱말(내부용·초안·오타·폰트·팀장님·작성자·법무팀 검토 …)은 보지 않는다
# (사용자 결정 2026-11-22: 낱말 규칙은 뺐다. 고객 문장이 낱말 때문에 슬라이드에서 빠지면 안 되고, 고객용 PPT 는 사람이 읽고 보낸다).
# `○○`·`OO`·`XX` 는 낱말 하나이거나 단위가 바로 붙을 때만이다 (`○○시청`·`OOO 팀` 은 고객 문장). 알려진 한계: `XX% 이상`·`기존 대비 OO배 빠른 처리`·`○○ 은행 콜센터 적용 사례` 는 자리표시로 보아 옮긴다.
_H = r"(?<![가-힣A-Za-z0-9])"
_PH_UNIT = r"(?:%|원|억|만원|개사|개|명|건|일|월|년|배)"
_PLACEHOLDER_SHAPE = re.compile(
    rf"{_H}(?<!Ｘ)[XxＸ]{{2,}}\s*%"
    rf"|{_H}(?:○{{2}}|OO|XX)(?:\s|{_PH_UNIT}(?![가-힣])|$)"
)


# 괄호 입력 표시 (베타 S, 사용자 결정 2026-11-23: 낱말 규칙을 뺄 때 같이 빠진 것을 되살렸다): 줄 전체가 `(수치 입력)`·`[일정 입력]`·`(입력)` 꼴이거나,
# 줄 끝이 `(문서 값 입력)` 이고 그 앞이 `화면|단계|조건|양식|폼` 이 아닐 때 (`견적 요청 화면 (수량 입력)` 은 고객 문장).
_BRACKET_INPUT_LINE = re.compile(r"^[\[\(][^()\[\]]{0,15}입력[\]\)]?$")
_BRACKET_INPUT_END = re.compile(rf"[\[\(][^()\[\]]{{0,8}}(?:{_DOC_NEAR}|이름|성명|회사명)[^()\[\]]{{0,10}}입력\s*[\]\)]?$")
_BRACKET_INPUT_AFTER_NOT = re.compile(r"(?:화면|단계|조건|양식|폼)$")


def _bracket_input(norm: str) -> bool:
    if len(norm) > 200:  # 자리표시 줄은 짧다. 긴 줄은 보지 않는다 (시간도 묶인다)
        return False
    if _BRACKET_INPUT_LINE.match(norm):
        return True
    m = _BRACKET_INPUT_END.search(norm)
    return m is not None and not _BRACKET_INPUT_AFTER_NOT.search(norm[:m.start()].rstrip())


def _placeholder_shape(line: str) -> bool:
    """채우지 않은 자리표시 모양(`XX%`·`○○`·`OO`·`XX`·괄호 입력 표시)이 든 줄인가. 공백을 한 칸으로 줄인 글에서 본다 (선형)."""
    norm = " ".join(line.split())
    return _PLACEHOLDER_SHAPE.search(norm) is not None or _bracket_input(norm)


def is_author_instruction(text: str) -> bool:
    """고객에게 보이는 줄이 아니라 작성자에게 하는 말처럼 쓰인 줄인가 (줄 끝 말투로만 본다)."""
    line = _strip_line(str(text or ""))
    if not line:
        return False
    if _placeholder_shape(line):
        return True
    if any(p.search(line) for p in AUTHOR_INSTRUCTION_PATTERNS):
        return True
    doc = _DOC_FIRST.search(line)
    if doc is not None and any(p.search(line, doc.end()) for p in _DOC_THEN):
        return True
    return _is_note_by_lead(line) or _is_slide_delete(line) or _is_real_value(line) or _is_later_fill(line) or _is_source_need(line)


# 마무리 장에서 AI 가 쓴 연락처 줄: 전화번호·이메일이 든 줄, 또는 "담당: 홍길동"/"연락처:" 같은 이름표만 있는 줄. "문의 환영" 같은 인사는 둔다.
_CONTACT_LINE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+|0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}"
    r"|^\s*(?:담당자?|담당\s*부서|연락처|문의처?|Tel|TEL|E-?mail|Email|Contact)\s*[:：]\s*(?:[^\s:：]+(?:\s+[^\s:：]+)?)?\s*$")


def _move_instructions(s: dict) -> int:
    """슬라이드 본문에서 작성자에게 하는 말을 빼 needs_check(확인할 점)로 옮긴다. 옮긴 줄 수를 돌려준다."""
    moved: list[str] = []

    def keep(items):
        kept = []
        for item in items:
            (moved if is_author_instruction(item) else kept).append(item)
        return kept

    for key in ("bullets", "left", "right"):
        s[key] = keep(s[key])
    s["rows"] = [[("" if is_author_instruction(c) and not moved.append(c) else c) for c in row] for row in s["rows"]]
    for st in s["steps"]:
        if is_author_instruction(st["detail"]):
            moved.append(st["detail"])
            st["detail"] = ""
    if is_author_instruction(s["key_message"]):
        moved.append(s["key_message"])
        s["key_message"] = ""
    # 시각 자료 장 (베타 AG): 글머리와 같은 규칙. 이름이 작성자 말이면 그 항목을 통째로, 설명만 그러면 설명만 옮긴다
    kept = []
    for item in s.get("kpis") or []:
        if any(is_author_instruction(item[k]) for k in ("value", "label")):
            moved.append(" ".join(x for x in (item["value"] + item["unit"], item["label"], item["note"]) if x))
            continue
        if is_author_instruction(item["note"]):
            moved.append(item["note"])
            item["note"] = ""
        kept.append(item)
    s["kpis"] = kept
    kept = []
    for node in s.get("nodes") or []:
        if is_author_instruction(node["label"]):
            moved.append(" ".join(x for x in (node["label"], node["detail"]) if x))
            continue
        for key in ("detail", "group"):
            if is_author_instruction(node[key]):
                moved.append(node[key])
                node[key] = ""
        kept.append(node)
    s["nodes"] = kept
    categories = s.get("categories") or []
    drop = {i for i, c in enumerate(categories) if is_author_instruction(c)}
    if drop:  # 항목 이름이 작성자 말이면 그 항목과 그 자리의 값을 함께 뺀다
        moved.extend(categories[i] for i in sorted(drop))
        s["categories"] = [c for i, c in enumerate(categories) if i not in drop]
        for series in s.get("series") or []:
            series["values"] = [v for i, v in enumerate(series["values"]) if i not in drop]
    for series in s.get("series") or []:
        if is_author_instruction(series["name"]):
            moved.append(series["name"])
            series["name"] = ""
    for key in ("note", "center", "axis_x", "axis_y"):
        if is_author_instruction(s.get(key)):
            moved.append(s[key])
            s[key] = ""
    if moved:
        s["needs_check"] = _unique(s["needs_check"] + moved)
    return len(moved)


def strip_title_period(title: str) -> str:
    """장 제목 끝의 마침표를 뗀다 (사용자 결정 2026-11-24: 제목은 마침표로 끝내지 않는다). 물음표·느낌표는 둔다."""
    return str(title or "").strip().rstrip(".。．").rstrip()


def _to_bullets(s: dict, lines: list[str]) -> None:
    s["kind"] = "bullets"
    s["bullets"] = s["bullets"] + [line for line in lines if line]


def _node_lines(nodes: list[dict]) -> list[str]:
    return [f"{n['label']}: {n['detail']}" if n["detail"] else n["label"] for n in nodes]


def _clean_visuals(s: dict) -> None:
    """핵심 수치·그래프·도식 장의 칸을 규칙에 맞게 고친다 (AI 구성안). 그릴 수 없는 장은 지어내지 않고 글머리나 표로 바꾼다."""
    kind = s["kind"]
    for key in VISUAL_TEXT_FIELDS:
        s[key] = s[key].strip()
    kpis = [{k: str(item.get(k) or "").strip() for k in ("value", "unit", "label", "note")} for item in s["kpis"]]
    kpis = [item for item in kpis if item["value"]]
    nodes = [{k: str(item.get(k) or "").strip() for k in ("label", "detail", "group")} for item in s["nodes"]]
    nodes = [item for item in nodes if item["label"]]
    categories = [str(c or "").strip() for c in s["categories"]]
    series = [{"name": str(x.get("name") or "").strip(), "values": list(x.get("values") or [])} for x in s["series"]]
    series = [x for x in series if x["values"]]
    s["kpis"], s["nodes"], s["categories"], s["series"] = [], [], [], []
    if kind == "kpi":
        if kpis:
            if len(kpis) > ontology.MAX_KPIS:
                s["needs_check"].append(f"핵심 수치가 많아 앞의 {ontology.MAX_KPIS}개만 담았습니다. 빠진 수치: " + ", ".join(i["label"] or i["value"] for i in kpis[ontology.MAX_KPIS:]))
            s["kpis"] = kpis[:ontology.MAX_KPIS]
        else:
            _to_bullets(s, [])
    elif kind == "chart":
        numbers = [[parse_number(v) for v in x["values"]] for x in series]
        ok = (bool(categories) and all(categories) and bool(series) and len(series) <= ontology.MAX_CHART_SERIES and len(categories) <= ontology.MAX_CHART_POINTS
              and all(len(n) == len(categories) and None not in n for n in numbers))
        if ok:
            s["categories"] = categories
            s["series"] = [{"name": x["name"], "values": n} for x, n in zip(series, numbers)]
            s["chart_type"] = s["chart_type"].lower() if s["chart_type"].lower() in ontology.CHART_TYPES else ontology.CHART_TYPE_DEFAULT
        elif categories or series:  # 값이 숫자가 아니거나 개수가 안 맞는다: 그래프로 그리지 않고 받은 글 그대로 표에 담는다
            width = max([len(categories), *(len(x["values"]) for x in series)])
            s["kind"] = "table"
            s["columns"] = ["항목", *((x["name"] or f"값 {i + 1}") for i, x in enumerate(series))]
            s["rows"] = [[categories[r] if r < len(categories) else "", *((number_text(x["values"][r]) or str(x["values"][r]).strip()) if r < len(x["values"]) and x["values"][r] is not None else "" for x in series)]
                         for r in range(width)]
            s["needs_check"].append(checks.CHART_TO_TABLE_NOTE)
        else:
            _to_bullets(s, [])
    elif kind == "diagram":
        dtype = s["diagram_type"].lower()
        if dtype not in ontology.DIAGRAM_TYPES:
            dtype = "before_after" if not nodes and (s["left"] or s["right"]) else ontology.DIAGRAM_TYPE_DEFAULT
        s["diagram_type"] = dtype
        if dtype == "before_after":
            if not (s["left"] or s["right"]):
                _to_bullets(s, _node_lines(nodes))
        elif not nodes:
            _to_bullets(s, [])
        else:
            limit = ontology.DIAGRAM_MAX_NODES[dtype]
            if len(nodes) > limit:
                s["needs_check"].append(f"도식 항목이 많아 앞의 {limit}개만 담았습니다. 빠진 항목: " + ", ".join(n["label"] for n in nodes[limit:]))
            s["nodes"] = nodes[:limit]
            if dtype == "matrix":
                for node in s["nodes"]:
                    node["group"] = matrix_quadrant(node["group"]) or ""
    if s["kind"] != "chart":
        s["chart_type"] = s["unit"] = ""
    if s["kind"] != "diagram":
        s["diagram_type"] = s["center"] = s["axis_x"] = s["axis_y"] = ""
    if s["kind"] not in ("kpi", "chart", "diagram"):
        s["note"] = ""


def _drop_empty_visual(s: dict) -> None:
    """작성자에게 하는 말을 옮긴 뒤 그릴 것이 남지 않은 시각 자료 장은 글머리 장으로 둔다 (빈 그래프·빈 도식을 만들지 않는다)."""
    kind = s["kind"]
    before_after = kind == "diagram" and s["diagram_type"] == "before_after"
    empty = ((kind == "kpi" and not s["kpis"]) or (kind == "chart" and not (s["categories"] and s["series"]))
             or (kind == "diagram" and not before_after and not s["nodes"]) or (before_after and not (s["left"] or s["right"])))
    if empty:
        s["kind"] = "bullets"
        s["kpis"], s["categories"], s["series"], s["nodes"] = [], [], [], []
        for key in VISUAL_TEXT_FIELDS:
            s[key] = ""


def clean_outline(out: "Outline", knowledge_dir: Path) -> dict:
    """AI 구성안을 저장용 dict 로 바꾼다. 종류·표·단계·출처를 규칙에 맞게 고치고 빈 항목을 버린다."""
    slides: list[dict] = []
    moved_total = dropped_contacts = 0
    for raw in out.slides:
        s = raw.model_dump()
        for key in ("kind", "title", "key_message", "subtitle", "left_title", "right_title"):
            s[key] = s[key].strip()
        s["kind"] = s["kind"].lower()
        for key in ("bullets", "left", "right", "needs_check"):
            s[key] = _strip_list(s[key])
        if s["kind"] not in SLIDE_KINDS:
            s["kind"] = "bullets"
        if s["kind"] == "cover" and slides:
            s["kind"] = "bullets"  # 표지는 맨 앞 한 장만
        _clean_visuals(s)
        s["columns"] = _strip_list(s["columns"])
        rows = [[c.strip() for c in row] for row in s["rows"]]
        rows = [row for row in rows if any(row)]
        if s["kind"] == "table" and not s["columns"]:
            s["kind"] = "bullets"
            s["bullets"] = s["bullets"] + [" / ".join(c for c in row if c) for row in rows]
        if s["kind"] == "table":
            n = len(s["columns"])
            s["rows"] = [(row + [""] * n)[:n] for row in rows]
        else:
            s["columns"], s["rows"] = [], []
        steps = [{"name": st["name"].strip(), "detail": st["detail"].strip()} for st in s["steps"]]
        s["steps"] = [st for st in steps if st["name"]][:MAX_STEPS]
        s["checked"] = []  # AI 가 새로 만든 구성안은 확인한 항목이 없다
        if s["kind"] == "closing":  # 연락처는 AI 가 쓰지 않는다: 지어내거나 자료 속 사람을 옮겨 오기 쉽다. 제안서를 만드는 사람 정보로 서버가 채운다
            kept = [b for b in s["bullets"] if not _CONTACT_LINE.search(b)]
            dropped_contacts += len(s["bullets"]) - len(kept)
            s["bullets"] = kept
        s["title"] = strip_title_period(s["title"])
        moved_total += _move_instructions(s)
        _drop_empty_visual(s)
        found, missing = check_sources(knowledge_dir, _strip_list(s["sources"]))
        s["sources"] = found
        s["needs_check"] = _unique(s["needs_check"] + [checks.source_unverified(m) for m in missing])
        if s["kind"] in ("kpi", "chart") and not s["sources"]:  # 수치는 읽은 자료에 있는 값만 쓴다: 출처가 없으면 늘 근거 문제로 알린다
            s["needs_check"] = _unique(s["needs_check"] + [checks.NO_FIGURE_SOURCE_NOTE])
        if s["kind"] not in ("cover", "closing") and not s["sources"] and not s["needs_check"]:
            s["needs_check"] = [NO_SOURCE_NOTE]
        slides.append(s)
    if moved_total or dropped_contacts:
        log.info("outline cleanup: %d author-instruction lines moved to needs_check, %d contact lines dropped from closing", moved_total, dropped_contacts)
    title = out.title.strip()
    if not slides or slides[0]["kind"] != "cover":
        from app.ai.proposal_tasks import Slide  # 순환 참조를 피해 늦게 불러온다
        slides.insert(0, Slide(kind="cover", title=title).model_dump())
    return {"title": title, "slides": slides}


def _lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.split("|")]


def slide_from_form(kind: str, form) -> dict:
    """편집 폼 값을 슬라이드 dict 로 바꾼다. 그 종류에 속한 칸만 읽고 나머지는 비운다. 종류가 틀리면 ValueError."""
    if kind not in SLIDE_KINDS:
        raise ValueError(kind)
    from app.ai.proposal_tasks import Slide  # 순환 참조를 피해 늦게 불러온다

    def text(name: str) -> str:
        return str(form.get(name) or "").strip()

    s = Slide(kind=kind).model_dump()
    s["title"], s["key_message"] = text("title"), text("key_message")
    s["sources"], s["needs_check"] = _lines(form.get("sources")), _unique(_lines(form.get("needs_check")))
    if kind == "cover":
        s["subtitle"] = text("subtitle")
    elif kind == "bullets":
        s["bullets"] = _lines(form.get("bullets"))
    elif kind == "two_column":
        s["left_title"], s["right_title"] = text("left_title"), text("right_title")
        s["left"], s["right"] = _lines(form.get("left")), _lines(form.get("right"))
    elif kind == "table":
        columns = [c for c in _cells(str(form.get("columns") or "").replace("\n", " ")) if c]
        rows = [_cells(line) for line in _lines(form.get("rows"))]
        rows = [row for row in rows if any(row)]
        if rows and not columns:
            raise ValueError(NO_COLUMNS)
        s["columns"] = columns
        s["rows"] = [(row + [""] * len(columns))[: len(columns)] for row in rows]
    elif kind == "steps":
        steps = []
        for line in _lines(form.get("steps")):
            name, _, detail = line.partition(":")
            if name.strip():
                steps.append({"name": name.strip(), "detail": detail.strip()})
        s["steps"] = steps[:MAX_STEPS]
    elif kind == "kpi":
        s["kpis"], s["note"] = _kpis_from_form(form.get("kpis")), text("note")
    elif kind == "chart":
        chart_type = text("chart_type") or ontology.CHART_TYPE_DEFAULT
        if chart_type not in ontology.CHART_TYPES:
            raise ValueError("알 수 없는 그래프 종류입니다.")
        s["chart_type"], s["unit"], s["note"] = chart_type, text("unit"), text("note")
        s["categories"], s["series"] = _chart_from_form(form.get("chart_data"))
    elif kind == "diagram":
        dtype = text("diagram_type") or ontology.DIAGRAM_TYPE_DEFAULT
        if dtype not in ontology.DIAGRAM_TYPES:
            raise ValueError("알 수 없는 도식 종류입니다.")
        s["diagram_type"], s["note"] = dtype, text("note")
        if dtype == "before_after":
            s["left_title"], s["right_title"] = text("left_title"), text("right_title")
            s["left"], s["right"] = _lines(form.get("left")), _lines(form.get("right"))
        else:
            s["nodes"] = _nodes_from_form(form.get("nodes"), dtype)
        if dtype in ("hub", "cycle"):
            s["center"] = text("center")
        if dtype == "matrix":
            s["axis_x"], s["axis_y"] = text("axis_x"), text("axis_y")
    return s


def _kpis_from_form(raw) -> list[dict]:
    """핵심 수치 칸: 한 줄에 `값 | 단위 | 이름 | 설명`."""
    items = []
    for n, line in enumerate(_lines(raw), start=1):
        cells = _cells(line)
        if len(cells) > 4:
            raise ValueError(f"핵심 수치 {n}번째 줄: 칸은 '값 | 단위 | 이름 | 설명' 네 개까지 씁니다.")
        value, unit, label, note = (cells + [""] * 4)[:4]
        if not value:
            raise ValueError(f"핵심 수치 {n}번째 줄: 맨 앞 칸에 값을 쓰세요.")
        items.append({"value": value, "unit": unit, "label": label, "note": note})
    if len(items) > ontology.MAX_KPIS:
        raise ValueError(f"핵심 수치는 한 장에 {ontology.MAX_KPIS}개까지 씁니다. 장을 나누세요.")
    return items


def _chart_from_form(raw) -> tuple[list[str], list[dict]]:
    """그래프 값 칸: 첫 줄은 `항목 | 계열 이름 …`, 다음 줄부터 `항목 이름 | 값 …`. 값은 숫자만 (단위는 단위 칸에)."""
    lines = _lines(raw)
    if not lines:
        return [], []
    head = _cells(lines[0])
    if len(head) < 2:
        raise ValueError("그래프 값의 첫 줄에는 '항목 | 계열 이름' 처럼 계열 이름을 쓰세요.")
    if all(parse_number(c) is not None for c in head[1:]):
        raise ValueError("그래프 값의 첫 줄은 값이 아니라 이름 줄입니다. '항목 | 계열 이름' 을 첫 줄에 쓰세요.")
    names = head[1:]
    if len(names) > ontology.MAX_CHART_SERIES:
        raise ValueError(f"그래프의 계열은 {ontology.MAX_CHART_SERIES}개까지 씁니다.")
    if len(lines) - 1 > ontology.MAX_CHART_POINTS:
        raise ValueError(f"그래프의 항목은 {ontology.MAX_CHART_POINTS}개까지 씁니다.")
    categories, columns = [], [[] for _ in names]
    for n, line in enumerate(lines[1:], start=2):
        cells = _cells(line)
        if not cells[0]:
            raise ValueError(f"그래프 값 {n}번째 줄: 맨 앞 칸에 항목 이름을 쓰세요.")
        if len(cells) != len(names) + 1:
            raise ValueError(f"그래프 값 {n}번째 줄: 값이 {len(names)}개여야 합니다 (첫 줄의 계열 수와 같게).")
        for c, cell in enumerate(cells[1:]):
            number = parse_number(cell)
            if number is None:
                raise ValueError(f"그래프 값 {n}번째 줄: 숫자가 아닌 값이 있습니다 ('{cell[:20]}'). 값에는 숫자만 쓰고 단위는 단위 칸에 쓰세요.")
            columns[c].append(number)
        categories.append(cells[0])
    if not categories:
        raise ValueError("그래프 값이 없습니다. 둘째 줄부터 '항목 이름 | 값' 을 쓰세요.")
    return categories, [{"name": name, "values": values} for name, values in zip(names, columns)]


def _nodes_from_form(raw, dtype: str) -> list[dict]:
    """도식 항목 칸: 한 줄에 `이름 | 설명 | 묶음`. 묶음은 일정에서는 기간, 2×2 에서는 자리(왼쪽 위 …)다."""
    nodes = []
    for n, line in enumerate(_lines(raw), start=1):
        cells = _cells(line)
        if len(cells) > 3:
            raise ValueError(f"도식 항목 {n}번째 줄: 칸은 '이름 | 설명 | 묶음' 세 개까지 씁니다.")
        label, detail, group = (cells + [""] * 3)[:3]
        if not label:
            raise ValueError(f"도식 항목 {n}번째 줄: 맨 앞 칸에 이름을 쓰세요.")
        if dtype == "matrix":
            place = matrix_quadrant(group)
            if place is None:
                raise ValueError(f"도식 항목 {n}번째 줄: 2×2 도식의 묶음 칸에는 {' · '.join(ontology.MATRIX_QUADRANTS)} 가운데 하나를 쓰세요.")
            group = place
        nodes.append({"label": label, "detail": detail, "group": group})
    limit = ontology.DIAGRAM_MAX_NODES[dtype]
    if len(nodes) > limit:
        raise ValueError(f"{ontology.DIAGRAM_TYPES[dtype]} 도식의 항목은 한 장에 {limit}개까지 씁니다. 장을 나누세요.")
    return nodes


def _join_cells(cells: list[str]) -> str:
    """칸들을 ` | ` 로 잇는다 (뒤쪽 빈 칸은 뺀다)."""
    cells = list(cells)
    while cells and not cells[-1]:
        cells.pop()
    return " | ".join(cells)


def slide_to_form(slide: dict) -> dict:
    """슬라이드 dict 를 편집 폼에 보일 문자열로 바꾼다 (slide_from_form 의 역변환)."""
    out = {k: slide.get(k, "") for k in ("title", "key_message", "subtitle", "left_title", "right_title")}
    for key in ("bullets", "left", "right", "sources", "needs_check"):
        out[key] = "\n".join(slide.get(key) or [])
    out["columns"] = " | ".join(slide.get("columns") or [])
    out["rows"] = "\n".join(" | ".join(row) for row in slide.get("rows") or [])
    out["steps"] = "\n".join(f"{st['name']}: {st['detail']}" if st["detail"] else st["name"] for st in slide.get("steps") or [])
    for key in VISUAL_TEXT_FIELDS:  # 옛 구성안에는 이 키들이 없다
        out[key] = slide.get(key) or ""
    out["kpis"] = "\n".join(_join_cells([str(i.get(k) or "") for k in ("value", "unit", "label", "note")]) for i in slide.get("kpis") or [] if isinstance(i, dict))
    out["nodes"] = "\n".join(_join_cells([str(n.get(k) or "") for k in ("label", "detail", "group")]) for n in slide.get("nodes") or [] if isinstance(n, dict))
    series = [x for x in slide.get("series") or [] if isinstance(x, dict)]
    lines = [" | ".join(["항목", *(str(x.get("name") or "") for x in series)])] if series else []
    for r, category in enumerate(slide.get("categories") or []):
        lines.append(" | ".join([str(category), *(number_text((x.get("values") or [])[r]) if r < len(x.get("values") or []) else "" for x in series)]))
    out["chart_data"] = "\n".join(lines)
    return out


def check_items(texts: list[str] | None, checked: list[str] | None = None) -> list[dict]:
    """확인 항목 문구를 화면용으로 바꾼다: 확인 안 한 근거 문제 → 확인 안 한 확인할 점 → 확인한 것 순서(각각 원래 순서)."""
    done = set(checked or [])
    items = [{"index": n, "text": t, "kind": check_kind(t), "checked": t in done} for n, t in enumerate(texts or []) if isinstance(t, str)]
    return sorted(items, key=lambda i: (i["checked"], i["kind"] != "evidence"))


def slide_check_state(slide: dict) -> dict:
    """한 장의 확인 현황: 안 한 근거 문제·확인할 점 개수, 확인한 개수, 카드에 쓸 단계(evidence / decide / clear)."""
    items = check_items(slide.get("needs_check"), slide.get("checked"))
    evidence = sum(1 for i in items if not i["checked"] and i["kind"] == "evidence")
    decide = sum(1 for i in items if not i["checked"] and i["kind"] == "decide")
    level = "evidence" if evidence else "decide" if decide else "clear"
    return {"evidence": evidence, "decide": decide, "done": sum(1 for i in items if i["checked"]), "level": level}


def outline_check_summary(outline: dict | None) -> dict:
    """구성안 전체의 장 수: evidence(근거 확인이 남은 장), decide(확인할 점만 남은 장), done(확인을 끝낸 장), open(남은 장 전체)."""
    states = [slide_check_state(s) for s in (outline or {}).get("slides") or [] if isinstance(s, dict)]
    evidence = sum(1 for s in states if s["level"] == "evidence")
    decide = sum(1 for s in states if s["level"] == "decide")
    done = sum(1 for s in states if s["level"] == "clear" and s["done"])
    return {"evidence": evidence, "decide": decide, "done": done, "open": evidence + decide}


def unresolved_check_count(outline: dict | None) -> int:
    """구성안 전체에서 아직 확인하지 않은 확인 항목(근거 문제 + 확인할 점)의 개수."""
    return sum(st["evidence"] + st["decide"] for st in (slide_check_state(s) for s in (outline or {}).get("slides") or [] if isinstance(s, dict)))


def open_check_slides(outline: dict | None) -> list[dict]:
    """확인하지 않은 항목이 남은 장: [{"index", "title", "open"}] (장 번호는 0부터). 제안서 상자의 경고가 그 장 카드로 잇는 데 쓴다."""
    found = []
    for i, s in enumerate((outline or {}).get("slides") or []):
        if not isinstance(s, dict):
            continue
        st = slide_check_state(s)
        if st["evidence"] + st["decide"]:
            found.append({"index": i, "title": s.get("title") or "(제목 없음)", "open": st["evidence"] + st["decide"]})
    return found


def contact_lines(user: User) -> list[str]:
    """마무리 장에 붙는 연락처 줄: 제안서를 만드는(로그인한) 사용자의 이름과, 계정 화면에서 적어 둔 연락처가 있으면 그것."""
    name = (user.name or user.username or "").strip()
    lines = [f"담당 {name}"] if name else []
    if (user.contact or "").strip():
        lines.append(f"연락처 {user.contact.strip()}")
    return lines


INTERNAL_SUFFIX = "-internal"


def internal_deck_path(path: Path) -> Path:
    """고객용 파일 옆의 내부 검토용 파일 경로 (`deck-….pptx` → `deck-…-internal.pptx`)."""
    return path.with_name(path.stem + INTERNAL_SUFFIX + path.suffix)


def check_summary_text(cs: dict) -> str:
    """"근거 문제 1장 · 확인할 점 2장" (0장인 부분은 뺀다). 남은 게 없으면 빈 글."""
    return " · ".join(p for p in (f"{CK['evidence']} {cs['evidence']}장" if cs["evidence"] else "", f"{CK['decide']} {cs['decide']}장" if cs["decide"] else "") if p)


def keep_checked(old: dict, new: dict) -> dict:
    """고친 장에서 더 이상 없는 문구는 확인 목록에서도 뺀다."""
    wanted = new.get("needs_check") or []
    return {**new, "checked": [t for t in old.get("checked") or [] if t in wanted]}


def save_outline(db: Session, p: Proposal, slides: list[dict], undo: dict | None = None) -> None:
    """구성안의 장 목록을 저장한다. 저장 시각은 늘 이전보다 커진다.
    `undo`(한 장 AI 다시 쓰기의 옛 장 `{"index", "slide"}`)를 주면 "되돌리기" 용으로 남기고, 주지 않으면(다른 모든 저장) 지운다: 장 번호·내용이 바뀌면 옛 장이 맞지 않는다."""
    now = datetime.now()
    if p.outline_updated_at is not None and now <= p.outline_updated_at:
        now = p.outline_updated_at + timedelta(microseconds=1)
    rest = {k: v for k, v in (p.outline or {}).items() if k != "slide_undo"}
    p.outline = {**rest, "slides": slides, **({"slide_undo": undo} if undo else {})}
    p.outline_updated_at = now
    if p.status == "deck":
        p.status = "outlined"  # 만든 제안서가 구성안과 달라졌다. 다시 만들어야 하는 건으로 되돌린다
    db.commit()


def reset_for_new_direction(p: Proposal, decks_dir: Path) -> None:
    """방향이 바뀌었을 때 구성안과 만든 제안서를 지우고, 이전 대화는 새 방향에 반영되지 않게 선을 긋는다. 커밋은 호출자가 한다."""
    deck = decks_dir / str(p.id) / p.deck_name if p.deck_name else None
    if deck is not None:
        internal_deck_path(deck).unlink(missing_ok=True)
        deck.with_suffix(".pdf").unlink(missing_ok=True)  # 제안서 PDF (베타 AJ, `pdf_export.pdf_path`)
    p.outline, p.outline_updated_at = None, None
    p.deck_name, p.deck_built_at = "", None
    p.chat_from_id = p.messages[-1].id if p.messages else None
    if deck is not None:
        deck.unlink(missing_ok=True)


def deck_is_stale(p: Proposal) -> bool:
    """만든 PPT 보다 구성안이 나중에 바뀌었으면 True."""
    return p.deck_built_at is not None and p.outline_updated_at is not None and p.outline_updated_at > p.deck_built_at


def deck_is_old(p: Proposal, decks_dir: Path) -> bool:
    """두 파일로 나누기 전에 만든 제안서인가: 파일 이름은 있는데 내부 검토용(`-internal`) 짝이 없다. 이 파일은 노트가 든 것이라 내려받지 못하게 하고 다시 만들게 한다."""
    if not p.deck_name:
        return False
    return not internal_deck_path(Path(decks_dir) / str(p.id) / p.deck_name).is_file()


def start_job(db: Session, p: Proposal, kind: str, user: User, extra: dict | None = None, rows: list | None = None) -> AIJob | None:
    """제안 건의 AI 작업을 시작한다. 이미 진행 중이면 None. 동시에 와도 하나만 들어간다.
    `rows`(대화 메시지 같은 함께 저장할 줄)는 작업과 같은 트랜잭션에 넣는다: 잠금·경쟁으로 실패하면 함께 저장되지 않는다.
    아직 저장하지 않은 새 제안 건(`p.id is None`, 제안 건 만들기)은 제안 건·작업·`rows` 를 한 번에 커밋한다 (잠금에 막히면 제안 건도 남지 않는다)."""
    if p.id is None:
        db.add(p)
        db.flush()
        job = AIJob(kind=kind, input={"proposal_id": p.id, **(extra or {})}, requester_id=user.id, priority=0, status="pending")
        db.add(job)
        db.flush()
        p.active_job_id = job.id
        for row in rows or []:
            db.add(row)
        db.commit()
        db.refresh(p)
        return job
    db.refresh(p)
    if p.active_job is not None and p.active_job.status in ontology.JOB_ACTIVE:
        return None
    pid, previous = p.id, p.active_job_id
    db.rollback()  # 읽기 트랜잭션을 끝내고 쓰기를 새로 시작한다
    # 작업은 커밋 전에는 워커에게 보이지 않는다. 같은 트랜잭션에서 조건부 UPDATE 로 자리를 잡고, 졌으면 되돌린다.
    job = AIJob(kind=kind, input={"proposal_id": pid, **(extra or {})}, requester_id=user.id, priority=0, status="pending")
    db.add(job)
    db.flush()
    same = Proposal.active_job_id.is_(None) if previous is None else Proposal.active_job_id == previous
    claimed = db.execute(update(Proposal).where(Proposal.id == pid, same).values(active_job_id=job.id))
    if claimed.rowcount != 1:  # 다른 요청이 먼저 잡았다.
        db.rollback()
        return None
    for row in rows or []:
        db.add(row)
    db.commit()
    db.refresh(p)
    return job


STEPS = tuple(ontology.PROPOSAL_STEPS.items())  # 단계 표시줄 (정의는 ontology)
_STATUS_STEP = ontology.PROPOSAL_STATUS_STEP
_JOB_STEP = ontology.JOB_KIND_STEP
RECENT_MESSAGES = 6


def _has_selection(p: Proposal) -> bool:
    return p.selected_index is not None and 0 <= p.selected_index < len(p.directions or [])


def _has_outline(p: Proposal) -> bool:
    return bool(p.outline and p.outline.get("slides"))


def step_states(p: Proposal) -> list[dict]:
    """단계 표시줄의 칸. state: done(끝남) / current(지금 할 단계) / todo(갈 수 있음) / locked(아직 못 감, 링크 없음).

    지금 단계가 아닌 단계의 AI 작업이 돌고 있으면 그 칸은 busy(진행 중)이고, 아직 못 가는 단계여도 링크가 걸린다."""
    keys = [k for k, _ in STEPS]
    current = keys.index(_STATUS_STEP.get(p.status, "directions"))
    reachable = {"target": True, "directions": True, "chat": _has_selection(p), "outline": _has_outline(p), "deck": _has_outline(p)}
    working = open_section(p) if _job_running(p) else None
    steps = []
    for i, (key, label) in enumerate(STEPS):
        state = "done" if i < current else "current" if i == current else "todo" if reachable[key] else "locked"
        busy = working == key and i != current
        steps.append({
            "key": key, "label": label, "state": state, "busy": busy,
            "href": None if state == "locked" and not busy else f"#{key}",
        })
    return steps


def _job_running(p: Proposal) -> bool:
    job = p.active_job
    return job is not None and job.status in ontology.JOB_ACTIVE and job.kind in _JOB_STEP


def open_section(p: Proposal) -> str:
    """처음부터 펼쳐 둘 구역. 진행 중인 AI 작업이 있으면 그 작업의 구역, 아니면 상태가 가리키는 구역."""
    job = p.active_job
    if job is not None and job.status in ontology.JOB_ACTIVE and job.kind in _JOB_STEP:
        return _JOB_STEP[job.kind]
    if job is not None and job.status == "failed" and job.kind == "proposal_chat" and p.messages and p.messages[-1].role == "user":
        return "chat"  # 답변을 못 받은 대화: 실패 안내와 다시 시도가 거기 있다
    return _STATUS_STEP.get(p.status, "directions")


def section_summaries(p: Proposal, candidates: list[Solution]) -> dict[str, str]:
    """접힌 구역에 보이는 한 줄 요약."""
    if is_group(p):
        who = target_name(p) + (f" · 고객 {len(p.group_customers)}곳 붙임" if p.group_customers else "") + (f" · {p.bm.name}" if p.bm is not None else "")
    else:
        who = target_name(p) + (f" · {p.project.name}" if p.project is not None else "") + (f" · {p.opportunity.name}" if p.opportunity is not None else "")
    # 세 숫자는 따로 부른다: "후보 솔루션 n개"(AI 에 주는 후보), "선택한 방향의 솔루션 n개"(조합). 방향을 고르기 전에는 조합 이야기를 하지 않는다
    target = f"{who} — 후보 솔루션 {len(candidates)}개" if candidates else who
    directions = p.directions or []
    if _has_selection(p):
        chosen = directions[p.selected_index]
        direction = f"선택한 방향: {chosen['title']} · 선택한 방향의 솔루션 {len(chosen.get('solutions') or [])}개"
    else:
        direction = f"방향 {len(directions)}개" if directions else "아직 만들지 않음"
    if not _has_selection(p):
        chat = "방향을 선택하면 시작할 수 있습니다"
    elif p.messages:
        chat = f"대화 {len(p.messages)}개 · 마지막 메시지 {ui.short_datetime(p.messages[-1].created_at, date.today())}"
    else:
        chat = "대화 없음"
    if _has_outline(p):
        slides = p.outline["slides"]
        left = check_summary_text(outline_check_summary(p.outline))
        outline = f"{len(slides)}장" + (f" · {left}" if left else "")
    else:
        outline = "아직 만들지 않음"
    deck = f"마지막 생성 {ui.short_datetime(p.deck_built_at, date.today())}" if p.deck_name and p.deck_built_at else "아직 만들지 않음"
    return {"target": target, "directions": direction, "chat": chat, "outline": outline, "deck": deck}


# ---------------------------------------------------------------- 구성안 AI 검토 (베타 AI)

REVIEW_CHANGES_MAX = 8
_REVIEW_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_COUNT_WORD = re.compile(r"([1-9])\s*(?:단계|가지)")  # "4단계", "3가지": 초안의 항목 수를 센 말이라 새 수치로 보지 않는다


def _visible_texts(slide: dict) -> list[str]:
    """고객에게 보이는 글 (출처·확인 항목은 뺀다)."""
    out = [str(slide.get(k) or "") for k in ("title", "key_message", "subtitle", "left_title", "right_title", "unit", "note", "center", "axis_x", "axis_y")]
    for key in ("bullets", "left", "right", "columns", "categories"):
        out += [str(v) for v in slide.get(key) or []]
    out += [str(c) for row in slide.get("rows") or [] for c in row]
    out += [str(st.get(k) or "") for st in slide.get("steps") or [] if isinstance(st, dict) for k in ("name", "detail")]
    out += [str(i.get(k) or "") for i in slide.get("kpis") or [] if isinstance(i, dict) for k in ("value", "unit", "label", "note")]
    out += [str(n.get(k) or "") for n in slide.get("nodes") or [] if isinstance(n, dict) for k in ("label", "detail", "group")]
    for s in slide.get("series") or []:
        if isinstance(s, dict):
            out.append(str(s.get("name") or ""))
            out += [str(v) for v in s.get("values") or []]
    return out


def _numbers(texts: list[str]) -> set[str]:
    """글 속 숫자들을 같은 모양으로 (쉼표 떼기, 소수 끝 0 떼기)."""
    found = set()
    for text in texts:
        text = _COUNT_WORD.sub("", text)
        for m in _REVIEW_NUMBER.findall(text):
            n = m.replace(",", "")
            if "." in n:
                n = n.rstrip("0").rstrip(".")
            found.add(n)
    return found


def review_changes(raw) -> list[str]:
    """AI 검토가 적은 고친 점: 빈 줄을 빼고 8개까지, 한 줄 200자까지."""
    items = [str(c).strip()[:200] for c in (raw or []) if str(c).strip()]
    return _unique(items)[:REVIEW_CHANGES_MAX]


def accept_review(draft: dict, reviewed: dict) -> tuple[dict | None, str]:
    """AI 검토가 고친 구성안을 받아들일지 정한다. 받으면 (구성안, ""), 버리면 (None, 이유).

    검토자는 자료 파일을 읽지 않으므로 새 사실을 더할 수 없다: 초안에 없는 숫자가 고객에게 보이는 글에 있으면 통째로 버린다.
    출처는 초안에 있던 경로만 남긴다. 초안의 확인 항목 중 검토본에서 빠진 것은 같은 제목의 장(없으면 같은 자리의 본문 장)에 되돌려 붙인다:
    확인 항목은 근거 문제를 알리는 장치라 검토가 지울 수 없다. 확인한 표시(`checked`)는 새 구성안이라 비어 있다."""
    slides = [dict(s) for s in reviewed.get("slides") or []]
    if not slides:
        return None, "검토본에 슬라이드가 없습니다"
    draft_slides = draft.get("slides") or []
    allowed = _numbers([t for s in draft_slides for t in _visible_texts(s)])
    new = sorted(_numbers([t for s in slides for t in _visible_texts(s)]) - allowed)
    if new:
        return None, "초안에 없는 숫자가 들어갔습니다: " + ", ".join(new[:5])
    draft_sources = {src for s in draft_slides for src in s.get("sources") or []}
    for s in slides:
        s["sources"] = [src for src in s.get("sources") or [] if src in draft_sources]
    present = {c for s in slides for c in s.get("needs_check") or []}
    body = [i for i, s in enumerate(slides) if s.get("kind") not in ("cover", "closing")] or list(range(len(slides)))
    by_title = {s.get("title"): i for i, s in enumerate(slides) if s.get("title")}
    for pos, s in enumerate(draft_slides):
        missing = [c for c in s.get("needs_check") or [] if c not in present]
        if not missing:
            continue
        target = by_title.get(s.get("title"))
        if target is None:
            target = min(body, key=lambda i: abs(i - pos))
        slides[target]["needs_check"] = _unique(list(slides[target].get("needs_check") or []) + missing)
        present.update(missing)
    for s in slides:
        s["checked"] = []
        if s.get("kind") not in ("cover", "closing") and not s.get("sources") and not s.get("needs_check"):
            s["needs_check"] = [NO_SOURCE_NOTE]
    return {**reviewed, "slides": slides}, ""

"""제안 건 도우미."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app import checks, ontology, ui
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


def candidate_condition(extra_ids):
    """제안 후보 규칙 (한 곳): 보류가 아닌 모든 단계의 솔루션 + 제안 건에 직접 추가한 솔루션 (`extra_solution_ids`)."""
    cond = Solution.stage != ontology.STAGE_HOLD
    ids = [i for i in (extra_ids or []) if isinstance(i, int)]
    return or_(cond, Solution.id.in_(ids)) if ids else cond


def _base_candidates(db: Session, p: Proposal) -> list[Solution]:
    ids = [i for i in (p.extra_solution_ids or []) if isinstance(i, int)]
    return list(db.scalars(select(Solution).where(candidate_condition(ids)).order_by(Solution.name, Solution.id)))


def priority_groups(db: Session, p: Proposal, candidates: list[Solution] | None = None) -> list[dict]:
    """제안 후보 중 앞에 둘 솔루션 묶음 (순서대로): 영업기회의 검토 솔루션, 그 영업기회의 BM 의 솔루션
    (영업기회가 없으면 프로젝트가 따르는 BM 의 솔루션). 각 묶음은 {"reason": 글, "solutions": [...]} (이름순, 앞 묶음에 든 것은 뺀다).
    후보가 아닌 솔루션(보류인데 직접 추가하지 않은 것)은 넣지 않는다: 후보의 집합은 `candidate_condition` 그대로다."""
    pool = {s.id: s for s in (candidates if candidates is not None else _base_candidates(db, p))}
    opp = p.opportunity
    bm = opp.bm if opp is not None else (p.project.bm if p.project is not None else None)
    sources = []
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
            groups.append({"reason": reason, "solutions": [pool[s.id] for s in picked]})
    return groups


def candidate_solutions(db: Session, p: Proposal) -> list[Solution]:
    """제안에 조합할 수 있는 솔루션: 집합은 `candidate_condition` 이 정한다. 순서만 영업기회의 검토 솔루션 → BM 의 솔루션 → 나머지(이름순)."""
    base = _base_candidates(db, p)
    front = [s for g in priority_groups(db, p, base) for s in g["solutions"]]
    first = {s.id for s in front}
    return front + [s for s in base if s.id not in first]


def display_title(p: Proposal) -> str:
    if p.title:
        return p.title
    if p.project is not None:
        return f"{p.customer.name} · {p.project.name} 제안"
    return f"{p.customer.name} 제안"


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
        cleaned.append({
            "title": d.title, "summary": d.summary, "solutions": solutions, "rationale": rationale,
            "effects": list(d.effects), "needs_check": needs_check,
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


# 고객에게 보이는 글에 섞인 "작성자에게 하는 말"을 알아보는 작고 보수적인 목록: 줄에 분명한 지시 표지가 있을 때만 본다.
# "AI가 초안을 작성", "한계를 보완", "책임 범위를 명시", "○○시청 도입 사례" 같은 보통 문구는 동사만으로는 건드리지 않는다.
# 이 목록은 어림이다. 한 줄을 분명한 규칙으로 가를 수 없으면 슬라이드에 둔다 (내부 검토용 PPT 와 확인할 점 상자가 안전망).
# 가르는 규칙 하나: `…필요`·`…요망`·`…하세요`·`…할 것` 은 대상이 문서 값(금액·수치·절감률·가격·단가·일정·날짜·비율·건수·사례·레퍼런스·근거·내용 …, `_DOC`)이거나
# 줄이 동사 하나뿐이거나 `[확인 필요]`·`확인 필요:`·`확인 필요 - 견적팀`·`확인 필요(영업팀)` 처럼 뒤에 이름표·괄호가 붙을 때만 작성자 말투로 본다.
# 대상이 설비·네트워크·현장·귀사 같은 고객 쪽 일이면("네트워크 구성 확인 필요", "현장 여건 검토 요망", "제안 내용을 검토해 주시기 바랍니다") 고객 문장이다.
_V_CORE = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|확인)"
_V_ASK = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|확인|추가|첨부)"  # "…바랍니다": 검토는 뺀다 ("귀사의 검토 바랍니다")
_V_WRITE = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|추가|첨부)"  # "…하세요"·"…할 것": 확인·검토는 뺀다 ("성과를 확인할 것", "앱에서 확인하세요")
_V_FILL = r"(?:기재|작성|명시|기입|입력|삽입|보완|수정|확인|추가|첨부|보강|업데이트|교체|포함|제시|검토)"  # 문서 값이 대상일 때
_DOC = r"(?:총액|금액|수치|절감률|가격표?|단가|견적|예산|일정|기간|날짜|비율|건수|수량|통계|실적|사례|레퍼런스|근거|내용)"
_PLACEHOLDER = r"(?:○{2,}|O{3,}|△{2,}|□{2,})"
_BLANK = r"(?:○{2,}|△{2,}|□{2,})"  # 칸에 든 자리표시 (라틴 O 는 "담당: OOO 팀" 처럼 이름일 수 있어 뺀다)
AUTHOR_INSTRUCTION_PATTERNS = tuple(re.compile(p) for p in (
    r"(?:함께|반드시)\s*(?:기재|명시|작성)(?:\s*(?:하세요|해\s*주세요|할\s*것))?$",   # "담당자 이름을 함께 기재", "보안 정책을 반드시 명시"
    rf"{_DOC}.*(?:함께|반드시)\s*제시(?:\s*(?:하세요|해\s*주세요|할\s*것))?$",      # "총액과 견적 유효기간을 함께 제시" ("ROI 분석 결과를 함께 제시" 는 고객 문장)
    rf"{_V_WRITE}\s*할\s*것$",                                          # "담당자 연락처를 추가할 것"
    rf"{_DOC}.*{_V_FILL}\s*할\s*것$",                                   # "근거를 제시할 것", "가격표를 포함할 것", "실제 수치로 교체할 것", "근거 자료 보강할 것"
    r"^(?:확인|검토)\s*(?:하실\s*|할\s*)?것$",                           # 줄 전체가 "확인할 것"
    r"(?:적을|넣을|쓸)\s*것$",                                           # "도입 사례를 구체적으로 적을 것"
    rf"{_V_ASK}\s*(?:(?:하시기|해\s*주시기)\s*)?바랍니다$",               # "자료를 확인하시기 바랍니다"
    r"(?:확인|수정|보완|기재|작성)\s*바람$",                              # "확인 바람"
    rf"^{_V_ASK}\s*요망$|^(?:검토)\s*요망$|{_DOC}.*{_V_FILL}\s*요망$",    # "검토 요망", "레퍼런스 추가 요망" ("현장 여건 검토 요망" 은 고객 문장)
    rf"{_V_WRITE}\s*(?:하세요|해\s*주세요)$",                             # "수치를 입력하세요"
    rf"{_DOC}.*(?:{_V_FILL}\s*(?:하세요|해\s*주세요)|(?:넣으세요|넣어\s*주세요|포함하세요))$",  # "레퍼런스를 포함하세요", "여기에 도입 사례를 넣으세요"
    rf"{_DOC}.*(?:{_V_FILL}|보강|업데이트|교체)\s*필요$",                  # "금액 확인 필요", "고객 사례 추가 필요", "내용 보강 필요", "수치 업데이트 필요"
    rf"^{_V_CORE}\s*필요$",                                              # 줄 전체가 "확인 필요"
    rf"^(?:도입|구축|계약|착수)\s*전\b.*{_V_CORE}\s*필요$",                # "도입 전 네트워크 환경 확인 필요" (앞 묶음이 정한 것)
    rf"{_V_CORE}\s*필요\s*[-–—:：(（\[\]]|\[\s*{_V_CORE}\s*필요$",         # "확인 필요: 절감률", "금액 확인 필요 - 견적팀", "확인 필요(영업팀)", 끝의 "[확인 필요]"
    rf"^[\(\[]\s*{_V_CORE}\s*필요\s*[\)\]]",                           # 맨 앞 "[확인 필요]"
    rf"^(?:{_DOC}.*)?(?:작성|기재|기입|삽입)\s*예정$",                     # "작성 예정"
    rf"{_DOC}.*(?:작성|기재|기입|삽입|추가|업데이트)\s*예정$",              # "사례 사진 삽입 예정"
    r"(?:금액|가격|단가|수치|절감률|비용|일정)\s*미정$",                    # "금액 미정"
    r"[\[\(<]\s*(?:TBD|TODO)\s*[\]\)>]|^(?:TBD|TODO)\s*[:：]?$|[:：]\s*(?:TBD|TODO)$|\s(?:TBD|TODO)$",  # 채우지 않은 자리표시 ("TODO 앱 연동", "서비스명: TBD Cloud" 는 고객 문장)
    rf"^[\(\[]?\s*{_PLACEHOLDER}\s*[\)\]]?$",                          # 줄 전체가 "○○" / "(OOO)"
    rf"[:：]\s*[\(\[]?{_BLANK}[\)\]]?\s*[^\s:：]{{0,3}}$",              # "담당자: ○○○", "비용: ○○원"
))
_LINE_TAIL = re.compile(r"[\s.。)\]]+$")
# 마무리 장에서 AI 가 쓴 연락처 줄: 전화번호·이메일이 든 줄, 또는 "담당: 홍길동"/"연락처:" 같은 이름표만 있는 줄. "문의 환영" 같은 인사는 둔다.
_CONTACT_LINE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+|0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}"
    r"|^\s*(?:담당자?|담당\s*부서|연락처|문의처?|Tel|TEL|E-?mail|Email|Contact)\s*[:：]\s*(?:[^\s:：]+(?:\s+[^\s:：]+)?)?\s*$")


def is_author_instruction(text: str) -> bool:
    """고객에게 보이는 줄이 아니라 작성자에게 하는 말처럼 쓰인 줄인가 (줄 끝 말투로만 본다)."""
    line = _LINE_TAIL.sub("", str(text or "").strip())
    return bool(line) and any(p.search(line) for p in AUTHOR_INSTRUCTION_PATTERNS)


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
    if moved:
        s["needs_check"] = _unique(s["needs_check"] + moved)
    return len(moved)


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
        moved_total += _move_instructions(s)
        found, missing = check_sources(knowledge_dir, _strip_list(s["sources"]))
        s["sources"] = found
        s["needs_check"] = _unique(s["needs_check"] + [checks.source_unverified(m) for m in missing])
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
    return s


def slide_to_form(slide: dict) -> dict:
    """슬라이드 dict 를 편집 폼에 보일 문자열로 바꾼다 (slide_from_form 의 역변환)."""
    out = {k: slide.get(k, "") for k in ("title", "key_message", "subtitle", "left_title", "right_title")}
    for key in ("bullets", "left", "right", "sources", "needs_check"):
        out[key] = "\n".join(slide.get(key) or [])
    out["columns"] = " | ".join(slide.get("columns") or [])
    out["rows"] = "\n".join(" | ".join(row) for row in slide.get("rows") or [])
    out["steps"] = "\n".join(f"{st['name']}: {st['detail']}" if st["detail"] else st["name"] for st in slide.get("steps") or [])
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


def save_outline(db: Session, p: Proposal, slides: list[dict]) -> None:
    """구성안의 장 목록을 저장한다. 저장 시각은 늘 이전보다 커진다."""
    now = datetime.now()
    if p.outline_updated_at is not None and now <= p.outline_updated_at:
        now = p.outline_updated_at + timedelta(microseconds=1)
    p.outline = {**(p.outline or {}), "slides": slides}
    p.outline_updated_at = now
    if p.status == "deck":
        p.status = "outlined"  # 만든 제안서가 구성안과 달라졌다. 다시 만들어야 하는 건으로 되돌린다
    db.commit()


def reset_for_new_direction(p: Proposal, decks_dir: Path) -> None:
    """방향이 바뀌었을 때 구성안과 만든 제안서를 지우고, 이전 대화는 새 방향에 반영되지 않게 선을 긋는다. 커밋은 호출자가 한다."""
    deck = decks_dir / str(p.id) / p.deck_name if p.deck_name else None
    if deck is not None:
        internal_deck_path(deck).unlink(missing_ok=True)
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
    `rows`(대화 메시지 같은 함께 저장할 줄)는 작업과 같은 트랜잭션에 넣는다: 잠금·경쟁으로 실패하면 함께 저장되지 않는다."""
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
    who = p.customer.name + (f" · {p.project.name}" if p.project is not None else "") + (f" · {p.opportunity.name}" if p.opportunity is not None else "")
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

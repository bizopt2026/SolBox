"""AI 작업 처리기 등록. 종류(kind) 이름으로 처리 함수를 찾는다.

처리기 모양: (db, input, runner, settings) -> 결과 문자열.
새 처리기는 `HANDLERS["kind"] = fn` 으로 등록한다. 처리기는 `runner.run(..., timeout_s=settings.ai_timeout_s)` 로 부른다.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.parsing import parse_json_output
from app.ai.runner import DEFAULT_TOOLS, ClaudeError, ClaudeRunner
from app.config import Settings
from app import ontology
from app.labels import ACTIVITY_KINDS
from app.models import AIJob, Customer, Partner, Solution, User

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
Handler = Callable[[Session, dict, ClaudeRunner, Settings], str]

HANDLERS: dict[str, Handler] = {}
# 작업이 끝난 뒤(성공·실패 모두) 새 세션으로 부르는 후처리. 모양: (db, input) -> None.
AFTER_JOB: dict[str, Callable[[Session, dict], None]] = {}
# 작업을 집기 전에 부르는 검사 (종류 → 함수(db, 작업 입력) -> 지금 집어도 되는가). False 면 이번에는 건너뛰고 다음 작업을 본다.
CLAIM_GUARDS: dict[str, Callable[[Session, dict], bool]] = {}

PROMPT_DIR = Path(__file__).parent / "prompts"
NO_READABLE_FILES = "텍스트를 읽을 수 있는 자료가 없습니다."
MEMO_OPEN, MEMO_CLOSE = "<<<MEMO", "MEMO>>>"
BAD_OUTPUT_MESSAGE = "AI 응답 형식이 올바르지 않습니다. 다시 시도하세요."


def load_prompt(name: str, **values) -> str:
    """`prompts/<name>.md` 를 읽어 `{{키}}` 를 값으로 바꾼다. 한 번에 바꾸므로 값 안의 `{{...}}` 는 다시 바뀌지 않는다."""
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    values = {**prompt_constants(), **values}
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def prompt_constants() -> dict[str, str]:
    """프롬프트 글에 끼워 넣는 고정 값. 사본 경로와 용어는 ontology 가 정한 것을 쓴다."""
    return {
        "ex_solution": ontology.rel_path("solution", id=3),
        "ex_customer": ontology.rel_path("customer", id=1),
        "ex_report": ontology.rel_path("research_report", day="YYYY-MM-DD", tag="market", id=1),
        "dir_solution": ontology.mirror_dir("solution", id="<id>"),
        "dir_activities": ontology.mirror_folder("activity") + "/",
        "dir_research": ontology.mirror_folder("research_report") + "/",
        "dir_opportunities": ontology.mirror_folder("opportunity") + "/",
        "dir_bms": ontology.mirror_folder("bm") + "/",
        "dir_partners": ontology.mirror_folder("partner") + "/",
        "dir_customers": ontology.mirror_folder("customer") + "/",
        "needs_heading": ontology.prop("activity", "demand_signals"),
        "ai_label": ontology.AI_GENERATED_LABEL,
        "business_type_guide": ontology.business_type_guide(),  # 사업방식 셋의 한 줄 뜻 (베타 Y)
        "business_type_codes": ", ".join(f"`{c}`({n})" for c, n in ontology.BUSINESS_TYPES.items()),
        "candidate_rule": ontology.proposal_candidate_rule_text(),
        "activity_kind_bullets": "\n".join(f"  - `{k}` {v}" for k, v in ontology.ACTIVITY_KINDS.items()),
        "activity_kind_inline": ", ".join(f"`{k}`({v})" for k, v in ontology.ACTIVITY_KINDS.items()),
    }


def run_json(
    db: Session, runner: ClaudeRunner, prompt: str, model: type[T], settings: Settings,
    *, allowed_tools: tuple[str, ...] | list[str] = DEFAULT_TOOLS,
    timeout_s: int | None = None, cwd: Path | None = None, file_scope: bool = False,
) -> T:
    """프롬프트를 실행해 JSON 을 `model` 로 검증한다. 형식 오류(bad_output)면 한 번만 다시 시도한다.

    CLI 는 오래 걸리므로 실행 전에 `db` 의 읽기 트랜잭션을 끝낸다 (호출자는 DB 읽기를 먼저 마쳐야 한다)."""
    for attempt in (1, 2):
        db.rollback()
        extra = {"file_scope": True} if file_scope else {}  # 다른 작업의 호출 모양은 그대로 둔다
        text = runner.run(
            prompt, allowed_tools=allowed_tools,
            timeout_s=settings.ai_timeout_s if timeout_s is None else timeout_s, cwd=cwd, **extra,
        )
        try:
            return parse_json_output(text, model)
        except ClaudeError as e:
            if e.kind != "bad_output":
                raise
            log.warning("AI 응답 형식 오류 (시도 %s): %s", attempt, e.message)
            if attempt == 2:
                raise ClaudeError("bad_output", BAD_OUTPUT_MESSAGE) from e
    raise ClaudeError("bad_output")  # 도달하지 않는다


class FollowUpDraft(BaseModel):
    task: str = ""
    owner: str = ""
    due: str = ""


class SignalDraft(BaseModel):
    customer: str = ""
    content: str = ""
    field: Any = ""  # 분야 제안 (고객니즈 관리 2026-11-27): 사용 중인 분야 이름 하나. 모르는 이름은 `solution_fields.suggest_id` 가 버린다


class Mentions(BaseModel):
    """원문에 나온 이름들. 프로젝트의 옛 이름 `businesses` 키도 받는다 (저장된 초안·정리안에 남아 있다)."""

    model_config = ConfigDict(populate_by_name=True)

    solutions: list[str] = []
    customers: list[str] = []
    projects: list[str] = Field(default=[], validation_alias=AliasChoices("projects", "businesses"))
    partners: list[str] = []


class ActivityDraft(BaseModel):
    kind: str = ontology.ACTIVITY_KIND_DEFAULT
    date: str | None = None
    title: str = ""
    attendees: list[Any] = []  # 글 또는 {name, title, company, internal} (담당자 연결 ①: app.contacts.attendee_list)
    discussion: list[str] = []
    decisions: list[str] = []
    follow_ups: list[FollowUpDraft] = []
    demand_signals: list[SignalDraft] = []
    mentions: Mentions = Mentions()


def _valid_day(value: str | None) -> bool:
    if not value or len(value) != 10:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def normalize_draft(d: ActivityDraft, today: date) -> ActivityDraft:
    return d.model_copy(update={
        "kind": d.kind if d.kind in ACTIVITY_KINDS else ontology.ACTIVITY_KIND_DEFAULT,
        "date": d.date if _valid_day(d.date) else today.isoformat(),
        "title": d.title.strip() or "제목 없음",
        "follow_ups": [f.model_copy(update={"due": f.due if _valid_day(f.due) else ""}) for f in d.follow_ups],
    })


_MENTION_MODELS = {key: ontology.model_for(code) for key, code in ontology.MENTION_KEYS.items()}


COMPANY_MARKER = re.compile(r"\(\s*주\s*\)|주식회사")  # NFKC 가 ㈜ 를 (주) 로 바꾼 뒤의 글


def normalize_name(name: str, company: bool = False) -> str:
    """이름 비교용 정규형: NFKC, 대소문자 무시, 공백·문장부호·기호를 모두 없앤다 (모든 문자의 글자·숫자·결합 문자는 남긴다).
    company=True (고객·파트너 이름을 견줄 때)면 회사 표지 ㈜·(주)·주식회사 도 없애 "㈜한빛" "(주) 한빛" "한빛 주식회사" 를 같은 이름으로 본다."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    if company:
        folded = COMPANY_MARKER.sub("", folded)
    return "".join(ch for ch in folded if unicodedata.category(ch)[0] in "LNM")


def aliases_of(obj) -> list[str]:
    """고객·파트너의 다른 이름들 (다른 유형은 빈 목록). `app.aliases` 는 이 모듈을 불러오므로 늦게 불러온다."""
    if not hasattr(obj, "aliases"):
        return []
    from app import aliases

    return aliases.of(obj)


def match_mentions(db: Session, mentions: Mentions) -> dict:
    """종류별로 {"matched": [id...], "new": [이름...], "ambiguous": [{"name", "candidates": [id...]}]}.

    정규형이 같으면 연결, 아니면 한쪽이 다른 쪽에 포함되는 등록 항목이 정확히 하나일 때 연결한다.
    후보가 여럿이면 자동 연결도 새 등록 제안도 하지 않고 ambiguous 로 돌려준다. 2자 미만 이름은 연결하지 않고, 3자 미만 등록명(AI, KT 등)은 포함 비교에 쓰지 않는다(완전 일치만)."""
    result = {}
    for rel, model in _MENTION_MODELS.items():
        registered = []
        for o in db.scalars(select(model).order_by(model.id)):
            names = [o.name, *aliases_of(o)]  # 고객·파트너는 다른 이름도 같은 정규형으로 견준다 (베타 AE)
            registered.extend((o.id, normalize_name(n)) for n in names)
        registered = [(i, k) for i, k in registered if len(k) >= 2]
        matched: list[int] = []
        new: list[str] = []
        ambiguous: list[dict] = []
        seen: set[str] = set()
        for name in getattr(mentions, rel):
            key = normalize_name(name)
            if not key or key in seen:
                continue
            seen.add(key)
            if len(key) < 2:
                found: list[int] = []
            else:
                found = [i for i, k in registered if k == key][:1]
                if not found:
                    found = list(dict.fromkeys(i for i, k in registered if len(k) >= 3 and (key in k or k in key)))
            if len(found) == 1:
                if found[0] not in matched:
                    matched.append(found[0])
            elif found:
                ambiguous.append({"name": name.strip(), "candidates": found})
            elif rel != "projects":
                new.append(name.strip())
        result[rel] = {"matched": matched, "new": new, "ambiguous": ambiguous}
    return result


def draft_summary_md(d: ActivityDraft) -> str:
    sections = []
    from app.contacts import attendee_list

    people = [a["name"] + (f" ({a['title']})" if a["title"] else "") for a in attendee_list(d.attendees)]
    for title, items in (("참석자", people), ("논의 내용", d.discussion), ("결정 사항", d.decisions)):
        items = [i.strip() for i in items if isinstance(i, str) and i.strip()]
        if items:
            sections.append(f"## {title}\n" + "\n".join(f"- {i}" for i in items))
    return "\n\n".join(sections)


def writer_name(db: Session, user_id: int | None) -> str:
    """프롬프트의 "작성자": 글을 올린 사용자의 표시 이름 (없으면 알 수 없음)."""
    user = db.get(User, user_id) if user_id else None
    return ((user.name or user.username).strip() if user else "") or "(알 수 없음)"


def _name_list(db: Session, model) -> str:
    names = [o.name for o in db.scalars(select(model).order_by(model.name))]
    return "\n".join(f"- {n}" for n in names) or "(없음)"


def _structure_activity(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    # 원문이 구분 표시를 닫아 버리지 못하게 한다.
    memo = str(input.get("raw_text", "")).replace(MEMO_CLOSE, "MEMO> >>").replace(MEMO_OPEN, "<<< MEMO")
    job = db.get(AIJob, input.get("_job_id")) if input.get("_job_id") else None
    from app import solution_fields as sfields

    prompt = load_prompt(
        "activity_structure", raw_text=memo, today=date.today().isoformat(), writer=writer_name(db, job.requester_id if job else None),
        solutions=_name_list(db, Solution), customers=_name_list(db, Customer),
        projects=_name_list(db, _MENTION_MODELS["projects"]), partners=_name_list(db, Partner), field_names=sfields.prompt_names(db),
    )
    draft = run_json(db, runner, prompt, ActivityDraft, settings)
    return normalize_draft(draft, date.today()).model_dump_json()


class SolutionDraft(BaseModel):
    summary: str = ""
    description: str = ""
    tags: list[str] = []
    business_types: Any = []  # 사업방식 제안 [{"code": "invest", "reason": "…"}]. 잘못된 모양·모르는 코드는 `business_types.clean_suggestions` 가 버린다 (저장하지 않는다)
    field: Any = ""  # 분야 제안: 프롬프트에 준 사용 중인 분야 이름 하나 (베타 AD). 목록에 없는 이름·글이 아닌 값은 `solution_fields.suggest_id` 가 버린다 (저장하지 않는다)


def _draft_solution(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    solution = db.get(Solution, input.get("solution_id"))
    if solution is None:
        raise ClaudeError("unknown", "솔루션을 찾을 수 없습니다.")
    folder = ontology.mirror_dir("solution_file", solution_id=solution.id)
    texts = settings.knowledge_dir / folder
    if not texts.is_dir() or not any(texts.glob("*.md")):
        raise ClaudeError("bad_output", NO_READABLE_FILES)
    from app import solution_fields as sfields

    prompt = load_prompt("solution_draft", solution_name=solution.name, folder=folder, field_names=sfields.prompt_names(db))
    return run_json(db, runner, prompt, SolutionDraft, settings).model_dump_json()


HANDLERS["activity_structure"] = _structure_activity
HANDLERS["solution_draft"] = _draft_solution

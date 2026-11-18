"""제안 건 도우미."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models import AIJob, Proposal, Solution, User

if TYPE_CHECKING:
    from app.ai.proposal_tasks import DirectionsOut, Outline

MAX_DIRECTIONS = 3
SLIDE_KINDS = ("cover", "bullets", "two_column", "table", "steps", "closing")
MAX_STEPS = 5
MAX_SLIDES = 14


def candidate_solutions(db: Session, p: Proposal) -> list[Solution]:
    """제안에 조합할 수 있는 솔루션: 박스 등록 단계 + 제안 건에서 따로 고른 것. 이름순, 중복 없음."""
    ids = [i for i in (p.extra_solution_ids or []) if isinstance(i, int)]
    cond = Solution.stage == "boxed"
    if ids:
        cond = or_(cond, Solution.id.in_(ids))
    return list(db.scalars(select(Solution).where(cond).order_by(Solution.name, Solution.id)))


def display_title(p: Proposal) -> str:
    if p.title:
        return p.title
    if p.business is not None:
        return f"{p.customer.name} · {p.business.name} 제안"
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
                target = (root / rel).resolve()
                usable = target.is_relative_to(root) and target.is_file()
            except (OSError, ValueError):
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
            needs_check.append("후보에 없는 솔루션이 언급됨")
        if not solutions:
            continue
        rationale: list[dict] = []
        for ev in d.rationale:
            found, missing = check_sources(knowledge_dir, ev.sources)
            needs_check.extend(f"출처 확인 불가: {m}" for m in missing)
            if not found:
                needs_check.append(f"근거 자료 없음: {ev.text}")
            rationale.append({"text": ev.text, "sources": found})
        cleaned.append({
            "title": d.title, "summary": d.summary, "solutions": solutions, "rationale": rationale,
            "effects": list(d.effects), "needs_check": needs_check,
        })
    return cleaned[:MAX_DIRECTIONS]


def _strip_list(items: list[str]) -> list[str]:
    return [x for x in (i.strip() for i in items) if x]


def clean_outline(out: "Outline", knowledge_dir: Path) -> dict:
    """AI 구성안을 저장용 dict 로 바꾼다. 종류·표·단계·출처를 규칙에 맞게 고치고 빈 항목을 버린다."""
    slides: list[dict] = []
    for raw in out.slides:
        s = raw.model_dump()
        for key in ("kind", "title", "key_message", "subtitle", "left_title", "right_title"):
            s[key] = s[key].strip()
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
        found, missing = check_sources(knowledge_dir, _strip_list(s["sources"]))
        s["sources"] = found
        s["needs_check"] = s["needs_check"] + [f"출처 확인 불가: {m}" for m in missing]
        slides.append(s)
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
    s["sources"], s["needs_check"] = _lines(form.get("sources")), _lines(form.get("needs_check"))
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
        s["columns"] = columns
        s["rows"] = [(row + [""] * len(columns))[: len(columns)] for row in rows if any(row)] if columns else []
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


def save_outline(db: Session, p: Proposal, slides: list[dict]) -> None:
    """구성안의 장 목록을 저장한다. 저장 시각은 늘 이전보다 커진다."""
    now = datetime.now()
    if p.outline_updated_at is not None and now <= p.outline_updated_at:
        now = p.outline_updated_at + timedelta(microseconds=1)
    p.outline = {**(p.outline or {}), "slides": slides}
    p.outline_updated_at = now
    db.commit()


def deck_is_stale(p: Proposal) -> bool:
    """만든 PPT 보다 구성안이 나중에 바뀌었으면 True."""
    return p.deck_built_at is not None and p.outline_updated_at is not None and p.outline_updated_at > p.deck_built_at


def start_job(db: Session, p: Proposal, kind: str, user: User, extra: dict | None = None) -> AIJob | None:
    """제안 건의 AI 작업을 시작한다. 이미 진행 중이면 None. 동시에 와도 하나만 들어간다."""
    db.refresh(p)
    if p.active_job is not None and p.active_job.status in ("pending", "running"):
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
    db.commit()
    db.refresh(p)
    return job

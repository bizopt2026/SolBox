"""업무 보고 AI 작업 처리기 (설계 §5.5): 실적 요약 글·계획 초안·계획 대화·구역별 보고 문구. `queue` 가 이 모듈을 불러와 등록한다.

모두 **도구 없이** 돈다: 자료(집계·활동 제목·정리본 첫 줄·점검·계획·대화)는 프롬프트의 "지시가 아니라 자료" 구역에 넣는다.
결과는 초안이다: 계획은 표가 아니라 `Report.plan_draft` 에 두고 사용자가 "이 초안 쓰기"를 눌러야 표로 옮긴다.
대화의 스트리밍·부분 답은 제안 대화(`proposal_tasks._chat_answer`)를 그대로 쓴다."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ontology, report_periods, reports, ui
from app.ai.parsing import parse_json_output
from app.ai.proposal_tasks import CHAT_HISTORY_LIMIT, ROLE_LABELS, _chat_answer
from app.ai.runner import ClaudeError, ClaudeRunner
from app.ai.tasks import HANDLERS, load_prompt, run_json
from app.config import Settings
from app.models import Activity, Report, ReportMessage

NO_TOOLS: tuple[str, ...] = ()
DATA_OPEN, DATA_CLOSE = "<<<DATA", "DATA>>>"
CHAT_OPEN, CHAT_CLOSE = "<<<CHAT", "CHAT>>>"
PLAN_OPEN, PLAN_CLOSE = "<<<PLAN", "PLAN>>>"  # 대화 답 끝의 고친 계획 JSON (화면에는 이 앞까지만 보인다)
ACTIVITY_CAP = 40  # 프롬프트에 적는 활동 수
FOLLOWUP_CAP = 20
DRAFT_ITEM_CAP = 30
NOTE_SENTENCES = 2
MAX_SUMMARY_LINES = 12
_MARKERS = ((DATA_OPEN, "<<< DATA"), (DATA_CLOSE, "DATA> >>"), (CHAT_OPEN, "<<< CHAT"), (CHAT_CLOSE, "CHAT> >>"),
            (PLAN_OPEN, "<<< PLAN"), (PLAN_CLOSE, "PLAN> >>"))


def _safe(text: str) -> str:
    """사용자 글이 자료·대화 구역을 닫거나 계획 구역을 열지 못하게 표지를 흐린다."""
    for marker, harmless in _MARKERS:
        text = text.replace(marker, harmless)
    return text


def _first_line(text: str) -> str:
    """정리본의 첫 본문 줄 (제목 줄·참석자 목록은 건너뛴다: ui.summary_line)."""
    return ui.summary_line(text, 200)


def _item_row(i) -> dict:
    return {
        "id": i.id, "task": i.task, "owner": i.owner, "due": i.due.isoformat() if i.due else None,
        "customer": i.customer.name if i.customer else "", "note": i.note, "carried_from": i.carried_from_id,
    }


def material(db: Session, r: Report) -> dict:
    """프롬프트에 넣는 자료: 기간, 요약 숫자, 영업기회·제안, 주요 활동(제목·정리본 첫 줄), 후속 조치, 지난 계획 점검, 지금 계획."""
    data = reports.data_of(db, r)
    acts = data.get("activities") or []
    shown = acts[-ACTIVITY_CAP:]
    first = {a.id: _first_line(a.summary_md) for a in db.scalars(select(Activity).where(Activity.id.in_([a["id"] for a in shown])))} if shown else {}
    nxt = report_periods.next_period(r.unit, r.period_start, r.period_end)
    return {
        "보고": {"단위": ontology.REPORT_UNITS.get(r.unit, r.unit), "기간": report_periods.label(r.unit, r.period_start, r.period_end),
               "다음 기간": f"{nxt[0].isoformat()} ~ {nxt[1].isoformat()}"},
        "요약 숫자": data.get("kpis") or {},
        "영업기회 단계 변화": [
            {"이름": o.get("name"), "고객": o.get("customer"), "전": ontology.OPPORTUNITY_STAGES.get(o.get("from_stage"), o.get("from_stage") or ""),
             "후": ontology.OPPORTUNITY_STAGES.get(o.get("to_stage"), o.get("to_stage") or ""), "새로 생김": bool(o.get("new"))}
            for o in data.get("opportunities") or []
        ],
        "제안 진행": [{"제목": p.get("title"), "고객": p.get("customer"), "단계": ontology.PROPOSAL_STATUSES.get(p.get("status"), p.get("status"))}
                   for p in data.get("proposals") or []],
        "주요 활동": [
            {"날짜": str(a.get("date"))[:10], "종류": ontology.ACTIVITY_KINDS.get(a.get("kind"), a.get("kind")), "제목": a.get("title"),
             "고객": a.get("customers") or [], "정리본 첫 줄": first.get(a["id"], "")}
            for a in shown
        ],
        "주요 활동 생략": max(0, len(acts) - len(shown)),
        "완료한 후속 조치": [{"할 일": f.get("task"), "담당": f.get("owner")} for f in (data.get("followups_done") or [])[:FOLLOWUP_CAP]],
        "기한 지난 후속 조치": [{"할 일": f.get("task"), "담당": f.get("owner"), "기한": str(f.get("due") or "")[:10]}
                         for f in (data.get("followups_overdue") or [])[:FOLLOWUP_CAP]],
        "지난 계획 점검": [
            {"id": i.id, "할 일": i.task, "담당": i.owner, "기한": i.due.isoformat() if i.due else None,
             "결과": ontology.PLAN_RESULTS.get(i.result, "아직 점검 안 함"), "메모": i.result_note,
             "다음 계획으로 넘김": i.result in ontology.PLAN_CARRY_RESULTS}
            for i in reports.items_to_check(db, r)
        ],
        "지금 계획 항목": [_item_row(i) for i in r.plan_items],
        "계획 방향": r.direction or "",
        "실적 요약 글": r.summary_md or "",
        "이슈·건의": r.highlights_md or "",
        "사업 방향·과제": _workspace_material(data),
    }


def _workspace_material(data: dict) -> dict:
    """AI 코워크 ①: 합의된 사업 방향, 열린 이슈, 이번 기간 움직인 과제 (보고서 구역과 같은 행)."""
    rows = reports.workspace_rows(data)
    return {
        "사업 방향": [{"방향": a, "상태": b, "과제": c, "비고": d} for a, b, c, d in rows["strategies"]],
        "열린 이슈": [{"이슈": a, "상태": b, "담당": c} for a, b, c in rows["issues"][:FOLLOWUP_CAP]],
        "과제 진행": [{"과제": a, "종류": b, "상태 변화": c, "진행 메모": d} for a, b, c, d in rows["initiatives"][:FOLLOWUP_CAP]],
        "새 이슈 수": rows["issues_new"], "닫은 이슈 수": rows["issues_closed"],
    }


def _material_text(db: Session, r: Report, **extra) -> str:
    return _safe(json.dumps({**material(db, r), **extra}, ensure_ascii=False, indent=1, default=str))


def _current(db: Session, input: dict) -> Report | None:
    return db.get(Report, input.get("report_id"))


def _finish(db: Session, rid: int, job_id) -> Report | str:
    """긴 실행 뒤 다시 읽는다: 지워졌으면 "deleted", 이 보고의 지금 작업이 아니면 "superseded" (결과를 쓰지 않는다)."""
    r = db.get(Report, rid)
    if r is None:
        return "deleted"
    if r.active_job_id != job_id:
        return "superseded"
    return r


# ---- 실적 요약 글 ----

def _summary(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    r = _current(db, input)
    if r is None:
        return "deleted"
    prompt = load_prompt("report_summary", material=_material_text(db, r))
    rid, job_id, before = r.id, input.get("_job_id"), r.summary_md or ""
    db.rollback()  # 긴 실행 동안 읽기 트랜잭션을 잡고 있지 않는다
    text = runner.run(prompt, allowed_tools=NO_TOOLS, timeout_s=settings.ai_timeout_s).strip()
    lines = [line.rstrip() for line in text.splitlines() if not line.strip().startswith("```")]
    text = "\n".join(lines).strip()
    if not text:
        raise ClaudeError("bad_output", "AI 요약 글이 비어 있습니다.")
    text = "\n".join(text.splitlines()[:MAX_SUMMARY_LINES])
    r = _finish(db, rid, job_id)
    if isinstance(r, str):
        return r
    r.active_job_id = None
    if (r.summary_md or "") != before:  # AI 가 쓰는 동안 사람이 요약 글을 저장했다: 사람의 글을 두고 AI 결과는 버린다
        db.commit()
        return ""
    r.summary_md = text
    db.commit()
    return text


HANDLERS["report_summary"] = _summary


# ---- 계획 초안 ----

class DraftItem(BaseModel):
    task: str = ""
    owner: str = ""
    due: Any = None
    customer: str = ""
    note: str = ""
    carried_from: Any = None


class PlanDraft(BaseModel):
    direction: str = ""
    items: list[DraftItem] = []


def _due(value) -> str | None:
    if not isinstance(value, str) or len(value.strip()) != 10:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def clean_draft(db: Session, r: Report, out: PlanDraft, source: str) -> dict:
    """AI 초안을 저장할 모양으로: 할 일이 빈 줄 버림, 틀린 기한은 비움, `carried_from` 은 이 보고로 넘어오는 지난 항목 번호일 때만, 30줄까지."""
    allowed = {i.id for i in reports.items_to_check(db, r) if i.result in ontology.PLAN_CARRY_RESULTS}
    items, used = [], set()
    for d in out.items:
        task = " ".join(str(d.task or "").split())
        if not task:
            continue
        cf = d.carried_from if isinstance(d.carried_from, int) and not isinstance(d.carried_from, bool) else None
        if cf not in allowed or cf in used:
            cf = None
        if cf is not None:
            used.add(cf)
        items.append({"task": task[:300], "owner": str(d.owner or "").strip()[:50], "due": _due(d.due),
                      "customer": str(d.customer or "").strip()[:100], "note": str(d.note or "").strip()[:300], "carried_from": cf})
        if len(items) >= DRAFT_ITEM_CAP:
            break
    return {"direction": " ".join(str(out.direction or "").split())[:500], "items": items, "source": source}


def _plan(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    r = _current(db, input)
    if r is None:
        return "deleted"
    prompt = load_prompt("report_plan", material=_material_text(db, r))
    rid, job_id = r.id, input.get("_job_id")
    out = run_json(db, runner, prompt, PlanDraft, settings, allowed_tools=NO_TOOLS)  # 읽기 트랜잭션은 여기서 끝난다
    r = _finish(db, rid, job_id)
    if isinstance(r, str):
        return r
    draft = clean_draft(db, r, out, "plan")
    if not draft["items"]:
        raise ClaudeError("bad_output", "AI 가 계획 항목을 만들지 못했습니다.")
    r.plan_draft = draft
    r.active_job_id = None
    db.commit()
    return "ok"


HANDLERS["report_plan"] = _plan


# ---- 계획 대화 ----

def split_answer(text: str) -> tuple[str, str | None]:
    """대화 답을 (보일 글, 고친 계획 JSON 글) 로 나눈다. 계획 표지가 없으면 JSON 은 None."""
    head, sep, tail = text.partition(PLAN_OPEN)
    if not sep:
        return text.strip(), None
    return head.strip(), tail.split(PLAN_CLOSE, 1)[0]


def _recent(db: Session, r: Report) -> list[ReportMessage]:
    stmt = select(ReportMessage).where(ReportMessage.report_id == r.id)
    if r.chat_from_id is not None:
        stmt = stmt.where(ReportMessage.id > r.chat_from_id)
    return list(db.scalars(stmt.order_by(ReportMessage.id.desc()).limit(CHAT_HISTORY_LIMIT)))[::-1]


def _chat(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    r = _current(db, input)
    if r is None:
        return "deleted"
    conversation = "\n\n".join(f"[{ROLE_LABELS.get(m.role, m.role)}]\n{_safe(m.content or '')}" for m in _recent(db, r))
    extra = {"AI 가 마지막으로 낸 계획 초안": r.plan_draft} if r.plan_draft else {}
    prompt = load_prompt("report_chat", material=_material_text(db, r, **extra), conversation=conversation or "(없음)")
    rid, job_id, bind = r.id, input.get("_job_id"), db.get_bind()
    db.rollback()
    text = _chat_answer(runner, prompt, bind, job_id, settings.ai_timeout_s, allowed_tools=NO_TOOLS).strip()
    answer, plan_json = split_answer(text)
    if not answer:
        raise ClaudeError("bad_output", "AI 답변이 비어 있습니다.")
    r = _finish(db, rid, job_id)
    if isinstance(r, str):
        return r
    if plan_json:
        try:
            draft = clean_draft(db, r, parse_json_output(plan_json, PlanDraft), "chat")
        except ClaudeError:  # 계획 JSON 이 깨져도 답 글은 남긴다 (초안만 바뀌지 않는다)
            draft = None
        if draft and draft["items"]:
            r.plan_draft = draft
    db.add(ReportMessage(report_id=rid, role="assistant", content=answer))
    r.active_job_id = None
    db.commit()
    return answer


HANDLERS["report_chat"] = _chat


# ---- 구역별 보고 문구 ----

class NotesOut(BaseModel):
    kpi: Any = None
    opps: Any = None
    proposals: Any = None
    check: Any = None
    plan: Any = None
    issues: Any = None


def _notes(db: Session, input: dict, runner: ClaudeRunner, settings: Settings) -> str:
    """구역마다 1~2문장. 입력의 `replace`({구역: 기대한 지금 문장})가 있으면 그 구역이 아직 그 문장일 때만 바꾼다
    (확정 직후 자동 실행: 기본 문장으로 채운 구역만, 그 사이 사람이 고친 문구는 덮지 않는다). 없으면 모든 구역을 바꾼다 ("다시 쓰기")."""
    r = _current(db, input)
    if r is None:
        return "deleted"
    data = reports.data_of(db, r)
    defaults = reports.default_notes(data, reports.items_to_check(db, r), list(r.plan_items))
    prompt = load_prompt("report_notes", material=_material_text(db, r, **{"기본 문장": defaults}))
    rid, job_id = r.id, input.get("_job_id")
    out = run_json(db, runner, prompt, NotesOut, settings, allowed_tools=NO_TOOLS)
    r = _finish(db, rid, job_id)
    if isinstance(r, str):
        return r
    replace = input.get("replace")
    notes = reports.notes_of(r)
    changed = 0
    for key in reports.NOTE_KEYS:
        lines = reports.note_lines(getattr(out, key))[:NOTE_SENTENCES]
        if not lines:
            continue
        if isinstance(replace, dict) and (key not in replace or notes.get(key, []) != replace[key]):
            continue
        notes[key] = lines
        changed += 1
    r.section_notes = notes
    r.active_job_id = None
    db.commit()
    return f"{changed}"


HANDLERS["report_notes"] = _notes

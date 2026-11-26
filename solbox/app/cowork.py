"""AI 동료 칸 (AI 코워크 ②) 의 규칙 한 곳: 대상 해석, AI 가 읽는 맥락, 작업 시작, 제안 정리·반영, 대상 삭제 때 대화 지우기.

대화는 팀 전체 공개다. AI 결과는 초안이고 제안은 사람이 [반영]해야 기록이 바뀐다."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import delete, event, select, update
from sqlalchemy.orm import Session

from app import ontology, workspaces
from app.models import AIJob, AIMessage, AIThread, Initiative, Opportunity, Partner, Solution, Strategy, User

MAX_MESSAGE = 4000
TITLE_MAX = 40
HISTORY_LIMIT = 20  # 프롬프트에 넣는 최근 메시지 수
FILE_CAP = 12000  # 자료 한 개에서 넣는 글자 수
FILES_CAP = 36000  # 자료 합계
EMPTY_MESSAGE = "메시지를 입력하세요."
TOO_LONG = f"메시지는 {MAX_MESSAGE:,}자까지 보낼 수 있습니다."
BUSY = "AI 가 이 대화에 답하는 중입니다. 답이 끝나면 이어서 물어보세요."
NOT_FOUND_EVIDENCE = "원문에서 근거를 찾지 못했어요"
FROM_CHAT_EVIDENCE = "대화에서 나온 말"

# 대상마다 받을 수 있는 제안 종류 (사업 방향은 제안 없음: 합의 내용은 사람만 고친다. 솔루션은 메모 칸이 없어 제안 없음)
SUGGESTION_KINDS: dict[str, tuple[str, ...]] = {
    "initiative": ("status", "due", "progress"),
    "opportunity": ("next_step",),
    "partner": ("memo",),
    "strategy": (),
    "solution": (),
}
SUGGESTION_LABELS = {"status": "상태", "due": "기한", "progress": "진행 메모 한 줄", "next_step": "다음 할 일", "memo": "메모 한 줄"}
MODELS = {"strategy": Strategy, "initiative": Initiative, "solution": Solution, "partner": Partner, "opportunity": Opportunity}


def get_target(db: Session, code: str, target_id: int):
    model = MODELS.get(code)
    return db.get(model, target_id) if model is not None else None


def target_url(code: str, target_id: int) -> str:
    return ontology.TYPES[code].url(target_id)


def target_name(obj) -> str:
    return getattr(obj, "title", None) or getattr(obj, "name", "") or ""


def threads_for(db: Session, code: str, target_id: int) -> list[AIThread]:
    return list(db.scalars(select(AIThread).where(AIThread.target_type == code, AIThread.target_id == target_id)
                           .order_by(AIThread.updated_at.desc(), AIThread.id.desc())))


def thread_title(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_MAX else line[:TITLE_MAX - 1] + "…"


def is_busy(thread: AIThread) -> bool:
    return thread.active_job is not None and thread.active_job.status in ontology.JOB_ACTIVE


def panel(db: Session, code: str, target, chosen: str | None = None) -> dict:
    """상세 화면에 붙는 대화 칸의 값: 대화 목록과 지금 연 대화 (`?thread=` 로 고르고, 없으면 가장 최근 대화)."""
    threads = threads_for(db, code, target.id)
    current = None
    if chosen == "new":
        current = None
    elif chosen and chosen.isdigit():
        current = next((t for t in threads if t.id == int(chosen)), None)
    elif threads:
        current = threads[0]
    return {"code": code, "target": target, "threads": threads, "thread": current, "new": chosen == "new" or not threads,
            "starters": ontology.COWORK_CHAT_STARTERS.get(code, ())}


def move_choices(db: Session) -> list[Initiative]:
    """대화를 옮길 수 있는 과제: 열린 과제, 최근 수정순."""
    return list(db.scalars(select(Initiative).where(Initiative.status.in_(ontology.INITIATIVE_OPEN))
                           .order_by(Initiative.updated_at.desc(), Initiative.id.desc())))


# ---- 작업 ----

def start_job(db: Session, thread: AIThread, user: User | None, rows: list | None = None) -> AIJob | None:
    """대화 작업을 넣는다 (메시지와 한 트랜잭션). 이미 이 대화에 작업이 돌고 있으면 None."""
    db.refresh(thread)
    if is_busy(thread):
        return None
    tid, previous = thread.id, thread.active_job_id
    job = AIJob(kind="cowork_chat", input={"thread_id": tid}, requester_id=getattr(user, "id", None), priority=0, status=ontology.JOB_PENDING)
    db.add(job)
    db.flush()
    same = AIThread.active_job_id.is_(None) if previous is None else AIThread.active_job_id == previous
    claimed = db.execute(update(AIThread).where(AIThread.id == tid, same).values(active_job_id=job.id, updated_at=datetime.now()))
    if claimed.rowcount != 1:
        db.rollback()
        return None
    for row in rows or []:
        db.add(row)
    db.commit()
    db.refresh(thread)
    return job


# ---- AI 가 읽는 맥락 ----

@dataclass
class Context:
    instructions: str  # 사용자 지시 (공간·속한 방향의 지침)
    strategies: str  # 합의된 사업 방향 (자료)
    record: str  # 대상의 지금 상태 (지식 사본 글, 자료)
    files: str  # 공간 자료의 뽑은 글 (자료)
    source: str  # 근거 확인용 원문 (기록 + 자료)


def _space_files(obj) -> list:
    if isinstance(obj, Strategy):
        return list(obj.files)
    if isinstance(obj, Initiative):
        return [*obj.files, *(obj.strategy.files if obj.strategy else [])]
    return []


def build_context(db: Session, mirror, obj, today: date | None = None) -> Context:
    today = today or date.today()
    instructions = []
    if isinstance(obj, (Strategy, Initiative)) and (obj.instructions or "").strip():
        instructions.append(f"- {ontology.label(ontology.type_of(obj))} 지침: {obj.instructions.strip()}")
    if isinstance(obj, Initiative) and obj.strategy and (obj.strategy.instructions or "").strip():
        instructions.append(f"- 속한 사업 방향의 지침: {obj.strategy.instructions.strip()}")
    active = workspaces.active_strategies(db, today)
    strategies = "\n\n".join(f"### {s.title} (사본 {ontology.rel_path('strategy', id=s.id)})\n{(s.consensus or '').strip() or '(합의 내용 없음)'}" for s in active)
    record = mirror.render_text(obj)
    parts, used = [], 0
    for f in _space_files(obj):
        path = mirror.path_for(f)
        if f.extract_status != "ok" or not path.is_file():
            parts.append(f"### {f.original_name}\n(글을 뽑지 못한 자료라 읽지 못했습니다)")
            continue
        text = path.read_text(encoding="utf-8")
        room = min(FILE_CAP, FILES_CAP - used)
        if room <= 0:
            parts.append(f"### {f.original_name}\n(자료가 많아 이 파일은 넣지 못했습니다)")
            continue
        cut = text[:room]
        used += len(cut)
        parts.append(f"### {f.original_name}\n{cut}" + ("\n(…길어서 뒷부분은 잘렸습니다)" if len(text) > room else ""))
    files = "\n\n".join(parts)
    return Context("\n".join(instructions) or "(없음)", strategies or "(합의된 사업 방향이 없습니다)", record, files or "(올린 자료 없음)",
                   record + "\n" + "\n".join(parts))


# ---- 제안 ----

def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _day(value) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


MIN_EVIDENCE = 4  # 이보다 짧은 근거는 원문에서 찾았다고 보지 않는다 (공백 뺀 글자 수)


def clean_suggestions(raw, code: str, source: str, chat: str = "", basis_rev: str = "", current=None) -> list[dict]:
    """AI 가 낸 제안 목록을 대상에 맞게 거른다: 받을 수 없는 종류·빈 값·틀린 값·지금 값과 같은 것은 버린다.
    근거를 기록 사본·자료 글에서 찾았으면 `verified`, 대화에서 사용자가 한 말에서만 찾았으면 `from_chat` (원문 근거로 치지 않는다).
    `basis_rev` 는 AI 가 읽은 때의 기록 고침 표시: 그 뒤 기록이 바뀌었으면 반영하지 않는다."""
    allowed = SUGGESTION_KINDS.get(code, ())
    out = []
    src, said = _norm(source), _norm(chat)
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        value = " ".join(str(item.get("value") or "").split())[:300]
        if kind not in allowed or not value:
            continue
        due = _day(item.get("due"))
        if kind == "status" and value not in ontology.INITIATIVE_STATUSES:
            continue
        if kind == "due":
            value = _day(value) or ""
            if not value:
                continue
        if current is not None and _same_as_now(current, kind, value, due):
            continue
        evidence = " ".join(str(item.get("evidence") or "").split())[:300]
        ev = _norm(evidence)
        long_enough = len(ev) >= MIN_EVIDENCE
        verified = long_enough and ev in src
        out.append({"id": len(out) + 1, "kind": kind, "value": value, "due": due if kind == "next_step" else None,
                    "evidence": evidence, "verified": verified, "from_chat": not verified and long_enough and ev in said,
                    "basis_rev": basis_rev, "state": "pending", "decided_by": None, "decided_at": None})
    return out[:5]


def _same_as_now(target, kind: str, value: str, due: str | None) -> bool:
    if kind == "status":
        return getattr(target, "status", None) == value
    if kind == "due":
        return bool(getattr(target, "due", None)) and target.due.isoformat() == value
    if kind == "next_step":
        now_due = target.next_step_due.isoformat() if getattr(target, "next_step_due", None) else None
        return (target.next_step or "") == value and now_due == due
    if kind == "memo":
        return value in (getattr(target, "memo", "") or "")
    return False


def supersede(db: Session, thread: AIThread) -> None:
    """새 답이 오면 이 대화의 반영 대기 제안을 닫는다."""
    for m in thread.messages:
        if any(s.get("state") == "pending" for s in m.suggestions or []):
            m.suggestions = [{**s, "state": "superseded"} if s.get("state") == "pending" else s for s in m.suggestions]


def suggestion_text(s: dict) -> str:
    v = s.get("value", "")
    if s.get("kind") == "status":
        v = ontology.INITIATIVE_STATUSES.get(v, v)
    if s.get("kind") == "next_step" and s.get("due"):
        v = f"{v} ({s['due']})"
    return f"{SUGGESTION_LABELS.get(s.get('kind'), s.get('kind'))}: {v}"


class SuggestionError(Exception):
    pass


def apply(db: Session, target, s: dict) -> None:
    """제안 하나를 대상에 반영한다 (커밋하지 않는다). 칸 이력은 guards 가 남긴다."""
    kind, value = s["kind"], s["value"]
    if isinstance(target, Initiative):
        if kind == "status":
            target.status = value
        elif kind == "due":
            target.due = date.fromisoformat(value)
        elif kind == "progress":
            target.progress = f"{date.today().isoformat()} {value}\n{target.progress or ''}".strip()
        else:
            raise SuggestionError(kind)
    elif isinstance(target, Opportunity) and kind == "next_step":
        target.next_step = value
        target.next_step_due = date.fromisoformat(s["due"]) if s.get("due") else None
    elif isinstance(target, Partner) and kind == "memo":
        target.memo = (f"{target.memo}\n{value}" if (target.memo or "").strip() else value)
    else:
        raise SuggestionError(kind)


def decide(message: AIMessage, sid: int, state: str, user: User) -> dict | None:
    """제안 하나의 상태를 바꾼 새 목록을 대입한다 (JSON 은 새 list). 반영 대기가 아니면 None."""
    found = None
    rows = []
    for s in message.suggestions or []:
        if s.get("id") == sid and s.get("state") == "pending":
            found = {**s, "state": state, "decided_by": user.id, "decided_at": datetime.now().isoformat(timespec="seconds")}
            rows.append(found)
        else:
            rows.append(s)
    if found is not None:
        message.suggestions = rows
    return found


# ---- 대상을 지우면 대화도 지운다 (한 곳: 모든 삭제 라우터가 ORM delete 를 쓴다) ----

@event.listens_for(Session, "before_flush")
def _drop_threads_of_deleted(session: Session, _ctx, _instances) -> None:
    gone = [(ontology.type_of(o), o.id) for o in session.deleted if isinstance(o, tuple(MODELS.values())) and o.id is not None]
    for code, oid in gone:
        session.execute(delete(AIThread).where(AIThread.target_type == code, AIThread.target_id == oid))

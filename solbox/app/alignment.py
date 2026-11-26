"""사업 방향 정렬 점검 (AI 코워크 ③) 규칙 한 곳: 대상 해석, 자동·수동 작업 넣기, 결과 저장, 화면·목록·보고서가 읽는 최신 판단.

AI 는 판단만 하고 기록을 바꾸지 않는다. 잣대는 합의 상태이고 기간 안인 사업 방향(`workspaces.active_strategies`)뿐이다."""
from __future__ import annotations

import hashlib
import re
from datetime import date

from sqlalchemy import delete, event, select, update
from sqlalchemy.orm import Session

from app import ontology, workspaces
from app.models import AIJob, AlignmentNote, Initiative, Opportunity, Solution, Strategy

KIND = "alignment_check"
MODELS = {"initiative": Initiative, "opportunity": Opportunity, "solution": Solution}
MAX_TEXT = 400
MIN_EVIDENCE = 4


def is_target(obj) -> bool:
    """점검하는 기록: 과제(모두), 진행 중 영업기회, 보류가 아닌 솔루션."""
    if isinstance(obj, Opportunity):
        return obj.stage in ontology.OPPORTUNITY_OPEN_STAGES
    if isinstance(obj, Solution):
        return obj.stage != ontology.STAGE_HOLD
    return isinstance(obj, Initiative)


def basis_of(strategies: list[Strategy]) -> str:
    """읽은 방향들의 뜻 없는 해시 (번호·합의 내용). 방향이 바뀌면 달라진다."""
    text = "\n".join(f"{s.id}:{(s.consensus or '').strip()}" for s in sorted(strategies, key=lambda s: s.id))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def current_basis(db: Session, today: date | None = None) -> str:
    return basis_of(workspaces.active_strategies(db, today or date.today()))


def pending_job(db: Session, code: str, target_id: int, statuses=ontology.JOB_ACTIVE) -> AIJob | None:
    for job in db.scalars(select(AIJob).where(AIJob.kind == KIND, AIJob.status.in_(statuses)).order_by(AIJob.id.desc())):
        if (job.input or {}).get("target_type") == code and (job.input or {}).get("target_id") == target_id:
            return job
    return None


def request(db: Session, obj, user_id: int | None, http_request, auto: bool = True) -> AIJob | None:
    """점검 작업 하나를 넣는다. 자동(저장할 때)은 합의된 방향이 있을 때만, 가장 낮은 우선순위로, 같은 대상의 대기 작업이 있으면 넣지 않는다.
    AI 처리기가 꺼져 있으면 아무것도 하지 않는다 (저장은 그대로 성공)."""
    from app.ai import gate
    from app.ai.queue import enqueue

    if obj is None or not is_target(obj) or gate.ai_is_off(http_request):
        return None
    if not workspaces.active_strategies(db, date.today()):
        return None
    code = ontology.type_of(obj)
    if pending_job(db, code, obj.id, ("pending",)) is not None:  # 도는 점검은 새 저장을 못 읽으니 하나 더 기다리게 한다
        return None
    priority = ontology.ALIGNMENT_AUTO_PRIORITY if auto else 0
    return enqueue(db, KIND, {"target_type": code, "target_id": obj.id}, user_id, priority)


def latest(db: Session, code: str, target_id: int) -> AlignmentNote | None:
    return db.scalars(select(AlignmentNote).where(AlignmentNote.target_type == code, AlignmentNote.target_id == target_id)
                      .order_by(AlignmentNote.id.desc()).limit(1)).first()


def latest_for(db: Session, code: str, ids) -> dict[int, AlignmentNote]:
    """여러 대상의 최신 판단 (목록·보고서용, 질의 하나)."""
    ids = list(ids)
    if not ids:
        return {}
    out: dict[int, AlignmentNote] = {}
    for n in db.scalars(select(AlignmentNote).where(AlignmentNote.target_type == code, AlignmentNote.target_id.in_(ids)).order_by(AlignmentNote.id)):
        out[n.target_id] = n
    return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def save(db: Session, code: str, target_id: int, out: dict, record_text: str, strategy_ids: set[int], basis: str, job_id) -> AlignmentNote:
    """점검 결과 한 줄 (커밋하지 않는다). `basis` 는 AI 가 읽은 방향들의 해시 (실행 전에 잰다)."""
    verdict = out.get("verdict") if out.get("verdict") in ontology.ALIGNMENT_VERDICTS else "unclear"
    sid = out.get("strategy_id")
    sid = sid if isinstance(sid, int) and not isinstance(sid, bool) and sid in strategy_ids else None
    evidence = " ".join(str(out.get("evidence") or "").split())[:MAX_TEXT]
    ev = _norm(evidence)
    note = AlignmentNote(target_type=code, target_id=target_id, verdict=verdict, strategy_id=sid,
                         reason=" ".join(str(out.get("reason") or "").split())[:MAX_TEXT], evidence=evidence,
                         verified=len(ev) >= MIN_EVIDENCE and ev in _norm(record_text), basis=basis, job_id=job_id)
    db.add(note)
    db.flush()
    old = db.scalars(select(AlignmentNote.id).where(AlignmentNote.target_type == code, AlignmentNote.target_id == target_id)
                     .order_by(AlignmentNote.id.desc()).offset(ontology.ALIGNMENT_KEPT)).all()
    if old:
        db.execute(delete(AlignmentNote).where(AlignmentNote.id.in_(old)))
    return note


def view(db: Session, obj) -> dict:
    """상세 화면의 "방향과의 정렬" 카드 값."""
    code = ontology.type_of(obj)
    note = latest(db, code, obj.id)
    strategies = workspaces.active_strategies(db, date.today())
    strategy = db.get(Strategy, note.strategy_id) if note is not None and note.strategy_id else None
    return {"code": code, "note": note, "strategy": strategy, "has_strategies": bool(strategies), "applies": is_target(obj),
            "stale": note is not None and note.basis != basis_of(strategies), "job": pending_job(db, code, obj.id)}


def misaligned_ids(db: Session, code: str) -> set[int]:
    """최신 판단이 "방향 밖"인 대상 번호 (목록의 걸러 보기)."""
    latest_rows: dict[int, str] = {}
    for tid, verdict in db.execute(select(AlignmentNote.target_id, AlignmentNote.verdict).where(AlignmentNote.target_type == code).order_by(AlignmentNote.id)):
        latest_rows[tid] = verdict
    return {tid for tid, v in latest_rows.items() if v == "misaligned"}


# 대상을 지우면 판단도 지우고, 방향을 지우면 "가장 가까운 방향" 번호를 비운다 (cowork 처리기와 같은 자리)
@event.listens_for(Session, "before_flush")
def _drop_notes_of_deleted(session: Session, _ctx, _instances) -> None:
    for o in list(session.deleted):
        if isinstance(o, tuple(MODELS.values())) and o.id is not None:
            session.execute(delete(AlignmentNote).where(AlignmentNote.target_type == ontology.type_of(o), AlignmentNote.target_id == o.id))
        elif isinstance(o, Strategy) and o.id is not None:
            session.execute(update(AlignmentNote).where(AlignmentNote.strategy_id == o.id).values(strategy_id=None))

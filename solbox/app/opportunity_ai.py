"""영업 B 단계 규칙 한 곳: 단계별 확인 항목, AI 자동 분석 요청, 제안 다듬기·저장·반영.
라우터·템플릿·AI 처리기는 여기 함수만 부른다. 여기 함수는 (따로 적지 않으면) 커밋하지 않는다."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import ontology
from app.models import Opportunity, OpportunityCheck


# ---- 확인 항목 ----

def items_of(stage: str) -> tuple[tuple[str, str], ...]:
    return ontology.OPPORTUNITY_CHECKLIST.get(stage, ())


def checked_items(opp: Opportunity) -> set[str]:
    return {c.item for c in opp.checks}


def set_check(db: Session, opp: Opportunity, item: str, on: bool, user_id: int | None, source: str = "", evidence: str = "") -> bool:
    """확인 항목을 켜거나 끈다 (이력·고친 표시는 guards 가 남긴다). 바뀌었으면 True. 모르는 코드는 ValueError."""
    if item not in ontology.OPPORTUNITY_CHECK_ITEMS:
        raise ValueError(item)
    found = next((c for c in opp.checks if c.item == item), None)
    if on and found is None:
        opp.checks.append(OpportunityCheck(item=item, checked_by_id=user_id, source=source, evidence=evidence))
        return True
    if not on and found is not None:
        opp.checks.remove(found)
        db.delete(found)
        return True
    return False


def open_items(opp: Opportunity, stage: str) -> list[tuple[str, str]]:
    """그 단계에서 아직 확인하지 않은 항목 [(코드, 이름)]."""
    done = checked_items(opp)
    return [(c, n) for c, n in items_of(stage) if c not in done]


def stage_counts(opp: Opportunity) -> dict[str, tuple[int, int]]:
    """단계마다 (확인한 수, 전체 수)."""
    done = checked_items(opp)
    return {stage: (sum(1 for c, _ in items if c in done), len(items)) for stage, items in ontology.OPPORTUNITY_CHECKLIST.items()}


def forward_move(old: str, new: str) -> bool:
    """진행 중 흐름(발굴 → 제안 중 → 협상)에서 앞으로 넘기는가 (남은 확인 항목을 알릴 때)."""
    flow = ontology.OPPORTUNITY_OPEN_STAGES
    return old in flow and new in flow and flow.index(new) > flow.index(old)


def leftovers_notice(opp: Opportunity, old: str, new: str) -> str | None:
    """앞으로 넘겼고 떠난 단계에 남은 항목이 있으면 알림 코드 `opportunity_stage_open.<옛>.<새>.<개수>`, 아니면 None."""
    if not forward_move(old, new):
        return None
    n = len(open_items(opp, old))
    return f"opportunity_stage_open.{old}.{new}.{n}" if n else None


def check_view(opp: Opportunity) -> dict | None:
    """상세 화면 "확인 항목" 카드 값 (진행 중 단계만). 앞 단계의 남은 항목도 함께."""
    if opp.stage not in ontology.OPPORTUNITY_CHECKLIST:
        return None
    by_item = {c.item: c for c in opp.checks}
    rows = [{"code": c, "label": n, "row": by_item.get(c)} for c, n in items_of(opp.stage)]
    flow = ontology.OPPORTUNITY_OPEN_STAGES
    earlier = [{"stage": s, "name": ontology.OPPORTUNITY_STAGES[s], "items": open_items(opp, s)} for s in flow[:flow.index(opp.stage)]]
    return {"stage": opp.stage, "rows": rows, "done": sum(1 for r in rows if r["row"]), "total": len(rows),
            "earlier": [e for e in earlier if e["items"]]}


# ---- AI 자동 분석 요청 ----

REVIEW_KIND = "opportunity_review"
REVIEW_PRIORITY = 5  # 사람이 누른 작업(0)보다 뒤, 리서치(10)보다 앞


def has_activities(db: Session, opp: Opportunity) -> bool:
    return bool(opp.activities)


def pending_job(db: Session, opp_id: int):
    """이 영업기회의 대기·진행 중 분석 작업 (없으면 None)."""
    from sqlalchemy import select

    from app.models import AIJob

    for job in db.scalars(select(AIJob).where(AIJob.kind == REVIEW_KIND, AIJob.status.in_(("pending", "running"))).order_by(AIJob.id)):
        if (job.input or {}).get("opportunity_id") == opp_id:
            return job
    return None


def request_review(db: Session, opp_ids, user_id: int | None, request, priority: int = REVIEW_PRIORITY) -> list:
    """활동 기록 저장 뒤 부른다: 진행 중이고 연결된 활동 기록이 있는 영업기회마다 분석 작업을 하나씩 (이미 대기·진행 중이면 넣지 않는다).
    AI 처리기가 꺼져 있으면 아무것도 하지 않는다 (저장은 그대로 성공). 이 함수만 자동으로 AI 작업을 만든다 (docs/rules/AI-작업과-오류.md)."""
    from app.ai import gate
    from app.ai.queue import enqueue

    if gate.ai_is_off(request):
        return []
    made = []
    for oid in dict.fromkeys(opp_ids):
        opp = db.get(Opportunity, oid)
        if opp is None or opp.stage not in ontology.OPPORTUNITY_OPEN_STAGES or not has_activities(db, opp) or pending_job(db, oid) is not None:
            continue
        made.append(enqueue(db, REVIEW_KIND, {"opportunity_id": oid}, user_id, priority))
    return made

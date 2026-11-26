"""AI 제안함 (UX 개선 2026-11-26): 시스템 전체에서 반영을 기다리는 AI 제안을 한곳에 모은다.

두 출처: 영업기회 분석(영업 B, 진행 중 영업기회의 최신 분석)과 AI 동료 대화(AI 코워크 ②, 답마다의 제안). 반영·무시는 각 출처의 POST 를 그대로 쓴다."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import cowork, ontology
from app.models import AIMessage, AIThread, Opportunity, OpportunityInsight


def _opportunity_rows(db: Session) -> list[dict]:
    open_ids = select(Opportunity.id).where(Opportunity.stage.in_(ontology.OPPORTUNITY_OPEN_STAGES))
    newest = select(func.max(OpportunityInsight.id)).where(OpportunityInsight.opportunity_id.in_(open_ids)).group_by(OpportunityInsight.opportunity_id)
    rows = []
    insights = db.scalars(select(OpportunityInsight).where(OpportunityInsight.id.in_(newest)).order_by(OpportunityInsight.id.desc())).all()
    opps = {o.id: o for o in db.scalars(select(Opportunity).where(Opportunity.id.in_([i.opportunity_id for i in insights])))} if insights else {}
    for ins in insights:
        o = opps.get(ins.opportunity_id)
        if o is None:
            continue
        for s in ins.suggestions or []:
            if s.get("state") != "pending":
                continue
            rows.append({"source": "opportunity", "target_type": "opportunity", "target": o, "title": o.name, "url": f"/opportunities/{o.id}",
                         "text": _opp_text(s), "evidence": s.get("evidence", ""), "verified": bool(s.get("verified")), "when": ins.created_at,
                         "apply": f"/opportunities/{o.id}/ai/{ins.id}/{s['id']}/apply", "dismiss": f"/opportunities/{o.id}/ai/{ins.id}/{s['id']}/dismiss"})
    return rows


def _opp_text(s: dict) -> str:
    kind, field = s.get("kind"), s.get("field")
    if kind == "stage":
        return f"단계: {ontology.OPPORTUNITY_STAGES.get(s.get('value'), s.get('value'))}"
    if kind == "check":
        return f"확인 항목: {ontology.check_label(s.get('item') or '')}"
    label = ontology.prop("opportunity", field) if field else "값"
    value = s.get("value")
    if field == "amount":
        from app import money

        value = money.format_amount(value)
    if field == "next_step" and s.get("due"):
        value = f"{value} ({s['due']})"
    return f"{label}: {value}"


def _cowork_rows(db: Session) -> list[dict]:
    msgs = db.scalars(select(AIMessage).where(AIMessage.role == "assistant", func.json_array_length(AIMessage.suggestions) > 0)
                      .order_by(AIMessage.id.desc())).all()
    rows = []
    threads = {t.id: t for t in db.scalars(select(AIThread).where(AIThread.id.in_({m.thread_id for m in msgs})))} if msgs else {}
    for m in msgs:
        t = threads.get(m.thread_id)
        pending = [s for s in m.suggestions or [] if s.get("state") == "pending"]
        if t is None or not pending:
            continue
        target = cowork.get_target(db, t.target_type, t.target_id)
        if target is None:
            continue
        for s in pending:
            rows.append({"source": "cowork", "target_type": t.target_type, "target": target, "title": cowork.target_name(target),
                         "url": f"{cowork.target_url(t.target_type, target.id)}?thread={t.id}#cowork", "thread": t.title,
                         "text": cowork.suggestion_text(s), "evidence": s.get("evidence", ""), "verified": bool(s.get("verified")), "when": m.created_at,
                         "apply": f"/cowork/threads/{t.id}/messages/{m.id}/suggestions/{s['id']}/apply",
                         "dismiss": f"/cowork/threads/{t.id}/messages/{m.id}/suggestions/{s['id']}/dismiss"})
    return rows


def pending(db: Session) -> list[dict]:
    """반영 대기 제안 전부, 최근 것이 위."""
    rows = _opportunity_rows(db) + _cowork_rows(db)
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows


def pending_count(db: Session) -> int:
    """반영 대기 제안 수 (홈 "내 할 일"). 질의 수는 기록 수와 무관하다 (대상 기록을 하나씩 읽지 않는다: 대상을 지우면 대화도 지워진다)."""
    from app import opportunity_ai

    open_ids = list(db.scalars(select(Opportunity.id).where(Opportunity.stage.in_(ontology.OPPORTUNITY_OPEN_STAGES))))
    n = sum(opportunity_ai.pending_counts(db, open_ids).values())
    for sugs in db.scalars(select(AIMessage.suggestions).where(AIMessage.role == "assistant", func.json_array_length(AIMessage.suggestions) > 0)):
        n += sum(1 for s in sugs or [] if s.get("state") == "pending")
    return n

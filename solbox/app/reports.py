"""팀 업무 보고의 실적 집계. 보고서의 모든 숫자는 여기서 센다 (순수 함수: 기간만 받고 오늘 날짜를 보지 않는다).
질의 수는 기록 수와 무관하다 (목록은 selectinload / joinedload 로 한꺼번에 읽는다)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app import ontology
from app.models import (
    Activity, Customer, CustomerNeed, FollowUp, Opportunity, OpportunityStageChange, Partner, Proposal, Report, ReportPlanItem, Solution,
)


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """[시작 0시, 끝 다음 날 0시) — 끝 날짜의 하루 전체를 포함한다."""
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def _in(col, lo: datetime, hi: datetime):
    return and_(col >= lo, col < hi)


def _activity_in(start: date, end: date):
    """활동 기록의 날짜 기준: `date`, 없으면 만든 날."""
    lo, hi = _bounds(start, end)
    return or_(and_(Activity.date.is_not(None), Activity.date >= start, Activity.date <= end), and_(Activity.date.is_(None), _in(Activity.created_at, lo, hi)))


def _count(db: Session, model, cond) -> int:
    return db.scalar(select(func.count()).select_from(model).where(cond)) or 0


def _followup_row(f: FollowUp) -> dict:
    return {
        "id": f.id, "task": f.task, "owner": f.owner, "due": f.due, "done_at": f.done_at,
        "activity_id": f.activity_id, "activity_title": f.activity.title if f.activity else "",
    }


def aggregate(db: Session, unit: str, start: date, end: date) -> dict:
    """[start, end] 기간의 실적. 날짜 기준: 활동은 `Activity.date`(없으면 만든 날), 니즈·영업기회·제안·솔루션·고객·파트너는 `created_at`,
    수주는 단계 변경 기록, 완료는 `done_at`, 기한 지남은 기간 마지막 날 기준 (다시 열어도 같은 값)."""
    lo, hi = _bounds(start, end)
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    # 활동
    acts = db.scalars(
        select(Activity).where(_activity_in(start, end)).options(selectinload(Activity.customers)).order_by(Activity.date, Activity.created_at, Activity.id)
    ).all()
    activities, by_kind = [], {}
    for a in acts:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        activities.append({
            "id": a.id, "date": a.date or a.created_at.date(), "kind": a.kind, "title": a.title,
            "customers": [c.name for c in a.customers],
        })
    activities.sort(key=lambda x: (x["date"], x["id"]))
    activities_prev = _count(db, Activity, _activity_in(prev_start, prev_end))

    # 영업기회: 기간 중 단계가 바뀐 것 + 새로 생긴 것
    changed_ids = select(OpportunityStageChange.opportunity_id).where(_in(OpportunityStageChange.changed_at, lo, hi))
    opps = db.scalars(
        select(Opportunity)
        .where(or_(Opportunity.id.in_(changed_ids), _in(Opportunity.created_at, lo, hi)))
        .options(joinedload(Opportunity.customer), selectinload(Opportunity.stage_changes))
        .order_by(Opportunity.id)
    ).all()
    opportunities = []
    for o in opps:
        mine = [c for c in o.stage_changes if lo <= c.changed_at < hi]  # 시각순 (관계 정렬)
        new = lo <= o.created_at < hi
        opportunities.append({
            "id": o.id, "name": o.name, "customer": o.customer.name if o.customer else "",
            "from_stage": mine[0].from_stage if mine else "", "to_stage": mine[-1].to_stage if mine else o.stage, "new": new,
        })
    won = _count(db, OpportunityStageChange, and_(OpportunityStageChange.to_stage == ontology.OPPORTUNITY_STAGE_WON, _in(OpportunityStageChange.changed_at, lo, hi)))

    # 제안: 기간 중 만들거나 진행한(고친) 것, 제안서를 만든 것
    prop_in = or_(_in(Proposal.created_at, lo, hi), _in(Proposal.updated_at, lo, hi), _in(Proposal.deck_built_at, lo, hi))
    props = db.scalars(select(Proposal).where(prop_in).options(joinedload(Proposal.customer)).order_by(Proposal.id)).all()
    proposals = [
        {"id": p.id, "title": p.title, "customer": p.customer.name if p.customer else p.group_name, "status": p.status} for p in props
    ]
    decks_built = _count(db, Proposal, _in(Proposal.deck_built_at, lo, hi))

    # 후속 조치
    done_rows = db.scalars(
        select(FollowUp).where(FollowUp.done.is_(True), _in(FollowUp.done_at, lo, hi)).options(joinedload(FollowUp.activity)).order_by(FollowUp.done_at, FollowUp.id)
    ).all()
    overdue_rows = db.scalars(
        select(FollowUp)
        .where(FollowUp.due.is_not(None), FollowUp.due <= end, or_(FollowUp.done.is_(False), FollowUp.done_at >= hi))
        .options(joinedload(FollowUp.activity)).order_by(FollowUp.due, FollowUp.id)
    ).all()

    # 날짜를 몰라 빠진 기록 (기능 도입 전): done 인데 done_at 이 없는 후속 조치, 단계 변경 이력이 없는 영업기회 — 기간 안에 고쳐진 것만
    legacy_followups = _count(db, FollowUp, and_(FollowUp.done.is_(True), FollowUp.done_at.is_(None), _in(FollowUp.updated_at, lo, hi)))
    legacy_opps = _count(
        db, Opportunity,
        and_(_in(Opportunity.updated_at, lo, hi), ~exists().where(OpportunityStageChange.opportunity_id == Opportunity.id)),
    )

    kpis = {
        "activities": len(activities),
        "activities_by_kind": by_kind,
        "activities_prev": activities_prev,
        "needs": _count(db, CustomerNeed, _in(CustomerNeed.created_at, lo, hi)),
        "opportunities_new": _count(db, Opportunity, _in(Opportunity.created_at, lo, hi)),
        "won": won,
        "proposals_active": len(proposals),
        "decks_built": decks_built,
        "solutions_new": _count(db, Solution, _in(Solution.created_at, lo, hi)),
        "customers_new": _count(db, Customer, _in(Customer.created_at, lo, hi)),
        "partners_new": _count(db, Partner, _in(Partner.created_at, lo, hi)),
        "followups_done": len(done_rows),
        "followups_overdue": len(overdue_rows),
    }
    return {
        "kpis": kpis,
        "activities": activities,
        "opportunities": opportunities,
        "proposals": proposals,
        "followups_done": [_followup_row(f) for f in done_rows],
        "followups_overdue": [_followup_row(f) for f in overdue_rows],
        "legacy_undated": legacy_followups + legacy_opps,
    }


def previous_report(db: Session, report: Report) -> Report | None:
    """지난 계획을 점검할 보고: 같은 단위의 확정 보고 중 이 보고보다 앞서는 가장 최근 것 (기간을 건너뛰어도 이어진다).
    그 보고의 항목을 이 보고가 아닌 다른 보고가 이미 점검했으면 건너뛴다."""
    other_checked = ReportPlanItem.checked_in_report_id.is_not(None)
    if report.id is not None:
        other_checked = and_(other_checked, ReportPlanItem.checked_in_report_id != report.id)
    taken = exists().where(ReportPlanItem.report_id == Report.id, other_checked)
    return db.scalars(
        select(Report)
        .where(Report.unit == report.unit, Report.status == "confirmed", Report.period_start < report.period_start, ~taken)
        .order_by(Report.period_start.desc(), Report.id.desc())
        .limit(1)
    ).first()

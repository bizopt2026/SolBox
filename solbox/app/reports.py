"""팀 업무 보고의 실적 집계. 보고서의 모든 숫자는 여기서 센다 (순수 함수: 기간만 받고 오늘 날짜를 보지 않는다).
질의 수는 기록 수와 무관하다 (목록은 selectinload / joinedload 로 한꺼번에 읽는다)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app import ontology, report_periods
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
    prev_start, prev_end = report_periods.prev_period(unit, start, end)

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
        mine = sorted((c for c in o.stage_changes if lo <= c.changed_at < hi), key=lambda c: (c.changed_at, c.id))  # 같은 시각이면 번호순
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
        "proposals_new": _count(db, Proposal, _in(Proposal.created_at, lo, hi)),
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
        .where(Report.unit == report.unit, Report.status == ontology.REPORT_STATUS_CONFIRMED, Report.period_start < report.period_start, ~taken)
        .order_by(Report.period_start.desc(), Report.id.desc())
        .limit(1)
    ).first()


# ---- 계획 넘김 · 점검 · 확정 ----

NOTE_KEYS = ("kpi", "opps", "proposals", "check", "plan", "issues")


def items_to_check(db: Session, report: Report) -> list[ReportPlanItem]:
    """이 보고가 점검할 지난 계획 항목 (앞 보고의 계획 항목 전부, 앞 보고가 없으면 빈 목록)."""
    prev = previous_report(db, report)
    return list(prev.plan_items) if prev else []


def save_check(db: Session, report: Report, results: dict[int, tuple[str, str]]) -> None:
    """점검 결과 저장: 항목 번호 -> (결과 코드, 메모). 점검 대상이 아닌 번호는 무시하고, 코드가 목록 밖이면 빈 값(아직 점검 안 함).
    결과도 메모도 빈 줄은 점검하지 않은 것으로 둔다 (점검한 보고 표시를 달지 않는다).
    확정 전이면 이어서 넘길 항목을 다시 맞춘다 (확정 뒤에는 넘어온 줄을 건드리지 않는다). 커밋은 부르는 쪽이 한다."""
    for item in items_to_check(db, report):
        if item.id not in results:
            continue
        code, note = results[item.id]
        item.result = code if code in ontology.PLAN_RESULTS else ""
        item.result_note = (note or "").strip()
        if item.result or item.result_note:
            item.checked_in_report_id = report.id
        elif item.checked_in_report_id == report.id:  # 빈 줄은 점검한 것으로 치지 않는다 (이 보고가 단 표시면 푼다)
            item.checked_in_report_id = None
    db.flush()
    if report.status != ontology.REPORT_STATUS_CONFIRMED:
        sync_carry(db, report)


def sync_carry(db: Session, report: Report) -> None:
    """미완료(미착수·진행 중·점검 안 함) 지난 항목을 이 보고 계획 맨 앞에 한 줄씩 두고 (task·owner 복사, 기한 비움),
    대상에서 빠진 항목의 넘어온 줄은 지운다 (사용자가 고친 줄도 같다). 이미 있는 줄은 다시 만들지 않는다. 확정된 보고는 그대로 둔다."""
    if report.status == ontology.REPORT_STATUS_CONFIRMED:
        return
    targets = [i for i in items_to_check(db, report) if i.result in ontology.PLAN_CARRY_RESULTS]
    target_ids = {i.id for i in targets}
    for line in list(report.plan_items):
        if line.carried_from_id is not None and line.carried_from_id not in target_ids:
            report.plan_items.remove(line)  # delete-orphan
    have = {l.carried_from_id for l in report.plan_items if l.carried_from_id is not None}
    fresh = [
        ReportPlanItem(report_id=report.id, task=i.task, owner=i.owner, due=None, carried_from_id=i.id)
        for i in targets if i.id not in have
    ]
    if fresh:
        report.plan_items[:0] = fresh
        db.flush()
        fresh_ids = {f.id for f in fresh}
        for pos, line in enumerate(sorted(report.plan_items, key=lambda l: (l.id not in fresh_ids, l.position, l.id))):
            line.position = pos
    db.flush()


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def note_lines(value) -> list[str]:
    """구역 보고 문구 한 칸의 값을 문장 줄 목록으로: 글(텍스트 칸)이면 빈 줄을 뺀 줄들, 목록이면 빈 항목을 뺀 것."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    return [line.strip() for line in (str(v) for v in value) if line.strip()]


def notes_of(report: Report) -> dict[str, list[str]]:
    """저장된 구역별 보고 문구 (아는 구역만, 값은 늘 list[str])."""
    saved = report.section_notes or {}
    return {k: note_lines(saved[k]) for k in NOTE_KEYS if k in saved}


def confirm(db: Session, report: Report, user) -> None:
    """계획 확정: 지금의 집계를 스냅숏으로 굳히고 비어 있는 구역 문구를 기본 문장으로 채운다. 커밋은 부르는 쪽이 한다."""
    data = _jsonable(aggregate(db, report.unit, report.period_start, report.period_end))
    report.snapshot = data
    report.status = ontology.REPORT_STATUS_CONFIRMED
    report.confirmed_at = datetime.now()
    notes = notes_of(report)
    if any(not notes.get(k) for k in NOTE_KEYS):
        defaults = default_notes(data, items_to_check(db, report), list(report.plan_items))
        for k in NOTE_KEYS:
            if not notes.get(k):
                notes[k] = defaults[k]
    report.section_notes = notes  # JSON 칸은 새 값을 넣어야 변경으로 잡힌다
    db.flush()


NOT_CONFIRMED = "확정된 보고가 아니라서 풀 것이 없어요."


def unconfirm_problem(db: Session, report: Report) -> str | None:
    """확정을 풀 수 없는 이유 (풀 수 있으면 None): 확정이 아니거나, 다음 보고가 이 보고의 계획을 이미 점검했다. 바꾸지 않는다."""
    if report.status != ontology.REPORT_STATUS_CONFIRMED:
        return NOT_CONFIRMED
    nxt = db.scalars(
        select(Report)
        .join(ReportPlanItem, ReportPlanItem.checked_in_report_id == Report.id)
        .where(ReportPlanItem.report_id == report.id, Report.id != report.id, Report.unit == report.unit, Report.period_start > report.period_start)
        .order_by(Report.period_start)
        .limit(1)
    ).first()
    if nxt is not None:
        when = report_periods.label(nxt.unit, nxt.period_start, nxt.period_end)
        return f"다음 보고({when})가 이 계획을 이미 점검해서 확정을 풀 수 없어요. 그 보고의 점검을 먼저 되돌려 주세요."
    return None


def unconfirm(db: Session, report: Report) -> str | None:
    """확정 풀기. 풀 수 없으면(`unconfirm_problem`) 바꾸지 않고 이유 글을 돌려준다."""
    problem = unconfirm_problem(db, report)
    if problem:
        return problem
    report.status = ontology.REPORT_STATUS_PLANNING
    report.snapshot = None
    report.confirmed_at = None
    db.flush()
    return None


def data_of(db: Session, report: Report) -> dict:
    """보고서에 보일 숫자: 확정이면 그때의 스냅숏, 아니면 지금 새로 센 집계."""
    if report.status == ontology.REPORT_STATUS_CONFIRMED and report.snapshot is not None:
        return report.snapshot
    return aggregate(db, report.unit, report.period_start, report.period_end)


def default_notes(data: dict, check: list[ReportPlanItem], plan: list[ReportPlanItem]) -> dict[str, list[str]]:
    """AI 없이 만드는 구역별 보고 문구 (숫자만 넣은 틀 문장, 구역마다 한 문장). 빈 구역도 "없음" 문장 한 줄을 돌려준다."""
    k = data.get("kpis") or {}
    n = lambda key: int(k.get(key) or 0)  # noqa: E731
    diff = n("activities") - n("activities_prev")
    versus = "지난 기간과 같음" if diff == 0 else f"지난 기간 대비 {diff:+d}"
    kpi = f"활동 기록 {n('activities')}건({versus}), 수주 {n('won')}건, 새 영업기회 {n('opportunities_new')}건입니다."

    opps = data.get("opportunities") or []
    if opps:
        new = sum(1 for o in opps if o.get("new"))
        opps_s = f"영업기회 {len(opps)}건에 움직임이 있었습니다(새로 생김 {new}건, 수주 {n('won')}건)."
    else:
        opps_s = "단계가 바뀌거나 새로 생긴 영업기회는 없습니다."

    props = data.get("proposals") or []
    if props:
        props_s = f"제안 {len(props)}건을 진행했습니다(새 제안 {n('proposals_new')}건, 제안서 작성 {n('decks_built')}건)."
    else:
        props_s = "이 기간에 진행한 제안은 없습니다."

    if check:
        done = sum(1 for i in check if i.result == "done")
        left = sum(1 for i in check if i.result in ontology.PLAN_CARRY_RESULTS)
        check_s = f"지난 계획 {len(check)}건 중 {done}건을 완료했습니다({round(done * 100 / len(check))}%)."
        if left:
            check_s += f" 미완료 {left}건은 다음 계획으로 넘깁니다."
    else:
        check_s = "처음 보고이거나 지난 계획이 없어 점검할 항목이 없습니다."

    if plan:
        carried = sum(1 for i in plan if i.carried_from_id is not None)
        plan_s = f"다음 계획은 {len(plan)}건입니다(지난 계획에서 넘어온 항목 {carried}건)."
    else:
        plan_s = "다음 계획 항목이 아직 없습니다."

    if n("followups_overdue"):
        issues_s = f"기한이 지난 후속 조치가 {n('followups_overdue')}건 있습니다(이 기간에 완료한 후속 조치 {n('followups_done')}건)."
    else:
        issues_s = "기한이 지난 후속 조치는 없습니다."
    return {"kpi": [kpi], "opps": [opps_s], "proposals": [props_s], "check": [check_s], "plan": [plan_s], "issues": [issues_s]}


# ---- 화면 저장 (라우터가 부른다; 커밋은 부르는 쪽이 한다) ----

def start_planning(report: Report) -> None:
    """점검·계획을 저장하면 "실적 확인" 에서 "계획 작성 중" 으로 (확정·이미 계획 중이면 그대로)."""
    if report.status == ontology.REPORT_STATUS_DEFAULT:
        report.status = ontology.REPORT_STATUS_PLANNING


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def save_plan(report: Report, ids: list[int | None], rows: list[tuple[str, str, str, str, str]], index) -> int:
    """계획 항목 표 저장: `rows` = 줄마다 (할 일, 담당, 기한 글자, 메모, 고객 이름), `ids` = 같은 순서의 줄 번호(새 줄은 None).
    활동 기록의 후속 조치 저장처럼 같은 번호는 고치고, 빠진 번호는 지우고, 새 줄은 만든다. 할 일이 빈 줄은 건너뛴다 (그 번호는 지워진다).
    고객은 `index`(activity_rows.LinkIndex)로 정확히 하나 맞을 때만 잇는다. 이름을 썼는데 잇지 못한 줄의 수를 돌려준다.
    영업기회·프로젝트 연결과 넘어온 표시(carried_from_id)는 고친 줄에서 그대로 남는다."""
    by_id = {i.id: i for i in report.plan_items}
    kept: list[ReportPlanItem] = []
    unlinked = 0
    for row_id, (task, owner, due, note, customer) in zip(ids, rows):
        if not task.strip():
            continue
        item = by_id.pop(row_id, None) if row_id is not None else None
        if item is None:
            item = ReportPlanItem(report_id=report.id)
        customer_id = index.customer_id(customer) if customer.strip() else None
        if customer.strip() and customer_id is None:
            unlinked += 1
        item.task, item.owner, item.due, item.note, item.customer_id = task.strip(), owner.strip(), _day(due.strip()), note.strip(), customer_id
        item.position = len(kept)
        kept.append(item)
    report.plan_items = kept  # 빠진 줄은 delete-orphan 으로 지워진다
    return unlinked

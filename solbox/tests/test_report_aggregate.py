"""팀 업무 보고 실적 집계 (app/reports.py): 기록을 만들고 같은 조건으로 따로 센 질의와 맞춰 본다."""
from datetime import date, datetime, timedelta

from sqlalchemy import and_, delete, event, func, select, update

from app import ontology, reports
from app.models import (
    Activity, Customer, CustomerNeed, FollowUp, Opportunity, OpportunityStageChange, Partner, Proposal, Report, ReportPlanItem, Solution,
)

START, END = date(2026, 11, 23), date(2026, 11, 29)  # 월~일
IN = datetime(2026, 11, 25, 10, 0)
OUT = datetime(2026, 12, 2, 10, 0)
PREV = datetime(2026, 11, 18, 10, 0)  # 앞 같은 길이 기간 (11-16 ~ 11-22)


def _customer(db, name="한빛병원", at=IN):
    c = Customer(name=name, created_at=at)
    db.add(c)
    db.flush()
    return c


def _activity(db, title, *, day=None, at=IN, kind="meeting", customers=()):
    a = Activity(title=title, raw_text="x", kind=kind, date=day, created_at=at, customers=list(customers))
    db.add(a)
    db.flush()
    return a


def _opp(db, customer, name="견적", stage="discovery", at=IN):
    o = Opportunity(name=name, customer_id=customer.id, stage=stage, created_at=at)
    db.add(o)
    db.flush()
    return o


def _set_changes(db, opp, *rows):
    """영업기회의 단계 변경 기록을 주어진 (from, to, 시각) 로 바꾼다 (처리기가 남긴 줄은 지운다)."""
    db.flush()
    db.execute(delete(OpportunityStageChange).where(OpportunityStageChange.opportunity_id == opp.id))
    for f, t, at in rows:
        db.add(OpportunityStageChange(opportunity_id=opp.id, from_stage=f, to_stage=t, changed_at=at))
    db.flush()


def _follow(db, act, task, *, due=None, done=False, done_at=None):
    f = FollowUp(activity_id=act.id, task=task, due=due, done=done)
    db.add(f)
    db.flush()
    db.execute(update(FollowUp).where(FollowUp.id == f.id).values(done_at=done_at))  # 처리기가 채운 값을 시험이 정한 값으로
    return f


def _agg(db, unit="week", start=START, end=END):
    db.commit()
    db.expire_all()
    return reports.aggregate(db, unit, start, end)


def test_counts_match_independent_queries(db):
    c = _customer(db)
    c_old = _customer(db, "옛고객", OUT)
    a1 = _activity(db, "킥오프", day=date(2026, 11, 24), kind="meeting", customers=[c])
    _activity(db, "통화", day=date(2026, 11, 26), kind="call", customers=[c])
    _activity(db, "회의2", day=date(2026, 11, 27), kind="meeting")
    _activity(db, "기간 밖", day=date(2026, 12, 3))
    _activity(db, "앞 기간", day=date(2026, 11, 18))
    _activity(db, "앞 기간2", day=date(2026, 11, 17))
    pt = Partner(name="솔루션사", created_at=OUT)
    db.add(pt)
    db.flush()
    db.add_all([
        CustomerNeed(activity_id=a1.id, content="n1", created_at=IN),
        CustomerNeed(activity_id=a1.id, content="n2", created_at=OUT),
        Solution(name="솔루션", partner_id=pt.id, stage="boxed", created_at=IN),
        Solution(name="솔루션밖", partner_id=pt.id, stage="boxed", created_at=OUT),
        Partner(name="파트너", customer_id=c_old.id, created_at=IN),
    ])
    o1 = _opp(db, c, "A")
    o2 = _opp(db, c, "B", at=PREV)
    _set_changes(db, o1, ("", "discovery", IN))
    _set_changes(db, o2, ("", "discovery", PREV), ("discovery", "won", datetime(2026, 11, 26, 9, 0)))
    pr1 = Proposal(title="제안1", customer_id=c.id, created_at=IN, updated_at=IN, deck_built_at=IN)
    pr2 = Proposal(title="제안2", customer_id=c.id, created_at=PREV, updated_at=PREV)
    db.add_all([pr1, pr2])
    _follow(db, a1, "끝낸 것", done=True, done_at=IN)
    _follow(db, a1, "예전에 끝낸 것", done=True, done_at=PREV)
    _follow(db, a1, "늦은 것", due=date(2026, 11, 25))
    out = _agg(db)
    k = out["kpis"]

    def count(model, col):
        return db.scalar(select(func.count()).select_from(model).where(and_(col >= datetime(2026, 11, 23), col < datetime(2026, 11, 30))))

    assert k["activities"] == 3 == len(out["activities"])
    assert k["activities_by_kind"] == {"meeting": 2, "call": 1}
    assert k["activities_prev"] == 2
    assert k["needs"] == count(CustomerNeed, CustomerNeed.created_at) == 1
    assert k["opportunities_new"] == count(Opportunity, Opportunity.created_at) == 1
    assert k["won"] == db.scalar(
        select(func.count()).select_from(OpportunityStageChange).where(
            OpportunityStageChange.to_stage == "won", OpportunityStageChange.changed_at >= datetime(2026, 11, 23), OpportunityStageChange.changed_at < datetime(2026, 11, 30)
        )
    ) == 1
    assert k["decks_built"] == count(Proposal, Proposal.deck_built_at) == 1
    assert k["proposals_active"] == 1 == len(out["proposals"])
    assert k["solutions_new"] == count(Solution, Solution.created_at) == 1
    assert k["customers_new"] == count(Customer, Customer.created_at) == 1
    assert k["partners_new"] == count(Partner, Partner.created_at) == 1
    assert k["followups_done"] == count(FollowUp, FollowUp.done_at) == 1 == len(out["followups_done"])
    assert k["followups_overdue"] == 1 == len(out["followups_overdue"])
    assert out["followups_overdue"][0]["task"] == "늦은 것"
    by_title = {a["title"]: a for a in out["activities"]}
    assert by_title["킥오프"]["customers"] == ["한빛병원"] and by_title["킥오프"]["date"] == date(2026, 11, 24)
    assert {o["name"]: (o["from_stage"], o["to_stage"], o["new"]) for o in out["opportunities"]} == {
        "A": ("", "discovery", True), "B": ("discovery", "won", False),
    }
    assert out["opportunities"][0]["customer"] == "한빛병원"
    assert out["proposals"][0]["title"] == "제안1" and out["proposals"][0]["customer"] == "한빛병원"
    assert out["legacy_undated"] == 0


def test_activity_date_fallback_created_at(db):
    _activity(db, "날짜 없음, 기간 안", day=None, at=IN)
    _activity(db, "날짜 없음, 기간 밖", day=None, at=OUT)
    _activity(db, "날짜가 기간 안, 만든 날은 밖", day=date(2026, 11, 24), at=OUT)
    _activity(db, "날짜가 밖, 만든 날은 안", day=date(2026, 12, 20), at=IN)
    out = _agg(db)
    titles = {a["title"]: a["date"] for a in out["activities"]}
    assert titles == {"날짜 없음, 기간 안": date(2026, 11, 25), "날짜가 기간 안, 만든 날은 밖": date(2026, 11, 24)}
    assert out["kpis"]["activities"] == 2


def test_won_counted_by_stage_change_date(db):
    c = _customer(db)
    o = _opp(db, c, "수주건", stage="won", at=datetime(2026, 9, 1))  # 만든 날은 옛날
    _set_changes(db, o, ("", "discovery", datetime(2026, 9, 1)), ("discovery", "won", datetime(2026, 11, 28, 23, 59)))
    assert _agg(db)["kpis"]["won"] == 1
    assert _agg(db, start=date(2026, 11, 30), end=date(2026, 12, 6))["kpis"]["won"] == 0
    assert _agg(db, "month", date(2026, 11, 1), date(2026, 11, 30))["kpis"]["won"] == 1
    opp = _agg(db)["opportunities"]
    assert [(x["name"], x["from_stage"], x["to_stage"], x["new"]) for x in opp] == [("수주건", "discovery", ontology.OPPORTUNITY_STAGE_WON, False)]


def test_overdue_uses_period_end(db):
    a = _activity(db, "활동")
    _follow(db, a, "끝까지 안 함", due=date(2026, 11, 20))
    _follow(db, a, "마지막 날이 기한", due=END)
    _follow(db, a, "기한 전", due=date(2026, 11, 30))
    _follow(db, a, "기간 안에 끝냄", due=date(2026, 11, 20), done=True, done_at=IN)
    _follow(db, a, "기간 뒤에 끝냄", due=date(2026, 11, 20), done=True, done_at=OUT)  # 기간 끝 기준으로는 아직 안 된 것
    out = _agg(db)
    assert {f["task"] for f in out["followups_overdue"]} == {"끝까지 안 함", "마지막 날이 기한", "기간 뒤에 끝냄"}
    assert out["kpis"]["followups_overdue"] == 3
    # 오늘(2026-11-26 또는 시험을 도는 날)과 무관하게 같은 값: 지난 기간을 다시 센다
    again = _agg(db)
    assert again["kpis"] == out["kpis"]
    # 지난 기간을 다시 세도 기간 끝 기준: 앞 기간(끝 11-22)에서는 기한이 11-20 인 셋이 모두 그때 안 된 것이다 (마지막 날 기한은 아직 아님)
    assert _agg(db, start=date(2026, 11, 16), end=date(2026, 11, 22))["kpis"]["followups_overdue"] == 3


def test_legacy_done_without_date_excluded_and_counted(db):
    a = _activity(db, "활동")
    f_legacy = _follow(db, a, "옛 완료", done=True, done_at=None)
    f_old = _follow(db, a, "기간 밖에 고친 옛 완료", done=True, done_at=None)
    o_old = _opp(db, _customer(db), "이력 없는 영업기회")
    o_untouched = _opp(db, _customer(db, "다른"), "기간 밖 영업기회", at=PREV)
    o_ok = _opp(db, _customer(db, "또다른"), "이력 있음")
    db.flush()
    db.execute(delete(OpportunityStageChange).where(OpportunityStageChange.opportunity_id.in_([o_old.id, o_untouched.id])))
    db.execute(update(FollowUp).where(FollowUp.id == f_legacy.id).values(updated_at=IN))
    db.execute(update(FollowUp).where(FollowUp.id == f_old.id).values(updated_at=OUT))
    db.execute(update(Opportunity).where(Opportunity.id == o_old.id).values(updated_at=IN))
    db.execute(update(Opportunity).where(Opportunity.id == o_untouched.id).values(updated_at=PREV))
    out = _agg(db)
    assert out["kpis"]["followups_done"] == 0 and out["followups_done"] == []  # 날짜를 몰라 숫자에 넣지 않는다
    assert o_ok.id not in {o["id"] for o in out["opportunities"] if o["name"] == "이력 없는 영업기회"}
    assert out["legacy_undated"] == 2  # 옛 완료 1 + 이력 없는 영업기회 1


def test_previous_report_skips_gap_and_none(db):
    def mk(start, status="confirmed", unit="week"):
        r = Report(unit=unit, period_start=start, period_end=start + timedelta(days=6), status=status)
        db.add(r)
        db.flush()
        return r

    cur = mk(date(2026, 11, 23), "draft")
    assert reports.previous_report(db, cur) is None  # 앞선 보고 없음
    mk(date(2026, 10, 26), "draft")  # 확정 아님은 건너뛴다
    mk(date(2026, 11, 9), unit="month")  # 다른 단위
    mk(date(2026, 12, 7))  # 뒤의 보고
    assert reports.previous_report(db, cur) is None
    old = mk(date(2026, 11, 2))
    assert reports.previous_report(db, cur) is old  # 한 주를 건너뛰어도 이어진다
    mid = mk(date(2026, 11, 9))
    assert reports.previous_report(db, cur) is mid  # 가장 최근
    # 다른 보고가 이미 점검한 보고는 건너뛴다
    other = mk(date(2026, 11, 16), "draft")
    item = ReportPlanItem(report_id=mid.id, task="t", checked_in_report_id=other.id)
    db.add(item)
    db.flush()
    assert reports.previous_report(db, cur) is old
    # 이 보고가 점검한 것이면 그대로 이 보고의 지난 계획이다
    item.checked_in_report_id = cur.id
    db.flush()
    assert reports.previous_report(db, cur) is mid


def _count_queries(db, fn):
    n = []
    hook = lambda *a, **k: n.append(1)  # noqa: E731
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", hook)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", hook)
    return len(n)


def _fill(db, n, offset):
    c = _customer(db, f"고객{offset}", IN)
    cs = [_customer(db, f"활동고객{offset}-{i}", IN) for i in range(2)]
    for i in range(n):
        a = _activity(db, f"활동{offset}-{i}", day=date(2026, 11, 24), kind="meeting" if i % 2 else "call", customers=cs)
        _follow(db, a, f"완료{i}", done=True, done_at=IN)
        _follow(db, a, f"지연{i}", due=date(2026, 11, 20))
        o = _opp(db, c, f"영업{offset}-{i}")
        _set_changes(db, o, ("", "discovery", IN), ("discovery", "won", IN))
        db.add(Proposal(title=f"제안{offset}-{i}", customer_id=c.id, created_at=IN, updated_at=IN, deck_built_at=IN))
        db.add(CustomerNeed(activity_id=a.id, content="n", created_at=IN))


def test_query_count_constant(db):
    _fill(db, 5, 0)
    db.commit()
    db.expire_all()
    reports.aggregate(db, "week", START, END)  # 워밍업
    db.expire_all()
    small = _count_queries(db, lambda: reports.aggregate(db, "week", START, END))
    _fill(db, 45, 1)
    db.commit()
    db.expire_all()
    out = {}
    big = _count_queries(db, lambda: out.update(reports.aggregate(db, "week", START, END)))
    assert out["kpis"]["activities"] == 50 and len(out["followups_done"]) == 50
    assert big == small

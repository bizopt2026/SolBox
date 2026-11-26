"""영업 C 단계: 파이프라인 숫자 (app/pipeline_stats.py)."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import pipeline_stats as ps
from app.models import Customer, Opportunity, OpportunityFieldChange, OpportunityStageChange, Team, User
from tests.conftest import make_user

TODAY = date(2026, 11, 26)


def D(m, d, h=9):
    return datetime(2026, m, d, h)


def _opp(db, name, created, owner=None, stage="discovery", amount=None, history=(), fields=(), legacy=False, close_reason=""):
    """history = [(시각, 옛 단계, 새 단계)]. 만든 줄은 legacy 가 아니면 저절로 (만든 때, "", 첫 단계)."""
    c = db.scalar(select(Customer)) or Customer(name="고객")
    db.add(c)
    db.flush()
    o = Opportunity(name=name, customer_id=c.id, stage=stage, amount=amount, owner_user_id=owner.id if owner else None,
                    created_at=created, close_reason=close_reason)
    db.add(o)
    db.flush()
    db.query(OpportunityStageChange).filter_by(opportunity_id=o.id).delete()  # guards 가 단 줄을 지우고 손으로 짠 이력만
    db.query(OpportunityFieldChange).filter_by(opportunity_id=o.id).delete()
    rows = [] if legacy else [(created, "", history[0][1] if history else stage)]
    for at, old, new in [*rows, *history]:
        db.add(OpportunityStageChange(opportunity_id=o.id, from_stage=old, to_stage=new, changed_at=at))
    for at, field, old, new in fields:
        db.add(OpportunityFieldChange(opportunity_id=o.id, field=field, old_value=old, new_value=new, changed_at=at))
    db.commit()
    db.expire_all()
    return o


def test_period_ranges():
    assert ps.period_range("90d", TODAY) == (date(2026, 8, 29), TODAY)
    assert ps.period_range("q", TODAY) == (date(2026, 10, 1), TODAY)
    assert ps.period_range("lq", TODAY) == (date(2026, 7, 1), date(2026, 9, 30))
    assert ps.period_range("y", TODAY) == (date(2026, 1, 1), TODAY)
    assert ps.period_range("12m", TODAY) == (date(2025, 11, 27), TODAY)
    assert ps.period_range("nope", TODAY) == ps.period_range("90d", TODAY)
    assert ps.period_range("lq", date(2026, 2, 3)) == (date(2025, 10, 1), date(2025, 12, 31))


def test_segments_known_and_unknown_start(db):
    o = _opp(db, "a", D(9, 1), stage="negotiating", history=[(D(9, 11), "discovery", "proposing"), (D(10, 1), "proposing", "negotiating")])
    segs = ps.segments(o)
    assert [(s.stage, s.start, s.end) for s in segs] == [("discovery", D(9, 1), D(9, 11)), ("proposing", D(9, 11), D(10, 1)), ("negotiating", D(10, 1), None)]
    old = _opp(db, "b", D(8, 1), stage="proposing", legacy=True, history=[(D(9, 20), "discovery", "proposing")])
    assert [(s.stage, s.start) for s in ps.segments(old)] == [("discovery", None), ("proposing", D(9, 20))]
    bare = _opp(db, "c", D(8, 1), stage="proposing", legacy=True)
    assert [(s.stage, s.start, s.end) for s in ps.segments(bare)] == [("proposing", None, None)]


def test_same_time_rows_keep_id_order(db):
    o = _opp(db, "a", D(9, 1), stage="won", history=[(D(9, 5), "discovery", "proposing"), (D(9, 5), "proposing", "won")])
    assert [s.stage for s in ps.segments(o)] == ["discovery", "proposing", "won"]


def test_stage_dwell_conversion_and_exclusions(db):
    _opp(db, "a", D(9, 1), stage="negotiating", history=[(D(9, 11), "discovery", "proposing"), (D(10, 1), "proposing", "negotiating")])
    _opp(db, "b", D(9, 1), stage="lost", history=[(D(9, 21), "discovery", "lost")])
    _opp(db, "c", D(9, 1), stage="proposing", legacy=True, history=[(D(9, 20), "discovery", "proposing")])  # 발굴 시작 모름
    _opp(db, "d", D(11, 16), stage="discovery")  # 지금 발굴 10일째
    r = ps.compute(db, "90d", None, TODAY)
    disc = next(s for s in r["stages"] if s["stage"] == "discovery")
    assert (disc["n"], disc["avg"], disc["median"]) == (3, 15.0, 15.0)  # 끝난 3구간 중 시작을 아는 a(10일)·b(20일)
    assert disc["forward"] == pytest.approx(2 / 3) and disc["lost"] == pytest.approx(1 / 3)
    assert (disc["current_n"], disc["current_avg"]) == (1, 10.0)
    assert r["excluded"] == 1
    prop = next(s for s in r["stages"] if s["stage"] == "proposing")
    assert (prop["n"], prop["avg"], prop["forward"]) == (1, 20.0, 1.0)
    neg = next(s for s in r["stages"] if s["stage"] == "negotiating")
    assert (neg["n"], neg["current_n"]) == (0, 1) and neg["avg"] is None


def test_closes_counted_per_event_with_reason_and_amount_at_that_time(db):
    o = _opp(db, "재수주", D(9, 1), stage="won", amount=500_000_000, close_reason="trust",
             history=[(D(9, 10), "discovery", "lost"), (D(10, 1), "lost", "discovery"), (D(11, 1), "discovery", "won")],
             fields=[(D(9, 10), "close_reason", "", "price"), (D(10, 1), "close_reason", "price", ""), (D(11, 1), "close_reason", "", "trust"),
                     (D(10, 15), "amount", "300000000", "500000000")])
    _opp(db, "실패", D(9, 1), stage="lost", amount=200_000_000, close_reason="competitor", history=[(D(10, 20), "discovery", "lost")],
         fields=[(D(10, 20), "close_reason", "", "competitor")])
    r = ps.compute(db, "90d", None, TODAY)
    t = r["tiles"]
    assert (t["won"], t["lost"], t["won_amount"]) == (1, 2, 500_000_000)
    assert t["win_rate"] == pytest.approx(1 / 3)
    assert t["avg_win_days"] == 61  # 9/1 → 11/1
    assert dict(r["reasons"]["lost"]) == {"가격": 1, "경쟁사 선택": 1} and dict(r["reasons"]["won"]) == {"관계·신뢰": 1}
    assert [(m["label"], m["won"], m["won_amount"], m["lost"]) for m in r["months"]] == [
        ("8월", 0, 0, 0), ("9월", 0, 0, 1), ("10월", 0, 0, 1), ("11월", 1, 500_000_000, 0)]
    assert ps.amount_at(o, D(9, 30)) == 300_000_000 and ps.amount_at(o, D(11, 2)) == 500_000_000


def test_period_boundaries(db):
    _opp(db, "시작날", D(8, 29, 0), stage="won", history=[(datetime(2026, 8, 29, 0, 0), "discovery", "won")])
    _opp(db, "전날", D(8, 1), stage="won", history=[(datetime(2026, 8, 28, 23, 59), "discovery", "won")])
    _opp(db, "내일", D(8, 1), stage="won", history=[(datetime(2026, 11, 27, 0, 0), "discovery", "won")])
    assert ps.compute(db, "90d", None, TODAY)["tiles"]["won"] == 1


def test_team_filter_and_compare(db):
    t1, t2 = Team(name="영업1팀", position=1), Team(name="영업2팀", position=2)
    db.add_all([t1, t2])
    db.commit()
    a, b, c = make_user(db, "a"), make_user(db, "b"), make_user(db, "c")
    a.team_id, b.team_id = t1.id, t2.id
    db.commit()
    _opp(db, "1팀 수주", D(9, 1), owner=a, stage="won", history=[(D(10, 1), "discovery", "won")])
    _opp(db, "1팀 진행", D(11, 1), owner=a, stage="proposing", amount=100, history=[(D(11, 10), "discovery", "proposing")])
    _opp(db, "2팀 실패", D(9, 1), owner=b, stage="lost", history=[(D(10, 1), "discovery", "lost")])
    _opp(db, "팀 없음", D(9, 1), owner=c, stage="won", history=[(D(10, 2), "discovery", "won")])
    only1 = ps.compute(db, "90d", t1.id, TODAY)
    assert (only1["tiles"]["won"], only1["tiles"]["lost"]) == (1, 0)
    assert ps.compute(db, "90d", "none", TODAY)["tiles"]["won"] == 1
    rows = {r["name"]: r for r in ps.compute(db, "90d", None, TODAY)["teams"]}
    assert list(rows) == ["영업1팀", "영업2팀", "팀 없음"]
    assert (rows["영업1팀"]["open_n"], rows["영업1팀"]["open_amount"], rows["영업1팀"]["won"]) == (1, 100, 1)
    assert rows["영업2팀"]["win_rate"] == 0.0 and rows["영업1팀"]["dwell"]["discovery"] == 19.5  # 30일과 9일


def test_numbers_hash_changes_with_numbers(db):
    h1 = ps.compute(db, "90d", None, TODAY)["numbers_hash"]
    _opp(db, "x", D(9, 1), stage="won", history=[(D(10, 1), "discovery", "won")])
    assert ps.compute(db, "90d", None, TODAY)["numbers_hash"] != h1

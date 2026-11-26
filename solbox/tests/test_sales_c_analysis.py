"""영업 C 단계: 영업기회 분석 화면."""
import re
from datetime import date, datetime, timedelta

from sqlalchemy import event, select

from app.models import Customer, Opportunity, OpportunityFieldChange, OpportunityStageChange, Team, User


def _world(db, n=3):
    me = db.scalar(select(User).where(User.username == "kim"))
    t = Team(name="영업1팀")
    db.add(t)
    db.commit()
    me.team_id = t.id
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    now = datetime.now()
    for i in range(n):
        o = Opportunity(name=f"건{i}", customer_id=c.id, stage="discovery", owner_user_id=me.id, amount=100_000_000)
        db.add(o)
        db.commit()
        o.stage = "proposing"
        db.commit()
        if i == 0:
            o.stage, o.close_reason = "won", "trust"
            db.commit()
        if i == 1:
            o.stage, o.close_reason = "lost", "competitor"
            db.commit()
    return t


def test_page_sections(user_client, db):
    _world(db)
    page = user_client.get("/opportunities/analysis").text
    assert "영업기회 분석" in page and "최근 90일" in page and "이번 분기" in page and "영업1팀" in page
    for label in ("수주", "실패", "수주율", "평균 수주 기간", "단계별 체류와 전환", "월별 수주 금액", "사유", "팀별 비교", "숫자는 이렇게 셉니다"):
        assert label in page, label
    assert "<svg" in page and "관계·신뢰" in page and "경쟁사 선택" in page
    assert "50%" in page  # 수주 1 · 실패 1
    assert '<nav class="crumbs"' in page or "영업기회" in page


def test_team_filter_hides_compare_table(user_client, db):
    t = _world(db)
    page = user_client.get(f"/opportunities/analysis?team={t.id}").text
    assert "팀별 비교" not in page and 'aria-current="page">영업1팀' in page


def test_bad_params_fall_back(user_client, db):
    _world(db)
    assert user_client.get("/opportunities/analysis?period=zzz&team=abc").status_code == 200


def test_excluded_notice(user_client, db):
    c = Customer(name="옛")
    db.add(c)
    db.commit()
    o = Opportunity(name="옛 건", customer_id=c.id, stage="discovery")
    db.add(o)
    db.commit()
    db.query(OpportunityStageChange).filter_by(opportunity_id=o.id).delete()
    db.add(OpportunityStageChange(opportunity_id=o.id, from_stage="discovery", to_stage="proposing", changed_at=datetime.now() - timedelta(days=1)))
    o.stage = "proposing"
    db.commit()
    page = user_client.get("/opportunities/analysis").text
    assert "1건은 체류일에서 뺐어요" in page


def _count(app, fn):
    n = []
    hook = lambda *a, **k: n.append(1)  # noqa: E731
    event.listen(app.state.engine, "before_cursor_execute", hook)
    try:
        fn()
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    return len(n)


def test_query_count_is_flat(app, user_client, db):
    _world(db, 3)
    user_client.get("/opportunities/analysis")
    small = _count(app, lambda: user_client.get("/opportunities/analysis"))
    _world_more = Customer(name="더")
    db.add(_world_more)
    db.commit()
    me = db.scalar(select(User).where(User.username == "kim"))
    for i in range(20):
        o = Opportunity(name=f"더{i}", customer_id=_world_more.id, stage="discovery", owner_user_id=me.id)
        db.add(o)
        db.commit()
    assert _count(app, lambda: user_client.get("/opportunities/analysis")) == small

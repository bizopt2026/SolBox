"""영업 A 단계: 홈의 "챙길 영업기회" 카드와 영업기회 카드의 금액 합계."""
import re
from datetime import date, timedelta

from sqlalchemy import event, select

from app.models import Customer, Opportunity, Team, User
from app.routers import home
from tests.conftest import make_user

TODAY = date.today()


def _world(db, n_mine=2):
    me = db.scalar(select(User).where(User.username == "kim"))
    t = Team(name="영업1팀")
    db.add(t)
    db.commit()
    mate = make_user(db, "park", name="박하늘")
    other = make_user(db, "lee", name="이현장")
    me.team_id = mate.team_id = t.id
    c = Customer(name="한빛물류")
    db.add(c)
    db.commit()
    for i in range(n_mine):
        db.add(Opportunity(name=f"내 멈춘 건 {i}", customer_id=c.id, owner_user_id=me.id, stage="proposing", next_step="", amount=100_000_000))
    db.add(Opportunity(name="팀 멈춘 건", customer_id=c.id, owner_user_id=mate.id, stage="negotiating", next_step="", amount=50_000_000))
    db.add(Opportunity(name="남의 멈춘 건", customer_id=c.id, owner_user_id=other.id, stage="discovery", next_step=""))
    db.add(Opportunity(name="괜찮은 건", customer_id=c.id, owner_user_id=me.id, stage="discovery", next_step="견적", next_step_due=TODAY + timedelta(days=3), amount=230_000_000))
    db.add(Opportunity(name="수주 건", customer_id=c.id, owner_user_id=me.id, stage="won", next_step="", amount=1_000_000_000))
    db.commit()
    return me


def _card(page):
    return re.search(r'<section class="card[^"]*" aria-labelledby="h-stale">.*?</section>', page, re.S).group(0)


def test_card_defaults_to_mine_with_tab_counts(user_client, db):
    _world(db)
    card = _card(user_client.get("/").text)
    assert "챙길 영업기회" in card
    assert "내 멈춘 건 0" in card and "팀 멈춘 건" not in card and "괜찮은 건" not in card and "수주 건" not in card
    assert re.search(r"내 건<b>2</b>", card) and re.search(r"우리 팀<b>3</b>", card) and re.search(r"전체<b>4</b>", card)
    assert "다음 할 일 없음" in card and "1억" in card


def test_card_team_and_all_views(user_client, db):
    _world(db)
    team = _card(user_client.get("/?opp=team").text)
    assert "팀 멈춘 건" in team and "남의 멈춘 건" not in team
    every = _card(user_client.get("/?opp=all").text)
    assert "남의 멈춘 건" in every


def test_card_empty_mine_message(user_client, db):
    _world(db, n_mine=0)
    card = _card(user_client.get("/").text)
    assert "멈춘 내 영업기회가 없습니다" in card


def test_card_shows_at_most_six_and_links_to_list(user_client, db):
    _world(db, n_mine=8)
    card = _card(user_client.get("/").text)
    assert card.count('class="opp-row"') == 6
    assert 'href="/opportunities?stale=1&amp;who=mine"' in card


def test_opportunity_card_shows_open_amount_sum(user_client, db):
    _world(db)
    page = user_client.get("/").text
    sec = re.search(r'aria-labelledby="h-opp">.*?</section>', page, re.S).group(0)
    # 진행 중: 1억 + 1억 + 0.5억 + (금액 없음) + 2.3억 = 4.8억, 금액 없는 진행 중 1건
    assert "합계 <b" in sec and "4.8억" in sec and "금액 미입력 1건" in sec
    assert "10억" in sec  # 수주 막대 옆 금액


def _count(app, fn):
    n = []
    hook = lambda *a, **k: n.append(1)  # noqa: E731
    event.listen(app.state.engine, "before_cursor_execute", hook)
    try:
        fn()
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    return len(n)


def test_query_count_does_not_grow_with_opportunities(app, user_client, db):
    _world(db, n_mine=2)
    user_client.get("/")
    small = _count(app, lambda: user_client.get("/"))
    c = db.scalar(select(Customer))
    me = db.scalar(select(User).where(User.username == "kim"))
    for i in range(25):
        db.add(Opportunity(name=f"더 {i}", customer_id=c.id, owner_user_id=me.id, stage="proposing", next_step=""))
    db.commit()
    large = _count(app, lambda: user_client.get("/"))
    assert large == small, (small, large)


def test_stale_opportunities_helper_counts(db):
    make_user(db, "kim", name="김영업")
    me = _world(db)
    got = home.stale_opportunities(db, me, TODAY, "all")
    assert got["counts"] == {"mine": 2, "team": 3, "all": 4} and got["who"] == "all" and len(got["items"]) == 4

"""영업 B 단계: 목록·홈의 "AI 제안 n" 배지와 "AI 제안 있음" 칩."""
import re

from sqlalchemy import event, select

from app import opportunity_ai as oai
from app.models import Customer, Opportunity, User


def _sug(i):
    return {"id": i, "kind": "field", "field": "amount", "item": None, "value": 100 * i, "due": None, "evidence": "", "activity_id": None,
            "verified": False, "state": "pending", "decided_by": None, "decided_at": None}


def _world(db, n=3):
    me = db.scalar(select(User).where(User.username == "kim"))
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    rows = []
    for i in range(n):
        o = Opportunity(name=f"건{i}", customer_id=c.id, stage="proposing", owner_user_id=me.id, next_step="")
        db.add(o)
        db.commit()
        rows.append(o)
    oai.save_insight(db, rows[0], None, {}, [_sug(1), _sug(2)], {})
    oai.save_insight(db, rows[1], None, {}, [{**_sug(1), "state": "applied"}], {})
    db.commit()
    return rows


def test_pending_counts(db, user_client):
    rows = _world(db)
    assert oai.pending_counts(db, [o.id for o in rows]) == {rows[0].id: 2}


def test_list_badge_and_ai_chip(user_client, db):
    rows = _world(db)
    page = user_client.get("/opportunities").text
    row0 = re.search(r"<tr>(?:(?!</tr>).)*>건0</a>.*?</tr>", page, re.S).group(0)
    assert "AI 제안 2" in row0 and "AI 제안" not in re.search(r"<tr>(?:(?!</tr>).)*>건1</a>.*?</tr>", page, re.S).group(0)
    assert "AI 제안 있음" in page
    only = user_client.get("/opportunities?ai=1").text
    assert ">건0</a>" in only and ">건1</a>" not in only and ">건2</a>" not in only


def test_home_card_badge(user_client, db):
    _world(db)
    card = re.search(r'aria-labelledby="h-stale">.*?</section>', user_client.get("/").text, re.S).group(0)
    assert "AI 제안 2" in card


def _count(app, fn):
    n = []
    hook = lambda *a, **k: n.append(1)  # noqa: E731
    event.listen(app.state.engine, "before_cursor_execute", hook)
    try:
        fn()
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    return len(n)


def test_list_and_home_query_counts_stay_flat(app, user_client, db):
    rows = _world(db)
    user_client.get("/opportunities")
    user_client.get("/")
    small = (_count(app, lambda: user_client.get("/opportunities")), _count(app, lambda: user_client.get("/")))
    c = db.scalar(select(Customer))
    for i in range(20):
        o = Opportunity(name=f"더{i}", customer_id=c.id, stage="proposing", owner_user_id=rows[0].owner_user_id, next_step="")
        db.add(o)
        db.commit()
        oai.save_insight(db, o, None, {}, [_sug(1)], {})
        db.commit()
    large = (_count(app, lambda: user_client.get("/opportunities")), _count(app, lambda: user_client.get("/")))
    assert large == small, (small, large)

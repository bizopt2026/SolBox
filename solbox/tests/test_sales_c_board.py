"""영업 C 단계: 영업기회 보드 보기와 단계 바꾼 뒤 보드로 돌아오기."""
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select

from app import opportunities as opps
from app.models import Customer, Opportunity, User
from tests.conftest import nloc


def _world(db):
    me = db.scalar(select(User).where(User.username == "kim"))
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    rows = {}
    for name, stage, amount, due in (("발굴A", "discovery", 100_000_000, date.today() + timedelta(days=5)),
                                     ("발굴B", "discovery", 300_000_000, date.today() + timedelta(days=2)),
                                     ("제안C", "proposing", None, None), ("협상D", "negotiating", 1_200_000_000, None),
                                     ("보류E", "hold", None, None), ("수주F", "proposing", 210_000_000, None)):
        o = Opportunity(name=name, customer_id=c.id, stage=stage, amount=amount, next_step="할 일" if due else "", next_step_due=due, owner_user_id=me.id)
        db.add(o)
        db.commit()
        rows[name] = o
    return rows


def _col(page, stage):
    return re.search(rf'<section class="board-col" data-stage="{stage}".*?</section>', page, re.S).group(0)


def test_board_columns_totals_and_order(user_client, db):
    rows = _world(db)
    user_client.post(f"/opportunities/{rows['수주F'].id}/close", data={"stage": "won", "reason": "trust"})
    page = user_client.get("/opportunities?view=board").text
    disc = _col(page, "discovery")
    assert "2건" in disc and "4억" in disc and disc.index("발굴B") < disc.index("발굴A")  # 다음 할 일 빠른 순
    prop = _col(page, "proposing")
    assert "제안C" in prop and "금액 없음 1" in prop and "수주F" not in prop
    assert "협상D" in _col(page, "negotiating") and "12억" in _col(page, "negotiating")
    closed = page.split('class="board-closed"')[1].split("</aside>")[0]
    assert "수주F" in closed and "관계·신뢰" in closed
    assert "보류 1건" in page and 'href="/opportunities?stage=hold"' in page
    assert 'aria-current="page">보드' in page or 'aria-current="page" >보드' in page or ">보드</a>" in page


def test_board_keeps_filters(user_client, db):
    rows = _world(db)
    page = user_client.get("/opportunities?view=board&q=발굴").text
    assert "발굴A" in page and "협상D" not in page


def test_card_stage_buttons_return_to_board(user_client, db):
    rows = _world(db)
    o = rows["발굴A"]
    page = user_client.get("/opportunities?view=board&who=mine").text
    card = re.search(rf'<article class="kcard"[^>]*data-id="{o.id}".*?</article>', page, re.S).group(0)
    assert 'name="back" value="board"' in card and 'name="qs" value="who=mine"' in card and 'value="proposing"' in card
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "proposing", "back": "board", "qs": "who=mine"}, follow_redirects=False)
    assert nloc(r).startswith("/opportunities?view=board&who=mine&notice=opportunity_stage")


def test_won_from_board_goes_through_reason_page_and_back(user_client, db):
    rows = _world(db)
    o = rows["협상D"]
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "won", "back": "board", "qs": "who=mine"}, follow_redirects=False)
    assert r.headers["location"] == f"/opportunities/{o.id}/close?to=won&back=board&qs=" + quote("who=mine")
    page = user_client.get(r.headers["location"]).text
    assert 'name="back" value="board"' in page and 'name="qs" value="who=mine"' in page
    r = user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": "price", "back": "board", "qs": "who=mine"}, follow_redirects=False)
    assert nloc(r) == "/opportunities?view=board&who=mine&notice=opportunity_stage.won"


def test_bad_back_or_qs_values_are_ignored(user_client, db):
    rows = _world(db)
    o = rows["발굴A"]
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "proposing", "back": "https://evil.example"}, follow_redirects=False)
    assert nloc(r).startswith(f"/opportunities/{o.id}?notice=")
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "negotiating", "back": "board", "qs": "next=//evil&who=team&x=1"}, follow_redirects=False)
    assert nloc(r).startswith("/opportunities?view=board&who=team&notice=")


def test_board_url_helper():
    assert opps.board_url("who=mine&stale=1&page=3&evil=x") == "/opportunities?view=board&who=mine&stale=1"
    assert opps.board_url("") == "/opportunities?view=board"


def test_board_has_drag_hooks_and_script(user_client, db):
    _world(db)
    page = user_client.get("/opportunities?view=board").text
    assert "data-board" in page and 'draggable="true"' in page and 'class="board-drop" data-stage="won"' in page
    js = user_client.get("/static/app.js").text
    assert "[data-board]" in js and '"back", "board"' in js

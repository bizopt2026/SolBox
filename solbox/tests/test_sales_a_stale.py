"""영업 A 단계: 멈춘 건 규칙과 영업기회 목록·상세 (담당 걸러 보기, 새 칸, 이력 카드, 멈춘 알림)."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app import opportunities as opps, ui
from app.guards import rev_of
from app.models import Activity, Customer, FollowUp, Opportunity, Team, User
from tests.conftest import fu_rows, make_user

TODAY = date(2026, 11, 26)


def _o(stage="negotiating", next_step="견적", due=None, close=None):
    return SimpleNamespace(stage=stage, next_step=next_step, next_step_due=due, expected_close=close)


# ---- 규칙 ----

def test_no_reasons_when_everything_is_fine():
    assert opps.stale_reasons(_o(due=TODAY, close=TODAY), TODAY, TODAY - timedelta(days=30)) == []


def test_each_reason_and_its_boundary():
    assert opps.stale_reasons(_o(due=TODAY - timedelta(days=1)), TODAY, TODAY) == ["overdue"]
    assert opps.stale_reasons(_o(close=TODAY - timedelta(days=1)), TODAY, TODAY) == ["close_past"]
    assert opps.stale_reasons(_o(next_step=""), TODAY, TODAY) == ["no_next"]
    assert opps.stale_reasons(_o(), TODAY, TODAY - timedelta(days=31)) == ["quiet"]


def test_reasons_keep_defined_order():
    o = _o(next_step="", due=TODAY - timedelta(days=3), close=TODAY - timedelta(days=11))
    assert opps.stale_reasons(o, TODAY, TODAY - timedelta(days=42)) == ["overdue", "close_past", "no_next", "quiet"]


def test_closed_and_hold_are_never_stale():
    for stage in ("won", "lost", "hold"):
        assert opps.stale_reasons(_o(stage=stage, next_step=""), TODAY, TODAY - timedelta(days=400)) == []


def test_reason_texts():
    o = _o(due=TODAY - timedelta(days=3), close=date(2026, 11, 15))
    last = TODAY - timedelta(days=42)
    assert opps.stale_text("overdue", o, TODAY, last) == "다음 할 일 3일 지남"
    assert opps.stale_text("close_past", o, TODAY, last) == "예상 수주일 지남 (11월 15일)"
    assert opps.stale_text("no_next", o, TODAY, last) == "다음 할 일 없음"
    assert opps.stale_text("quiet", o, TODAY, last) == "42일째 소식 없음"


def test_short_date():
    assert ui.short_date(date(2026, 12, 20), TODAY) == "12월 20일"
    assert ui.short_date(date(2027, 1, 31), TODAY) == "2027년 1월 31일"
    assert ui.short_date(None, TODAY) == ""


# ---- 세계 ----

def _world(db):
    me = db.scalar(select(User).where(User.username == "kim"))
    t1, t2 = Team(name="영업1팀"), Team(name="영업2팀")
    db.add_all([t1, t2])
    db.commit()
    mate = make_user(db, "park", name="박하늘")
    other = make_user(db, "lee", name="이현장")
    me.team_id, mate.team_id, other.team_id = t1.id, t1.id, t2.id
    c = Customer(name="한빛물류")
    db.add(c)
    db.commit()
    rows = {}
    for name, owner, amount in (("내 건", me, 1_200_000_000), ("팀 건", mate, 350_000_000), ("남의 건", other, None)):
        o = Opportunity(name=name, customer_id=c.id, owner_user_id=owner.id, amount=amount, next_step="견적", next_step_due=TODAY + timedelta(days=5))
        db.add(o)
        rows[name] = o
    db.commit()
    return me, rows


def test_who_tabs_filter_by_owner_and_team(user_client, db):
    me, rows = _world(db)
    page = user_client.get("/opportunities").text
    assert "우리 팀" in page and "내 건" in page
    mine = user_client.get("/opportunities?who=mine").text
    assert "내 건" in mine and "팀 건</a>" not in mine and "남의 건" not in mine
    team = user_client.get("/opportunities?who=team").text
    assert "팀 건" in team and "남의 건" not in team


def test_no_team_user_has_no_team_tab_and_team_means_all(user_client, db):
    me, rows = _world(db)
    me.team_id = None
    db.commit()
    page = user_client.get("/opportunities?who=team").text
    assert ">우리 팀<" not in page and "남의 건" in page


def test_list_has_progress_columns_and_no_bm_column(user_client, db):
    me, rows = _world(db)
    page = user_client.get("/opportunities").text
    head = page.split("<thead>")[1].split("</thead>")[0]
    for label in ("예상 금액", "예상 수주일", "담당", "다음 할 일"):
        assert label in head
    for gone in ("BM", "프로젝트", "검토 사업방식", "연결"):
        assert gone not in head
    assert "12억" in page and "3.5억" in page
    assert "영업1팀" in page


def test_stale_chip_lists_only_stale(user_client, db):
    me, rows = _world(db)
    rows["팀 건"].next_step = ""
    db.commit()
    page = user_client.get("/opportunities?stale=1").text
    assert "팀 건" in page and "내 건</a>" not in page
    assert "다음 할 일 없음" in page


def test_amount_sort_puts_blank_last(user_client, db):
    me, rows = _world(db)
    page = user_client.get("/opportunities?sort=amount").text
    assert page.index("내 건</a>") < page.index("팀 건</a>") < page.index("남의 건</a>")


def test_close_sort_soonest_first_blank_last(user_client, db):
    me, rows = _world(db)
    rows["내 건"].expected_close = date(2027, 3, 1)
    rows["팀 건"].expected_close = date(2026, 12, 1)
    db.commit()
    page = user_client.get("/opportunities?sort=close").text
    assert page.index("팀 건</a>") < page.index("내 건</a>") < page.index("남의 건</a>")


def test_detail_shows_summary_alert_history_and_open_follow_ups(user_client, db):
    me, rows = _world(db)
    o = rows["내 건"]
    data = {"name": o.name, "customer_id": str(o.customer_id), "_rev": rev_of(o), "progress": "1", "amount": "15억",
            "expected_close": "", "owner_user_id": str(me.id), "next_step": "견적", "next_step_due": (date.today() - timedelta(days=400)).isoformat()}
    assert user_client.post(f"/opportunities/{o.id}", data=data, follow_redirects=False).status_code == 303
    a = Activity(kind="meeting", date=date(2026, 10, 15), title="회의", raw_text="r", follow_ups=fu_rows(("구조 진단 업체 섭외", "", "", False), ("끝난 일", "", "", True)))
    db.add(a)
    a.opportunities = [o]
    db.commit()
    page = user_client.get(f"/opportunities/{o.id}").text
    assert "변경 이력" in page and "12억" in page and "15억" in page and "예상 금액" in page
    assert "멈춘 건" in page and "다음 할 일 400일 지남" in page
    assert "구조 진단 업체 섭외" in page and "끝난 일" not in page
    assert 'class="sumband"' in page


def test_detail_without_problems_has_no_alert(user_client, db):
    me, rows = _world(db)
    o = rows["내 건"]
    a = Activity(kind="meeting", date=date.today(), title="회의", raw_text="r")
    a.opportunities = [o]
    db.add(a)
    db.commit()
    assert "멈춘 건" not in user_client.get(f"/opportunities/{o.id}").text


def test_last_touches_uses_activity_or_created_date(db):
    c = Customer(name="x")
    db.add(c)
    db.commit()
    o1 = Opportunity(name="a", customer_id=c.id, created_at=datetime(2026, 9, 1))
    o2 = Opportunity(name="b", customer_id=c.id, created_at=datetime(2026, 9, 1))
    db.add_all([o1, o2])
    db.commit()
    a = Activity(kind="meeting", date=date(2026, 11, 2), title="t", raw_text="r")
    a.opportunities = [o1]
    db.add(a)
    db.commit()
    assert opps.last_touches(db, [o1, o2]) == {o1.id: date(2026, 11, 2), o2.id: date(2026, 9, 1)}

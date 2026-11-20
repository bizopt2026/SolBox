"""홈(오늘 할 일): 숫자가 직접 센 값과 같음, 질의 수 상한, 한 줄 요약, 죽은 링크 없음."""
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

import pytest
from sqlalchemy import event, func, select

from app import ontology
from app.models import Activity, FollowUp, Intake, Opportunity, Solution
from app.routers import home as home_mod
from app.routers.home import summary_sentence
from tests import relfix
from tests.conftest import make_user

TODAY = date(2026, 11, 20)


@pytest.fixture
def world(db, user_client):
    kim = make_user(db, "owner1", name="owner1")
    return relfix.build(db, owner=kim)


def test_summary_sentence_omits_zero_parts():
    assert summary_sentence(1, 2) == "기한이 지난 후속 조치 1건, 확인 대기 2건"
    assert summary_sentence(0, 2) == "확인 대기 2건"
    assert summary_sentence(3, 0) == "기한이 지난 후속 조치 3건"
    assert "없습니다" in summary_sentence(0, 0)


def test_home_numbers_match_direct_counts(user_client, db, world, monkeypatch):
    monkeypatch.setattr(home_mod, "today", lambda: TODAY)
    kim = db.query(__import__("app.models", fromlist=["User"]).User).filter_by(username="kim").one()
    db.add(Intake(note="내 묶음", status="ready", created_by_id=kim.id))
    db.add(Intake(note="정리중", status="analyzing", created_by_id=kim.id))
    db.add(FollowUp(activity_id=world.a_kick.id, task="지난 일", due=TODAY - timedelta(days=4), position=9))
    db.commit()
    page = user_client.get("/").text
    for code in ontology.STAGE_FLOW:
        n = db.scalar(select(func.count()).select_from(Solution).where(Solution.stage == code))
        cell = re.search(rf'<a class="flow-cell[^"]*" href="/solutions\?stage={code}".*?</a>', page, re.S).group(0)
        assert f"<b>{n}</b>" in cell
    hold = db.scalar(select(func.count()).select_from(Solution).where(Solution.stage == "hold"))
    assert re.search(rf'data-stage="hold">[^<]*<b>{hold}</b>', page)
    for code in (*ontology.OPPORTUNITY_OPEN_STAGES, "won"):
        n = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.stage == code))
        row = re.search(rf'<a class="sb-row" href="/opportunities\?stage={code}".*?</a>', page, re.S).group(0)
        assert f"<b>{n}</b>" in row
    open_n = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.stage.in_(ontology.OPPORTUNITY_OPEN_STAGES)))
    assert f'?stage=open">{open_n}건' in page
    overdue = db.scalar(select(func.count()).select_from(FollowUp).where(FollowUp.done.is_(False), FollowUp.due < TODAY))
    assert f"기한이 지난 후속 조치 {overdue}건, 확인 대기 1건" in page and "11월 20일 금요일" in page
    assert "4일 지남" in page and "정리 중 1건" in page


def test_home_flow_week_sublines_are_truthful(user_client, db):
    old = datetime(2026, 11, 1)
    db.add_all([Solution(name="옛", stage="intake", created_at=old), Solution(name="새", stage="intake", created_at=datetime.now())])
    db.commit()
    page = user_client.get("/").text
    cell = re.search(r'<a class="flow-cell"[^>]*data-stage="intake".*?</a>', page, re.S).group(0)
    assert "이번 주 +1" in cell and "<b>2</b>" in cell
    assert "이번 주" not in re.search(r'data-stage="tapping".*?</a>', page, re.S).group(0)


def _count_queries(app, fn):
    n = []
    hook = lambda *a, **k: n.append(1)  # noqa: E731
    event.listen(app.state.engine, "before_cursor_execute", hook)
    try:
        fn()
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    return len(n)


def test_home_query_count_is_bounded(app, user_client, db, world):
    user_client.get("/")  # 워밍업
    small = _count_queries(app, lambda: user_client.get("/"))
    kim = db.query(__import__("app.models", fromlist=["User"]).User).filter_by(username="kim").one()
    for i in range(40):
        a = Activity(raw_text="x", title=f"활동{i}", date=TODAY - timedelta(days=i))
        db.add(a)
        db.flush()
        db.add(FollowUp(activity_id=a.id, task=f"일{i}", due=TODAY + timedelta(days=i % 10), position=0))
        db.add(Solution(name=f"S{i}", stage="boxed"))
        db.add(Intake(note=f"B{i}", status="ready", created_by_id=kim.id))
    db.commit()
    large = _count_queries(app, lambda: user_client.get("/"))
    assert large <= small + 4 and large <= 30, (small, large)  # 조건부 질의(묶음 파일 수 등)만 더해지고 기록 수와는 무관


def test_home_empty_database_renders_intentionally(user_client):
    page = user_client.get("/").text
    assert "급한 일은 없습니다" in page and "처리할 후속 조치가 없습니다." in page and "<b>0</b>" in page
    assert "소개서·녹취록·사진을 올리면" in page


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href", "").startswith("/"):
            self.hrefs.add(a["href"])


def test_every_link_on_home_resolves(user_client, admin_client, db, world, monkeypatch):
    kim = db.query(__import__("app.models", fromlist=["User"]).User).filter_by(username="kim").one()
    db.add(Intake(note="내 묶음", status="ready", created_by_id=kim.id))
    db.commit()
    p = _Links()
    p.feed(user_client.get("/").text)
    assert len(p.hrefs) > 20
    for href in sorted(p.hrefs):
        if href in ("/logout",):
            continue
        r = user_client.get(href, follow_redirects=False)
        assert r.status_code in (200, 303), (href, r.status_code)
        if r.status_code == 303:
            assert r.headers["location"] != href
    # 관리자 쪽 링크도
    p2 = _Links()
    p2.feed(admin_client.get("/").text)
    for href in sorted(p2.hrefs):
        assert admin_client.get(href, follow_redirects=False).status_code in (200, 303), href

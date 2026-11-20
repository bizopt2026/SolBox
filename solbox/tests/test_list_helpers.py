"""목록 화면 도우미: 연결 수(N+1 없음), 최근 활동 날짜, 상대 기한 글, 구분 탭, 쪽 범위, 온톨로지 설명."""
from datetime import date

import pytest
from sqlalchemy import event

from app import ontology, relations, ui
from app.models import Activity, Customer, Opportunity, Proposal, Solution
from app.routers._common import paginate
from tests import relfix
from tests.conftest import make_user


@pytest.fixture
def world(db):
    return relfix.build(db, owner=make_user(db, "kim", name="kim"))


def _count_queries(app, fn):
    n = []
    hook = lambda *a, **k: n.append(1)  # noqa: E731
    event.listen(app.state.engine, "before_cursor_execute", hook)
    try:
        fn()
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    return len(n)


# ---- 연결 수 ----

def test_link_counts_match_related_totals(db, world):
    ids = [world.s_hvac.id, world.s_sensor.id, world.s_solar.id, world.s_light.id, world.s_cold.id]
    for key, to in (("D7", "customer"), ("R14", "proposal"), ("R28", "opportunity")):
        got = relations.link_counts(db, "solution", ids, key, to)
        for sid in ids:
            obj = db.get(Solution, sid)
            expect = len(relations.path_query(db, obj, [key] if key.startswith("D") else [f"{key}:{to}"]))
            assert got.get(sid, 0) == expect, (key, sid)
    assert relations.link_counts(db, "solution", ids, "R14", "proposal")[world.s_hvac.id] == 1
    assert relations.link_counts(db, "customer", [world.c_hanbit.id], "R2", "project") == {world.c_hanbit.id: 1}


def test_link_counts_empty_and_unlinked(db, world):
    assert relations.link_counts(db, "solution", [], "R14", "proposal") == {}
    got = relations.link_counts(db, "partner", [world.p_green.id, world.p_sense.id], "R1", "solution")
    assert got == {world.p_green.id: 2, world.p_sense.id: 2}
    assert relations.link_counts(db, "customer", [world.c_mirae.id], "R2", "project").get(world.c_mirae.id, 0) == 0


def test_link_counts_derived_with_where(db, world):
    # 남은 후속 조치(D6): 한빛병원은 견적 보내기·제안서 일정 잡기 (자료 공유는 완료)
    got = relations.link_counts(db, "customer", [world.c_hanbit.id, world.c_daesung.id], "D6", "follow_up")
    assert got == {world.c_hanbit.id: 2, world.c_daesung.id: 2}


def test_latest_activity_dates(db, world):
    ids = [world.s_hvac.id, world.s_solar.id]
    got = relations.latest_activity_dates(db, "solution", ids)
    assert got == {world.s_hvac.id: date(2026, 11, 14)}  # 태양광은 연결된 활동이 없다
    got = relations.latest_activity_dates(db, "customer", [world.c_green.id, world.c_daesung.id])
    assert got == {world.c_daesung.id: date(2026, 11, 11)}  # 날짜 없는 활동만 있으면 없음
    assert relations.latest_activity_dates(db, "project", [world.pr_hanbit.id]) == {world.pr_hanbit.id: date(2026, 11, 14)}


def test_list_links_definition_is_traversable():
    for code, specs in ontology.LIST_LINKS.items():
        for text, key, to in specs:
            assert text and (key in ontology.DERIVED_BY_KEY or relations._edge(key, code, to))
    for code, key in ontology.LIST_LATEST_ACTIVITY.items():
        assert relations._edge(key, code, "activity")


def test_link_counts_query_bound_does_not_grow_with_rows(app, db, world):
    def run(ids):
        for code, specs in (("solution", ontology.LIST_LINKS["solution"]),):
            for _text, key, to in specs:
                relations.link_counts(db, code, ids, key, to)
        relations.latest_activity_dates(db, "solution", ids)
    few = [world.s_hvac.id, world.s_sensor.id]
    db.expire_all()
    small = _count_queries(app, lambda: run(few))
    many = []
    for n in range(80):
        s = Solution(name=f"S{n}", stage="boxed")
        c = Customer(name=f"C{n}")
        db.add_all([s, c])
        db.flush()
        a = Activity(kind="meeting", date=date(2026, 9, 1), title=f"A{n}", raw_text="r")
        p = Proposal(customer_id=c.id, title=f"P{n}")
        db.add_all([a, p])
        db.flush()
        a.solutions, a.customers = [s], [c]
        p.solutions = [s]
        many.append(s.id)
    db.commit()
    db.expire_all()
    large = _count_queries(app, lambda: run(many))
    assert large <= small + 1 and large < 15, (small, large)


# ---- 상대 기한 ----

TODAY = date(2026, 11, 20)


@pytest.mark.parametrize("due,text,tone", [
    (date(2026, 11, 17), "3일 지남", "bad"),
    (date(2026, 11, 19), "1일 지남", "bad"),
    (TODAY, "오늘", "today"),
    (date(2026, 11, 21), "내일", ""),
    (date(2026, 11, 24), "11월 24일", ""),
    (date(2027, 1, 5), "2027년 1월 5일", ""),
    (date(2025, 12, 31), "324일 지남", "bad"),  # 오래 지난 것도 며칠 지났는지
])
def test_relative_due(due, text, tone):
    r = ui.relative_due(due, TODAY, done=False)
    assert (r.text, r.tone) == (text, tone)


def test_relative_due_done_and_none():
    assert ui.relative_due(date(2026, 11, 1), TODAY, done=True).tone == ""
    assert ui.relative_due(date(2026, 11, 1), TODAY, done=True).text == "11월 1일"
    assert ui.relative_due(None, TODAY).text == "기한 없음"


def test_today_line_and_short_date():
    assert ui.today_line(date(2026, 11, 20)) == "11월 20일 금요일"
    assert ui.short_date(date(2026, 11, 18), TODAY) == "11월 18일"
    assert ui.short_date(date(2025, 3, 2), TODAY) == "2025년 3월 2일"
    assert ui.short_date(None, TODAY) == ""


# ---- 구분 탭, 쪽 범위 ----

class _Req:
    def __init__(self, path, query):
        from starlette.datastructures import QueryParams
        self.url = type("U", (), {"path": path})()
        self.query_params = QueryParams(query)


def test_seg_tabs_keep_other_params_and_drop_page():
    req = _Req("/solutions", "q=공조&customer_id=3&stage=boxed&page=2")
    tabs = ui.seg_tabs(req, "stage", [("intake", "아이템 접수"), ("boxed", "박스 등록")], {"intake": 4, "boxed": 5}, "boxed", total=9)
    assert [(t.label, t.count, t.active) for t in tabs] == [("전체", 9, False), ("아이템 접수", 4, False), ("박스 등록", 5, True)]
    assert tabs[0].href == "/solutions?q=%EA%B3%B5%EC%A1%B0&customer_id=3"
    assert tabs[1].href == "/solutions?q=%EA%B3%B5%EC%A1%B0&customer_id=3&stage=intake"
    assert tabs[2].href.endswith("stage=boxed")
    assert ui.seg_tabs(req, "stage", [("a", "A")], {}, "", total=0)[0].active is True


def test_page_range():
    p = paginate(list(range(120)), "2")
    assert (p.first, p.last) == (51, 100)
    assert (paginate(list(range(120)), "3").first, paginate(list(range(120)), "3").last) == (101, 120)
    assert (paginate([], "1").first, paginate([], "1").last) == (0, 0)


# ---- 온톨로지 ----

def test_core_types_have_description_and_follow_up_has_list():
    for code in ("solution", "partner", "customer", "project", "opportunity", "bm", "activity", "proposal", "intake", "research_run", "ai_job"):
        assert ontology.TYPES[code].description, code
    assert ontology.TYPES["follow_up"].list_url == "/follow-ups"

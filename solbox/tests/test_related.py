"""단위 5: 공통 관계 조회(related / path_query), 따라가서 얻는 관계, 목록 걸러 보기, 연결 구역, 지식 사본의 "## 연결", 확인 질문 5개."""
import re
from datetime import date

import pytest
from sqlalchemy import event, select

from app import ontology, relations
from app.knowledge import KnowledgeMirror
from app.models import Activity, Customer, Opportunity, Proposal, Solution, User
from tests import relfix
from tests.conftest import make_user


@pytest.fixture
def kim(db):
    return make_user(db, "kim", name="kim")


@pytest.fixture
def world(db, kim):
    return relfix.build(db, owner=kim)


def names(objs):
    return sorted(getattr(o, "name", None) or getattr(o, "title", None) or getattr(o, "task", None) or getattr(o, "original_name", None) or str(o.id) for o in objs)


def pq(db, start, *keys):
    return relations.path_query(db, start, list(keys))


# ---- 1. 저장된 관계: 정의(레지스트리)가 따라가기를 모두 지원한다 ----

def _traversable():
    return [r.key for r in ontology.RELATIONS if r.key not in ontology.UNTRAVERSED]


def test_untraversed_relations_have_reasons():
    assert set(ontology.UNTRAVERSED) <= {r.key for r in ontology.RELATIONS}
    assert all(len(reason) > 10 for reason in ontology.UNTRAVERSED.values())


@pytest.mark.parametrize("rel", [r.key for r in ontology.RELATIONS])
def test_every_stored_relation_is_traversable_both_ways(rel):
    """새 관계를 정의했는데 따라가기가 없으면 이 시험이 깨진다 (양방향 걸음과 보는 이름이 모두 있어야 한다)."""
    if rel in ontology.UNTRAVERSED:
        pytest.skip(ontology.UNTRAVERSED[rel])
    r = next(r for r in ontology.RELATIONS if r.key == rel)
    edges = {(e.src, e.dst) for e in relations.edges_for(rel)}
    models = lambda codes: {relations._model_type(c) for c in codes} - {None}  # noqa: E731
    assert edges, f"{rel}: 따라가기 없음"
    for s in models(r.source):
        for d in models(r.target):
            assert (s, d) in edges and (d, s) in edges, f"{rel}: {s}<->{d} 양방향이 아니다"
    for e in relations.edges_for(rel):
        assert (rel, e.src, e.dst) in ontology.VIEW_LABELS, f"{rel} {e.src}->{e.dst} 보는 이름 없음"


def test_json_backed_relations_are_marked():
    for key in ("R15", "R18", "R21"):
        assert all(e.storage in ("json", "text") for e in relations.edges_for(key))
    assert {e.storage for e in relations.edges_for("R1")} == {"fk"}
    assert {e.storage for e in relations.edges_for("R4")} == {"link"}


# 관계 -> (시작, 앞으로 가서 닿는 것, 도착에서 거꾸로 가서 닿는 것의 기대). w 의 이름으로 적는다.
EXPECT = {
    "R1": [("p_green", "R1", ["s_hvac", "s_solar"]), ("s_hvac", "R1", ["p_green"])],
    "R2": [("c_hanbit", "R2", ["pr_hanbit"]), ("pr_hanbit", "R2", ["c_hanbit"])],
    "R3": [("s_hvac", "R3", ["file"]), ("file", "R3", ["s_hvac"])],
    "R4": [("a_kick", "R4", ["s_hvac", "s_light"]), ("s_hvac", "R4", ["a_kick", "a_hanbit2"])],
    "R5": [("a_kick", "R5", ["c_hanbit"]), ("c_hanbit", "R5", ["a_kick", "a_hanbit2"])],
    "R6": [("a_daesung", "R6:project", ["pr_daesung"]), ("a_daesung", "R6:opportunity", ["o_daesung"]),
           ("pr_daesung", "R6", ["a_daesung"]), ("o_daesung", "R6", ["a_daesung"])],
    "R7": [("a_kick", "R7", ["p_green"]), ("p_green", "R7", ["a_kick", "a_green"])],
    "R8": [("a_kick", "R8", ["fu_quote", "fu_share"]), ("fu_quote", "R8", ["a_kick"])],
    "R9": [("a_kick", "R9", ["n_kick"]), ("n_kick", "R9", ["a_kick"])],
    "R10": [("n_kick", "R10", ["c_hanbit"]), ("c_hanbit", "R10", ["n_kick", "n_hanbit2"])],
    "R11": [("fu_quote", "R11", ["kim"]), ("kim", "R11", ["fu_quote"])],
    "R12": [("pro_hanbit", "R12", ["c_hanbit"]), ("c_hanbit", "R12", ["pro_hanbit"])],
    "R13": [("pro_hanbit", "R13", ["pr_hanbit"]), ("pr_hanbit", "R13", ["pro_hanbit"])],
    "R14": [("pro_hanbit", "R14", ["s_hvac", "s_solar"]), ("s_hvac", "R14", ["pro_hanbit"])],
    "R15": [("pro_hanbit", "R15", ["s_light"]), ("s_light", "R15", ["pro_hanbit"])],
    "R18": [("report", "R18", ["s_hvac", "s_sensor"]), ("s_hvac", "R18", ["report"])],
    "R19": [("report", "R19", ["s_solar"]), ("s_solar", "R19", ["report"])],
    "R20": [("intake", "R20", ["ifile"]), ("ifile", "R20", ["intake"])],
    "R21": [("ifile", "R21", ["a_mirae"]), ("a_mirae", "R21", ["ifile"])],
    "R22": [("intake", "R22:activity", ["a_hanbit2", "a_mirae"]), ("intake", "R22:solution", ["s_light"]), ("a_mirae", "R22", ["intake"])],
    "R23": [("run", "R23", ["report"]), ("report", "R23", ["run"])],
    "R24": [("kim", "R24:customer", ["c_hanbit"]), ("c_hanbit", "R24", ["kim"])],
    "R25": [("p_green", "R25", ["c_green"]), ("c_green", "R25", ["p_green"])],
    "R26": [("n_mirae", "R26", ["o_mirae"]), ("o_mirae", "R26", ["n_mirae"])],
    "R27": [("o_hanbit", "R27", ["c_hanbit"]), ("c_hanbit", "R27", ["o_hanbit"])],
    "R28": [("o_hanbit", "R28", ["s_hvac", "s_light"]), ("s_hvac", "R28", ["o_hanbit"])],
    "R29": [("pro_hanbit", "R29", ["o_hanbit"]), ("o_hanbit", "R29", ["pro_hanbit"])],
    "R30": [("o_daesung", "R30", ["pr_daesung"]), ("pr_daesung", "R30", ["o_daesung"])],
    "R31": [("pr_hanbit", "R31", ["bm_om"]), ("bm_om", "R31", ["pr_hanbit"])],
    "R32": [("o_hanbit", "R32", ["bm_energy"]), ("bm_energy", "R32", ["o_hanbit"])],
    "R33": [("bm_energy", "R33", ["s_hvac", "s_solar", "s_light"]), ("s_hvac", "R33", ["bm_energy"])],
}


def test_expectation_table_covers_every_traversable_relation():
    assert set(EXPECT) == set(_traversable())


def _obj(w, db, kim, name):
    special = {"kim": kim, "run": lambda: w.report.run, "intake": lambda: w.intake,
               "fu_quote": lambda: w.a_kick.follow_ups[0], "fu_share": lambda: w.a_kick.follow_ups[1],
               "n_kick": lambda: w.a_kick.needs[0], "n_hanbit2": lambda: w.a_hanbit2.needs[0], "n_mirae": lambda: w.a_mirae.needs[0]}
    if name in special:
        v = special[name]
        return v() if callable(v) else v
    return getattr(w, name)


@pytest.mark.parametrize("rel,case", [(rel, c) for rel, cases in EXPECT.items() for c in cases])
def test_registry_traversal_follows_every_relation_both_ways(db, kim, world, rel, case):
    start, key, expected = case
    got = relations.path_query(db, _obj(world, db, kim, start), [key])
    assert sorted(o.id for o in got) == sorted(_obj(world, db, kim, e).id for e in expected), (rel, start, key)


def test_path_query_multi_hop_and_empty(db, world):
    got = pq(db, world.p_green, "R1", "R14", "R12")
    assert names(got) == ["한빛병원"]
    assert pq(db, world.c_mirae, "R25") == []  # 같은 회사 파트너 없음
    assert pq(db, world.p_sense, "R25", "R2") == []
    with pytest.raises(LookupError):
        pq(db, world.p_green, "R99")


# ---- 2. 따라가서 얻는 관계 ----

def derived(db, obj, key):
    return {g.key: g for g in ontology.related(db, obj)}[key]


def test_derived_definitions_are_consistent_with_stored_relations():
    """경로의 방향·유형이 저장된 관계 정의와 맞는지, 모든 키·보는 이름이 있는지."""
    assert len({d.key for d in ontology.DERIVED}) == len(ontology.DERIVED)
    for d in ontology.DERIVED:
        assert d.label
        for steps in relations._flatten(d.key):
            cur = d.source
            for step in steps:
                edge = relations._edge(step.rel, cur, step.to)
                rel = next(r for r in ontology.RELATIONS if r.key == step.rel)
                assert (cur in {relations._model_type(c) for c in rel.source}) != step.reverse, (d.key, step)
                cur = edge.dst
            assert cur == d.target, d.key


def test_d1_spoken_solutions_of_customer(db, world):
    g = derived(db, world.c_hanbit, "D1")
    assert sorted(i.label for i in g.items) == ["AI 공조 최적제어", "스마트 조명 제어"]
    hvac = next(i for i in g.items if i.label == "AI 공조 최적제어")
    assert hvac.via == "활동 기록 2건을 거쳐"  # 중복 없이 한 번, 거친 활동은 2건


def test_d2_proposed_solutions_of_customer(db, world):
    assert names(o.obj for o in derived(db, world.c_hanbit, "D2").items) == ["AI 공조 최적제어", "태양광 자가소비 진단"]


def test_d3_proposals_with_partner_solutions(db, world):
    g = derived(db, world.p_green, "D3")
    assert [i.label for i in g.items] == ["한빛 에너지 절감 제안"]


def test_d4_customers_reached_by_partner_solutions(db, world):
    g = derived(db, world.p_green, "D4")
    assert sorted(i.label for i in g.items) == ["한빛병원"]
    g2 = derived(db, world.p_sense, "D4")
    assert sorted(i.label for i in g2.items) == ["대성물류센터", "미래제조"]  # 센서: 활동·제안·영업기회 어느 쪽으로든 닿은 고객
    assert all("을 거쳐" in i.via for i in g2.items)


def test_d5_projects_and_opportunities_where_solution_was_discussed(db, world):
    assert names(o.obj for o in derived(db, world.s_hvac, "D5").items) == ["한빛병원 설비 유지보수"]
    assert names(o.obj for o in derived(db, world.s_cold, "D5b").items) == ["대성 온도 관제"]
    stored = {g.key: g for g in ontology.related(db, world.s_hvac)}["R28:opportunity"]  # 검토 중인 영업기회는 저장된 관계
    assert [i.label for i in stored.items] == ["한빛 전력 절감"]


def test_d6_remaining_follow_ups_of_customer(db, world):
    g = derived(db, world.c_daesung, "D6")
    assert sorted(i.label for i in g.items) == ["계약 검토", "현장 방문 일정"]  # 완료한 것(회의록 정리)은 빠진다
    assert all(not i.done for i in g.items)
    assert g.items[0].label == "현장 방문 일정"  # 기한이 빠른 순


def test_d7_customers_interested_in_solution(db, world):
    g = derived(db, world.s_hvac, "D7")
    assert sorted(i.label for i in g.items) == ["한빛병원"]
    sensor = derived(db, world.s_sensor, "D7")
    assert sorted(i.label for i in sensor.items) == ["대성물류센터", "미래제조"]


def test_d8_customers_of_projects_following_bm(db, world):
    assert names(o.obj for o in derived(db, world.bm_energy, "D8").items) == ["대성물류센터"]


def test_d9_activities_where_opportunity_needs_came_from(db, world):
    assert names(o.obj for o in derived(db, world.o_hanbit, "D9").items) == ["한빛 킥오프"]


def test_same_company_hops(db, world):
    assert names(o.obj for o in derived(db, world.p_green, "D10").items) == ["그린 사옥 에너지 진단"]
    assert {g.key for g in ontology.related(db, world.p_green)} >= {"D10"}
    assert names(o.obj for o in derived(db, world.c_green, "D13").items) == ["AI 공조 최적제어", "태양광 자가소비 진단"]
    assert pq(db, world.p_green, "D11") == []  # 같은 회사 고객에게 영업기회가 없으면 빈 결과
    assert "D11" not in {g.key for g in ontology.related(db, world.p_green)}


def test_research_suggestions_mentioning_solution_is_json_backed(db, world):
    g = {g.key: g for g in ontology.related(db, world.s_hvac)}["R18:research_report"]
    assert g.storage == "json" and g.target_type == "suggestion"
    assert [i.label for i in g.items] == ["공조 제어 확산"]
    assert g.items[0].url.endswith("#suggestions")
    assert "R18:research_report" not in {g.key for g in ontology.related(db, world.s_cold)}


# ---- 3. 중복 없음, 상한, 건수, 모두 보기 ----

def test_cap_total_and_more_url(db, world):
    c = world.c_hanbit
    for n in range(14):
        a = Activity(kind="meeting", date=date(2026, 10, 1 + n), title=f"활동 {n}", raw_text="r")
        db.add(a)
        db.flush()
        a.customers = [c]
        a.solutions = [world.s_hvac]
    db.commit()
    g = {g.key: g for g in ontology.related(db, c)}["R5:activity"]
    assert len(g.items) == 10 and g.total == 16 and g.hidden == 6
    assert g.more_url == f"/activities?customer_id={c.id}"
    assert g.items[0].note >= g.items[-1].note  # 최신순
    small = ontology.related(db, c, limit=3)
    assert len({id(i) for g in small for i in g.items}) and {g.key: g for g in small}["R5:activity"].total == 16
    # 따라가서 얻는 그룹은 거쳐 온 수만큼만 세고 한 솔루션은 한 번만 나온다
    d1 = {g.key: g for g in ontology.related(db, c)}["D1"]
    assert d1.total == 2 and sorted(i.label for i in d1.items) == ["AI 공조 최적제어", "스마트 조명 제어"]
    assert next(i for i in d1.items if i.label == "AI 공조 최적제어").via == "활동 기록 16건을 거쳐"


def test_empty_groups_are_not_returned(db, world):
    keys = {g.key for g in ontology.related(db, world.c_mirae)}
    assert "R2:project" not in keys and "D1" in keys


def test_quiet_relation_is_not_listed_but_traversable(db, world):
    assert all(g.relation != "R24" for g in ontology.related(db, world.c_hanbit))
    assert len(pq(db, world.c_hanbit, "R24")) == 1


# ---- 4. 올리기 묶음 보임 ----

def _group(db, obj, key, viewer):
    return {g.key: g for g in ontology.related(db, obj, viewer=viewer)}.get(key)


def test_intake_backed_groups_respect_viewer(db, world, kim):
    other = make_user(db, "lee", name="lee")
    admin = make_user(db, "boss", role="admin", name="boss")
    key = "R22:intake"
    assert _group(db, world.a_mirae, key, None) is None
    assert _group(db, world.a_mirae, key, other) is None
    assert [i.label for i in _group(db, world.a_mirae, key, kim).items] == [f"올리기 묶음 {world.intake.id}번"]
    assert _group(db, world.a_mirae, key, admin) is not None
    files = "R21:intake_file"
    assert _group(db, world.a_mirae, files, other) is None and _group(db, world.a_mirae, files, kim) is not None
    # 묶음 자신: 열 수 없는 사람에게는 아무것도
    assert ontology.related(db, world.intake, viewer=other) == []
    assert {g.key for g in ontology.related(db, world.intake, viewer=kim)} >= {"R22:activity", "R22:solution", "R20:intake_file"}
    assert ontology.related(db, world.intake) == []


# ---- 5. 목록 걸러 보기 ----

def _count_rows(html):
    return len(re.findall(r'<a href="/(?:activities|proposals|opportunities|projects|solutions)/\d+"', html))


def test_list_filters_match_group_totals(admin_client, db, world):
    for (list_type, param), (owner, key) in ontology.LIST_FILTERS.items():
        owner_obj = _owner(world, owner, key)
        if owner_obj is None:
            continue
        ids = relations.filter_ids(db, list_type, param, owner_obj.id)
        group = next((g for g in ontology.related(db, owner_obj, limit=1000) if g.key.split(":")[0] == key and g.target_type == list_type), None)
        assert sorted(ids) == sorted(_group_ids(db, owner_obj, key, list_type)), (list_type, param)
        if group is not None:
            assert group.total == len(ids)
            assert group.more_url is None or group.more_url == f"{ontology.TYPES[list_type].list_url}?{param}={owner_obj.id}"
        r = admin_client.get(f"{ontology.TYPES[list_type].list_url}?{param}={owner_obj.id}")
        assert r.status_code == 200
        assert _count_rows(r.text) == len(ids), (list_type, param, owner_obj.id)


def _owner(w, owner, key):
    return {"customer": w.c_hanbit, "solution": w.s_hvac, "partner": w.p_green, "project": w.pr_hanbit, "opportunity": w.o_hanbit, "bm": w.bm_energy}[owner]


def _group_ids(db, owner_obj, key, list_type):
    if key.startswith("D"):
        return [o.id for o in relations.path_query(db, owner_obj, [key])]
    return [o.id for o in relations.path_query(db, owner_obj, [f"{key}:{list_type}"])]


@pytest.mark.parametrize("list_type,param", sorted(ontology.LIST_FILTERS))
def test_list_filter_bad_values_give_empty_list_not_error(admin_client, list_type, param):
    base = ontology.TYPES[list_type].list_url
    for bad in ("999999", "abc", "-1", "1.5", "99999999999999999999999"):
        r = admin_client.get(f"{base}?{param}={bad}")
        assert r.status_code == 200, (base, param, bad)
        assert _count_rows(r.text) == 0
    assert admin_client.get(f"{base}?{param}=").status_code == 200


def test_filtered_list_shows_note_and_clear_link(admin_client, world):
    r = admin_client.get(f"/activities?customer_id={world.c_hanbit.id}")
    assert "한빛병원" in r.text and "한빛 킥오프" in r.text and "한빛 후속" in r.text and "대성 태핑" not in r.text
    assert 'href="/activities"' in r.text  # 걸러 보기 해제


# ---- 6. N+1 없음 ----

def _count_queries(app, fn):
    engine = app.state.engine
    n = []
    def hook(*a, **k):
        n.append(1)
    event.listen(engine, "before_cursor_execute", hook)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", hook)
    return len(n)


def test_related_query_count_does_not_grow_with_links(app, db, world):
    types = (world.c_hanbit, world.s_hvac, world.p_green, world.pr_hanbit, world.o_hanbit, world.bm_energy, world.a_kick, world.pro_hanbit)

    def run():
        for o in types:
            ontology.related(db, o, viewer=None)
    db.expire_all()
    small = _count_queries(app, run)
    # 연결을 크게 늘린다
    for n in range(60):
        c = Customer(name=f"고객{n}")
        s = Solution(name=f"솔루션{n}", partner_id=world.p_green.id, stage="boxed")
        db.add_all([c, s])
        db.flush()
        o = Opportunity(name=f"기회{n}", customer_id=world.c_hanbit.id, bm_id=world.bm_energy.id, stage="discovery")
        p = Proposal(customer_id=world.c_hanbit.id, title=f"제안{n}", project_id=world.pr_hanbit.id, opportunity_id=None)
        a = Activity(kind="meeting", date=date(2026, 9, 1), title=f"활동{n}", raw_text="r")
        db.add_all([o, p, a])
        db.flush()
        a.customers, a.solutions, a.partners, a.projects = [world.c_hanbit, c], [world.s_hvac, s], [world.p_green], [world.pr_hanbit]
        p.solutions = [world.s_hvac, s]
        o.solutions = [world.s_hvac, s]
        world.bm_energy.solutions.append(s)
    db.commit()
    db.expire_all()
    large = _count_queries(app, run)
    assert large <= small + 4, (small, large)
    assert large < 400


# ---- 7. 화면: 공통 연결 구역 ----

def test_customer_page_shows_shared_related_card(admin_client, world):
    html = admin_client.get(f"/customers/{world.c_hanbit.id}").text
    assert 'class="card related stack"' in html
    for text in ("이야기된 솔루션", "제안된 솔루션", "남은 후속 조치", "고객 니즈", "건너서 연결된 것", "견적 보내기", "AI 공조 최적제어", "전기요금 절감 방안이 필요하다",
                 "한빛 전력 절감", "한빛 에너지 절감 제안", "한빛 킥오프", "한빛병원 설비 유지보수", "활동 기록 2건을 거쳐"):
        assert text in html, text
    assert "자료 공유" not in html  # 완료한 후속 조치는 남은 것이 아니다
    html = admin_client.get(f"/customers/{world.c_green.id}").text
    assert "같은 회사 파트너가 공급하는 솔루션" in html and "태양광 자가소비 진단" in html
    assert f'href="/partners/{world.p_green.id}"' in html


def test_customer_needs_show_opportunity_state(admin_client, world):
    html = admin_client.get(f"/customers/{world.c_hanbit.id}").text
    assert "한빛 전력 절감 · 제안 중" in html  # 니즈가 묶인 영업기회와 단계
    assert "야간 조명 전력 줄이기" in html


def test_partner_page(admin_client, world):
    html = admin_client.get(f"/partners/{world.p_green.id}").text
    assert 'class="card related stack"' in html
    for text in ("솔루션이 들어간 제안 건", "한빛 에너지 절감 제안", "솔루션이 닿은 고객", "한빛병원", "AI 공조 최적제어", "그린 파트너 미팅", "한빛 킥오프",
                 "같은 회사 고객의 프로젝트"):
        assert text in html, text


def test_solution_page(admin_client, world):
    html = admin_client.get(f"/solutions/{world.s_hvac.id}").text
    for text in ("관심을 보인 고객", "한빛병원", "이야기된 프로젝트", "한빛병원 설비 유지보수", "이 솔루션을 검토한 영업기회", "한빛 전력 절감", "언급한 리서치 제안",
                 "공조 제어 확산", "이 솔루션이 들어간 제안 건", "한빛 에너지 절감 제안", "한빛 킥오프", "에너지 절감 구독형"):
        assert text in html, text
    assert html.count("소개서.pdf") >= 1  # 자료 구역은 그대로


def test_other_detail_pages_render_shared_card(admin_client, world):
    for url, texts in {
        f"/projects/{world.pr_hanbit.id}": ["한빛 에너지 절감 제안", "한빛 킥오프", "한빛 후속"],
        f"/opportunities/{world.o_hanbit.id}": ["전기요금 절감 방안이 필요하다", "AI 공조 최적제어", "한빛 에너지 절감 제안", "니즈가 나온 활동 기록", "한빛 킥오프"],
        f"/bms/{world.bm_energy.id}": ["AI 공조 최적제어", "대성 냉동창고 개선", "한빛 전력 절감", "따르는 프로젝트의 고객", "대성물류센터"],
        f"/activities/{world.a_kick.id}": ["AI 공조 최적제어", "스마트 조명 제어", "한빛병원", "그린에너지테크", "한빛병원 설비 유지보수"],
        f"/proposals/{world.pro_hanbit.id}": ["AI 공조 최적제어", "태양광 자가소비 진단"],
    }.items():
        html = admin_client.get(url).text
        assert 'class="card related stack"' in html or "main-rel" in html, url  # 연결 그룹은 왼쪽 카드(main-rel) 또는 옆 칸 카드
        for t in texts:
            assert t in html, (url, t)
    html = admin_client.get(f"/activities/{world.a_daesung.id}").text
    assert "대성 온도 관제" in html and "대성 냉동창고 개선" in html


def test_derived_section_collapses_when_many(admin_client, db, world):
    html = admin_client.get(f"/customers/{world.c_hanbit.id}").text
    assert "related-derived" not in html and "건너서 연결된 것" in html  # 세 그룹까지는 펼쳐 둔다 (남은 후속 조치는 왼쪽 카드로 올라갔다)
    world.p_sense.customer_id = world.c_hanbit.id  # 같은 회사 파트너가 생겨 D13 이 더해진다 (옆 칸 네 그룹)
    db.commit()
    html = admin_client.get(f"/customers/{world.c_hanbit.id}").text
    assert "같은 회사 파트너가 공급하는 솔루션" in html and "related-derived" not in html  # 옆 칸에는 D1·D2·D13 세 그룹
    html = admin_client.get(f"/partners/{world.p_sense.id}").text  # 파트너 쪽은 같은 회사 고객 덕에 그룹이 더 많다
    assert re.search(r"<details class=\"related-derived\"", html)
    html = admin_client.get(f"/activities/{world.a_kick.id}").text
    assert "건너서 연결된 것" not in html  # 활동 기록은 따라가서 얻는 관계가 없다


def test_detail_pages_do_not_leak_intake_to_other_users(app, db, world):
    from fastapi.testclient import TestClient

    make_user(db, "lee", name="lee")
    c = TestClient(app)
    assert c.post("/login", data={"username": "lee", "password": "pw1234"}, follow_redirects=False).status_code == 303
    assert "올리기 묶음" not in c.get(f"/activities/{world.a_mirae.id}").text.split("<main ")[1].split("유래")[0]


# ---- 8. 지식 사본 ----

def test_mirror_cards_have_connections_section(db, world, settings):
    mirror = KnowledgeMirror(settings.knowledge_dir)
    mirror.rebuild_all(db, settings.uploads_dir)
    text = mirror.path_for(world.c_hanbit).read_text(encoding="utf-8")
    assert "\n## 연결\n" in text
    section = text.split("\n## 연결\n")[1].split("\n## ")[0]
    assert f"({mirror.rel_path(world.s_hvac)})" in section and "이야기된 솔루션" in section and "남은 후속 조치" in section
    assert f"({mirror.rel_path(world.pr_hanbit)})" in section
    for obj in (world.s_hvac, world.p_green, world.pr_hanbit, world.o_hanbit, world.bm_energy):
        card = mirror.path_for(obj).read_text(encoding="utf-8")
        assert "\n## 연결\n" in card, obj
    partner = mirror.path_for(world.p_green).read_text(encoding="utf-8").split("\n## 연결\n")[1]
    assert "솔루션이 들어간 제안 건" in partner and "솔루션이 닿은 고객" in partner and f"({mirror.rel_path(world.c_hanbit)})" in partner
    assert "올리기 묶음" not in text  # 올리기 묶음은 사본에 없다 (열 수 있는 사람만 보이는 정보)
    assert "(" in section and "추가" not in section


def test_mirror_connections_are_capped(db, world, settings):
    for n in range(12):
        a = Activity(kind="meeting", date=date(2026, 10, 1 + n), title=f"추가{n}", raw_text="r")
        db.add(a)
        db.flush()
        a.customers = [world.c_hanbit]
    db.commit()
    mirror = KnowledgeMirror(settings.knowledge_dir)
    mirror.sync(world.c_hanbit)
    section = mirror.path_for(world.c_hanbit).read_text(encoding="utf-8").split("\n## 연결\n")[1]
    line = next(l for l in section.splitlines() if l.startswith("- 활동 기록"))
    assert "외 " in line and line.count("activities/") <= 8


def test_knowledge_format_bumped_and_prompts_mention_connections():
    from app import knowledge

    assert knowledge.KNOWLEDGE_FORMAT >= 6
    import pathlib

    prompts = pathlib.Path(__file__).resolve().parent.parent / "app" / "ai" / "prompts"
    hits = [p.name for p in prompts.glob("*.md") if "## 연결" in p.read_text(encoding="utf-8")]
    assert len(hits) >= 2


def test_resync_related_includes_registry_neighbors(db, world, settings):
    from app.routers._common import resync_related

    mirror = KnowledgeMirror(settings.knowledge_dir)
    mirror.rebuild_all(db, settings.uploads_dir)
    for o in (world.p_green, world.c_green, world.s_hvac, world.bm_energy, world.o_hanbit, world.pr_hanbit):
        mirror.path_for(o).write_text("낡은 사본", encoding="utf-8")
    resync_related(db, mirror, world.s_hvac)
    for o in (world.p_green, world.bm_energy, world.o_hanbit, world.a_kick):
        assert mirror.path_for(o).read_text(encoding="utf-8") != "낡은 사본", o


# ---- 9. 확인 질문 5개 (docs/ontology.md 11장) ----

def test_q1_proposals_with_partner_solutions_and_their_customers(db, world):
    props = pq(db, world.p_green, "D3")
    assert names(props) == ["한빛 에너지 절감 제안"]
    assert names(pq(db, world.p_green, "R1", "R14", "R12")) == ["한빛병원"]
    assert names(pq(db, world.p_sense, "D3")) == ["대성 온도 관제 제안"]


def test_q2_spoken_vs_proposed_solutions_of_customer(db, world):
    spoken = {s.id for s in pq(db, world.c_hanbit, "D1")}
    proposed = {s.id for s in pq(db, world.c_hanbit, "D2")}
    assert names(db.get(Solution, i) for i in spoken - proposed) == ["스마트 조명 제어"]
    assert names(db.get(Solution, i) for i in proposed - spoken) == ["태양광 자가소비 진단"]


def test_q3_remaining_follow_ups_of_customers_interested_in_solution(db, world):
    customers = pq(db, world.s_sensor, "D7")
    assert names(customers) == ["대성물류센터", "미래제조"]
    result = {c.name: names(pq(db, c, "D6")) for c in customers}
    assert result == {"대성물류센터": ["계약 검토", "현장 방문 일정"], "미래제조": ["설비 목록 요청"]}
    assert names(pq(db, world.s_sensor, "D7", "D6")) == ["계약 검토", "설비 목록 요청", "현장 방문 일정"]


def test_q4_partners_supplying_solutions_discussed_for_project(db, world):
    partners = pq(db, world.pr_hanbit, "R6", "R4", "R1")
    assert names(partners) == ["그린에너지테크"]  # 논의된 솔루션: 공조(그린에너지테크)·조명(공급 파트너 없음)
    assert names(pq(db, world.pr_hanbit, "R6", "R4")) == ["AI 공조 최적제어", "스마트 조명 제어"]
    assert names(pq(db, world.pr_daesung, "R6", "R4", "R1")) == ["센스웍스"]


def test_q5_needs_from_latest_intake_grouped_by_customer_with_opportunity(db, world):
    intake = world.intake
    needs = pq(db, intake, "R22:activity", "R9")
    assert sorted(n.content for n in needs) == ["설비 고장 예측", "야간 조명 전력 줄이기"]
    grouped = {}
    for n in needs:
        opp = pq(db, n, "R26")
        grouped.setdefault(db.get(Customer, n.customer_id).name, []).append((n.content, names(opp)))
    assert grouped == {"미래제조": [("설비 고장 예측", ["미래제조 예지보전"])], "한빛병원": [("야간 조명 전력 줄이기", [])]}

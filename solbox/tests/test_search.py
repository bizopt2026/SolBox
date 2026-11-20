"""전체 검색: 이름·제목을 대소문자 구분 없이, 유형별로 묶어서."""
import re
from datetime import date

from app import search
from app.models import BM, Activity, Customer, Intake, Opportunity, Partner, Project, Proposal, Solution


def _world(db):
    c = Customer(name="한빛병원")
    p = Partner(name="한빛센서테크")
    s = Solution(name="AI 공조 최적제어", stage="boxed")
    db.add_all([c, p, s, BM(name="설비 관제 구독형")])
    db.flush()
    db.add_all([
        Project(name="한빛 설비 유지보수", customer_id=c.id),
        Opportunity(name="한빛 냉동창고 관제", customer_id=c.id),
        Proposal(customer_id=c.id, title="한빛 에너지 제안"),
        Activity(title="한빛 킥오프 미팅", raw_text="x", date=date(2026, 11, 17)),
    ])
    db.commit()
    return c, p, s


def test_search_requires_login(client):
    r = client.get("/search?q=한빛", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_groups_by_type_with_ontology_labels_and_detail_urls(user_client, db):
    c, p, s = _world(db)
    html = user_client.get("/search?q=한빛").text
    for label in ("고객", "파트너", "프로젝트", "영업기회", "제안 건", "활동 기록"):
        assert f'<h2>{label}' in html, label
    assert f'href="/customers/{c.id}"' in html and f'href="/partners/{p.id}"' in html
    assert "AI 공조" not in html and "설비 관제 구독형" not in html
    # 유형마다 건수
    assert re.search(r"<h2>고객 <span class=\"count\">1건</span>", html)


def test_matches_solution_and_bm_and_is_case_insensitive(user_client, db):
    _, _, s = _world(db)
    html = user_client.get("/search?q=ai 공조").text
    assert f'href="/solutions/{s.id}"' in html
    assert "<h2>BM" not in html
    assert "설비 관제 구독형" in user_client.get("/search?q=구독형").text


def test_wildcards_are_escaped(user_client, db):
    _world(db)
    db.add(Solution(name="100% 재생", stage="intake"))
    db.commit()
    html = user_client.get("/search?q=0%25").text
    assert "100% 재생" in html and "한빛" not in html
    assert "한빛" not in user_client.get("/search?q=한_").text


def test_caps_each_type_and_reports_the_total(user_client, db):
    db.add_all([Solution(name=f"공조 {i:02d}", stage="intake") for i in range(search.PER_TYPE + 4)])
    db.commit()
    html = user_client.get("/search?q=공조").text
    assert html.count('href="/solutions/') == search.PER_TYPE
    assert f'{search.PER_TYPE + 4}건' in html
    assert 'href="/solutions?q=' in html  # 목록에서 모두 보기


def test_short_and_empty_queries(user_client, db):
    _world(db)
    assert "두 글자 이상" in user_client.get("/search?q=a").text  # 영문·숫자 한 글자는 거절
    assert "한빛" not in user_client.get("/search?q=a").text
    one = user_client.get("/search?q=한").text  # 한글 한 글자는 찾는다 (D8)
    assert "두 글자 이상" not in one and "한빛" in one
    r = user_client.get("/search")
    assert r.status_code == 200 and "찾을 이름이나 제목" in r.text
    assert "두 글자 이상" not in r.text


def test_no_results_state(user_client, db):
    html = user_client.get("/search?q=없는이름").text
    assert "결과가 없습니다" in html and "없는이름" in html


def test_query_is_escaped(user_client):
    html = user_client.get("/search?q=<script>x</script>").text
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


def test_intakes_are_not_searched(user_client, db):
    db.add(Intake(note="비밀스러운묶음", status="ready", created_by_id=1))
    db.commit()
    html = user_client.get("/search?q=비밀스러운").text
    assert "결과가 없습니다" in html and "올리기 묶음" not in html.split("<main")[1]


def test_topbar_search_box_is_a_plain_get_form(user_client):
    html = user_client.get("/solutions").text
    m = re.search(r'<form[^>]*role="search"[^>]*>.*?</form>', html, re.S)
    assert m and 'method="get"' in m.group(0) and 'action="/search"' in m.group(0)
    assert 'type="search"' in m.group(0) and 'name="q"' in m.group(0) and 'id="global-search"' in m.group(0)
    assert 'value="공조"' in user_client.get("/search?q=공조").text  # 결과 화면에서는 입력칸에 검색어가 남는다


def test_slash_key_script_exists():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "global-search" in js and '"/"' in js


def test_search_module_has_a_field_per_searchable_type():
    from app import ontology

    assert set(search.SEARCH_TYPES) <= set(ontology.TYPES)
    assert "intake" not in search.SEARCH_TYPES

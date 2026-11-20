"""웨이브 3: 모든 상세 화면의 두 칸 틀(내용 + 옆 칸의 연결된 것), 제목 줄 동작 순서·삭제 권한, 다음 동작 링크, 폼 저장."""
import re

import pytest

from app.models import Activity, BM, Customer, Opportunity, Partner, Project, Solution, User


def _make(db, owner_id):
    c = Customer(name="고객A", industry="물류", created_by_id=owner_id)
    p = Partner(name="파트너A", description="센서 회사", created_by_id=owner_id)
    db.add_all([c, p])
    db.flush()
    bm = BM(name="BM-A", summary="구독형", created_by_id=owner_id)
    db.add(bm)
    db.flush()
    s = Solution(name="솔루션A", summary="한 줄", partner_id=p.id, created_by_id=owner_id)
    pr = Project(name="프로젝트A", customer_id=c.id, period="2026", status="진행", created_by_id=owner_id)
    o = Opportunity(name="기회A", customer_id=c.id, bm_id=bm.id, created_by_id=owner_id)
    a = Activity(title="활동A", raw_text="원문", created_by_id=owner_id)
    db.add_all([s, pr, o, a])
    db.commit()
    return {"/solutions": s, "/partners": p, "/customers": c, "/projects": pr, "/opportunities": o, "/bms": bm, "/activities": a}


def test_every_detail_page_has_two_column_grid_with_related_in_aside(user_client, db):
    for path, obj in _make(db, 1).items():
        html = user_client.get(f"{path}/{obj.id}").text
        assert '<div class="dgrid">' in html and '<div class="dmain">' in html, path
        aside = re.search(r'<aside class="daside".*?</aside>', html, re.S).group(0)
        assert 'class="card related stack"' in aside and "연결된 것" in aside and "기록 정보" in aside, path
        assert "작성자" in aside


def test_header_actions_respect_delete_permission(user_client, db):
    mine = _make(db, 1)  # user_client 의 사용자(kim)가 만든 기록
    from tests.conftest import make_user
    others = _make(db, make_user(db, "lee", name="lee").id)
    for path in mine:
        head = re.search(r'<header class="page-header">.*?</header>', user_client.get(f"{path}/{mine[path].id}").text, re.S).group(0)
        assert f'action="{path}/{mine[path].id}/delete"' in head and head.index("ghost danger") < head.index("수정"), path
        head = re.search(r'<header class="page-header">.*?</header>', user_client.get(f"{path}/{others[path].id}").text, re.S).group(0)
        assert "/delete" not in head and "수정" in head, path


def test_primary_actions_link_to_working_routes(user_client, db):
    objs = _make(db, 1)
    expected = {"/solutions": "/proposals/new", "/customers": "/opportunities/new?customer_id=", "/projects": "/proposals/new?customer_id=",
                "/partners": "/solutions/new?partner_id=", "/opportunities": "/proposals/new?customer_id="}
    for path, prefix in expected.items():
        html = user_client.get(f"{path}/{objs[path].id}").text
        head = re.search(r'<header class="page-header">.*?</header>', html, re.S).group(0)
        href = re.search(r'<a class="button primary" href="([^"]+)"', head).group(1).replace("&amp;", "&")
        assert href.startswith(prefix), (path, href)
        assert user_client.get(href).status_code == 200, href
    for path in ("/bms", "/activities"):  # 자연스러운 다음 동작이 없다
        head = re.search(r'<header class="page-header">.*?</header>', user_client.get(f"{path}/{objs[path].id}").text, re.S).group(0)
        assert "button primary" not in head


def test_won_opportunity_primary_is_convert_and_works(user_client, db):
    objs = _make(db, 1)
    o = objs["/opportunities"]
    o.stage = "won"
    db.commit()
    head = re.search(r'<header class="page-header">.*?</header>', user_client.get(f"/opportunities/{o.id}").text, re.S).group(0)
    assert f'class="button primary" href="/opportunities/{o.id}/convert"' in head
    assert user_client.get(f"/opportunities/{o.id}/convert").status_code == 200


def test_partner_prefill_selects_the_partner(user_client, db):
    p = _make(db, 1)["/partners"]
    html = user_client.get(f"/solutions/new?partner_id={p.id}").text
    assert f'<option value="{p.id}" selected>' in html
    assert 'selected>파트너A' not in user_client.get("/solutions/new?partner_id=99999").text


def test_stepper_sits_directly_under_the_header_and_still_posts(user_client, db):
    objs = _make(db, 1)
    html = user_client.get(f"/solutions/{objs['/solutions'].id}").text
    assert html.index("</header>") < html.index('class="stagebar"') < html.index('class="dgrid"')
    r = user_client.post(f"/solutions/{objs['/solutions'].id}/stage", data={"stage": "tapping"}, follow_redirects=False)
    assert r.status_code == 303


def test_forms_render_field_errors_next_to_the_field(user_client):
    r = user_client.post("/customers", data={"name": " "})
    assert r.status_code == 400
    assert 'role="alert"' in r.text and 'id="e-name"' in r.text and 'aria-invalid="true"' in r.text
    ok = user_client.post("/customers", data={"name": "새 고객", "industry": "제조"}, follow_redirects=False)
    assert ok.status_code == 303


def test_file_upload_is_a_disclosure_without_js(user_client, db):
    s = _make(db, 1)["/solutions"]
    html = user_client.get(f"/solutions/{s.id}").text
    assert re.search(r'<details class="card-add">\s*<summary>\+ 자료 추가</summary>\s*<form[^>]*enctype="multipart/form-data"', html)

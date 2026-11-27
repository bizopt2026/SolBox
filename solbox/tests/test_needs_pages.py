"""소스 › 고객니즈 화면 (2026-11-27): 목록·상세·추가·수정·삭제."""
import re

from app.guards import rev_of
from app.models import Activity, Customer, CustomerNeed, Opportunity, SolutionField, User
from tests.conftest import make_user


def _world(db):
    ga = Customer(name="가나제지"); da = Customer(name="다라물류")
    energy = SolutionField(name="에너지", sort_order=90, active=True)
    off = SolutionField(name="옛 분야", sort_order=91, active=False)
    db.add_all([ga, da, energy, off]); db.flush()
    a = Activity(kind="meeting", title="11월 통화", raw_text="r")
    db.add(a); db.flush()
    rows = [
        CustomerNeed(activity_id=a.id, position=0, customer_name="가나제지", customer_id=ga.id, content="전기요금 부담", field_id=energy.id),
        CustomerNeed(activity_id=a.id, position=1, customer_name="가나", customer_id=None, content="초기 투자 어려움", status="reviewing"),
        CustomerNeed(customer_name="다라물류", customer_id=da.id, content="냉동창고 온도 이력", status="hold", memo="내년 예산"),
    ]
    db.add_all(rows); db.commit()
    return ga, da, energy, off, a, rows


def _tab_count(page, label):
    m = re.search(re.escape(label) + r"\D{0,80}?(\d+)", page)
    return int(m.group(1)) if m else None


def test_menu_and_list_with_status_tabs_and_filters(user_client, db):
    ga, da, energy, off, a, rows = _world(db)
    page = user_client.get("/needs").text
    assert 'href="/needs" aria-current="page"' in page and "고객니즈" in page
    for n in rows:
        assert f'href="/needs/{n.id}"' in page
    assert "직접 입력" in page and "11월 통화" in page and "에너지" in page
    hold = user_client.get("/needs?status=hold").text
    assert "냉동창고 온도 이력" in hold and "전기요금 부담" not in hold
    mine = user_client.get(f"/needs?customer_id={ga.id}").text
    assert "전기요금 부담" in mine and "냉동창고" not in mine
    by_field = user_client.get(f"/needs?field_id={energy.id}").text
    assert "전기요금 부담" in by_field and "초기 투자" not in by_field
    found = user_client.get("/needs?q=예산").text  # 메모에서도 찾는다
    assert "냉동창고 온도 이력" in found and "전기요금" not in found
    assert user_client.get("/needs?customer_id=abc").status_code == 200


def test_group_by_customer_and_field(user_client, db):
    _world(db)
    by_c = user_client.get("/needs?group=customer").text
    assert "가나제지" in by_c and "다라물류" in by_c and "고객 미연결" in by_c
    by_f = user_client.get("/needs?group=field").text
    assert "에너지" in by_f and "분야 미지정" in by_f


def test_detail_shows_source_status_and_next_action(user_client, db):
    ga, da, energy, off, a, rows = _world(db)
    page = user_client.get(f"/needs/{rows[0].id}").text
    assert "전기요금 부담" in page and "새 니즈" in page and "11월 통화" in page and f'href="/activities/{a.id}"' in page
    assert f"/opportunities/new?customer_id={ga.id}&amp;need_id={rows[0].id}" in page
    direct = user_client.get(f"/needs/{rows[2].id}").text
    assert "직접 입력" in direct and "내년 예산" in direct
    assert user_client.get("/needs/999999").status_code == 404


def test_create_direct_need_once(user_client, db):
    ga, da, energy, off, a, rows = _world(db)
    form = user_client.get(f"/needs/new?customer_id={ga.id}").text
    tok = re.search(r'name="_tok" value="([^"]+)"', form).group(1)
    assert f'<option value="{ga.id}" selected' in form
    data = {"_tok": tok, "customer_id": str(ga.id), "content": "옥상 공간 활용", "field_id": str(energy.id), "status": "new", "memo": ""}
    r1 = user_client.post("/needs", data=data, follow_redirects=False)
    r2 = user_client.post("/needs", data=data, follow_redirects=False)
    assert r1.status_code == 303 and r2.status_code == 303
    made = db.query(CustomerNeed).filter_by(content="옥상 공간 활용").all()
    assert len(made) == 1 and made[0].customer_id == ga.id and made[0].customer_name == "가나제지" and made[0].activity_id is None
    assert made[0].created_by_id == db.query(User).filter_by(username="kim").one().id


def test_create_requires_customer_content_and_active_field(user_client, db):
    ga, da, energy, off, a, rows = _world(db)
    assert user_client.post("/needs", data={"customer_id": "", "content": "x"}).status_code == 400
    assert user_client.post("/needs", data={"customer_id": str(ga.id), "content": " "}).status_code == 400
    assert user_client.post("/needs", data={"customer_id": str(ga.id), "content": "x", "field_id": str(off.id)}).status_code == 400
    assert user_client.post("/needs", data={"customer_id": str(ga.id), "content": "x", "status": "opportunity"}).status_code == 400


def test_edit_links_customer_and_keeps_activity_form_consistent(user_client, db):
    ga, da, energy, off, a, rows = _world(db)
    n = rows[1]  # 활동에서 나왔고 고객이 연결 안 된 줄
    form = user_client.get(f"/needs/{n.id}/edit").text
    assert "쓴 이름" in form and "가나" in form
    data = {"_rev": rev_of(n), "customer_id": str(ga.id), "content": n.content, "field_id": "", "status": "reviewing", "memo": "다시 묻기"}
    assert user_client.post(f"/needs/{n.id}", data=data, follow_redirects=False).status_code == 303
    db.expire_all()
    n = db.get(CustomerNeed, n.id)
    assert (n.customer_id, n.customer_name, n.memo, n.activity_id) == (ga.id, "가나제지", "다시 묻기", a.id)


def test_edit_conflict_and_old_activity_form_conflict(user_client, db):
    ga, da, energy, off, a, rows = _world(db)
    n = rows[0]
    stale_need = rev_of(n)
    stale_activity = rev_of(a)
    data = {"_rev": stale_need, "customer_id": str(ga.id), "content": "전기요금 부담 (수정)", "field_id": str(energy.id), "status": "reviewing", "memo": ""}
    assert user_client.post(f"/needs/{n.id}", data=data, follow_redirects=False).status_code == 303
    assert user_client.post(f"/needs/{n.id}", data={**data, "content": "다른 수정"}).status_code == 409
    old = {"_rev": stale_activity, "kind": a.kind, "date": "", "title": a.title, "raw_text": a.raw_text, "summary_md": "",
           "ds_id": [str(rows[0].id), str(rows[1].id)], "ds_customer": ["가나제지", "가나"], "ds_content": ["전기요금 부담", "초기 투자 어려움"]}
    assert user_client.post(f"/activities/{a.id}", data=old).status_code == 409


def test_changing_customer_unbinds_and_bound_status_is_locked(user_client, db):
    ga, da, energy, off, a, rows = _world(db)
    n = rows[0]
    o = Opportunity(name="가나 태양광", customer_id=ga.id); db.add(o); db.flush()
    n.opportunity_id = o.id; db.commit()
    data = {"_rev": rev_of(n), "customer_id": str(ga.id), "content": n.content, "field_id": str(energy.id), "status": "resolved", "memo": ""}
    assert user_client.post(f"/needs/{n.id}", data=data, follow_redirects=False).status_code == 303
    db.expire_all(); n = db.get(CustomerNeed, n.id)
    assert n.opportunity_id == o.id and n.status != "resolved"  # 묶인 동안 상태 칸은 무시
    data = {"_rev": rev_of(n), "customer_id": str(da.id), "content": n.content, "field_id": "", "status": "reviewing", "memo": ""}
    assert user_client.post(f"/needs/{n.id}", data=data, follow_redirects=False).status_code == 303
    db.expire_all(); n = db.get(CustomerNeed, n.id)
    assert n.customer_id == da.id and n.opportunity_id is None


def test_delete_permission(user_client, db, app):
    ga, da, energy, off, a, rows = _world(db)
    other = make_user(db, "lee")
    rows[2].created_by_id = other.id; db.commit()
    assert user_client.post(f"/needs/{rows[2].id}/delete", follow_redirects=False).status_code == 403
    mine = CustomerNeed(customer_id=ga.id, customer_name=ga.name, content="내 니즈", created_by_id=db.query(User).filter_by(username="kim").one().id)
    db.add(mine); db.commit()
    mine_id = mine.id
    assert user_client.get(f"/needs/{mine_id}/delete").status_code == 200  # 확인 화면
    assert user_client.post(f"/needs/{mine_id}/delete", follow_redirects=False).status_code == 303
    db.expunge_all()
    assert db.query(CustomerNeed).filter_by(id=mine_id).first() is None


def test_customer_detail_offers_add_need_and_search_links_to_need(user_client, db):
    ga, da, energy, off, a, rows = _world(db)
    assert f"/needs/new?customer_id={ga.id}" in user_client.get(f"/customers/{ga.id}").text
    hits = user_client.get("/search?q=냉동창고").text
    assert f'href="/needs/{rows[2].id}"' in hits

"""고객니즈 관리 (2026-11-27) 행위 규칙: 손댄 니즈는 활동 기록에서 빠져도 남고, 묶으면 검토 중이 되며, 직접 추가한 니즈도 영업기회에 묶을 수 있다."""
from app import needs, opportunities as opps, solution_fields as sfields
from app.models import Activity, Customer, CustomerNeed, Opportunity, SolutionField


def _post(client, activity=None, **extra):
    data = {"raw_text": "원문", **extra}
    return client.post(f"/activities/{activity.id}" if activity else "/activities", data=data, follow_redirects=False)


def _two_needs(user_client, db):
    c = Customer(name="가나제지"); db.add(c); db.commit()
    _post(user_client, ds_id=["", ""], ds_customer=["가나제지", "가나제지"], ds_content=["전기요금", "초기 투자"])
    a = db.query(Activity).one()
    n1, n2 = a.needs
    return c, a, n1, n2


def test_effective_status_and_untouched():
    n = CustomerNeed(content="x", status="new", memo="")
    assert needs.effective_status(n) == "new" and needs.is_untouched(n)
    n.memo = "통화 때 다시 묻기"
    assert not needs.is_untouched(n)
    n.memo, n.status = "", "hold"
    assert not needs.is_untouched(n)
    n.status, n.opportunity_id = "new", 3
    assert needs.effective_status(n) == "opportunity" and not needs.is_untouched(n)


def test_removing_row_from_activity_form_keeps_touched_need(user_client, db):
    c, a, n1, n2 = _two_needs(user_client, db)
    n1.memo = "중요"
    db.commit()
    ids = (n1.id, n2.id)
    db.expire_all()
    a = db.get(Activity, a.id)
    assert _post(user_client, a, ds_id=[], ds_customer=[], ds_content=[]).status_code == 303
    db.expire_all()
    kept = db.get(CustomerNeed, ids[0])
    assert kept is not None and kept.activity_id is None and kept.content == "전기요금"
    assert db.get(CustomerNeed, ids[1]) is None  # 손대지 않은 줄은 지운다


def test_deleting_activity_keeps_touched_needs_only(user_client, db):
    c, a, n1, n2 = _two_needs(user_client, db)
    n2.status = "reviewing"
    db.commit()
    ids = (n1.id, n2.id)
    assert user_client.post(f"/activities/{a.id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(CustomerNeed, ids[0]) is None
    kept = db.get(CustomerNeed, ids[1])
    assert kept is not None and kept.activity_id is None and kept.customer_id == c.id


def test_binding_marks_reviewing_and_unbinding_shows_reviewing(user_client, db):
    c, a, n1, n2 = _two_needs(user_client, db)
    o = Opportunity(name="가나 태양광", customer_id=c.id)
    db.add(o); db.flush()
    assert opps.apply_needs(db, o, [str(n1.id)]) is None
    db.commit()
    assert needs.effective_status(n1) == "opportunity" and n1.status == "reviewing"
    assert opps.apply_needs(db, o, []) is None
    db.commit()
    db.refresh(n1)
    assert n1.opportunity_id is None and needs.effective_status(n1) == "reviewing"


def test_direct_need_can_be_bundled(db):
    c = Customer(name="다라물류"); db.add(c); db.flush()
    n = CustomerNeed(customer_name=c.name, customer_id=c.id, content="냉동창고 온도 이력")
    o = Opportunity(name="다라 냉동", customer_id=c.id)
    db.add_all([n, o]); db.commit()
    assert n in opps.bundlable_needs(db, c.id, o)
    assert opps.apply_needs(db, o, [str(n.id)]) is None
    db.commit()
    assert opps.needs_of(db, o) == [n]


def test_status_condition_matches_effective_status(db):
    c = Customer(name="가나"); db.add(c); db.flush()
    rows = [CustomerNeed(customer_id=c.id, content=s, status=s) for s in ("new", "reviewing", "hold", "resolved")]
    rows.append(CustomerNeed(customer_id=c.id, content="묶임", status="reviewing", opportunity_id=99))
    db.add_all(rows); db.commit()
    for code in ("new", "reviewing", "opportunity", "hold", "resolved"):
        got = set(db.query(CustomerNeed).filter(needs.status_condition(code)).all())
        assert got == {n for n in rows if needs.effective_status(n) == code}, code


def test_field_used_by_need_cannot_be_deleted(db):
    f = SolutionField(name="에너지", sort_order=99, active=True)
    db.add(f); db.flush()
    db.add(CustomerNeed(content="전기요금", field_id=f.id)); db.commit()
    assert sfields.delete_problem(db, f) is not None and "니즈" in sfields.delete_problem(db, f)


def test_opportunity_copy_shows_direct_need(db, settings):
    from app.knowledge import KnowledgeMirror

    c = Customer(name="다라물류"); db.add(c); db.flush()
    o = Opportunity(name="다라 냉동", customer_id=c.id); db.add(o); db.flush()
    db.add(CustomerNeed(customer_name=c.name, customer_id=c.id, content="온도 이력", opportunity_id=o.id)); db.commit()
    mirror = KnowledgeMirror(settings.knowledge_dir)
    mirror.sync(o)
    text = next(settings.knowledge_dir.rglob(f"opportunities/{o.id}.md")).read_text(encoding="utf-8")
    assert "온도 이력 (직접 입력)" in text


# ---- 분야 (Task 5) ----

def test_activity_form_saves_need_field_and_old_form_keeps_it(user_client, db):
    f = SolutionField(name="에너지", sort_order=90, active=True)
    c = Customer(name="가나제지"); db.add_all([f, c]); db.commit()
    form = user_client.get("/activities/new?manual=1").text
    assert 'name="ds_field"' in form and f'<option value="{f.id}">에너지</option>' in form
    _post(user_client, ds_id=[""], ds_customer=["가나제지"], ds_content=["전기요금"], ds_field=[str(f.id)])
    a = db.query(Activity).one()
    n = a.needs[0]
    assert n.field_id == f.id and n.created_by_id is not None
    edit = user_client.get(f"/activities/{a.id}/edit").text
    assert f'<option value="{f.id}" selected>에너지</option>' in edit
    _post(user_client, a, ds_id=[str(n.id)], ds_customer=["가나제지"], ds_content=["전기요금 (수정)"])  # 분야 칸이 없는 옛 화면
    db.expire_all()
    assert db.get(CustomerNeed, n.id).field_id == f.id


def test_ai_draft_preselects_suggested_field(db):
    from app.ai.tasks import ActivityDraft
    from app.routers.activities import draft_values

    f = SolutionField(name="에너지", sort_order=90, active=True); db.add(f); db.commit()
    d = ActivityDraft(demand_signals=[{"customer": "가나", "content": "전기요금", "field": "에너지"}, {"customer": "", "content": "기타", "field": "없는 분야"}])
    values = draft_values(db, d, "원문")
    assert [s["field_id"] for s in values["demand_signals"]] == [f.id, None]


def test_intake_apply_saves_need_field(app, user_client, db, settings):
    from tests.conftest import form_from_page, ready_intake

    f = SolutionField(name="조명", sort_order=90, active=True); db.add(f); db.commit()
    it, s1, c1 = ready_intake(db, settings)
    form = form_from_page(user_client, it)
    assert "act1__ds_field" in user_client.get(f"/intake/{it.id}").text
    form.update({"act1__ds_customer": ["한빛병원"], "act1__ds_content": ["조명 교체 문의"], "act1__ds_field": [str(f.id)]})
    assert user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False).status_code == 303
    db.expire_all()
    n = db.query(Activity).one().needs[0]
    assert n.field_id == f.id and n.created_by_id is not None


def test_activity_form_customer_change_with_field_column_unbinds(user_client, db):
    """검토에서 찾은 문제: 분야 칸(ds_field)이 오는 실제 폼에서도 고객이 바뀌면 고객 연결을 옮기고 영업기회에서 푼다."""
    ga = Customer(name="가나제지"); da = Customer(name="다라물류"); db.add_all([ga, da]); db.commit()
    _post(user_client, ds_id=[""], ds_customer=["가나제지"], ds_content=["전기요금"], ds_field=[""])
    a = db.query(Activity).one()
    n = a.needs[0]
    o = Opportunity(name="가나 태양광", customer_id=ga.id); db.add(o); db.flush()
    n.opportunity_id = o.id; db.commit()
    nid = n.id
    db.expire_all()
    a = db.get(Activity, a.id)
    assert _post(user_client, a, ds_id=[str(nid)], ds_customer=["다라물류"], ds_content=["전기요금"], ds_field=[""]).status_code == 303
    db.expire_all()
    n = db.get(CustomerNeed, nid)
    assert (n.customer_name, n.customer_id, n.opportunity_id) == ("다라물류", da.id, None)

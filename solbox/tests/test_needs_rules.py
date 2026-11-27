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

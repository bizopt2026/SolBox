"""팀 업무 보고가 기간별로 세는 두 날짜: 후속 조치 완료일(done_at), 영업기회 단계 변경 기록."""
from sqlalchemy import select

from app import idmarks
from app.models import Activity, Customer, FollowUp, IdMark, Opportunity, OpportunityStageChange
from tests.conftest import fu_rows, make_user


class _NoIndex:
    def user_id(self, name):
        return None


def _activity(db, *rows):
    a = Activity(title="미팅", raw_text="x", follow_ups=fu_rows(*rows))
    db.add(a)
    db.commit()
    return a


def _opp(db, **kw):
    c = Customer(name="한빛병원")
    db.add(c)
    db.flush()
    o = Opportunity(name="기회", customer_id=c.id, **kw)
    db.add(o)
    db.commit()
    return o


def _changes(db, opp):
    return list(db.scalars(select(OpportunityStageChange).where(OpportunityStageChange.opportunity_id == opp.id).order_by(OpportunityStageChange.id)))


def test_done_at_set_and_cleared(db):
    fu = _activity(db, ("견적", "", "", False)).follow_ups[0]
    assert fu.done_at is None
    fu.done = True
    db.commit()
    first = fu.done_at
    assert first is not None
    fu.task = "견적 보내기"  # 다른 칸만 고침
    db.commit()
    assert fu.done_at == first
    fu.done = False
    db.commit()
    assert fu.done_at is None


def test_new_done_follow_up_has_done_at(db):
    fu = _activity(db, ("이미 끝남", "", "", True)).follow_ups[0]
    assert fu.done_at is not None


def test_done_toggle_route_sets_done_at(user_client, db):
    user_client.post("/activities", data={"raw_text": "x", "fu_task": ["할 일"], "fu_owner": [""], "fu_due": [""]})
    a = db.query(Activity).one()
    assert a.follow_ups[0].done_at is None
    assert user_client.post(f"/activities/{a.id}/followups/0/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(FollowUp).one().done_at is not None


def test_stage_change_logged_on_create_and_change(db):
    db.info["user_id"] = None
    o = _opp(db)
    rows = _changes(db, o)
    assert [(r.from_stage, r.to_stage) for r in rows] == [("", "discovery")]
    assert all(r.id is not None for r in rows)
    uid = make_user(db, "lee").id
    db.info["user_id"] = uid
    o.stage = "won"
    db.commit()
    rows = _changes(db, o)
    assert [(r.from_stage, r.to_stage) for r in rows] == [("", "discovery"), ("discovery", "won")]
    assert rows[1].changed_by_id == uid and rows[1].changed_at is not None
    o.memo = "메모"
    db.commit()
    assert len(_changes(db, o)) == 2
    assert [r.opportunity for r in rows] == [o, o]
    assert o.stage_changes == rows


def test_stage_change_ids_keep_id_marks_consistent(db):
    o = _opp(db)
    o.stage = "won"
    db.commit()
    top = max(r.id for r in _changes(db, o))
    mark = db.scalar(select(IdMark.last_id).where(IdMark.table_name == "opportunity_stage_changes"))
    assert mark is not None and mark >= top


def test_stage_change_on_won_conversion(user_client, db):
    o = _opp(db)
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "won"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert [(c.from_stage, c.to_stage) for c in _changes(db, o)] == [("", "discovery"), ("discovery", "won")]


def test_stage_changes_deleted_with_opportunity(db):
    o = _opp(db)
    o.stage = "won"
    db.commit()
    oid = o.id
    db.delete(o)
    db.commit()
    assert db.scalars(select(OpportunityStageChange).where(OpportunityStageChange.opportunity_id == oid)).all() == []


def test_reconcile_keeps_done_at(db):
    """활동 고치기(행 맞추기)로 같은 줄을 다시 저장해도 완료일은 그대로, 새로 완료로 들어온 줄은 완료일이 생긴다."""
    from app.activity_rows import reconcile_follow_ups

    a = _activity(db, ("견적", "", "", False))
    fu = a.follow_ups[0]
    fu.done = True
    db.commit()
    first = fu.done_at
    reconcile_follow_ups(a, [str(fu.id), ""], [("견적", "", "", True), ("계약", "", "", True)], _NoIndex())
    db.commit()
    assert a.follow_ups[0].done_at == first
    assert a.follow_ups[1].done_at is not None

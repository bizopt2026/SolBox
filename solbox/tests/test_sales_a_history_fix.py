"""영업 A 최종 리뷰에서 고친 것: 담당·팀이 바뀌면 영업기회 지식 사본 갱신, 사유 이력 이름은 그때 단계로."""
from datetime import date

from sqlalchemy import select

from app import opportunities as opps
from app.guards import rev_of
from app.models import Customer, Opportunity, Team, User
from tests.conftest import make_user


def _setup(db):
    t = Team(name="영업1팀")
    db.add(t)
    db.commit()
    kim = make_user(db, "kim9", name="김영업")
    kim.team_id = t.id
    c = Customer(name="한빛물류")
    db.add(c)
    db.commit()
    return t, kim, c


def _mirror(settings, o):
    return (settings.knowledge_dir / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")


def test_team_rename_and_delete_resync_owned_opportunities(admin_client, db, settings):
    t, kim, c = _setup(db)
    r = admin_client.post("/opportunities", data={"name": "건", "customer_id": str(c.id), "progress": "1", "owner_user_id": str(kim.id)}, follow_redirects=False)
    assert r.status_code == 303
    o = db.query(Opportunity).one()
    assert "- 담당: 김영업 (영업1팀)" in _mirror(settings, o)
    db.refresh(t)
    admin_client.post(f"/admin/teams/{t.id}", data={"name": "B2B팀", "_rev": rev_of(t)})
    assert "- 담당: 김영업 (B2B팀)" in _mirror(settings, o)
    admin_client.post(f"/admin/teams/{t.id}/delete")
    assert "- 담당: 김영업\n" in _mirror(settings, o)


def test_user_save_resyncs_owned_opportunities(admin_client, db, settings):
    t, kim, c = _setup(db)
    admin_client.post("/opportunities", data={"name": "건", "customer_id": str(c.id), "progress": "1", "owner_user_id": str(kim.id)})
    o = db.query(Opportunity).one()
    data = {"name": "김영업 과장", "role": "user", "is_active": "on", "team_field": "1", "team_id": "", "_rev": rev_of(db.get(User, kim.id))}
    assert admin_client.post(f"/admin/users/{kim.id}", data=data, follow_redirects=False).status_code == 303
    assert "- 담당: 김영업 과장\n" in _mirror(settings, o)


def test_history_reason_names_use_the_stage_of_each_side(user_client, db):
    c = Customer(name="x")
    db.add(c)
    db.commit()
    o = Opportunity(name="건", customer_id=c.id, stage="negotiating")
    db.add(o)
    db.commit()
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": "price"})
    user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "proposing"})
    db.expire_all()
    rows = [r for r in opps.history_rows(db, db.get(Opportunity, o.id), date.today()) if r["field"] == "close_reason"]
    assert [(r["old"], r["new"]) for r in rows] == [("가격 경쟁력", ""), ("", "가격 경쟁력")]

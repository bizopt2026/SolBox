"""영업 A 단계: 관리 › 사용자의 팀 만들기·이름 바꾸기·지우기·사람 배정 (app/teams.py, routers/admin.py)."""
from sqlalchemy import select

from app import teams
from app.guards import rev_of
from app.models import Customer, Opportunity, Team, User
from tests.conftest import make_user, nloc


def _team(db, name, position=0):
    t = Team(name=name, position=position)
    db.add(t)
    db.commit()
    return t


def test_only_admin_manages_teams(user_client, db):
    t = _team(db, "영업1팀")
    assert user_client.post("/admin/teams", data={"name": "x"}).status_code == 403
    assert user_client.post(f"/admin/teams/{t.id}", data={"name": "x"}).status_code == 403
    assert user_client.post(f"/admin/teams/{t.id}/delete").status_code == 403
    assert user_client.get(f"/admin/teams/{t.id}/delete").status_code == 403


def test_create_team_trims_and_rejects_blank_or_duplicate(admin_client, db):
    r = admin_client.post("/admin/teams", data={"name": "  영업1팀 "}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == "/admin/users?notice=team_created"
    assert [t.name for t in db.scalars(select(Team))] == ["영업1팀"]
    r = admin_client.post("/admin/teams", data={"name": "영업1팀"})
    assert r.status_code == 400 and teams.DUPLICATE in r.text
    r = admin_client.post("/admin/teams", data={"name": "  "})
    assert r.status_code == 400 and teams.NAME_REQUIRED in r.text


def test_rename_team_with_revision_stamp(admin_client, db):
    t = _team(db, "영업1팀")
    stamp = rev_of(t)
    r = admin_client.post(f"/admin/teams/{t.id}", data={"name": "영업 1팀", "_rev": stamp}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == "/admin/users?notice=team_saved"
    db.expire_all()
    assert db.get(Team, t.id).name == "영업 1팀"
    r = admin_client.post(f"/admin/teams/{t.id}", data={"name": "다른 이름", "_rev": stamp})  # 옛 표시
    assert r.status_code == 409
    db.expire_all()
    assert db.get(Team, t.id).name == "영업 1팀"
    assert admin_client.post("/admin/teams/999", data={"name": "x"}).status_code == 404


def test_delete_team_clears_members_and_keeps_opportunities(admin_client, db):
    t = _team(db, "영업1팀")
    kim = make_user(db, "kim2", name="김영업")
    kim.team_id = t.id
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    opp = Opportunity(name="건", customer_id=c.id, owner_user_id=kim.id)
    db.add(opp)
    db.commit()
    tid = t.id
    page = admin_client.get(f"/admin/teams/{tid}/delete")
    assert page.status_code == 200 and "영업1팀" in page.text and "팀 없음" in page.text
    r = admin_client.post(f"/admin/teams/{tid}/delete", follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == "/admin/users?notice=team_deleted"
    db.expire_all()
    assert db.get(Team, tid) is None and db.get(User, kim.id).team_id is None
    assert db.get(Opportunity, opp.id).owner_user_id == kim.id


def test_assign_team_on_user_save(admin_client, db):
    t = _team(db, "영업1팀")
    kim = make_user(db, "kim2", name="김영업")
    data = {"name": "김영업", "role": "user", "is_active": "on", "team_field": "1", "team_id": str(t.id), "_rev": rev_of(kim)}
    assert admin_client.post(f"/admin/users/{kim.id}", data=data, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(User, kim.id).team_id == t.id
    data = {"name": "김영업", "role": "user", "is_active": "on", "team_field": "1", "team_id": "999", "_rev": rev_of(db.get(User, kim.id))}
    r = admin_client.post(f"/admin/users/{kim.id}", data=data)
    assert r.status_code == 400 and teams.BAD_TEAM in r.text
    data = {"name": "김영업", "role": "user", "is_active": "on", "team_field": "1", "team_id": "", "_rev": rev_of(db.get(User, kim.id))}
    assert admin_client.post(f"/admin/users/{kim.id}", data=data, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(User, kim.id).team_id is None


def test_old_user_form_without_team_field_keeps_team(admin_client, db):
    t = _team(db, "영업1팀")
    kim = make_user(db, "kim2", name="김영업")
    kim.team_id = t.id
    db.commit()
    data = {"name": "김영업 과장", "role": "user", "is_active": "on", "_rev": rev_of(kim)}
    assert admin_client.post(f"/admin/users/{kim.id}", data=data, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(User, kim.id).team_id == t.id


def test_users_page_shows_team_select_and_team_card(admin_client, db):
    _team(db, "영업2팀", 2)
    _team(db, "영업1팀", 1)
    page = admin_client.get("/admin/users").text
    assert 'name="team_id"' in page and "팀 없음" in page
    assert page.index("영업1팀") < page.index("영업2팀")
    assert 'action="/admin/teams"' in page


def test_grouped_users_orders_by_team_and_keeps_inactive_current(db):
    t1, t2 = _team(db, "영업1팀", 1), _team(db, "영업2팀", 2)
    a = make_user(db, "a", name="가나")
    b = make_user(db, "b", name="다라")
    gone = make_user(db, "c", name="마바")
    loner = make_user(db, "d", name="사아")
    a.team_id, b.team_id, gone.team_id = t2.id, t1.id, t1.id
    gone.is_active = False
    db.commit()
    groups = teams.grouped_users(db)
    names = [(g, [u.name for u in us]) for g, us in groups]
    assert ("영업1팀", ["다라"]) in names and ("영업2팀", ["가나"]) in names
    assert names[-1][0] == "팀 없음" and "사아" in names[-1][1]
    assert [g for g, _ in names][:2] == ["영업1팀", "영업2팀"]
    assert not any("마바" in us for _, us in names)
    kept = [(g, [u.name for u in us]) for g, us in teams.grouped_users(db, keep=gone)]
    assert ("영업1팀", ["다라", "마바"]) in kept

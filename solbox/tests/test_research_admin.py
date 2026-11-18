from app.models import AppSetting, ResearchReport, ResearchRun, ResearchTopic
from app.research import Schedule, active_topics, get_schedule, set_schedule


def test_default_schedule_is_monday_six(db):
    assert get_schedule(db) == Schedule(enabled=True, weekday=0, hour=6, minute=0)


def test_invalid_stored_fields_fall_back_field_by_field(db):
    db.add(AppSetting(key="research_schedule", value={"enabled": False, "weekday": 9, "hour": "x"}))
    db.commit()
    assert get_schedule(db) == Schedule(enabled=False, weekday=0, hour=6, minute=0)


def test_set_schedule_roundtrip(db):
    set_schedule(db, Schedule(True, 4, 22, 15))
    set_schedule(db, Schedule(False, 5, 1, 2))
    db.expire_all()
    assert get_schedule(db) == Schedule(False, 5, 1, 2)


def test_models_defaults_and_cascade(db):
    run = ResearchRun(trigger="manual")
    db.add(run)
    db.flush()
    rep = ResearchReport(run_id=run.id, kind="area", area="tech", title="t")
    db.add(rep)
    db.commit()
    assert run.status == "running" and run.slot is None and run.started_at is not None
    assert (rep.trends, rep.insights, rep.suggestions, rep.status, rep.error) == ([], [], [], "pending", "")
    db.delete(run)
    db.commit()
    assert db.query(ResearchReport).count() == 0


def test_admin_saves_schedule_and_rejects_bad_values(admin_client, db):
    admin_client.post("/admin/research/schedule", data={"enabled": "on", "weekday": "2", "hour": "7", "minute": "30"})
    assert get_schedule(db) == Schedule(True, 2, 7, 30)
    admin_client.post("/admin/research/schedule", data={"weekday": "2", "hour": "7", "minute": "30"})
    db.expire_all()
    assert get_schedule(db).enabled is False
    for bad in (
        {"weekday": "7", "hour": "7", "minute": "0"},
        {"weekday": "1", "hour": "24", "minute": "0"},
        {"weekday": "1", "hour": "1", "minute": "60"},
        {"weekday": "-1", "hour": "1", "minute": "0"},
        {"weekday": "abc", "hour": "1", "minute": "0"},
        {"weekday": "1", "hour": "", "minute": "0"},
    ):
        assert admin_client.post("/admin/research/schedule", data=bad).status_code == 400


def test_topic_crud_and_active_topics(admin_client, db):
    admin_client.post("/admin/research/topics", data={"name": "건물 에너지 관리", "keywords": "BEMS, AI 공조", "area": "tech"})
    admin_client.post("/admin/research/topics", data={"name": "전기요금 정책", "area": "policy"})
    t = db.query(ResearchTopic).filter_by(area="policy").one()
    admin_client.post(f"/admin/research/topics/{t.id}", data={"name": t.name, "area": "policy"})
    db.expire_all()
    assert list(active_topics(db)) == ["tech"] and active_topics(db)["tech"][0].keywords == "BEMS, AI 공조"
    admin_client.post(f"/admin/research/topics/{t.id}/delete")
    assert db.query(ResearchTopic).count() == 1


def test_topic_update_validation(admin_client, db):
    admin_client.post("/admin/research/topics", data={"name": "a", "area": "tech"})
    t = db.query(ResearchTopic).one()
    assert admin_client.post(f"/admin/research/topics/{t.id}", data={"name": " ", "area": "tech"}).status_code == 400
    assert admin_client.post(f"/admin/research/topics/{t.id}", data={"name": "a", "area": "x"}).status_code == 400
    assert admin_client.post("/admin/research/topics/999", data={"name": "a", "area": "tech"}).status_code == 404
    assert admin_client.post("/admin/research/topics/999/delete").status_code == 404


def test_topic_validation(admin_client, db):
    post = lambda **d: admin_client.post("/admin/research/topics", data=d).status_code
    assert post(name="  ", area="tech") == 400
    assert post(name="a", area="x") == 400
    assert post(name="a" * 101, area="tech") == 400
    assert post(name="a", keywords="k" * 501, area="tech") == 400
    assert db.query(ResearchTopic).count() == 0


def test_page_renders_for_admin(admin_client):
    admin_client.post("/admin/research/topics", data={"name": "건물 에너지 관리", "area": "tech"})
    r = admin_client.get("/admin/research")
    assert r.status_code == 200 and "건물 에너지 관리" in r.text and "리서치 설정" in r.text


def test_normal_user_forbidden(user_client):
    assert user_client.get("/admin/research").status_code == 403
    assert user_client.post("/admin/research/topics", data={"name": "a", "area": "tech"}).status_code == 403
    assert user_client.post("/admin/research/topics/1", data={"name": "a", "area": "tech"}).status_code == 403
    assert user_client.post("/admin/research/topics/1/delete").status_code == 403
    assert user_client.post("/admin/research/schedule", data={"weekday": "1", "hour": "1", "minute": "0"}).status_code == 403

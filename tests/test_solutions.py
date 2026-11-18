from datetime import date

from app.models import Activity, Partner, Solution


def test_create_solution_defaults_to_intake_and_writes_card(user_client, db, settings):
    user_client.post("/solutions", data={"name": "AI 에너지 절감", "tags": "에너지, AI"})
    s = db.query(Solution).one()
    assert s.stage == "intake" and s.tags == "에너지,AI"      # 태그는 공백을 떼고 저장
    assert (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").exists()


def test_tags_drop_empty_items(user_client, db):
    user_client.post("/solutions", data={"name": "A", "tags": " a , ,b,, "})
    assert db.query(Solution).one().tags == "a,b"


def test_create_requires_name_valid_stage_and_partner(user_client, db):
    assert user_client.post("/solutions", data={"name": " "}).status_code == 400
    assert user_client.post("/solutions", data={"name": "A", "stage": "nope"}).status_code == 400
    assert user_client.post("/solutions", data={"name": "A", "partner_id": "999"}).status_code == 400
    assert db.query(Solution).count() == 0


def test_create_with_partner_and_update(user_client, db):
    p = Partner(name="P"); db.add(p); db.commit()
    r = user_client.post("/solutions", data={"name": "A", "partner_id": str(p.id), "stage": "tapping"}, follow_redirects=False)
    assert r.status_code == 303
    s = db.query(Solution).one()
    assert s.partner_id == p.id and s.stage == "tapping"
    assert user_client.post(f"/solutions/{s.id}", data={"name": "B", "stage": "nope"}).status_code == 400
    r = user_client.post(f"/solutions/{s.id}", data={"name": "B", "summary": "요약", "stage": "hold"}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(s)
    assert (s.name, s.summary, s.stage, s.partner_id) == ("B", "요약", "hold", None)
    assert "B" in user_client.get(f"/solutions/{s.id}").text
    assert user_client.get(f"/solutions/{s.id}/edit").status_code == 200
    assert user_client.get("/solutions/new").status_code == 200


def test_stage_change_and_invalid_stage(user_client, db):
    s = Solution(name="A"); db.add(s); db.commit()
    assert user_client.post(f"/solutions/{s.id}/stage", data={"stage": "boxed"}, follow_redirects=False).status_code == 303
    db.refresh(s); assert s.stage == "boxed"
    assert user_client.post(f"/solutions/{s.id}/stage", data={"stage": "nope"}).status_code == 400


def test_list_filters_by_stage_and_query(user_client, db):
    db.add_all([Solution(name="에너지 절감", stage="boxed"), Solution(name="안전 관제", stage="hold", tags="CCTV", summary="영상 분석")]); db.commit()
    assert "안전 관제" not in user_client.get("/solutions?stage=boxed").text
    assert "안전 관제" in user_client.get("/solutions?q=CCTV").text
    assert "안전 관제" in user_client.get("/solutions?q=영상").text
    assert "에너지 절감" not in user_client.get("/solutions?q=CCTV").text


def test_detail_lists_activities_newest_first(user_client, db):
    s = Solution(name="A")
    for d, t in ((date(2026, 1, 1), "옛날회의"), (date(2026, 6, 1), "최근회의")):
        a = Activity(raw_text="x", title=t, date=d); a.solutions.append(s); db.add(a)
    db.add(s); db.commit()
    text = user_client.get(f"/solutions/{s.id}").text
    assert text.index("최근회의") < text.index("옛날회의")


def test_delete_solution_keeps_activities(admin_client, db, settings):   # Review Focus 2
    s = Solution(name="A"); a = Activity(raw_text="x", title="t", date=date(2026, 11, 1)); a.solutions.append(s)
    db.add_all([s, a]); db.commit(); sid, aid = s.id, a.id
    admin_client.post(f"/solutions/{sid}/delete")
    db.expire_all()
    assert db.get(Solution, sid) is None and db.get(Activity, aid) is not None
    assert not (settings.knowledge_dir / "solutions" / str(sid)).exists()

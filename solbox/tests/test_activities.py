from datetime import date

from app.models import Activity, Project, Customer, Partner, Solution
from tests.conftest import nloc


def test_save_raw_only(user_client, db, settings):
    r = user_client.post("/activities/raw", data={"raw_text": "한빛병원 미팅\n전력비 부담 언급"}, follow_redirects=False)
    assert r.status_code == 303
    a = db.query(Activity).one()
    assert a.title == "한빛병원 미팅" and a.kind == "meeting" and a.date == date.today() and a.summary_md == ""
    assert nloc(r) == f"/activities/{a.id}?notice=created"
    assert (settings.knowledge_dir / "activities" / str(a.date.year) / f"{a.id}.md").exists()


def test_empty_memo_rejected(user_client):
    assert user_client.post("/activities/raw", data={"raw_text": "  "}).status_code == 400


def test_memo_file_cp949_and_200kb_text(user_client, db):
    big = "가나다라 " * 40_000
    user_client.post("/activities/raw", data={"raw_text": big},
                     files={"memo_file": ("m.txt", "파일 메모".encode("cp949"), "text/plain")})
    a = db.query(Activity).one()
    assert a.raw_text.startswith("가나다라") and a.raw_text.endswith("파일 메모")


def test_save_structured_with_links_and_new_entities(user_client, db, settings):
    s = Solution(name="AI 에너지 절감"); c = Customer(name="한빛병원"); db.add_all([s, c]); db.commit()
    data = {"kind": "tapping", "date": "2026-11-10", "title": "한빛병원 태핑", "raw_text": "원문",
            "summary_md": "## 논의\n- 전력비", "fu_task": ["견적 송부", ""], "fu_owner": ["김과장", ""], "fu_due": ["2026-11-20", ""],
            "ds_customer": ["한빛병원"], "ds_content": ["전력비 절감 관심"],
            "solution_ids": [str(s.id)], "customer_ids": [str(c.id)], "new_partners": ["그린테크", " 그린테크 "]}
    user_client.post("/activities", data=data)
    a = db.query(Activity).one()
    assert [(f.task, f.owner, f.due, f.done, f.position) for f in a.follow_ups] == [("견적 송부", "김과장", date(2026, 11, 20), False, 0)]
    assert [(n.customer_name, n.customer_id, n.content) for n in a.needs] == [("한빛병원", c.id, "전력비 절감 관심")]
    assert a.legacy_follow_ups == [] and a.legacy_demand_signals == []  # 옛 JSON 열은 쓰지 않는다
    assert [p.name for p in a.partners] == ["그린테크"] and db.query(Partner).count() == 1
    card = (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")
    assert "한빛병원 태핑" in card
    assert "한빛병원 태핑" in user_client.get(f"/solutions/{s.id}").text


def test_new_solution_is_intake_and_new_entity_reuses_existing_name(user_client, db):
    db.add(Customer(name="한빛병원")); db.commit()
    user_client.post("/activities", data={"raw_text": "x", "new_customers": ["한빛병원 "], "new_solutions": ["신규 솔루션"]})
    a = db.query(Activity).one()
    assert db.query(Customer).count() == 1 and [c.name for c in a.customers] == ["한빛병원"]
    assert [(s.name, s.stage) for s in a.solutions] == [("신규 솔루션", "intake")]


def test_unlinking_updates_old_entity_mirror(user_client, db, settings):
    s = Solution(name="솔루션A"); db.add(s); db.commit()
    user_client.post("/activities", data={"raw_text": "원문", "title": "연결 제목", "solution_ids": [str(s.id)]})
    a = db.query(Activity).one()
    card = settings.knowledge_dir / "solutions" / str(s.id) / "card.md"
    assert "연결 제목" in card.read_text(encoding="utf-8")
    r = user_client.post(f"/activities/{a.id}", data={"raw_text": "원문", "title": "연결 제목"}, follow_redirects=False)
    assert r.status_code == 303
    assert "연결 제목" not in card.read_text(encoding="utf-8")


def test_toggle_follow_up(user_client, db):
    user_client.post("/activities", data={"raw_text": "x", "fu_task": ["할 일"], "fu_owner": [""], "fu_due": ["어제"]})
    a = db.query(Activity).one()
    assert [(f.task, f.owner, f.due, f.done) for f in a.follow_ups] == [("할 일", "", None, False)]
    url = f"/activities/{a.id}/followups/0/toggle"
    r = user_client.post(url, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/activities/{a.id}"
    db.refresh(a); assert a.follow_ups[0].done is True
    r = user_client.post(url, headers={"referer": "http://testserver/activities"}, follow_redirects=False)
    assert r.headers["location"] == "http://testserver/activities"
    db.refresh(a); assert a.follow_ups[0].done is False
    assert user_client.post(f"/activities/{a.id}/followups/5/toggle").status_code == 404


def test_edit_keeps_done_flags(user_client, db):
    user_client.post("/activities", data={"raw_text": "x", "fu_task": ["a", "b"], "fu_owner": ["", ""], "fu_due": ["", ""]})
    a = db.query(Activity).one()
    user_client.post(f"/activities/{a.id}/followups/1/toggle")
    user_client.post(f"/activities/{a.id}", data={"raw_text": "x", "fu_task": ["a", "b"], "fu_owner": ["", ""], "fu_due": ["", ""], "fu_done": ["1"]})
    db.refresh(a)
    assert [f.done for f in a.follow_ups] == [False, True]


def test_invalid_date_falls_back_to_today(user_client, db):
    r = user_client.post("/activities", data={"raw_text": "x", "date": "어제"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.query(Activity).one().date == date.today()


def test_list_search_and_pages(user_client, db):
    user_client.post("/activities", data={"raw_text": "x", "title": "찾을 제목", "kind": "tapping"})
    user_client.post("/activities", data={"raw_text": "y", "title": "다른", "kind": "meeting"})
    assert "찾을 제목" in user_client.get("/activities?q=찾을").text
    assert "다른" not in user_client.get("/activities?q=찾을").text
    assert "다른" not in user_client.get("/activities?kind=tapping").text
    assert user_client.get("/activities/new").status_code == 200
    a = db.query(Activity).first()
    assert user_client.get(f"/activities/{a.id}").status_code == 200
    assert user_client.get(f"/activities/{a.id}/edit").status_code == 200
    assert user_client.get("/activities/9999").status_code == 404


def test_delete_syncs_linked_and_forbids_non_owner(user_client, admin_client, db, settings):
    s = Solution(name="솔루션A"); db.add(s); db.commit()
    user_client.post("/activities", data={"raw_text": "원문", "title": "지울 기록", "solution_ids": [str(s.id)]})
    a = db.query(Activity).one()
    assert admin_client.post(f"/activities/{a.id}/delete", follow_redirects=False).status_code == 303
    assert db.query(Activity).count() == 0
    card = settings.knowledge_dir / "solutions" / str(s.id) / "card.md"
    assert "지울 기록" not in card.read_text(encoding="utf-8")


def test_delete_by_non_owner_forbidden(user_client, admin_client, db):
    admin_client.post("/activities", data={"raw_text": "원문"})
    a = db.query(Activity).one()
    assert user_client.post(f"/activities/{a.id}/delete").status_code == 403
    assert db.query(Activity).count() == 1


def test_memo_file_with_other_extension_rejected(user_client, db):
    r = user_client.post("/activities/raw", data={"raw_text": "메모"},
                         files={"memo_file": ("m.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 400 and "텍스트 파일(.txt, .md)만 올릴 수 있습니다." in r.text
    assert db.query(Activity).count() == 0


def test_new_names_reuse_normalized_existing(user_client, db):
    c = Customer(name="한빛병원"); db.add(c); db.commit()
    user_client.post("/activities", data={"raw_text": "원문", "title": "t", "kind": "meeting", "date": "2026-11-10",
                                          "new_customers": ["한빛병원 ", "한빛 병원", "한빛", "(한빛병원)"]})
    names = sorted(x.name for x in db.query(Customer))
    assert names == ["한빛", "한빛병원"]
    assert sorted(x.name for x in db.query(Activity).one().customers) == ["한빛", "한빛병원"]

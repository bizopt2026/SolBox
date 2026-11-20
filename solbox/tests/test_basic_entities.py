from datetime import date

from app.models import Activity, Project, Customer, Partner, Solution
from tests.conftest import make_user


def test_customer_create_search_edit(user_client, db, settings):
    r = user_client.post("/customers", data={"name": "한빛병원", "industry": "의료"}, follow_redirects=False)
    assert r.status_code == 303
    c = db.query(Customer).one()
    assert c.created_by_id is not None
    assert r.headers["location"] == f"/customers/{c.id}"
    assert (settings.knowledge_dir / "customers" / f"{c.id}.md").exists()
    assert "한빛병원" in user_client.get("/customers?q=한빛").text
    assert "한빛병원" not in user_client.get("/customers?q=없는이름").text
    r = user_client.post(f"/customers/{c.id}", data={"name": "한빛종합병원"}, follow_redirects=False)
    assert r.status_code == 303
    assert "한빛종합병원" in (settings.knowledge_dir / "customers" / f"{c.id}.md").read_text(encoding="utf-8")


def test_blank_name_rejected(user_client):
    assert user_client.post("/partners", data={"name": "  "}).status_code == 400


def test_blank_name_rerenders_form_with_values_and_message(user_client, db):
    r = user_client.post("/partners", data={"name": " ", "contact": "02-123-4567"})
    assert r.status_code == 400
    assert "이름을 입력하세요." in r.text
    assert "02-123-4567" in r.text
    assert db.query(Partner).count() == 0


def test_blank_name_on_edit_rejected(user_client, db):
    p = Partner(name="그린테크"); db.add(p); db.commit()
    r = user_client.post(f"/partners/{p.id}", data={"name": ""})
    assert r.status_code == 400 and "이름을 입력하세요." in r.text
    db.refresh(p)
    assert p.name == "그린테크"


def test_delete_only_by_owner_or_admin(user_client, admin_client, db):
    other = make_user(db, "park")
    p = Partner(name="그린테크", created_by_id=other.id); db.add(p); db.commit()
    assert user_client.post(f"/partners/{p.id}/delete").status_code == 403
    assert admin_client.post(f"/partners/{p.id}/delete", follow_redirects=False).status_code == 303


def test_delete_removes_row_and_mirror_file(user_client, db, settings):
    user_client.post("/partners", data={"name": "그린테크"})
    p = db.query(Partner).one()
    md = settings.knowledge_dir / "partners" / f"{p.id}.md"
    assert md.exists()
    r = user_client.post(f"/partners/{p.id}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/partners"
    db.expire_all()
    assert db.query(Partner).count() == 0
    assert not md.exists()


def test_customer_with_projects_cannot_be_deleted(admin_client, db):
    c = Customer(name="한빛병원"); db.add(c); db.flush()
    db.add(Project(name="유지보수", customer_id=c.id)); db.commit()
    r = admin_client.post(f"/customers/{c.id}/delete")
    assert r.status_code == 400 and "프로젝트" in r.text
    assert db.get(Customer, c.id) is not None


def test_deleting_partner_clears_solution_partner(admin_client, db, settings):
    p = Partner(name="그린테크"); db.add(p); db.flush()
    s = Solution(name="AI 절감", partner_id=p.id); db.add(s); db.commit()
    admin_client.post(f"/partners/{p.id}/delete")
    db.refresh(s)
    assert s.partner_id is None
    assert "그린테크" not in (settings.knowledge_dir / "solutions" / str(s.id) / "card.md").read_text(encoding="utf-8")


def test_business_requires_customer_and_missing_id_is_404(user_client):
    assert user_client.post("/projects", data={"name": "유지보수"}).status_code == 400
    r = user_client.get("/customers/999")
    assert r.status_code == 404 and "항목을 찾을 수 없습니다." in r.text


def test_business_create_search_by_name_or_customer_and_delete_unlinks_activity(user_client, db, settings):
    c = Customer(name="한빛병원"); db.add(c); db.commit()
    r = user_client.post("/projects", data={"name": "유지보수", "customer_id": c.id}, follow_redirects=False)
    assert r.status_code == 303
    b = db.query(Project).one()
    assert b.created_by_id is not None
    assert (settings.knowledge_dir / "projects" / f"{b.id}.md").exists()
    assert "유지보수" in user_client.get("/projects?q=유지").text
    assert "유지보수" in user_client.get("/projects?q=한빛").text
    assert "유지보수" not in user_client.get("/projects?q=없는이름").text
    # 고객 상세에 사업이 보인다
    assert "유지보수" in user_client.get(f"/customers/{c.id}").text
    # 활동 기록 연결
    a = Activity(raw_text="메모", title="킥오프", date=date(2026, 1, 5)); a.projects.append(b)
    db.add(a); db.commit()
    assert "킥오프" in user_client.get(f"/projects/{b.id}").text
    project_id = b.id
    r = user_client.post(f"/projects/{project_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Project).count() == 0
    assert db.get(Activity, a.id) is not None
    assert not (settings.knowledge_dir / "projects" / f"{project_id}.md").exists()
    assert "유지보수" not in (settings.knowledge_dir / "activities" / "2026" / f"{a.id}.md").read_text(encoding="utf-8")


def test_business_with_unknown_customer_rejected(user_client):
    r = user_client.post("/projects", data={"name": "유지보수", "customer_id": "999"})
    assert r.status_code == 400


def test_business_edit_form_shows_customer_select(user_client, db):
    c = Customer(name="한빛병원"); db.add(c); db.flush()
    b = Project(name="유지보수", customer_id=c.id); db.add(b); db.commit()
    html = user_client.get(f"/projects/{b.id}/edit").text
    assert "<select" in html and "한빛병원" in html
    assert user_client.post(
        f"/projects/{b.id}", data={"name": "유지보수2", "customer_id": c.id, "status": "진행"},
        follow_redirects=False,
    ).status_code == 303
    db.refresh(b)
    assert b.name == "유지보수2" and b.status == "진행"


def test_login_required(client):
    r = client.get("/partners", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"

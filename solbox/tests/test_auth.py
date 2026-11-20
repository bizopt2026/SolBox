from conftest import make_user

from app.models import User


def test_anonymous_is_redirected_to_login(client):
    r = client.get("/admin/users", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_success_and_wrong_password(client, db):
    make_user(db, "kim", password="pw123456")
    assert client.post("/login", data={"username": "kim", "password": "x"}).status_code == 401
    r = client.post("/login", data={"username": "kim", "password": "pw123456"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_normal_user_cannot_open_admin(user_client):
    assert user_client.get("/admin/users").status_code == 403


def test_admin_creates_user_and_duplicate_username_rejected(admin_client, db):
    data = {"username": "lee", "name": "이대리", "password": "pw123456", "role": "user"}
    assert admin_client.post("/admin/users", data=data, follow_redirects=False).status_code == 303
    assert db.query(User).filter_by(username="lee").one().role == "user"
    assert admin_client.post("/admin/users", data=data).status_code == 400


def test_deactivated_user_session_is_rejected(user_client, db):
    db.query(User).filter_by(username="kim").one().is_active = False
    db.commit()
    r = user_client.get("/admin/users", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_htmx_request_gets_hx_redirect(client):
    r = client.get("/admin/users", headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/login"


def test_admin_cannot_deactivate_self(admin_client, db):
    me = db.query(User).filter_by(username="admin").one()
    r = admin_client.post(f"/admin/users/{me.id}", data={"name": "관리자", "role": "admin"})
    assert r.status_code == 400


def test_admin_page_shows_admin_menu_and_logout_clears_session(admin_client):
    page = admin_client.get("/admin/users")
    assert page.status_code == 200 and "관리" in page.text and "솔루션" in page.text
    assert admin_client.post("/logout", follow_redirects=False).status_code == 303
    assert admin_client.get("/admin/users", follow_redirects=False).status_code == 303


def test_admin_updates_other_user_and_password(admin_client, client, db):
    other = make_user(db, "park")
    r = admin_client.post(
        f"/admin/users/{other.id}",
        data={"name": "박과장", "role": "user", "is_active": "on", "password": "newpw9999"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    ok = client.post("/login", data={"username": "park", "password": "newpw9999"}, follow_redirects=False)
    assert ok.status_code == 303


def test_create_admin_cli_helper(app, db):
    from app.cli import create_admin

    create_admin(app.state.session_factory, "root", "루트", "pw1234")
    assert db.query(User).filter_by(username="root").one().role == "admin"


def test_session_cookie_is_samesite_lax(client, db):
    make_user(db, "kim", password="pw1234")
    r = client.post("/login", data={"username": "kim", "password": "pw1234"}, follow_redirects=False)
    assert "samesite=lax" in r.headers["set-cookie"].lower()


def test_http_errors_render_korean_html_page_for_browsers(user_client):
    r = user_client.get("/admin/users", headers={"accept": "text/html"})
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("text/html")
    assert '<a href="/">' in r.text and "솔박스" in r.text
    assert "관리자" in r.text  # 403 의 안내 문구를 그대로 보여 준다


def test_http_errors_are_plain_text_for_htmx(user_client):
    r = user_client.get("/admin/users", headers={"HX-Request": "true"})
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("text/plain") and "관리자" in r.text

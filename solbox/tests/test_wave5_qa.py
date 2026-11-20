"""화면 마무리(5차): 본문 건너뛰기, 파비콘, 500 화면, 처음 시작하기 안내, 파일 고르기 칸."""
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app.models import Activity, Customer, Solution
from app.routers.home import is_fresh_install


def test_skip_link_lang_title_and_inline_favicon(user_client):
    page = user_client.get("/").text
    assert '<html lang="ko">' in page
    assert '<a class="skip" href="#main">' in page and '<main id="main"' in page
    assert page.index('class="skip"') < page.index('class="side"')  # 첫 Tab 이 본문 건너뛰기
    assert "<title>홈 · 솔박스</title>" in page
    assert 'rel="icon"' in page and 'href="data:image/svg+xml' in page
    assert "http://" not in page.split('rel="icon"')[1].split(">")[0].replace("http://www.w3.org/2000/svg", "")


def test_login_page_has_favicon_and_skip(client):
    page = client.get("/login").text
    assert 'rel="icon"' in page and 'class="skip"' in page


def test_server_error_page_is_friendly_and_leaks_nothing(app, user_client, caplog):
    boom = APIRouter()

    @boom.get("/_boom")
    def _boom():
        raise RuntimeError("비밀 내부 경로 /srv/secret/token-123")

    app.include_router(boom)
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.update(user_client.cookies)
    with caplog.at_level("ERROR", logger="app"):
        r = c.get("/_boom")
    assert r.status_code == 500
    assert "문제가 생겼어요" in r.text and 'href="/"' in r.text
    assert "token-123" not in r.text and "RuntimeError" not in r.text and "Traceback" not in r.text
    assert any("token-123" in (rec.exc_text or "") or "RuntimeError" in str(rec.exc_info) for rec in caplog.records)  # 로그에는 남는다
    htmx = c.get("/_boom", headers={"HX-Request": "true"})
    assert htmx.status_code == 500 and "token-123" not in htmx.text


def test_fresh_install_guide_only_when_nothing_exists(user_client, db):
    assert is_fresh_install(db)
    page = user_client.get("/").text
    assert "처음 시작하기" in page and "파일 올리기" in page and "정리안 확인" in page and "제안 만들기" in page
    db.add(Customer(name="고객")); db.commit()
    assert not is_fresh_install(db)
    assert "처음 시작하기" not in user_client.get("/").text


def test_fresh_install_each_kind_hides_guide(db):
    assert is_fresh_install(db)
    db.add(Solution(name="가")); db.commit()
    assert not is_fresh_install(db)
    db.query(Solution).delete(); db.add(Activity(raw_text="x")); db.commit()
    assert not is_fresh_install(db)


def test_bare_file_inputs_use_styled_pick_label(admin_client, db):
    page = admin_client.get("/activities/new").text
    assert 'class="filepick"' in page and "파일 고르기" in page and 'type="file" name="memo_file"' in page
    s = Solution(name="가"); db.add(s); db.commit()
    page = admin_client.get(f"/solutions/{s.id}").text
    assert 'class="filepick"' in page and 'name="files" multiple required' in page


def test_page_header_detail_class_only_on_detail_pages(admin_client, db):
    s = Solution(name="가"); db.add(s); db.commit()
    assert 'class="page-header detail"' in admin_client.get(f"/solutions/{s.id}").text
    assert 'class="page-header"' in admin_client.get("/solutions").text

"""좁은 화면: 모든 표는 .tablewrap 안에 있고, 좁은 화면 규칙이 stylesheet 에 있다."""
import re
from datetime import date

from app.models import Activity, Customer, Partner, Solution
from tests.conftest import refining_proposal


def _unwrapped_tables(html):
    return [m.start() for m in re.finditer(r"<table", html) if not html[:m.start()].endswith('<div class="tablewrap">')]


def test_every_table_is_inside_a_tablewrap(admin_client, db, settings):
    p = refining_proposal(db, settings)
    p.outline = {"title": "t", "slides": [
        {"kind": "cover", "title": "표지"},
        {"kind": "table", "title": "표", "columns": ["a", "b"], "rows": [["1", "2"]]}]}
    s = Solution(name="S", created_by_id=1)
    db.add_all([s, Partner(name="P"), Customer(name="C"), Activity(raw_text="x", title="t", date=date.today())])
    db.commit()
    urls = ["/", "/solutions", "/customers", "/businesses", "/partners", "/proposals", "/activities", "/research",
            f"/solutions/{s.id}", f"/proposals/{p.id}/outline", "/admin/users", "/admin/jobs", "/admin/research", "/admin/system"]
    for url in urls:
        html = admin_client.get(url).text
        assert _unwrapped_tables(html) == [], url


def test_stylesheet_has_narrow_screen_rules(client):
    css = client.get("/static/app.css").text
    assert "@media (max-width: 900px)" in css and ".tablewrap" in css and "white-space:nowrap" in css


def test_topbar_is_sticky_and_menu_closes_on_outside_click_and_escape(admin_client):
    css = admin_client.get("/static/app.css").text
    assert re.search(r"\.topbar \{ position:sticky; top:0; z-index:10;", css)
    html = admin_client.get("/").text
    assert 'details.menu[open]' in html and '"Escape"' in html and 'addEventListener("click"' in html

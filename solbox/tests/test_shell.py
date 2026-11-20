"""앱 셸(사이드 메뉴·상단 줄·제목 줄): 메뉴는 온톨로지 정의에서 나오고, 모든 화면이 같은 제목 매크로를 쓴다."""
import re
from pathlib import Path

import pytest

from app import ontology
from app.models import AIJob, Customer, Intake, Solution

APP_DIR = Path(__file__).resolve().parent.parent / "app"
TEMPLATES = APP_DIR / "templates"


def _side(html):
    return re.search(r'<aside class="side".*?</aside>', html, re.S).group(0)


def test_every_nav_item_has_a_known_group():
    groups = dict(ontology.NAV_GROUPS)
    assert list(groups) == ["today", "discover", "sales", "admin"]
    for item in ontology.NAV_ITEMS + ontology.ADMIN_NAV_ITEMS:
        assert item.group in groups, item
    for t in ontology.TYPES.values():
        if t.nav:
            assert t.nav_group in groups, f"{t.code}: 메뉴에 있는 대상은 묶음(nav_group)이 있어야 한다"
            assert any(i.path == t.list_url.strip("/") and i.label == t.nav for i in ontology.NAV_ITEMS), f"{t.code}: 메뉴 정의에 빠졌다"
        else:
            assert t.nav_group is None, t.code


def test_nav_groups_order_and_labels():
    out = ontology.nav_groups(is_admin=False)
    assert [(g.label, [i.label for i in g.items]) for g in out] == [
        ("오늘", ["홈", "올리기 묶음", "활동 기록"]),
        ("발굴", ["솔루션", "파트너", "리서치"]),
        ("영업", ["고객", "영업기회", "제안", "프로젝트", "BM"]),
    ]
    admin = ontology.nav_groups(is_admin=True)
    assert admin[-1].label == "관리"
    assert [i.href for i in admin[-1].items] == ["/admin/users", "/admin/jobs", "/admin/research", "/admin/system"]


def test_current_nav_item():
    cur = ontology.nav_current
    assert cur("/") == "" and cur("/solutions") == "solutions" and cur("/solutions/3/edit") == "solutions"
    assert cur("/intake/new") == "intake" and cur("/research/runs/2") == "research"
    assert cur("/admin/jobs") == "admin/jobs" and cur("/admin/research") == "admin/research"
    assert cur("/search") is None and cur("/nothing") is None
    assert cur("/follow-ups") == ""  # 후속 조치 목록은 홈 구역


def test_every_nav_url_resolves(admin_client):
    for g in ontology.nav_groups(is_admin=True):
        for item in g.items:
            assert admin_client.get(item.href).status_code == 200, item.href


def test_sidebar_is_grouped_from_the_ontology(user_client):
    side = _side(user_client.get("/solutions").text)
    for key, label in ontology.NAV_GROUPS[:3]:
        assert f'aria-label="{label}"' in side
    assert side.count('aria-current="page"') == 1
    assert 'href="/solutions" aria-current="page"' in side
    assert 'href="/intake/new"' in side and "올리기" in side
    assert "솔루션 사업 워크스페이스" in side


def test_admin_group_only_for_admins(user_client, admin_client):
    assert "/admin/users" not in user_client.get("/").text
    side = _side(admin_client.get("/admin/jobs").text)
    assert 'aria-label="관리"' in side and 'href="/admin/jobs" aria-current="page"' in side
    assert side.count('aria-current="page"') == 1


def test_detail_page_highlights_its_section(user_client, db):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    assert 'href="/customers" aria-current="page"' in _side(user_client.get(f"/customers/{c.id}").text)


def test_sidebar_user_block_has_logout_form(user_client):
    side = _side(user_client.get("/").text)
    assert re.search(r'<form[^>]*method="post"[^>]*action="/logout"', side)
    assert "로그아웃" in side and "kim" in side


def test_logged_out_pages_have_no_sidebar(client):
    html = client.get("/login").text
    assert "<aside" not in html and "솔박스" in html and 'class="auth"' in html


def test_ready_badge_stays_in_sidebar(user_client, db):
    db.add(Intake(status="ready", created_by_id=1))
    db.commit()
    assert re.search(r'<a href="/intake"[^>]*>.*?class="nav-badge"', _side(user_client.get("/").text), re.S)


# --- AI 상태 -------------------------------------------------------------------------------------------------------


def _status(html):
    m = re.search(r'<(?:a|span) class="aistat[^"]*"[^>]*>(.*?)</(?:a|span)>', html, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else None


def test_ai_status_states(admin_client, db):
    # 작업 처리기가 꺼진 시험 설정에서는 "AI 꺼짐" (고장이 아니라 꺼 둔 것)
    assert _status(admin_client.get("/").text) == "AI 꺼짐"


@pytest.fixture
def ai_on(app):
    app.state.settings.ai_worker_enabled = True


def test_ai_status_available_busy_and_problem(admin_client, db, ai_on):
    assert _status(admin_client.get("/").text) == "AI 사용 가능 · 대기 0"
    db.add_all([AIJob(kind="activity_draft", status="pending"), AIJob(kind="activity_draft", status="pending")])
    db.commit()
    assert _status(admin_client.get("/").text) == "AI 사용 가능 · 대기 2"
    db.add(AIJob(kind="activity_draft", status="running"))
    db.commit()
    assert _status(admin_client.get("/").text) == "AI 작업 중 1 · 대기 2"
    from datetime import datetime

    db.query(AIJob).delete()
    db.add(AIJob(kind="activity_draft", status="failed", error_kind="auth", finished_at=datetime.now()))
    db.commit()
    assert _status(admin_client.get("/").text) == "AI 사용 불가"  # 로그인이 안 된 CLI: 모두에게 쓸 수 없다


def test_one_failed_job_is_a_warning_not_unavailable(admin_client, db, ai_on):
    from datetime import datetime, timedelta

    now = datetime.now()
    db.add(AIJob(kind="intake_analyze", status="failed", error_kind="timeout", finished_at=now))
    db.commit()
    html = admin_client.get("/").text
    assert _status(html) == "마지막 AI 작업 실패 (올린 파일 정리)" and 'class="aistat warn"' in html
    # 그 뒤에 성공한 작업이 나오면 다시 사용 가능
    db.add(AIJob(kind="activity_draft", status="done", finished_at=now + timedelta(seconds=5)))
    db.commit()
    assert _status(admin_client.get("/").text) == "AI 사용 가능 · 대기 0"
    # CLI 없음도 사용 불가
    db.add(AIJob(kind="activity_draft", status="failed", error_kind="not_installed", finished_at=now + timedelta(seconds=9)))
    db.commit()
    assert _status(admin_client.get("/").text) == "AI 사용 불가"


def test_status_text_is_in_the_title_for_the_dot_only_phone_layout(admin_client):
    html = admin_client.get("/").text
    assert 'title="AI 꺼짐 · AI 작업 보기"' in html and 'class="aistat-text"' in html


def test_ai_status_links_to_jobs_only_for_admin(admin_client, user_client, ai_on):
    assert '<a class="aistat' in admin_client.get("/").text and 'href="/admin/jobs"' in admin_client.get("/").text
    assert '<span class="aistat' in user_client.get("/").text


def test_ai_status_hidden_when_logged_out_and_never_raises(client, user_client, monkeypatch):
    assert "aistat" not in client.get("/login").text
    import app.main as main

    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(main, "_ai_status_data", boom)
    r = user_client.get("/")
    assert r.status_code == 200 and "aistat" not in r.text


# --- 제목 매크로 ---------------------------------------------------------------------------------------------------

# 로그인·오류 화면은 사이드 메뉴가 없는 가운데 카드라 제목 매크로를 쓰지 않는다
HEADER_EXEMPT = {"auth/login.html", "error.html"}


def _top_level_templates():
    out = []
    for p in sorted(TEMPLATES.rglob("*.html")):
        rel = p.relative_to(TEMPLATES).as_posix()
        if rel == "base.html" or p.name.startswith("_"):
            continue
        out.append((rel, p.read_text(encoding="utf-8")))
    return out


def test_every_page_template_extends_base():
    """밑줄로 시작하지 않는 템플릿은 모두 화면이다: 같은 틀(base.html)을 확장한다. 조각은 밑줄로 시작한다."""
    for rel, text in _top_level_templates():
        assert re.search(r"\{%\s*extends \"base.html\"", text), rel


def test_every_page_uses_the_page_header_macro():
    missing = []
    for rel, text in _top_level_templates():
        if not re.search(r"\{%\s*extends", text) or rel in HEADER_EXEMPT:
            continue
        if not re.search(r"\b(page_header|list_head|detail_head)\(", text):  # 목록·상세 제목 줄은 page_header 를 감싼 것
            missing.append(rel)
    assert not missing, f"page_header 를 쓰지 않는 화면: {missing}"


def test_no_page_hand_writes_an_h1():
    for rel, text in _top_level_templates():
        if rel in HEADER_EXEMPT or not re.search(r"\{%\s*extends", text):
            continue
        assert "<h1" not in text, f"{rel}: 제목은 page_header 로만 그린다"


def test_no_external_hosts_in_templates_and_css():
    pat = re.compile(r"""(?:src|href|action|url\()\s*=?\s*["']?\s*(?:https?:)?//""", re.I)
    files = [*TEMPLATES.rglob("*.html"), APP_DIR / "static" / "app.css", APP_DIR / "static" / "app.js"]
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            assert not pat.search(line), f"{f.name}:{n}: 바깥 주소를 부른다"
            assert "@import" not in line and "fonts.googleapis" not in line, f"{f.name}:{n}"
    css = (APP_DIR / "static" / "app.css").read_text(encoding="utf-8")
    assert "http://" not in css and "https://" not in css


def test_vendored_font_and_licence_ship_with_the_app():
    fonts = APP_DIR / "static" / "fonts"
    assert (fonts / "PretendardVariable.woff2").stat().st_size > 1_000_000
    assert "SIL OPEN FONT LICENSE" in (fonts / "Pretendard-OFL.txt").read_text(encoding="utf-8")
    css = (APP_DIR / "static" / "app.css").read_text(encoding="utf-8")
    assert "@font-face" in css and "/static/fonts/PretendardVariable.woff2" in css and "font-display: swap" in css
    font_token = re.search(r"--font:([^;]*);", css).group(1)
    assert font_token.strip().startswith('"Pretendard Variable"') and '"Malgun Gothic"' in font_token and "sans-serif" in font_token
    assert "var(--font)" in css.split("\nbody {")[1].split("}")[0]


def test_font_is_served(client):
    r = client.get("/static/fonts/PretendardVariable.woff2")
    assert r.status_code == 200 and r.headers["content-type"].startswith("font/woff2")


def test_badge_tones_cover_real_codes_only():
    for key, tones in ontology.CODE_TONES.items():
        assert key in ontology.CODE_LISTS, key
        assert set(tones) <= set(ontology.CODE_LISTS[key].items), key
        assert set(tones.values()) <= set(ontology.TONES), key
    css = (APP_DIR / "static" / "app.css").read_text(encoding="utf-8")
    for tone in ontology.TONES:
        assert f".badge.tone-{tone}" in css
    # 단계 목록은 모든 값에 색이 있다
    for key in ("stage", "opportunity_stage", "intake_status", "proposal_status"):
        assert set(ontology.CODE_TONES[key]) == set(ontology.CODE_LISTS[key].items), key

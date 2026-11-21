"""베타 E 묶음: 3.11 에서도 돌아야 하는 문법, AI 문, 문구 걸러내기 등의 회귀 시험."""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def _py_files():
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def fstring_problems(source: str) -> list[str]:
    """3.12 에서만 되는 f-string 쓰기(같은 따옴표 겹쓰기·치환 칸의 역슬래시·주석)를 찾는다.

    3.12 이상에서는 f-string 이 FSTRING_START/MIDDLE/END 토큰으로 나뉘므로 그것으로 센다.
    3.11 이하에서는 컴파일 자체가 같은 문법을 거절하므로 이 검사는 건너뛴다.
    """
    if sys.version_info < (3, 12):
        return []
    out: list[str] = []
    stack: list[dict] = []  # 열린 f-string: {"q": 따옴표, "depth": 치환 칸 깊이}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        t = tok.type
        if t == tokenize.FSTRING_START:
            q = tok.string[-1]
            if len(tok.string) >= 4 and tok.string[-3:] in ('"""', "'''"):
                q = tok.string[-3:]
            if stack and stack[-1]["depth"] > 0 and stack[-1]["q"] == q:
                out.append(f"{tok.start[0]}: 같은 따옴표 {q} 를 치환 칸 안에서 다시 씀")
            stack.append({"q": q, "depth": 0})
        elif t == tokenize.FSTRING_END:
            stack.pop()
        elif stack:
            top = stack[-1]
            if t == tokenize.OP and tok.string == "{":
                top["depth"] += 1
            elif t == tokenize.OP and tok.string == "}":
                top["depth"] = max(0, top["depth"] - 1)
            if top["depth"] > 0:
                if t == tokenize.COMMENT:
                    out.append(f"{tok.start[0]}: 치환 칸 안의 주석")
                if t == tokenize.STRING:
                    body = tok.string.lstrip("rbuRBU")
                    if body[:1] == top["q"][:1] and not (top["q"] == '"""' and body[:3] != '"""'):
                        out.append(f"{tok.start[0]}: 같은 따옴표 {top['q']} 문자열을 치환 칸 안에서 씀")
                    if "\\" in tok.string:
                        out.append(f"{tok.start[0]}: 치환 칸 안 문자열의 역슬래시")
    return out


def test_scanner_catches_nested_same_quotes():
    if sys.version_info < (3, 12):
        pytest.skip("3.12 이상에서만 토큰으로 검사한다 (3.11 은 컴파일이 거절)")
    bad = 'x = f"a {f("b")} c"\n'
    assert fstring_problems(bad)
    assert not fstring_problems("y = f\"a {f('b')} c\"\n")
    assert fstring_problems('z = f"{\'\\n\'.join(a)}"\n')


def test_no_py312_fstring_syntax_in_app():
    problems = []
    for p in _py_files():
        for msg in fstring_problems(p.read_text(encoding="utf-8")):
            problems.append(f"{p.relative_to(ROOT)}:{msg}")
    assert not problems, "\n".join(problems)


def test_app_compiles_on_py311_when_available():
    exe = shutil.which("python3.11")
    if exe is None:
        pytest.skip("python3.11 이 없다")
    r = subprocess.run([exe, "-m", "compileall", "-q", str(APP), str(ROOT / "tests")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_newer_stdlib_features():
    import re
    pat = re.compile(r"itertools\.batched|\bPath\([^)]*\)\.walk\(|\.walk\(top|copy\.replace\(|"
                     r"from typing import[^\n]*\boverride\b|^type \w+ *=|^\s*def \w+\[|^\s*class \w+\[", re.M)
    hits = []
    for p in _py_files():
        for m in pat.finditer(p.read_text(encoding="utf-8")):
            hits.append(f"{p.relative_to(ROOT)}: {m.group(0)}")
    assert not hits, hits


# ---- 공통 보조 ----

import inspect
import re
from datetime import datetime, timedelta

from app.models import AIJob, Intake, Proposal
from tests.conftest import make_proposal_world, outlined_proposal, refining_proposal


def _fail(db, kind="not_installed", when=None):
    db.add(AIJob(kind="intake_analyze", input={}, status="failed", error_kind=kind, finished_at=when or datetime.now()))
    db.commit()


# ---- E2 사용 불가는 막지 않고, 성공하면 저절로 풀린다 ----

def test_down_keeps_buttons_enabled_and_success_clears_the_note(app, admin_client, db, worker_on):
    _fail(db)
    page = admin_client.get("/activities/new").text
    assert 'data-ai-down="1"' in page and "AI 사용 불가" in page
    assert "다시 시도하면 연결을 다시 확인" in page and 'href="/admin/system"' in page and "AI 연결 확인" in page
    assert "disabled" not in re.search(r'<button[^>]*formaction="/activities/draft"[^>]*>', page).group(0)
    p = refining_proposal(db, app.state.settings)
    page = admin_client.get(f"/proposals/{p.id}").text
    assert 'data-ai-down="1"' in page and not re.search(r'<button[^>]*disabled[^>]*>(보내기|구성안 만들기|방향 다시 만들기)', page)
    assert "누를 수 없어요" not in page
    db.add(AIJob(kind="intake_analyze", input={}, status="done", finished_at=datetime.now() + timedelta(seconds=5)))
    db.commit()
    page = admin_client.get("/activities/new").text
    assert "data-ai-down" not in page and "AI 사용 불가" not in page


def test_down_note_for_plain_user_tells_to_ask_admin(app, user_client, db, worker_on):
    _fail(db, "auth")
    page = user_client.get("/activities/new").text
    assert "관리자에게 알려 주세요" in page and 'href="/admin/system"' not in page


def test_no_note_tells_to_press_a_disabled_button(app, admin_client, db, worker_on):
    from app import ontology
    for text in (*ontology.AI_BLOCK_REASONS.values(), *ontology.AI_BLOCK_UPLOAD.values(), ontology.AI_DOWN_RETRY):
        assert "누를 수 없" not in text
    assert "누르세요" not in ontology.AI_BLOCK_UPLOAD["down"]


# ---- E4 템플릿이 가져오는 이름은 모두 매크로다 ----

def test_every_template_import_names_a_macro():
    tdir = APP / "templates"
    pat = re.compile(r'\{%-?\s*from\s+"([^"]+)"\s+import\s+([^%]*?)\s*-?%\}')
    bad = []
    for f in sorted(tdir.rglob("*.html")):
        for m in pat.finditer(f.read_text(encoding="utf-8")):
            src = (tdir / m.group(1))
            text = src.read_text(encoding="utf-8")
            for part in m.group(2).split(","):
                name = part.strip().split(" as ")[0].strip()
                if not name:
                    continue
                if not re.search(r"\{%-?\s*macro\s+" + re.escape(name) + r"\s*\(", text) and not re.search(r"\{%-?\s*set\s+" + re.escape(name) + r"\b", text):
                    bad.append(f"{f.relative_to(tdir)}: {name} (from {m.group(1)})")
    assert not bad, bad


def test_full_proposal_page_shows_stalled_notice_when_off(app, admin_client, db, settings):
    p = refining_proposal(db, settings)
    job = AIJob(kind="proposal_outline", input={"proposal_id": p.id}, status="pending")
    db.add(job)
    db.flush()
    p.active_job_id = job.id
    db.commit()
    page = admin_client.get(f"/proposals/{p.id}").text  # 처리기 꺼짐(시험 서버 기본)
    assert "AI 작업 처리기가 꺼져 있어 작업이 시작되지 않습니다" in page


# ---- E5 서버가 AI 시작을 모두 거절한다 (꺼짐) ----

# AI 작업을 새로 만드는 POST 라우트 (require_ai_on 을 다는 곳). 새 것이 생기면 여기와 알림 표를 함께 고친다.
AI_POSTS = {
    "/solutions/{solution_id}/draft", "/activities/draft", "/activities/draft/{job_id}/retry", "/admin/system/ai-check",
    "/jobs/{job_id}/retry", "/proposals/{proposal_id}/directions", "/proposals/{proposal_id}/chat",
    "/proposals/{proposal_id}/chat/retry", "/proposals/{proposal_id}/outline", "/research/run",
    "/research/reports/{report_id}/retry", "/intake/{intake_id}/analyze",
}
# 처리기가 꺼져 있으면 일은 하되 작업을 넣지 않는 라우트 (알림으로 알린다)
AI_KEEPS = {"/intake", "/proposals"}
JOB_MAKERS = ("enqueue(", "start_job(", "start_analysis(", "start_run(", "AIJob(", "_start(")


def _post_routes(app):
    """포함된 라우터(`_IncludedRouter`)를 풀어 모든 POST 라우트 (경로에 접두어가 이미 붙어 있다)."""
    out = []
    for r in app.routes:
        inner = getattr(r, "original_router", None)
        for x in (inner.routes if inner is not None else [r]):
            if "POST" in (getattr(x, "methods", None) or ()):
                out.append(x)
    return out


def _has_gate(route) -> bool:
    from app.ai.gate import require_ai_on
    return any(d.dependency is require_ai_on for d in route.dependencies)


def test_ai_posts_are_exactly_the_gated_routes(app):
    gated = {r.path for r in _post_routes(app) if _has_gate(r)}
    assert gated == AI_POSTS


def test_no_other_post_route_makes_ai_jobs(app):
    for r in _post_routes(app):
        if r.path in AI_POSTS or r.path in AI_KEEPS:
            continue
        src = inspect.getsource(r.endpoint)
        assert not any(k in src for k in JOB_MAKERS), r.path


@pytest.mark.ai_gate
def test_every_ai_post_refuses_when_off(app, admin_client, db, settings):
    assert not settings.ai_worker_enabled
    sol = refining_proposal(db, settings)  # 제안 건 1 (고객·솔루션 포함)
    paths = {
        "/solutions/{solution_id}/draft": "/solutions/1/draft", "/activities/draft": "/activities/draft",
        "/activities/draft/{job_id}/retry": "/activities/draft/1/retry", "/admin/system/ai-check": "/admin/system/ai-check",
        "/jobs/{job_id}/retry": "/jobs/1/retry", "/proposals/{proposal_id}/directions": f"/proposals/{sol.id}/directions",
        "/proposals/{proposal_id}/chat": f"/proposals/{sol.id}/chat", "/proposals/{proposal_id}/chat/retry": f"/proposals/{sol.id}/chat/retry",
        "/proposals/{proposal_id}/outline": f"/proposals/{sol.id}/outline", "/research/run": "/research/run",
        "/research/reports/{report_id}/retry": "/research/reports/1/retry", "/intake/{intake_id}/analyze": "/intake/1/analyze",
    }
    assert set(paths) == AI_POSTS
    before = db.query(AIJob).count()
    for route, url in paths.items():
        r = admin_client.post(url, data={"message": "x", "raw_text": "메모"}, headers={"referer": "http://testserver/proposals/1"}, follow_redirects=False)
        assert r.status_code == 303, (route, r.status_code)
        assert r.headers["location"].startswith("/proposals/1?notice=ai_off&nt="), (route, r.headers["location"])
    db.expire_all()
    assert db.query(AIJob).count() == before  # 끝나지 않는 대기 줄이 생기지 않았다


@pytest.mark.ai_gate
def test_refusal_goes_back_to_same_screen_and_htmx_gets_header(app, admin_client, db, settings):
    r = admin_client.post("/jobs/1/retry", headers={"referer": "http://testserver/admin/jobs?status=failed", "HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 204 and r.headers["HX-Redirect"].startswith("/admin/jobs?status=failed&notice=ai_off&nt=")
    r = admin_client.post("/jobs/1/retry", headers={"referer": "http://evil.example/x"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?notice=ai_off")
    page = admin_client.get(r.headers["location"]).text
    assert "AI 작업 처리기가 꺼져 있어 시작하지 못했습니다" in page


@pytest.mark.ai_gate
def test_flush_hook_refuses_new_jobs_in_request_sessions(app, db):
    from app.ai.gate import AIOff
    db.info["ai_off"] = True
    db.add(AIJob(kind="ai_ping", input={}, status="pending"))
    with pytest.raises(AIOff):
        db.flush()
    db.rollback()
    db.info["ai_off"] = False
    db.add(AIJob(kind="ai_ping", input={}, status="pending"))
    db.flush()
    db.rollback()


@pytest.mark.ai_gate
def test_upload_while_off_keeps_files_and_creates_no_job(app, user_client, db):
    r = user_client.post("/intake", data={"note": ""}, files=[("files", ("a.txt", "회의 메모".encode(), "text/plain"))], follow_redirects=False)
    assert r.status_code == 303 and "notice=intake_kept_ai_off" in r.headers["location"]
    it = db.query(Intake).one()
    assert it.status == "uploaded" and it.job_id is None and len(it.files) == 1
    assert db.query(AIJob).count() == 0
    page = user_client.get(r.headers["location"]).text
    assert "정리는 시작하지 않았습니다" in page and "정리 시작" in page


@pytest.mark.ai_gate
def test_proposal_create_while_off_registers_without_job(app, admin_client, db, settings):
    p, c, *_ = make_proposal_world(db, settings)
    n = db.query(Proposal).count()
    r = admin_client.post("/proposals", data={"customer_id": str(c.id)}, follow_redirects=False)
    assert r.status_code == 303 and "notice=created_ai_off" in r.headers["location"]
    db.expire_all()
    assert db.query(Proposal).count() == n + 1 and db.query(AIJob).count() == 0


@pytest.mark.ai_gate
def test_off_disables_every_ai_control_on_screens(app, admin_client, db, settings):
    p = outlined_proposal(db, settings)
    failed = AIJob(kind="proposal_outline", input={"proposal_id": p.id}, status="failed", error_kind="timeout", finished_at=datetime.now())
    db.add(failed)
    db.flush()
    p.active_job_id = failed.id
    db.commit()
    page = admin_client.get(f"/proposals/{p.id}").text
    # AI 를 시작하는 폼 안의 모든 제출 버튼이 막혀 있다 (제안서 만들기 deck 은 AI 가 아니다)
    for form in re.findall(r'<form[^>]*action="/proposals/\d+/(?:directions|chat|chat/retry|outline)"[^>]*>.*?</form>', page, re.S):
        for btn in re.findall(r"<button[^>]*>", form):
            assert "disabled" in btn, btn
    assert "data-starter=" in page and not re.search(r'<button[^>]*data-starter="[^"]*"(?![^>]*disabled)[^>]*>', page)


def test_chat_ctrl_enter_handler_respects_disabled_submit():
    js = (APP / "static" / "app.js").read_text(encoding="utf-8")
    i = js.index('t.id !== "chat-message"')
    block = js[i:i + 700]
    assert "send.disabled" in block

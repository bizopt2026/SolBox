"""단위 1: 프로젝트 이름 바꾸기(코드·화면만), 옛 주소·옛 경로·옛 키 호환, 통일한 용어, 제안 후보 규칙."""
import json
from datetime import date

from fastapi.testclient import TestClient

from app import ontology
from app.ai.tasks import ActivityDraft, Mentions, match_mentions
from app.knowledge import KNOWLEDGE_FORMAT, KnowledgeMirror, rebuild_if_stale
from app.models import Activity, AppSetting, Customer, Project, Proposal, Solution
from app.sources import describe_source
from tests.conftest import need_rows, ready_intake


def _world(db):
    c = Customer(name="한빛병원")
    db.add(c); db.flush()
    p = Project(name="설비 유지보수", customer_id=c.id)
    db.add(p); db.commit()
    return c, p


# ---- 옛 주소 ----

def test_old_businesses_urls_redirect_permanently(user_client, db):
    c, p = _world(db)
    for old, new in (("/businesses", "/projects"), (f"/businesses/{p.id}", f"/projects/{p.id}"),
                     (f"/businesses/{p.id}/edit", f"/projects/{p.id}/edit"), ("/businesses/new?customer_id=3", "/projects/new?customer_id=3"),
                     ("/businesses?q=한빛", "/projects?q=%ED%95%9C%EB%B9%9B")):
        r = user_client.get(old, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == new, old
    assert user_client.get(f"/projects/{p.id}").status_code == 200


def test_forms_post_to_new_urls(user_client, db):
    c, p = _world(db)
    assert f'action="/projects/{p.id}"' in user_client.get(f"/projects/{p.id}/edit").text
    assert 'action="/projects"' in user_client.get("/projects/new").text
    assert 'name="project_id"' in user_client.get("/proposals/new").text


# ---- 용어 ----

def test_nav_and_pages_use_new_terms(user_client, db):
    c, p = _world(db)
    home = user_client.get("/").text
    assert ">프로젝트</a>" in home and "기존 사업" not in home and ">제안</a>" in home and ">올리기" in home
    for url in ("/projects", f"/projects/{p.id}", f"/customers/{c.id}", "/projects/new", f"/projects/{p.id}/edit", "/proposals/new"):
        t = user_client.get(url).text
        assert "기존 사업" not in t and "프로젝트" in t, url
    assert "프로젝트명" in user_client.get("/projects/new").text


def test_activity_pages_say_customer_needs(user_client, db):
    c, p = _world(db)
    a = Activity(raw_text="원문", title="킥오프", date=date(2026, 1, 5), projects=[p],
                 needs=need_rows(("한빛병원", "전력 절감")))
    db.add(a); db.commit()
    detail = user_client.get(f"/activities/{a.id}").text
    assert "고객 니즈" in detail and "수요 단서" not in detail
    assert f'href="/projects/{p.id}"' in detail
    form = user_client.get(f"/activities/{a.id}/edit").text
    assert "고객 니즈" in form and "수요 단서" not in form and "기존 사업" not in form and 'data-field="project_ids"' in form


def test_intake_page_terms(app, user_client, db, settings):
    it, s1, c1 = ready_intake(db, settings)
    page = user_client.get(f"/intake/{it.id}").text
    assert f"올리기 묶음 {it.id}번" in page and "올린 파일" in page


def test_mirror_headings_and_fields_use_ontology_terms(db, tmp_path):
    c, p = _world(db)
    s = Solution(name="S", summary="소개", applicable_types="병원", description="설명", stage="boxed")
    a = Activity(raw_text="원문", summary_md="내용", date=date(2026, 1, 5), projects=[p], solutions=[s],
                 needs=need_rows(("한빛병원", "절감")))
    db.add_all([s, a]); db.commit()
    m = KnowledgeMirror(tmp_path)
    m.sync(s); m.sync(c); m.sync(p); m.sync(a)
    sol = m.path_for(s).read_text(encoding="utf-8")
    assert "- 한 줄 소개: 소개" in sol and "- 적용 가능 유형: 병원" in sol and "## 상세 설명" in sol and "한 줄 요약" not in sol
    assert (tmp_path / "projects" / f"{p.id}.md").exists() and not (tmp_path / "businesses").exists()
    assert "## 프로젝트" in m.path_for(c).read_text(encoding="utf-8")
    act = m.path_for(a).read_text(encoding="utf-8")
    assert "- 일자: 2026-01-05" in act and "- 프로젝트:" in act and "## 고객 니즈" in act and "## 정리본" in act and "수요 단서" not in act


def test_research_suggestion_reason_and_sources_labels(app, db, settings, tmp_path):
    from app.models import ResearchReport, ResearchRun

    run = ResearchRun(trigger="manual", status="done"); db.add(run); db.flush()
    r = ResearchReport(run_id=run.id, kind="suggest", status="done", title="t",
                       suggestions=[{"kind": "new", "title": "T", "detail": "D", "reason": "왜냐하면", "sources": ["customers/1.md"]}])
    db.add(r); db.commit()
    text = KnowledgeMirror(tmp_path)
    text.sync(r)
    body = text.path_for(r).read_text(encoding="utf-8")
    assert "- 이유: 왜냐하면" in body and "- 출처: customers/1.md" in body and "근거:" not in body


# ---- 저장된 옛 값은 계속 읽는다 ----

def test_old_business_source_paths_still_resolve_and_are_never_rewritten(db):
    c, p = _world(db)
    d = describe_source(db, f"businesses/{p.id}.md")
    assert d == {"label": "설비 유지보수", "url": f"/projects/{p.id}", "kind": "프로젝트"}
    assert describe_source(db, f"projects/{p.id}.md")["url"] == f"/projects/{p.id}"
    assert describe_source(db, "businesses/9999.md")["url"] is None


def test_old_proposal_with_business_source_chip_renders(user_client, db, settings):
    from tests.conftest import make_proposal_world

    p, c, b, s1, s2, s3 = make_proposal_world(db, settings)
    old = f"businesses/{b.id}.md"
    p.directions = [{"title": "방향", "summary": "", "solutions": [{"solution_id": s1.id, "name": s1.name, "role": ""}],
                     "rationale": [{"text": "근거", "sources": [old]}], "effects": [], "needs_check": []}]
    p.status = "directions"
    db.commit()
    page = user_client.get(f"/proposals/{p.id}").text
    assert f'href="/projects/{b.id}"' in page and "설비 유지보수" in page
    db.refresh(p)
    assert p.directions[0]["rationale"][0]["sources"] == [old]


def test_mentions_accept_old_businesses_key():
    d = ActivityDraft.model_validate({"title": "t", "mentions": {"businesses": ["옛 사업명"], "solutions": []}})
    assert d.mentions.projects == ["옛 사업명"]
    assert "projects" in d.mentions.model_dump() and "businesses" not in d.mentions.model_dump()
    assert Mentions(projects=["x"]).projects == ["x"]


def test_match_mentions_finds_projects(db):
    c, p = _world(db)
    out = match_mentions(db, Mentions.model_validate({"businesses": ["설비 유지보수"]}))
    assert out["projects"]["matched"] == [p.id] and "businesses" not in out


def test_intake_plan_stored_with_businesses_key_still_renders(app, user_client, db, settings):
    it, s1, c1 = ready_intake(db, settings)
    c, p = _world(db)
    plan = json.loads(json.dumps(it.plan))
    for item in plan["items"]:
        if item["kind"] == "activity":
            item["matched"] = {"solutions": [], "customers": [], "businesses": [p.id], "partners": []}
            item["ambiguous"] = {"businesses": [{"name": "유지", "candidates": [p.id]}]}
            item["draft"]["mentions"] = {"customers": [], "solutions": [], "businesses": ["설비 유지보수"], "partners": []}
    it.plan = plan
    db.commit()
    page = user_client.get(f"/intake/{it.id}").text
    assert 'name="act1__project_ids" value="%d" checked' % p.id in page
    assert "설비 유지보수" in page
    assert "기존 사업" not in page


# ---- 시작할 때 사본을 한 번 다시 만든다 ----

def test_startup_rebuilds_mirror_once_when_old_folder_exists(app, db, settings):
    c, p = _world(db)
    stale = settings.knowledge_dir / "businesses"
    stale.mkdir(parents=True)
    (stale / f"{p.id}.md").write_text("# 옛 사본", encoding="utf-8")
    with TestClient(app):
        pass
    assert not stale.exists()
    assert (settings.knowledge_dir / "projects" / f"{p.id}.md").exists()
    db.expire_all()
    assert db.get(AppSetting, "knowledge_format").value == {"version": KNOWLEDGE_FORMAT}
    # 표시가 있으면 다시 만들지 않는다
    marker = settings.knowledge_dir / "marker.md"
    marker.write_text("x", encoding="utf-8")
    assert rebuild_if_stale(KnowledgeMirror(settings.knowledge_dir), db, settings.uploads_dir) is False
    assert marker.exists()
    stale.mkdir()
    assert rebuild_if_stale(KnowledgeMirror(settings.knowledge_dir), db, settings.uploads_dir) is True
    assert not stale.exists() and not marker.exists()


# ---- 제안 후보 규칙 ----

def test_candidate_rule_is_defined_once_and_described(db):
    from app.proposals import candidate_condition

    assert "보류" in ontology.proposal_candidate_rule_text()
    s_hold = Solution(name="보류", stage="hold"); s_in = Solution(name="접수", stage="intake")
    db.add_all([s_hold, s_in]); db.commit()
    names = lambda ids: sorted(s.name for s in db.query(Solution).where(candidate_condition(ids)))  # noqa: E731
    assert names([]) == ["접수"] and names([s_hold.id]) == ["보류", "접수"]


def test_directions_prompt_describes_candidates_and_uses_project_wording(app, user_client, db, settings, fake_claude):
    from tests.conftest import make_proposal_world
    from tests.test_proposal_directions import _run, _set, good_directions

    p, c, b, s1, s2, s3 = make_proposal_world(db, settings)
    _set(fake_claude, good_directions(s1, s2))
    user_client.post(f"/proposals/{p.id}/directions")
    _run(app, settings)
    prompt = fake_claude.calls()[0]["stdin"]
    assert f"projects/{b.id}.md" in prompt and "businesses/" not in prompt and "기존 사업" not in prompt
    assert "보류 단계를 뺀 모든 솔루션" in prompt and "{{" not in prompt

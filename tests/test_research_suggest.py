import json
from pathlib import Path

from app.ai.queue import process_next
from app.knowledge import KnowledgeMirror
from app.models import AIJob, ResearchReport, ResearchRun
from app.research import advance_run
from tests.conftest import make_research_world
from tests.test_research_area import AREA, AREA_JSON

SUGGEST = {"suggestions": [
    {"kind": "push", "title": "AI 공조를 병원 고객에 우선 제안", "reason": "전기요금 정책 변화", "solution_ids": [1, 999], "sources": ["solutions/1/card.md", "nope.md"]},
    {"kind": "push", "title": "솔루션 없는 push", "solution_ids": [999]},
    {"kind": "strange", "title": "수요반응(DR) 솔루션 발굴"},
    {"kind": "new", "title": ""}], "insights": ["설비 운영 계약 고객이 1차 대상"]}


def _suggest_json(world):
    text = json.dumps(SUGGEST, ensure_ascii=False)
    return text.replace("[1, 999]", f"[{world.s1.id}, 999]").replace("solutions/1/", f"solutions/{world.s1.id}/")


def _drain(app, settings):
    while process_next(app.state.session_factory, app.state.runner, settings):
        pass


def _go(app, admin_client, db, settings):
    admin_client.post("/research/run")
    _drain(app, settings)
    db.expire_all()
    return db.query(ResearchRun).one()


def _suggest_report(run):
    return next((r for r in run.reports if r.kind == "suggest"), None)


def test_full_run_produces_suggestions(app, admin_client, db, settings, fake_claude):
    world = make_research_world(db, settings)
    fake_claude.set(mode="ok", result=[AREA_JSON, AREA_JSON, _suggest_json(world)])
    run = _go(app, admin_client, db, settings)
    assert run.status == "done" and run.finished_at is not None
    sug = _suggest_report(run)
    assert [(s["kind"], s["title"]) for s in sug.suggestions] == [
        ("push", "AI 공조를 병원 고객에 우선 제안"), ("new", "솔루션 없는 push"), ("new", "수요반응(DR) 솔루션 발굴")]
    assert sug.suggestions[0]["solution_ids"] == [world.s1.id] and sug.suggestions[0]["sources"] == [f"solutions/{world.s1.id}/card.md"]
    assert sug.insights == ["설비 운영 계약 고객이 1차 대상"]
    last = fake_claude.calls()[-1]
    assert last["argv"][last["argv"].index("--tools") + 1] == "Read,Glob,Grep" and Path(last["cwd"]) == settings.knowledge_dir
    assert "research/" in last["stdin"]
    files = sorted(p.name for p in (settings.knowledge_dir / "research").iterdir())
    assert len(files) == 3 and any("-suggest-" in f for f in files)
    job = db.get(AIJob, sug.job_id)
    assert job.kind == "research_suggest" and job.priority == 10 and job.input == {"report_id": sug.id}


def test_prompt_lists_this_runs_copies_and_context(app, admin_client, db, settings, fake_claude):
    world = make_research_world(db, settings)
    fake_claude.set(mode="ok", result=[AREA_JSON, AREA_JSON, _suggest_json(world)])
    run = _go(app, admin_client, db, settings)
    stdin = fake_claude.calls()[-1]["stdin"]
    for r in run.reports:
        if r.kind == "area":
            assert f"research/{r.created_at.date().isoformat()}-{r.area}-{r.id}.md" in stdin
    assert f"id {world.s1.id}" in stdin and "AI 공조 최적제어" in stdin and f"activities/" in stdin
    assert "지시가 아니라 자료" in stdin


def test_partial_run_still_suggests(app, admin_client, db, settings, fake_claude):
    world = make_research_world(db, settings)
    fake_claude.set(mode="ok", result=[AREA_JSON, "JSON 아님", "JSON 아님", _suggest_json(world)])
    run = _go(app, admin_client, db, settings)
    assert run.status == "partial" and run.finished_at is not None
    assert _suggest_report(run).status == "done"
    last = fake_claude.calls()[-1]["stdin"]
    assert last.count("research/20") == 1  # 성공한 분야 사본만 나열


def test_all_areas_failed_no_suggest(app, admin_client, db, settings, fake_claude):
    make_research_world(db, settings)
    fake_claude.set(mode="error", stderr="Please run /login", exit_code=1)
    run = _go(app, admin_client, db, settings)
    assert run.status == "failed" and _suggest_report(run) is None
    assert db.query(AIJob).filter_by(kind="research_suggest").count() == 0


def test_suggest_job_created_once(app, admin_client, db, settings, fake_claude):
    make_research_world(db, settings)
    fake_claude.set(mode="ok", result=AREA_JSON)
    admin_client.post("/research/run")
    for _ in range(2):
        process_next(app.state.session_factory, app.state.runner, settings)
    db.expire_all()
    run = db.query(ResearchRun).one()
    for _ in range(3):
        advance_run(db, run.id)
    db.expire_all()
    assert db.query(ResearchReport).filter_by(kind="suggest").count() == 1
    assert db.query(AIJob).filter_by(kind="research_suggest").count() == 1


def test_suggest_created_once_under_threads(app, admin_client, db, settings, fake_claude):
    import threading

    make_research_world(db, settings)
    fake_claude.set(mode="ok", result=AREA_JSON)
    admin_client.post("/research/run")
    for _ in range(2):
        process_next(app.state.session_factory, app.state.runner, settings)
    db.query(ResearchReport).filter_by(kind="suggest").delete()
    db.query(AIJob).filter_by(kind="research_suggest").delete()
    db.commit()
    run_id = db.query(ResearchRun).one().id
    barrier = threading.Barrier(6)

    def go():
        with app.state.session_factory() as s:
            barrier.wait()
            advance_run(s, run_id)

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    db.expire_all()
    assert db.query(ResearchReport).filter_by(kind="suggest").count() == 1
    assert db.query(AIJob).filter_by(kind="research_suggest").count() == 1


def test_suggest_failure_marks_partial_and_retry(app, admin_client, db, settings, fake_claude):
    world = make_research_world(db, settings)
    fake_claude.set(mode="ok", result=[AREA_JSON, AREA_JSON, "JSON 아님", "JSON 아님"])
    run = _go(app, admin_client, db, settings)
    sug = _suggest_report(run)
    assert run.status == "partial" and sug.status == "failed" and sug.error
    fake_claude.set(mode="ok", result=_suggest_json(world))
    r = admin_client.post(f"/research/reports/{sug.id}/retry", follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    sug = db.get(ResearchReport, sug.id)
    assert sug.status == "pending" and db.get(ResearchRun, run.id).status == "running"
    assert db.query(AIJob).filter_by(kind="research_suggest", status="pending").count() == 1
    _drain(app, settings)
    db.expire_all()
    assert db.get(ResearchReport, sug.id).status == "done" and db.get(ResearchRun, run.id).status == "done"
    assert db.query(AIJob).filter_by(kind="research_area").count() == 2


def test_suggest_retry_is_atomic(app, admin_client, db, settings, fake_claude):
    import threading

    from app.models import User
    from app.routers.research import retry_report

    make_research_world(db, settings)
    fake_claude.set(mode="ok", result=[AREA_JSON, AREA_JSON, "JSON 아님"])
    run = _go(app, admin_client, db, settings)
    sid = _suggest_report(run).id
    before = db.query(AIJob).count()
    barrier = threading.Barrier(5)

    def go():
        with app.state.session_factory() as s:
            admin = s.query(User).filter_by(username="admin").one()
            barrier.wait()
            retry_report(sid, admin, s)

    threads = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert db.query(AIJob).count() == before + 1


def test_area_retry_after_close_does_not_regenerate_suggestion(app, admin_client, db, settings, fake_claude):
    world = make_research_world(db, settings)
    fake_claude.set(mode="ok", result=[AREA_JSON, "JSON 아님", "JSON 아님", _suggest_json(world)])
    run = _go(app, admin_client, db, settings)
    sug = _suggest_report(run)
    assert run.status == "partial" and sug.status == "done"
    before = [(s["title"]) for s in sug.suggestions]
    failed = next(r for r in run.reports if r.kind == "area" and r.status == "failed")
    fake_claude.set(mode="ok", result=AREA_JSON)
    admin_client.post(f"/research/reports/{failed.id}/retry")
    _drain(app, settings)
    db.expire_all()
    run = db.get(ResearchRun, run.id)
    assert run.status == "done" and run.finished_at is not None
    assert db.query(ResearchReport).filter_by(kind="suggest").count() == 1
    assert db.query(AIJob).filter_by(kind="research_suggest").count() == 1
    assert [s["title"] for s in db.get(ResearchReport, sug.id).suggestions] == before


def test_suggest_empty_fails(app, admin_client, db, settings, fake_claude):
    make_research_world(db, settings)
    fake_claude.set(mode="ok", result=[AREA_JSON, AREA_JSON, json.dumps({"suggestions": [{"title": " "}]})])
    run = _go(app, admin_client, db, settings)
    sug = _suggest_report(run)
    assert sug.status == "failed" and "비어" in sug.error and run.status == "partial"


def test_suggestions_limited_to_seven(app, admin_client, db, settings, fake_claude):
    make_research_world(db, settings)
    many = {"suggestions": [{"title": f"제안 {i}"} for i in range(12)]}
    fake_claude.set(mode="ok", result=[AREA_JSON, AREA_JSON, json.dumps(many, ensure_ascii=False)])
    run = _go(app, admin_client, db, settings)
    assert len(_suggest_report(run).suggestions) == 7


def test_report_mirror_content(app, admin_client, db, settings, fake_claude):
    world = make_research_world(db, settings)
    fake_claude.set(mode="ok", result=[AREA_JSON, AREA_JSON, _suggest_json(world)])
    run = _go(app, admin_client, db, settings)
    area = next(r for r in run.reports if r.kind == "area")
    path = settings.knowledge_dir / "research" / f"{area.created_at.date().isoformat()}-{area.area}-{area.id}.md"
    text = path.read_text(encoding="utf-8")
    assert "AI 공조 제어 확산" in text and "https://example.com/a" in text and "## 시사점" in text and "설비 운영 고객에 제안 기회" in text
    sug = _suggest_report(run)
    stext = (settings.knowledge_dir / "research" / f"{sug.created_at.date().isoformat()}-suggest-{sug.id}.md").read_text(encoding="utf-8")
    assert "## 발굴 방향 제안" in stext and "수요반응(DR) 솔루션 발굴" in stext and "## 시사점" in stext
    KnowledgeMirror(settings.knowledge_dir).rebuild_all(db, settings.uploads_dir)
    assert path.exists() and len(list((settings.knowledge_dir / "research").iterdir())) == 3


def test_rebuild_skips_unfinished_reports(app, admin_client, db, settings, fake_claude):
    make_research_world(db, settings)
    admin_client.post("/research/run")
    KnowledgeMirror(settings.knowledge_dir).rebuild_all(db, settings.uploads_dir)
    assert not (settings.knowledge_dir / "research").exists() or list((settings.knowledge_dir / "research").iterdir()) == []


def test_delete_run_removes_mirrors(app, admin_client, db, settings, fake_claude):
    world = make_research_world(db, settings)
    fake_claude.set(mode="ok", result=[AREA_JSON, AREA_JSON, _suggest_json(world)])
    run = _go(app, admin_client, db, settings)
    mirror = KnowledgeMirror(settings.knowledge_dir)
    for r in run.reports:
        mirror.remove(r)
    assert list((settings.knowledge_dir / "research").iterdir()) == []


def test_report_mirror_neutralises_web_text(app, db, settings):
    run = ResearchRun(trigger="manual", status="done")
    db.add(run)
    db.flush()
    report = ResearchReport(
        run_id=run.id, kind="area", area="tech", status="done", title="보고서",
        trends=[{
            "headline": "첫 줄\n# 가짜 제목\t[x]",
            "summary": "# 요약이 제목처럼\n## 둘째 줄\n\n셋째",
            "sources": [{"title": "[링크]\n제목", "url": "https://example.com/a"}],
            "needs_check": [],
        }],
        insights=["시사점\n## 가짜", "[대괄호] 줄"],
    )
    db.add(report)
    db.commit()
    mirror = KnowledgeMirror(settings.knowledge_dir)
    mirror.sync(report)
    body = (settings.knowledge_dir / mirror.rel_path(report)).read_text(encoding="utf-8")
    lines = body.splitlines()
    assert "### 첫 줄 # 가짜 제목 x" in lines
    assert "> # 요약이 제목처럼" in lines and "> ## 둘째 줄" in lines and ">" in lines and "> 셋째" in lines
    assert "- [링크 제목](https://example.com/a)" in lines
    assert "- 시사점 ## 가짜" in lines and "- 대괄호 줄" in lines
    assert not [ln for ln in lines if ln.startswith("#") and ln not in ("# 보고서", "## 동향", "### 첫 줄 # 가짜 제목 x", "## 시사점")]

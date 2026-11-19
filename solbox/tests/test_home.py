from datetime import date, datetime, timedelta

from app.labels import ERROR_KINDS
from app.models import AIJob, Activity, Solution
from app.routers.home import upcoming_follow_ups


def test_upcoming_follow_ups_window(db):
    today = date(2026, 11, 18)
    a = Activity(raw_text="x", title="회의", date=today, follow_ups=[
        {"task": "지난 일", "owner": "", "due": "2026-11-10", "done": False},
        {"task": "곧", "owner": "김", "due": "2026-11-25", "done": False},
        {"task": "먼 일", "owner": "", "due": "2026-12-20", "done": False},
        {"task": "끝난 일", "owner": "", "due": "2026-11-19", "done": True},
        {"task": "기한 없음", "owner": "", "due": "", "done": False}])
    db.add(a); db.commit()
    items = upcoming_follow_ups(db, today)
    assert [(i["task"], i["overdue"], i["index"]) for i in items] == [("지난 일", True, 0), ("곧", False, 1)]
    assert items[1]["due"] == date(2026, 11, 25) and items[1]["owner"] == "김" and items[1]["activity"].id == a.id


def test_home_shows_stage_counts_and_recent(user_client, db):
    db.add_all([Solution(name="가", stage="boxed"), Solution(name="나", stage="boxed"), Solution(name="다", stage="hold")])
    db.add(Activity(raw_text="x", title="지난주 회의록", date=date(2026, 11, 10)))
    db.commit()
    page = user_client.get("/").text
    assert "지난주 회의록" in page
    assert 'href="/solutions?stage=boxed"' in page and 'href="/solutions?stage=intake"' in page
    for code, name in (("boxed", "박스 등록"), ("intake", "아이템 접수"), ("hold", "보류")):
        assert name in page
    row = lambda code: page.split(f'href="/solutions?stage={code}"')[1].split("</li>")[0]
    assert ">2<" in row("boxed") and ">0<" in row("intake") and ">1<" in row("hold")


def test_home_recent_activities_limited_to_ten_newest(user_client, db):
    for i in range(12):
        db.add(Activity(raw_text="x", title=f"기록{i:02d}", date=date(2026, 1, 1) + timedelta(days=i)))
    db.commit()
    page = user_client.get("/").text
    assert "기록11" in page and "기록02" in page and "기록01" not in page


def test_home_follow_up_has_toggle_and_overdue_mark(user_client, db):
    a = Activity(raw_text="x", title="회의", date=date.today(), follow_ups=[
        {"task": "견적 보내기", "owner": "", "due": (date.today() - timedelta(days=2)).isoformat(), "done": False}])
    db.add(a); db.commit()
    page = user_client.get("/").text
    assert "견적 보내기" in page and "기한 지남" in page
    assert f'action="/activities/{a.id}/followups/0/toggle"' in page


def test_home_shows_ai_problem_banner(user_client, db):
    db.add(AIJob(kind="x", status="failed", error_kind="auth", input={}, finished_at=datetime.now())); db.commit()
    assert ERROR_KINDS["auth"] in user_client.get("/").text


def test_home_no_banner_when_latest_job_ok_or_other_error(user_client, db):
    now = datetime.now()
    db.add(AIJob(kind="x", status="failed", error_kind="auth", input={}, finished_at=now - timedelta(hours=1)))
    db.add(AIJob(kind="x", status="done", input={}, finished_at=now))
    db.commit()
    assert ERROR_KINDS["auth"] not in user_client.get("/").text
    db.add(AIJob(kind="x", status="failed", error_kind="bad_output", input={}, finished_at=now + timedelta(hours=1)))
    db.commit()
    assert ERROR_KINDS["bad_output"] not in user_client.get("/").text


def test_home_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_admin_menu_links(admin_client, user_client):
    page = admin_client.get("/").text
    assert 'href="/admin/jobs"' in page and 'href="/admin/users"' in page
    assert 'href="/admin/jobs"' not in user_client.get("/").text


def test_home_latest_research(user_client, db, settings):
    from app.models import ResearchRun
    from app.routers.home import latest_research
    from tests.conftest import finished_run

    assert latest_research(db) is None
    assert "아직 리서치 결과가 없습니다." in user_client.get("/").text
    db.add(ResearchRun(trigger="manual", status="running")); db.commit()
    assert latest_research(db) is None  # 진행 중인 실행은 건너뛴다
    run = finished_run(db, settings)
    data = latest_research(db)
    assert data["run"].id == run.id
    assert data["insights"] == ["설비 운영 고객에 제안 기회"]
    assert [s["title"] for s in data["suggestions"]] == ["수요반응(DR) 솔루션 발굴", "AI 공조를 병원 고객에 우선 제안"]
    page = user_client.get("/").text
    assert "최신 리서치" in page and "설비 운영 고객에 제안 기회" in page and "수요반응(DR) 솔루션 발굴" in page
    assert f'href="/research/runs/{run.id}"' in page


def test_latest_research_caps(db, settings):
    from app.models import ResearchReport, ResearchRun
    from app.routers.home import latest_research

    run = ResearchRun(trigger="manual", status="done", finished_at=datetime.now())
    db.add(run); db.flush()
    db.add(ResearchReport(run_id=run.id, kind="area", area="tech", status="done", insights=[f"시사점{i}" for i in range(4)]))
    db.add(ResearchReport(run_id=run.id, kind="area", area="market", status="done", insights=[f"시장{i}" for i in range(4)]))
    db.add(ResearchReport(run_id=run.id, kind="suggest", status="done", suggestions=[
        {"kind": "new", "title": f"제안{i}", "detail": "", "reason": "", "solution_ids": [], "sources": []} for i in range(5)]))
    db.commit()
    data = latest_research(db)
    assert len(data["insights"]) == 5 and len(data["suggestions"]) == 3


def test_home_section_order_and_trimmed_research(user_client, db):
    from app.models import ResearchReport, ResearchRun

    run = ResearchRun(trigger="manual", status="done", finished_at=datetime.now())
    db.add(run); db.flush()
    db.add(ResearchReport(run_id=run.id, kind="area", area="tech", status="done", insights=[f"시사점{i}" for i in range(4)]))
    db.add(ResearchReport(run_id=run.id, kind="suggest", status="done", suggestions=[
        {"kind": "new", "title": f"제안{i}", "detail": "", "reason": "", "solution_ids": [], "sources": []} for i in range(5)]))
    db.commit()
    page = user_client.get("/").text
    heads = [page.index(h) for h in ("후속 조치", "진행 중인 제안 건", "발굴 단계별 솔루션", "최신 리서치", "최근 활동 기록")]
    assert heads == sorted(heads)
    assert "시사점1" in page and "시사점2" not in page
    assert "제안1" in page and "제안2" not in page


def test_home_follow_up_row_has_check_button_and_no_bullets(user_client, db):
    a = Activity(raw_text="x", title="회의", date=date.today(), follow_ups=[
        {"task": "자료 보내기", "owner": "김", "due": (date.today() + timedelta(days=1)).isoformat(), "done": False}])
    db.add(a); db.commit()
    page = user_client.get("/").text
    assert f'action="/activities/{a.id}/followups/0/toggle"' in page
    assert 'aria-label="완료로 표시"' in page
    assert 'class="followups"' in page

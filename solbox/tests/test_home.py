import re
from datetime import date, datetime, timedelta

from app.labels import ERROR_KINDS
from app.models import AIJob, Activity, Solution
from app.routers.home import upcoming_follow_ups
from tests.conftest import fu_rows


def test_upcoming_follow_ups_window(db):
    today = date(2026, 11, 18)
    a = Activity(raw_text="x", title="회의", date=today, follow_ups=fu_rows(
        ("지난 일", "", "2026-11-10", False), ("곧", "김", "2026-11-25", False), ("먼 일", "", "2026-12-20", False),
        ("끝난 일", "", "2026-11-19", True), ("기한 없음", "", "", False)))
    db.add(a); db.commit()
    items = upcoming_follow_ups(db, today)
    assert [(i["task"], i["overdue"], i["index"]) for i in items] == [("지난 일", True, 0), ("곧", False, 1)]
    assert items[1]["due"] == date(2026, 11, 25) and items[1]["owner"] == "김" and items[1]["activity"].id == a.id
    assert items[1]["id"] == a.follow_ups[1].id


def test_home_shows_stage_counts_and_recent(user_client, db):
    db.add_all([Solution(name="가", stage="boxed"), Solution(name="나", stage="boxed"), Solution(name="다", stage="hold")])
    db.add(Activity(raw_text="x", title="지난주 회의록", date=date(2026, 11, 10)))
    db.commit()
    page = user_client.get("/").text
    assert "지난주 회의록" in page
    assert 'href="/solutions?stage=boxed"' in page and 'href="/solutions?stage=intake"' in page
    for code, name in (("boxed", "박스 등록"), ("intake", "아이템 접수"), ("hold", "보류")):
        assert name in page
    cell = lambda code: re.search(rf'<a[^>]*href="/solutions\?stage={code}"[^>]*>.*?</a>', page, re.S).group(0)
    assert "<b>2</b>" in cell("boxed") and "<b>0</b>" in cell("intake") and "<b>1</b>" in cell("hold")  # 흐름 띠의 칸(보류는 띠 옆 링크)


def test_home_recent_activities_limited_to_eight_newest(user_client, db):
    for i in range(12):
        db.add(Activity(raw_text="x", title=f"기록{i:02d}", date=date(2026, 1, 1) + timedelta(days=i)))
    db.commit()
    page = user_client.get("/").text
    assert "기록11" in page and "기록04" in page and "기록03" not in page  # 여덟 건만, 나머지는 "모두 보기"


def test_home_follow_up_has_toggle_and_overdue_mark(user_client, db):
    a = Activity(raw_text="x", title="회의", date=date.today(), follow_ups=fu_rows(
        ("견적 보내기", "", (date.today() - timedelta(days=2)).isoformat(), False)))
    db.add(a); db.commit()
    page = user_client.get("/").text
    assert "견적 보내기" in page and "2일 지남" in page and 'class="due late"' in page  # 상대 기한(나쁜 색)
    assert f'action="/follow-ups/{a.follow_ups[0].id}/toggle"' in page


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
    # UX 개선(2026-11-26): 맨 위 "내 할 일" 띠 → 두 열(왼쪽: 후속 조치·최근 활동, 오른쪽: 올리기·영업기회·제안 건) → 최신 리서치 → 맨 아래 발굴 흐름
    # 올리기 카드(h-up)는 확인 대기·정리 중 묶음이 있을 때만 (UX 2순위 2026-11-27): test_home_upload_card_only_when_bundles_wait
    heads = [page.index(h) for h in ('aria-label="내 할 일"', 'id="h-fu"', 'id="h-act"', 'id="h-opp"', 'id="h-prop"', 'id="h-res"', 'id="h-flow"')]
    assert heads == sorted(heads)
    assert "시사점1" in page and "시사점2" not in page
    assert "제안1" in page and "제안2" not in page


def test_home_follow_up_row_has_check_button_and_no_bullets(user_client, db):
    a = Activity(raw_text="x", title="회의", date=date.today(), follow_ups=fu_rows(
        ("자료 보내기", "김", (date.today() + timedelta(days=1)).isoformat(), False)))
    db.add(a); db.commit()
    page = user_client.get("/").text
    assert f'action="/follow-ups/{a.follow_ups[0].id}/toggle"' in page
    assert 'aria-label="완료로 표시"' in page
    assert 'class="fu-list"' in page and 'class="followups"' not in page


def _bundle(db, user, status, note=""):
    from app.models import Intake
    it = Intake(note=note, status=status, created_by_id=user.id)
    db.add(it); db.commit()
    return it


def test_home_upload_card_only_when_bundles_wait(user_client, db):
    """UX 2순위(2026-11-27): 홈 제목 줄의 주 버튼은 "활동 기록 쓰기", 올리기는 보조 버튼 하나.
    올리기 카드는 확인 대기·정리 중 묶음이 있을 때만 그 목록을 보인다 (비어 있을 때 안내 글만 있던 카드는 없앴다)."""
    from sqlalchemy import select

    from app.models import User

    page = user_client.get("/").text
    assert 'action="/intake"' not in page and 'id="h-up"' not in page
    head = page.split('<header class="page-header">')[1].split("</header>")[0]
    assert 'class="button to-intake" href="/intake/new">+ 올리기</a>' in head
    assert 'class="button primary" href="/activities/new">활동 기록 쓰기</a>' in head and head.count("primary") == 1
    kim = db.scalars(select(User).where(User.username == "kim")).one()
    _bundle(db, kim, "ready", note="한빛 자료")
    card = user_client.get("/").text.split('id="h-up"')[1].split("</section>")[0]
    assert "확인 대기 1건" in card and "한빛 자료" in card and 'href="/intake"' in card


def test_intake_summary_counts_only_own_bundles(db):
    from app.routers.home import intake_summary
    from tests.conftest import make_user
    kim, lee = make_user(db, "kim2"), make_user(db, "lee2")
    mine = [_bundle(db, kim, "ready", f"내것{i}") for i in range(7)]
    _bundle(db, kim, "analyzing"); _bundle(db, kim, "applied"); _bundle(db, kim, "failed")
    _bundle(db, lee, "ready", "남의것"); _bundle(db, lee, "analyzing")
    s = intake_summary(db, kim)
    assert s["ready_count"] == 7 and s["analyzing_count"] == 1
    assert [i.id for i in s["ready"]] == [i.id for i in reversed(mine)][:5]
    admin = make_user(db, "boss", role="admin")
    # 관리자도 내 묶음만 센다 (남의 묶음은 올리기 목록에서 본다)
    _bundle(db, admin, "ready", "관리자 것")
    a = intake_summary(db, admin)
    assert a["ready_count"] == 1 and a["analyzing_count"] == 0
    assert [i.note for i in a["ready"]] == ["관리자 것"]


def test_home_shows_pending_confirmation_links(user_client, db):
    from app.models import User
    kim = db.query(User).filter_by(username="kim").one()
    from tests.conftest import make_user
    other = make_user(db, "park")
    mine = _bundle(db, kim, "ready", "내 묶음")
    _bundle(db, kim, "analyzing")
    _bundle(db, other, "ready", "남의 묶음")
    page = user_client.get("/").text
    assert "확인 대기 1건" in page and "정리 중 1건" in page
    assert f'href="/intake/{mine.id}"' in page and 'href="/intake"' in page
    assert "남의 묶음" not in page


def test_home_hides_counts_when_nothing_pending(user_client):
    page = user_client.get("/").text
    assert "확인 대기" not in page and "정리 중" not in page


def test_admin_home_lists_only_own_bundles(admin_client, db):
    from tests.conftest import make_user
    _bundle(db, make_user(db, "park"), "ready", "남의 묶음")
    page = admin_client.get("/").text
    assert "확인 대기" not in page and "남의 묶음" not in page

"""AI 코워크 ①: 업무 보고의 사업 방향·이슈·과제 구역 (reports.aggregate["workspace"], report_content, 파일)."""
import re
from datetime import date, datetime

from sqlalchemy import select

from app import reports
from app.models import Initiative, Report, Strategy, User, WorkspaceChange
from tests.conftest import make_user

START, END = date(2026, 11, 23), date(2026, 11, 29)
BEFORE = datetime(2026, 11, 10, 9, 0)
IN1 = datetime(2026, 11, 24, 10, 0)
IN2 = datetime(2026, 11, 26, 10, 0)
AFTER = datetime(2026, 12, 3, 10, 0)


def _at(db, obj, when):
    """만든 때와 그때 남은 이력 줄의 시각을 `when` 으로 옮긴다 (시험 세계를 과거로)."""
    obj.created_at = when
    for ch in obj.changes:
        ch.changed_at = when
    db.commit()


def _set(db, obj, when, **values):
    seen = {ch.id for ch in obj.changes}
    for k, v in values.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    for ch in obj.changes:
        if ch.id not in seen:  # 방금 남은 줄
            ch.changed_at = when
    db.commit()


def _world(db):
    s = Strategy(title="공공 에너지 우선", status="draft", consensus="지자체 우선")
    db.add(s)
    db.commit()
    _at(db, s, BEFORE)
    _set(db, s, IN1, status="agreed", consensus="지자체·공공기관 우선")
    issue_open = Initiative(title="부지 인허가 지연", kind="issue", strategy=s)
    issue_closed = Initiative(title="견적 오류", kind="issue")
    task = Initiative(title="실증 추진", kind="task", strategy=s, progress="부지 후보 3곳\n이전 메모")
    task_later = Initiative(title="기간 밖 변경", kind="task")
    new_issue = Initiative(title="새 이슈", kind="issue")
    db.add_all([issue_open, issue_closed, task, task_later, new_issue])
    db.commit()
    for i in (issue_open, issue_closed, task, task_later):
        _at(db, i, BEFORE)
    _at(db, new_issue, IN2)
    _set(db, issue_closed, IN1, status="done")
    _set(db, task, IN1, status="doing")
    _set(db, task, IN2, status="blocked")
    _set(db, task_later, AFTER, status="done")
    return s, issue_open, issue_closed, task, task_later, new_issue


def test_aggregate_workspace_section(db):
    s, issue_open, issue_closed, task, task_later, new_issue = _world(db)
    w = reports.aggregate(db, "week", START, END)["workspace"]
    st = {x["title"]: x for x in w["strategies"]}
    assert st["공공 에너지 우선"]["status"] == "agreed" and st["공공 에너지 우선"]["changed"] is True
    assert st["공공 에너지 우선"]["open_tasks"] == 2  # 인허가 이슈 + 실증 과제 (기간 끝 기준)
    assert {x["title"] for x in w["issues_open"]} == {"부지 인허가 지연", "새 이슈"}
    assert w["issues_new"] == 1 and w["issues_closed"] == 1
    changed = {x["title"]: x for x in w["initiatives_changed"]}
    assert set(changed) == {"견적 오류", "실증 추진", "새 이슈"}  # 기간 밖 변경은 빠진다
    assert (changed["실증 추진"]["from_status"], changed["실증 추진"]["to_status"]) == ("open", "blocked")
    assert changed["실증 추진"]["progress"] == "부지 후보 3곳"
    assert changed["새 이슈"]["new"] is True


def test_issue_count_matches_list_when_period_is_now(user_client, db):
    db.add_all([Initiative(title="a", kind="issue"), Initiative(title="b", kind="issue", status="done"), Initiative(title="c", kind="task")])
    db.commit()
    today = date.today()
    w = reports.aggregate(db, "day", today, today)["workspace"]
    page = user_client.get("/initiatives?kind=issue").text
    rows = len(re.findall(r'<a href="/initiatives/\d+" class="row-title"', page))
    assert len(w["issues_open"]) == rows == 1


def test_report_content_and_files_have_workspace(db, settings):
    _world(db)
    kim = make_user(db, "kim")
    r = Report(unit="week", period_start=START, period_end=END, created_by_id=kim.id)
    db.add(r)
    db.commit()
    c = reports.report_content(db, r)
    assert ("공공 에너지 우선", "합의", "진행 2 · 완료 0", "이번 기간에 바뀜") in c["workspace"]["strategies"]
    assert any(row[0] == "실증 추진" and "열림 → 막힘" in row[2] for row in c["workspace"]["initiatives"])
    from app import report_files

    md = report_files.write(c, "md", settings.data_dir / "out", "r").read_text(encoding="utf-8")
    assert "사업 방향·과제" in md and "부지 인허가 지연" in md
    for fmt in ("docx", "pptx"):
        assert report_files.write(c, fmt, settings.data_dir / "out", "r").is_file()


def test_old_snapshot_without_workspace_still_renders(db):
    r = Report(unit="week", period_start=START, period_end=END, status="confirmed",
               snapshot={"kpis": {}, "activities": [], "opportunities": [], "proposals": [], "followups_done": [], "followups_overdue": []})
    db.add(r)
    db.commit()
    c = reports.report_content(db, r)
    assert c["workspace"] == {"strategies": [], "issues": [], "initiatives": [], "issues_new": 0, "issues_closed": 0}


def test_report_page_shows_workspace_card(user_client, db):
    _world(db)
    r = Report(unit="week", period_start=START, period_end=END)
    db.add(r)
    db.commit()
    page = user_client.get(f"/reports/{r.id}").text
    assert 'id="workspace"' in page and "부지 인허가 지연" in page and "실증 추진" in page

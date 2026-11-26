"""팀 업무 보고 흐름 (app/reports.py): 지난 계획 점검·계획 넘김·확정·확정 풀기·기본 보고 문구."""
import json
from datetime import date, datetime

from app import ontology, report_periods, reports
from app.models import Activity, Report, ReportPlanItem

W1 = (date(2026, 11, 16), date(2026, 11, 22))
W2 = (date(2026, 11, 23), date(2026, 11, 29))
W3 = (date(2026, 11, 30), date(2026, 12, 6))


def _report(db, period, status="draft"):
    r = Report(unit="week", period_start=period[0], period_end=period[1], status=status)
    db.add(r)
    db.commit()
    return r


def _prev_with_items(db, n=3):
    """확정된 지난 보고 + 계획 항목 n개."""
    prev = _report(db, W1, status="confirmed")
    items = []
    for i in range(n):
        it = ReportPlanItem(report_id=prev.id, position=i, task=f"할 일 {i}", owner=f"담당{i}", due=date(2026, 11, 20))
        db.add(it)
        items.append(it)
    db.commit()
    return prev, items


def _carried(r):
    return [i for i in r.plan_items if i.carried_from_id is not None]


def test_items_to_check(db):
    cur = _report(db, W2)
    assert reports.items_to_check(db, cur) == []
    prev, items = _prev_with_items(db)
    assert [i.id for i in reports.items_to_check(db, cur)] == [i.id for i in items]


def test_carry_doing_todo_unchecked_not_done_dropped(db):
    prev, (a, b, c) = _prev_with_items(db, 3)
    d = ReportPlanItem(report_id=prev.id, position=3, task="넷째")
    e = ReportPlanItem(report_id=prev.id, position=4, task="다섯째")
    db.add_all([d, e])
    db.commit()
    cur = _report(db, W2)
    reports.save_check(db, cur, {a.id: ("done", "끝"), b.id: ("doing", "절반"), c.id: ("dropped", ""), d.id: ("todo", "")})
    db.commit()
    carried = {i.carried_from_id: i for i in _carried(cur)}
    assert set(carried) == {b.id, d.id, e.id}  # e 는 점검 안 함 -> 넘어감
    assert carried[b.id].task == "할 일 1" and carried[b.id].owner == "담당1" and carried[b.id].due is None
    assert a.checked_in_report_id == cur.id and a.result == "done" and a.result_note == "끝"
    assert e.checked_in_report_id is None


def test_save_check_unknown_code_becomes_blank_and_ignores_foreign_ids(db):
    prev, (a, *_) = _prev_with_items(db, 2)
    cur = _report(db, W2)
    other = ReportPlanItem(report_id=cur.id, task="내 항목")
    db.add(other)
    db.commit()
    reports.save_check(db, cur, {a.id: ("bogus", "메모"), other.id: ("done", "")})
    assert a.result == "" and a.result_note == "메모" and a.checked_in_report_id == cur.id
    assert other.result == "" and other.checked_in_report_id is None


def test_carry_once_and_resync(db):
    prev, (a, b) = _prev_with_items(db, 2)
    cur = _report(db, W2)
    mine = ReportPlanItem(report_id=cur.id, position=0, task="내가 쓴 계획")
    db.add(mine)
    db.commit()
    reports.sync_carry(db, cur)
    reports.sync_carry(db, cur)
    db.commit()
    assert len(_carried(cur)) == 2
    assert [i.task for i in cur.plan_items][:2] == ["할 일 0", "할 일 1"] and cur.plan_items[-1].task == "내가 쓴 계획"
    assert len({i.position for i in cur.plan_items}) == 3
    reports.save_check(db, cur, {a.id: ("done", ""), b.id: ("todo", "")})
    db.commit()
    assert [i.carried_from_id for i in _carried(cur)] == [b.id]
    reports.save_check(db, cur, {a.id: ("todo", "")})
    db.commit()
    assert {i.carried_from_id for i in _carried(cur)} == {a.id, b.id}


def test_no_resync_after_confirm(db):
    prev, (a, b) = _prev_with_items(db, 2)
    cur = _report(db, W2)
    reports.save_check(db, cur, {a.id: ("todo", ""), b.id: ("todo", "")})
    db.commit()
    reports.confirm(db, cur, None)
    db.commit()
    assert len(_carried(cur)) == 2
    reports.save_check(db, cur, {a.id: ("done", "")})
    db.commit()
    assert a.result == "done" and len(_carried(cur)) == 2  # 점검은 저장되지만 넘김은 그대로
    reports.sync_carry(db, cur)
    assert len(_carried(cur)) == 2


def test_confirm_freezes_numbers(db):
    cur = _report(db, W2)
    db.add(Activity(title="a1", raw_text="x", kind="meeting", date=date(2026, 11, 24), created_at=datetime(2026, 11, 24, 9)))
    db.commit()
    assert reports.data_of(db, cur)["kpis"]["activities"] == 1
    reports.confirm(db, cur, None)
    db.commit()
    assert cur.status == ontology.REPORT_STATUS_CONFIRMED and cur.confirmed_at is not None
    json.dumps(cur.snapshot)  # 직렬화 가능
    assert cur.snapshot["activities"][0]["date"] == "2026-11-24"
    db.add(Activity(title="a2", raw_text="x", kind="meeting", date=date(2026, 11, 25), created_at=datetime(2026, 11, 25, 9)))
    db.commit()
    db.expire_all()
    assert reports.data_of(db, cur)["kpis"]["activities"] == 1
    cur.status = "planning"
    assert reports.data_of(db, cur)["kpis"]["activities"] == 2


def test_confirm_fills_only_empty_notes(db):
    cur = _report(db, W2)
    cur.section_notes = {"kpi": "내가 쓴 문구"}
    db.commit()
    reports.confirm(db, cur, None)
    db.commit()
    assert cur.section_notes["kpi"] == ["내가 쓴 문구"]  # 옛 글 값도 줄 목록으로 맞춘다
    assert all(isinstance(v, list) for v in cur.section_notes.values())
    assert set(cur.section_notes) == {"kpi", "opps", "proposals", "check", "plan", "issues"}
    assert all(cur.section_notes[k] for k in cur.section_notes)


def test_unconfirm_blocked_when_next_checked(db):
    prev, (a,) = _prev_with_items(db, 1)
    cur = _report(db, W2)
    reports.save_check(db, cur, {a.id: ("done", "")})
    db.commit()
    reason = reports.unconfirm(db, prev)
    assert isinstance(reason, str) and reason and prev.status == "confirmed"
    # 아직 점검 안 한 보고는 풀린다
    c3 = _report(db, W3, status="confirmed")
    c3.snapshot = {"kpis": {}}
    assert reports.unconfirm(db, c3) is None
    assert c3.status == "planning" and c3.snapshot is None


def test_unconfirm_ok_when_next_not_checked(db):
    prev, _ = _prev_with_items(db, 1)
    _report(db, W2)
    assert reports.unconfirm(db, prev) is None
    assert prev.status == "planning" and prev.snapshot is None and prev.confirmed_at is None


def test_default_notes_texts(db):
    prev, items = _prev_with_items(db, 6)
    cur = _report(db, W2)
    res = {items[i].id: ("done", "") for i in range(4)}
    res[items[4].id] = ("doing", "")
    res[items[5].id] = ("todo", "")
    reports.save_check(db, cur, res)
    db.commit()
    data = {
        "kpis": {"activities": 14, "activities_prev": 11, "won": 1, "opportunities_new": 2, "proposals_new": 1, "proposals_active": 4,
                 "decks_built": 2, "followups_done": 3, "followups_overdue": 2},
        "opportunities": [{"new": True}, {"new": True}, {"new": False}], "proposals": [{}] * 4,
    }
    notes = reports.default_notes(data, reports.items_to_check(db, cur), cur.plan_items)
    assert set(notes) == {"kpi", "opps", "proposals", "check", "plan", "issues"}
    assert all(isinstance(v, list) and v and all(isinstance(s, str) and s for s in v) for v in notes.values())
    assert notes["kpi"] == ["활동 기록 14건(지난 기간 대비 +3), 수주 1건, 새 영업기회 2건입니다."]
    assert notes["check"] == ["지난 계획 6건 중 4건을 완료했습니다(67%). 미완료 2건은 다음 계획으로 넘깁니다."]
    assert "3건" in notes["opps"][0] and "2건" in notes["opps"][0]
    assert "4건" in notes["proposals"][0]
    assert "2건" in notes["plan"][0]
    assert "2건" in notes["issues"][0]


def test_default_notes_empty(db):
    data = {"kpis": {}, "opportunities": [], "proposals": []}
    notes = reports.default_notes(data, [], [])
    assert "처음 보고" in notes["check"][0]
    assert all(len(v) == 1 and v[0] for v in notes.values())
    assert notes["kpi"][0].startswith("활동 기록 0건")


def test_save_check_blank_row_is_not_checked(db):
    prev, (a, b) = _prev_with_items(db, 2)
    cur = _report(db, W2)
    reports.save_check(db, cur, {a.id: ("", ""), b.id: ("", "메모만")})
    db.commit()
    assert a.checked_in_report_id is None
    assert b.checked_in_report_id == cur.id and b.result == ""
    reports.save_check(db, cur, {b.id: ("", "  ")})  # 다시 비우면 표시도 푼다
    db.commit()
    assert b.checked_in_report_id is None


def test_unconfirm_not_confirmed_is_refused(db):
    cur = _report(db, W2, status="planning")
    assert reports.unconfirm(db, cur) == reports.NOT_CONFIRMED
    assert cur.status == ontology.REPORT_STATUS_PLANNING


def test_note_lines_normalizes():
    assert reports.note_lines("가\n\n 나 \n") == ["가", "나"]
    assert reports.note_lines(["가", "", " "]) == ["가"]
    assert reports.note_lines(None) == []

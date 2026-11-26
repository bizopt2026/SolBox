"""팀 업무 보고 화면 (app/routers/reports.py): 목록·만들기·상세 4단계·저장 POST."""
import re
from datetime import date

import pytest
from sqlalchemy import func, select

from app import guards, ontology
from app.models import Activity, Customer, Report, ReportPlanItem, User
from tests.conftest import nloc

W1 = (date(2026, 11, 16), date(2026, 11, 22))
W2 = (date(2026, 11, 23), date(2026, 11, 29))
W3 = (date(2026, 11, 30), date(2026, 12, 6))

REV = re.compile(r'name="_rev" value="([^"]+)"')
TOK = re.compile(r'name="_tok" value="([^"]+)"')


def _report(db, period=W2, unit="week", status="draft", **kw):
    r = Report(unit=unit, period_start=period[0], period_end=period[1], status=status, **kw)
    db.add(r)
    db.commit()
    return r


def _prev_with_items(db, n=3):
    prev = _report(db, W1, status="confirmed")
    items = [ReportPlanItem(report_id=prev.id, position=i, task=f"할 일 {i}", owner=f"담당{i}", due=date(2026, 11, 20)) for i in range(n)]
    db.add_all(items)
    db.commit()
    return prev, items


def _rev(client, url):
    return REV.search(client.get(url).text).group(1)


def _form_rev(html, action):
    """그 폼(action) 안의 고침 표시."""
    m = re.search(r'action="%s"[^>]*>.*?name="_rev" value="([^"]+)"' % re.escape(action), html, re.S)
    return m.group(1)


def test_list_and_tabs(user_client, db):
    _report(db, W2, status="planning")
    _report(db, (date(2026, 11, 24), date(2026, 11, 24)), unit="day")
    _report(db, (date(2026, 10, 1), date(2026, 10, 31)), unit="month", status="confirmed")
    html = user_client.get("/reports").text
    assert "주간 2026-11-23 ~ 11-29" in html and "일간 2026-11-24 (화)" in html and "월간 2026-10" in html
    assert re.search(r"전체<b>3</b>", html) and re.search(r"일간<b>1</b>", html) and re.search(r"주간<b>1</b>", html) and re.search(r"월간<b>1</b>", html)
    assert html.count('data-code="planning"') == 1  # 배지는 줄마다 한 번
    assert 'href="/reports/new"' in html
    day = user_client.get("/reports?unit=day").text
    assert "일간 2026-11-24 (화)" in day and "주간 2026-11-23 ~ 11-29" not in day
    assert user_client.get("/reports?unit=xx").status_code == 200  # 틀린 값은 전체


def test_create_week_report(user_client, db):
    page = user_client.get("/reports/new?unit=week&day=2026-11-25")
    assert page.status_code == 200
    assert "2026-11-23(월) ~ 11-29(일)" in page.text and "11-30 ~ 12-06" in page.text  # 기간 미리보기
    tok = TOK.search(page.text).group(1)
    r = user_client.post("/reports", data={"unit": "week", "day": "2026-11-25", "_tok": tok}, follow_redirects=False)
    assert r.status_code == 303
    rep = db.scalars(select(Report)).one()
    assert nloc(r) == f"/reports/{rep.id}?notice=report_created"
    assert (rep.unit, rep.period_start, rep.period_end, rep.status) == ("week", *W2, "draft")
    assert db.get(User, rep.created_by_id).username == "kim"
    # 같은 표로 다시 내면 하나만
    again = user_client.post("/reports", data={"unit": "week", "day": "2026-11-25", "_tok": tok}, follow_redirects=False)
    assert again.status_code == 303 and db.scalar(select(func.count()).select_from(Report)) == 1


def test_create_carries_unchecked_previous_items(user_client, db):
    prev, items = _prev_with_items(db, 2)
    user_client.post("/reports", data={"unit": "week", "day": "2026-11-25"}, follow_redirects=False)
    rep = db.scalars(select(Report).where(Report.id != prev.id)).one()
    assert sorted(i.carried_from_id for i in rep.plan_items) == sorted(i.id for i in items)


def test_create_bad_values_are_400(user_client, db):
    assert user_client.post("/reports", data={"unit": "year", "day": "2026-11-25"}, follow_redirects=False).status_code == 400
    assert user_client.post("/reports", data={"unit": "week", "day": "어제"}, follow_redirects=False).status_code == 400
    assert db.scalar(select(func.count()).select_from(Report)) == 0


def test_new_same_period_redirects_to_existing(user_client, db):
    existing = _report(db, W2)
    r = user_client.post("/reports", data={"unit": "week", "day": "2026-11-28"}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == f"/reports/{existing.id}?notice=report_exists"
    assert db.scalar(select(func.count()).select_from(Report)) == 1
    assert "이미 있는" in user_client.get("/reports/new?unit=week&day=2026-11-28").text  # 미리보기도 알려 준다


def test_detail_steps_render(user_client, db):
    c = Customer(name="한빛시청")
    db.add(c)
    db.add_all([Activity(title=f"회의 {i}", raw_text="x", kind="meeting", date=date(2026, 11, 24), customers=[c]) for i in range(7)])
    db.commit()
    rep = _report(db, W2)
    res = user_client.get(f"/reports/{rep.id}")
    assert res.status_code == 200
    assert re.search(r'data-kpi="activities">7<', res.text)
    assert "7건 모두 보기" in res.text and res.text.count("회의 ") >= 5
    assert 'aria-current="step"' in res.text
    for step in ("result", "check", "plan", "files"):
        page = user_client.get(f"/reports/{rep.id}?step={step}")
        assert page.status_code == 200, step
        assert re.search(r'class="step current[^"]*"><a href="\?step=%s"' % step, page.text), step
    assert "처음 보고" in user_client.get(f"/reports/{rep.id}?step=check").text
    assert user_client.get(f"/reports/{rep.id}?step=zzz").status_code == 200


def test_default_step_follows_status(user_client, db):
    rep = _report(db, W2, status="planning")
    assert re.search(r'class="step current[^"]*"><a href="\?step=plan"', user_client.get(f"/reports/{rep.id}").text)
    rep.status = "confirmed"
    db.commit()
    assert re.search(r'class="step current[^"]*"><a href="\?step=files"', user_client.get(f"/reports/{rep.id}").text)


def test_summary_saves(user_client, db):
    rep = _report(db, W2)
    rev = _rev(user_client, f"/reports/{rep.id}")
    r = user_client.post(f"/reports/{rep.id}/summary", data={"_rev": rev, "summary_md": "- 수주 1건", "highlights_md": "예산 심의 지연"}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == f"/reports/{rep.id}?step=result&notice=saved#summary"
    db.refresh(rep)
    assert (rep.summary_md, rep.highlights_md) == ("- 수주 1건", "예산 심의 지연")


def test_summary_conflict_409(user_client, db):
    rep = _report(db, W2, summary_md="처음")
    rev = _rev(user_client, f"/reports/{rep.id}")
    rep.summary_md = "다른 사람이 고침"
    db.commit()
    r = user_client.post(f"/reports/{rep.id}/summary", data={"_rev": rev, "summary_md": "내 글", "highlights_md": ""}, follow_redirects=False)
    assert r.status_code == 409
    assert "내 글" in r.text  # 쓴 값 그대로 다시 그린다
    db.refresh(rep)
    assert rep.summary_md == "다른 사람이 고침"


def test_notes_save_as_lines(user_client, db):
    rep = _report(db, W2)
    rev = _rev(user_client, f"/reports/{rep.id}")
    r = user_client.post(f"/reports/{rep.id}/notes", data={"_rev": rev, "step": "result", "sn_kpi": "활동 7건입니다.\n\n  수주 0건입니다. "}, follow_redirects=False)
    assert r.status_code == 303 and "notice=saved" in r.headers["location"]
    db.refresh(rep)
    assert rep.section_notes == {"kpi": ["활동 7건입니다.", "수주 0건입니다."]}
    rev = _rev(user_client, f"/reports/{rep.id}")
    user_client.post(f"/reports/{rep.id}/notes", data={"_rev": rev, "step": "check", "sn_check": "점검 1건"}, follow_redirects=False)
    db.refresh(rep)
    assert rep.section_notes == {"kpi": ["활동 7건입니다.", "수주 0건입니다."], "check": ["점검 1건"]}  # 보낸 구역만 바뀐다


def test_check_saves_and_carries(user_client, db):
    prev, (a, b, c) = _prev_with_items(db, 3)
    rep = _report(db, W2)
    url = f"/reports/{rep.id}?step=check"
    html = user_client.get(url).text
    assert "할 일 0" in html and "할 일 2" in html
    rev = _form_rev(html, f"/reports/{rep.id}/check")
    data = {
        "_rev": rev, "item_id": [str(a.id), str(b.id), str(c.id)],
        f"result_{a.id}": "done", f"note_{a.id}": "끝", f"result_{b.id}": "doing", f"note_{b.id}": "",
        f"result_{c.id}": "", f"note_{c.id}": "",
    }
    r = user_client.post(f"/reports/{rep.id}/check", data=data, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == f"/reports/{rep.id}?step=check&notice=report_checked"
    db.expire_all()
    assert (a.result, a.result_note, a.checked_in_report_id) == ("done", "끝", rep.id)
    assert (b.result, b.checked_in_report_id) == ("doing", rep.id)
    assert (c.result, c.checked_in_report_id) == ("", None)  # 빈 줄은 점검한 것으로 치지 않는다
    rep = db.get(Report, rep.id)
    assert rep.status == "planning"
    assert sorted(i.carried_from_id for i in rep.plan_items if i.carried_from_id) == sorted([b.id, c.id])
    # 옛 표시로 다시 내면 409 이고 값은 그대로
    stale = dict(data, **{f"result_{a.id}": "dropped"})
    assert user_client.post(f"/reports/{rep.id}/check", data=stale, follow_redirects=False).status_code == 409
    db.expire_all()
    assert a.result == "done"


def test_plan_rows_add_edit_delete(user_client, db):
    rep = _report(db, W2, status="planning")
    one = ReportPlanItem(report_id=rep.id, position=0, task="첫째", owner="김")
    two = ReportPlanItem(report_id=rep.id, position=1, task="둘째", owner="이", project_id=None)
    db.add_all([one, two])
    db.commit()
    two_id = two.id
    html = user_client.get(f"/reports/{rep.id}?step=plan").text
    rev = _form_rev(html, f"/reports/{rep.id}/plan")
    data = {
        "_rev": rev,
        "pi_id": [str(one.id), "", ""], "pi_task": ["첫째 고침", "새 일", ""], "pi_owner": ["김팀장", "박", ""],
        "pi_due": ["2026-12-02", "날짜아님", ""], "pi_note": ["메모", "", ""], "pi_customer": ["", "", ""],
    }
    r = user_client.post(f"/reports/{rep.id}/plan", data=data, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == f"/reports/{rep.id}?step=plan&notice=report_planned"
    db.expire_all()
    rep = db.get(Report, rep.id)
    rows = [(i.id, i.position, i.task, i.owner, i.due, i.note) for i in rep.plan_items]
    assert rows[0] == (one.id, 0, "첫째 고침", "김팀장", date(2026, 12, 2), "메모")  # 같은 번호는 고친다
    assert rows[1][1:] == (1, "새 일", "박", None, "")  # 새 줄
    assert len(rows) == 2 and db.get(ReportPlanItem, two_id) is None  # 빠진 번호는 지운다


def test_plan_customer_link_exact_only(user_client, db):
    db.add_all([Customer(name="한빛시청"), Customer(name="동명상사"), Customer(name="동명 상사")])
    db.commit()
    rep = _report(db, W2)
    rev = _form_rev(user_client.get(f"/reports/{rep.id}?step=plan").text, f"/reports/{rep.id}/plan")
    data = {
        "_rev": rev, "pi_id": ["", "", ""], "pi_task": ["가", "나", "다"], "pi_owner": ["", "", ""], "pi_due": ["", "", ""], "pi_note": ["", "", ""],
        "pi_customer": ["한빛시청", "동명상사", "한빛"],
    }
    r = user_client.post(f"/reports/{rep.id}/plan", data=data, follow_redirects=False)
    assert "notice=report_planned_unlinked" in r.headers["location"]  # 연결하지 못한 고객 이름이 있다
    db.expire_all()
    items = db.get(Report, rep.id).plan_items
    hanbit = db.scalars(select(Customer).where(Customer.name == "한빛시청")).one()
    assert [i.customer_id for i in items] == [hanbit.id, None, None]
    assert db.get(Report, rep.id).status == "planning"


def test_plan_item_change_bumps_report_rev(db):
    rep = _report(db, W2)
    item = ReportPlanItem(report_id=rep.id, task="가")
    db.add(item)
    db.commit()
    before = guards.rev_of(rep)
    item.task = "나"
    db.commit()
    assert guards.rev_of(rep) != before


def test_confirm_then_unconfirm(user_client, db):
    rep = _report(db, W2, status="planning")
    tok = TOK.search(user_client.get(f"/reports/{rep.id}?step=files").text).group(1)
    r = user_client.post(f"/reports/{rep.id}/confirm", data={"_tok": tok}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == f"/reports/{rep.id}?step=files&notice=report_confirmed"
    db.refresh(rep)
    assert rep.status == "confirmed" and rep.snapshot is not None
    assert all(isinstance(v, list) for v in rep.section_notes.values())
    assert "확정 풀기" in user_client.get(f"/reports/{rep.id}").text
    r = user_client.post(f"/reports/{rep.id}/unconfirm", follow_redirects=False)
    assert nloc(r) == f"/reports/{rep.id}?step=files&notice=report_unconfirmed"
    db.refresh(rep)
    assert rep.status == "planning" and rep.snapshot is None
    # 확정이 아닌 보고를 풀면 바꾸지 않고 알린다
    r = user_client.post(f"/reports/{rep.id}/unconfirm", follow_redirects=False)
    assert nloc(r) == f"/reports/{rep.id}?step=files&notice=report_unconfirm_blocked"


def test_unconfirm_blocked_when_next_report_checked(user_client, db):
    prev, (a, *_) = _prev_with_items(db, 1)
    nxt = _report(db, W2)
    a.result, a.checked_in_report_id = "done", nxt.id
    db.commit()
    assert "이미 점검" in user_client.get(f"/reports/{prev.id}?step=files").text
    r = user_client.post(f"/reports/{prev.id}/unconfirm", follow_redirects=False)
    assert nloc(r) == f"/reports/{prev.id}?step=files&notice=report_unconfirm_blocked"
    db.refresh(prev)
    assert prev.status == "confirmed"


def test_delete_has_confirm_page(user_client, db):
    prev, (a, *_) = _prev_with_items(db, 1)
    rep = _report(db, W2)
    a.checked_in_report_id = rep.id
    db.commit()
    rid, aid = rep.id, a.id
    page = user_client.get(f"/reports/{rep.id}/delete")
    assert page.status_code == 200 and "주간 2026-11-23 ~ 11-29" in page.text
    assert f'data-confirm-post="/reports/{rep.id}/delete"' in user_client.get(f"/reports/{rep.id}").text
    r = user_client.post(f"/reports/{rep.id}/delete", follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == "/reports?notice=deleted"
    db.expire_all()
    assert db.get(Report, rid) is None
    assert db.get(ReportPlanItem, aid).checked_in_report_id is None  # 지난 항목은 남고 표시만 비워진다


@pytest.mark.ai_gate
def test_works_with_ai_off(user_client, db, app):
    assert app.state.settings.ai_worker_enabled is False
    prev, (a, b) = _prev_with_items(db, 2)
    r = user_client.post("/reports", data={"unit": "week", "day": "2026-11-25"}, follow_redirects=False)
    assert r.status_code == 303
    rid = int(re.search(r"/reports/(\d+)", r.headers["location"]).group(1))
    for step in ("result", "check", "plan", "files"):
        assert user_client.get(f"/reports/{rid}?step={step}").status_code == 200
    html = user_client.get(f"/reports/{rid}?step=check").text
    r = user_client.post(f"/reports/{rid}/check", data={
        "_rev": _form_rev(html, f"/reports/{rid}/check"), "item_id": [str(a.id), str(b.id)],
        f"result_{a.id}": "done", f"note_{a.id}": "", f"result_{b.id}": "todo", f"note_{b.id}": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    html = user_client.get(f"/reports/{rid}?step=plan").text
    ids = re.findall(r'name="pi_id" value="(\d*)"', html)
    tasks = re.findall(r'name="pi_task"[^>]*value="([^"]*)"', html)
    assert ids[0] and tasks[0] == "할 일 1"  # 미착수 항목이 넘어와 있다
    r = user_client.post(f"/reports/{rid}/plan", data={
        "_rev": _form_rev(html, f"/reports/{rid}/plan"), "pi_id": [ids[0], ""], "pi_task": [tasks[0], "직접 쓴 계획"],
        "pi_owner": ["", ""], "pi_due": ["", ""], "pi_note": ["", ""], "pi_customer": ["", ""],
    }, follow_redirects=False)
    assert r.status_code == 303
    tok = TOK.search(user_client.get(f"/reports/{rid}?step=files").text).group(1)
    r = user_client.post(f"/reports/{rid}/confirm", data={"_tok": tok}, follow_redirects=False)
    assert r.status_code == 303
    rep = db.get(Report, rid)
    db.refresh(rep)
    assert rep.status == ontology.REPORT_STATUS_CONFIRMED and [i.task for i in rep.plan_items] == ["할 일 1", "직접 쓴 계획"]
    assert rep.section_notes["plan"]  # 기본 보고 문구


def test_confirmed_plan_is_locked(user_client, db):
    rep = _report(db, W2, status="confirmed")
    db.add(ReportPlanItem(report_id=rep.id, task="확정한 일"))
    db.commit()
    html = user_client.get(f"/reports/{rep.id}?step=plan").text
    assert "<fieldset class=\"bare\" disabled>" in html
    r = user_client.post(f"/reports/{rep.id}/plan", data={"pi_id": [""], "pi_task": ["바꿈"]}, follow_redirects=False)
    assert r.status_code == 409
    db.expire_all()
    assert [i.task for i in db.get(Report, rep.id).plan_items] == ["확정한 일"]

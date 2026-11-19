"""활동 기록 폼: 체크 상자 연결 선택, 구역 나누기, 저장 줄."""
import re

from app.models import Activity, Customer, Solution


def _make(db, n, prefix="솔루션"):
    items = [Solution(name=f"{prefix}{i:02d}") for i in range(n)]
    db.add_all(items)
    db.commit()
    return items


def _picker(html, field):
    return re.search(rf'<fieldset class="picker[^"]*"[^>]*data-field="{field}".*?</fieldset>', html, re.S).group(0)


def test_link_pickers_are_checkboxes_not_selects(user_client, db):
    s = _make(db, 3)
    html = user_client.get(f"/activities/{_new_activity(db, s).id}/edit").text
    assert "multiple" not in html and 'type="checkbox" name="solution_ids"' in html


def _new_activity(db, solutions):
    a = Activity(kind="meeting", title="t", raw_text="원문", created_by_id=1)
    a.solutions = list(solutions)
    db.add(a)
    db.commit()
    return a


def test_selected_items_first_and_count(user_client, db):
    s = _make(db, 5)
    a = _new_activity(db, [s[3], s[4]])
    box = _picker(user_client.get(f"/activities/{a.id}/edit").text, "solution_ids")
    ids = re.findall(r'name="solution_ids" value="(\d+)"', box)
    assert ids[:2] == [str(s[3].id), str(s[4].id)] and len(ids) == 5
    assert box.count(" checked") == 2
    assert "2개 선택" in box
    assert "picker-filter" not in box and "scroll" not in box.split(">", 1)[0]


def test_filter_and_scroll_only_above_eight_items(user_client, db):
    _make(db, 9)
    box = _picker(user_client.get(f"/activities/{_new_activity(db, []).id}/edit").text, "solution_ids")
    assert "picker-filter" in box and "scroll" in box.split(">", 1)[0]


def test_form_values_still_save(user_client, db):
    s = _make(db, 2)
    r = user_client.post(f"/activities/{_new_activity(db, [s[0]]).id}", data={
        "kind": "meeting", "date": "2026-11-10", "title": "t", "raw_text": "원문", "solution_ids": [str(s[1].id)]},
        follow_redirects=False)
    assert r.status_code == 303
    assert [x.id for x in db.query(Activity).one().solutions] == [s[1].id]


def test_form_sections_and_save_bar(user_client, db):
    a = _new_activity(db, [])
    html = user_client.get(f"/activities/{a.id}/edit").text
    for name in ("기본 정보", "원문 메모", "정리본", "후속 조치", "수요 단서", "연결"):
        assert name in html
    assert re.search(r'<details class="formsec" id="raw-memo" open>', html)
    assert 'class="row savebar"' in html


def test_draft_page_collapses_raw_memo(app, user_client, db, settings, fake_claude):
    from tests.test_activity_ai import GOOD, _run
    import json
    fake_claude.set(mode="ok", result=json.dumps(GOOD, ensure_ascii=False))
    url = user_client.post("/activities/draft", data={"raw_text": "메모 원문"}, follow_redirects=False).headers["location"]
    _run(app, settings)
    html = user_client.get(url).text
    assert re.search(r'<details class="formsec" id="raw-memo">', html)
    assert "메모 원문" in html and 'class="row savebar"' in html


def test_proposal_target_uses_same_picker(user_client, db):
    s = [Solution(name=f"S{i}", stage="survey") for i in range(3)]
    db.add_all([s[0], s[1], s[2], Customer(name="C")])
    db.commit()
    html = user_client.get("/proposals/new").text
    assert 'data-field="extra_solution_ids"' in html and html.count('name="extra_solution_ids"') == 3

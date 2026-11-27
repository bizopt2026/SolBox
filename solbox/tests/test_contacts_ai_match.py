"""담당자 관리 부담 줄이기: 회의 참석자 맞추기 (`app/contacts.py`)."""
from app import contacts as ct
from app.ai.tasks import ActivityDraft, draft_summary_md


class _C:
    def __init__(self, id, name):
        self.id, self.name = id, name


PEOPLE = [_C(1, "최현수"), _C(2, "이정민"), _C(3, "김 서윤"), _C(4, "김서윤")]


def test_attendee_list_accepts_strings_and_objects():
    rows = ct.attendee_list(["최현수 상무", {"name": "이정민", "title": "차장", "company": "한빛", "internal": False}, {"name": "김영업", "internal": True}, 3, {"title": "x"}])
    assert rows[0] == {"name": "최현수 상무", "title": "", "company": "", "internal": False}
    assert rows[1] == {"name": "이정민", "title": "차장", "company": "한빛", "internal": False}
    assert rows[2]["internal"] is True and len(rows) == 3


def test_match_attendees_statuses():
    att = ct.attendee_list([{"name": "최현수", "title": "상무"}, {"name": "김서윤"}, {"name": "박도윤", "title": "대리"},
                            {"name": "김영업", "internal": True}, {"name": "박 하늘"}, {"name": "이"}])
    got = ct.match_attendees(PEOPLE, att, staff_names={"박하늘"})
    assert [(r["name"], r["status"], r["contact_id"]) for r in got] == [
        ("최현수", "matched", 1), ("김서윤", "ambiguous", None), ("박도윤", "new", None)]
    assert got[1]["candidates"] == [3, 4] and got[2]["title"] == "대리"


def test_match_attendees_name_with_title_suffix():
    got = ct.match_attendees(PEOPLE, ct.attendee_list(["최현수 상무"]), staff_names=set())
    assert got[0]["status"] == "matched" and got[0]["contact_id"] == 1


def test_found_in_text_unique_names_only():
    text = "오늘 최현수 상무, 김서윤 과장과 회의."
    assert [c.id for c in ct.found_in_text(PEOPLE, text)] == [1]  # 김서윤은 둘이라 빼고, 이정민은 글에 없다
    assert ct.found_in_text([_C(9, "이")], "이 회의") == []


def test_draft_attendees_object_shape_and_summary():
    d = ActivityDraft.model_validate({"attendees": [{"name": "최현수", "title": "상무"}, "이정민"]})
    assert "- 최현수 (상무)\n- 이정민" in draft_summary_md(d)


def test_found_in_text_uses_word_boundaries_and_skips_staff():
    people = [_C(1, "이수정"), _C(2, "Ann"), _C(3, "김민수"), _C(4, "최현수")]
    assert ct.found_in_text(people, "이 수정안을 planning 회의에서 검토") == []
    assert [c.id for c in ct.found_in_text(people, "최현수님과 Ann, 김민수 매니저(우리측)")] == [2, 3, 4]
    assert [c.id for c in ct.found_in_text(people, "최현수님과 김민수 매니저", staff={"김민수"})] == [4]

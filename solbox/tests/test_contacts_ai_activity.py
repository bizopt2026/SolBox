"""담당자 관리 부담 줄이기 ①: 활동 기록 저장이 참석한 담당자를 잇고 영업기회로 번진다."""
import json

from sqlalchemy import select

from app.ai.queue import process_next
from app.models import AIJob, Activity, Contact, Customer, Opportunity, Partner
from tests.conftest import nloc


def _world(db):
    c, other = Customer(name="한빛물류"), Customer(name="대성")
    db.add_all([c, other])
    db.commit()
    choi = Contact(name="최현수", title="상무", role="decision", customer=c)
    lee = Contact(name="이정민", title="차장", customer=c)
    kim = Contact(name="김민수", customer=other)
    o = Opportunity(name="태양광", customer_id=c.id, stage="proposing")
    db.add_all([choi, lee, kim, o])
    db.commit()
    return c, other, choi, lee, kim, o


DRAFT = {"kind": "meeting", "date": "2026-11-20", "title": "한빛물류 미팅",
         "attendees": [{"name": "최현수", "title": "상무", "company": "한빛물류"}, {"name": "박도윤", "title": "대리", "company": "한빛물류"},
                       {"name": "김영업", "internal": True}],
         "mentions": {"customers": ["한빛물류"]}}


def _draft_page(app, user_client, settings, fake_claude, memo="한빛물류 회의: 최현수 상무, 박도윤 대리, 이정민 차장 참석"):
    r = user_client.post("/activities/draft", data={"raw_text": memo}, follow_redirects=False)
    fake_claude.set(mode="ok", result=json.dumps(DRAFT))
    assert process_next(app.state.session_factory, app.state.runner, settings)
    return user_client.get(nloc(r).split("?")[0]).text


def test_draft_prechecks_matched_and_text_found_and_offers_new_person(app, user_client, db, settings, fake_claude):
    c, other, choi, lee, kim, o = _world(db)
    page = _draft_page(app, user_client, settings, fake_claude)
    box = page.split('id="attendees"')[1]
    assert f'name="contact_ids" value="{choi.id}" checked' in box and f'name="contact_ids" value="{lee.id}" checked' in box  # AI 참석자 + 글에서 찾음
    assert f'value="{kim.id}"' not in box  # 다른 고객 담당자는 없다
    assert 'name="new_people" value="박도윤|대리">' in box and "김영업" not in box


def _post_activity(user_client, c, o, contact_ids, new_people=(), memo="회의"):
    data = {"kind": "meeting", "date": "2026-11-20", "title": "미팅", "raw_text": memo, "customer_ids": [str(c.id)],
            "opportunity_ids": [str(o.id)], "contact_field": "1", "contact_ids": [str(i) for i in contact_ids], "new_people": list(new_people)}
    return user_client.post("/activities", data=data, follow_redirects=False)


def test_save_links_contacts_spreads_to_opportunity_and_creates_new_person(user_client, db):
    c, other, choi, lee, kim, o = _world(db)
    r = _post_activity(user_client, c, o, [choi.id, kim.id], ["박도윤|대리"], memo="이정민 차장도 왔음")
    assert r.status_code == 303
    a = db.scalar(select(Activity))
    db.refresh(o)
    assert sorted(p.name for p in a.contacts) == ["박도윤", "최현수"]  # 범위 밖 김민수는 버리고, 체크 안 한 이정민은 붙지 않는다
    park = db.scalar(select(Contact).where(Contact.name == "박도윤"))
    assert (park.customer_id, park.title, park.origin_type) == (c.id, "대리", "manual")
    assert sorted(p.name for p in o.contacts) == ["박도윤", "최현수"]
    detail = user_client.get(f"/activities/{a.id}").text
    assert "참석한 담당자" in detail and f'href="/contacts/{choi.id}"' in detail


def test_new_person_needs_exactly_one_company(user_client, db):
    c, other, choi, lee, kim, o = _world(db)
    data = {"kind": "meeting", "title": "미팅", "raw_text": "r", "customer_ids": [str(c.id), str(other.id)], "contact_field": "1", "new_people": ["박도윤|대리"]}
    user_client.post("/activities", data=data)
    assert db.scalar(select(Contact).where(Contact.name == "박도윤")) is None


def test_raw_save_auto_links_names_in_text(user_client, db):
    c, other, choi, lee, kim, o = _world(db)
    r = user_client.post("/activities/raw", data={"raw_text": "최현수 상무와 통화. 김민수 언급", "customer_id": str(c.id)}, follow_redirects=False)
    assert r.status_code == 303
    a = db.scalar(select(Activity))
    assert [p.name for p in a.contacts] == ["최현수"]


def test_edit_form_shows_and_can_remove_attendees(user_client, db):
    c, other, choi, lee, kim, o = _world(db)
    _post_activity(user_client, c, o, [choi.id, lee.id])
    a = db.scalar(select(Activity))
    page = user_client.get(f"/activities/{a.id}/edit").text
    assert f'name="contact_ids" value="{choi.id}" checked' in page
    user_client.post(f"/activities/{a.id}", data={"kind": "meeting", "title": "미팅", "raw_text": "최현수", "customer_ids": [str(c.id)],
                                                  "contact_field": "1", "contact_ids": [str(lee.id)]})
    db.refresh(a)
    assert [p.name for p in a.contacts] == ["이정민"]  # 푼 사람은 글에 이름이 있어도 다시 붙지 않는다
    user_client.post(f"/activities/{a.id}", data={"kind": "meeting", "title": "미팅 2", "raw_text": "최현수", "customer_ids": [str(c.id)]})
    db.refresh(a)
    assert [p.name for p in a.contacts] == ["이정민"]  # 칸이 없는 옛 폼은 그대로


def test_mirror_line(app, user_client, db):
    c, other, choi, lee, kim, o = _world(db)
    _post_activity(user_client, c, o, [choi.id])
    a = db.scalar(select(Activity))
    assert "- 참석한 담당자: 최현수 상무" in app.state.mirror.path_for(a).read_text(encoding="utf-8")


def test_intake_activity_links_matched_attendees(app, user_client, db, settings):
    from app.ai.intake_tasks import ActivityItem
    from app.ai.tasks import ActivityDraft
    from tests.conftest import form_from_page, ready_bundle

    c, other, choi, lee, kim, o = _world(db)
    draft = ActivityDraft(title="한빛물류 미팅", attendees=[{"name": "최현수", "title": "상무"}, {"name": "박도윤"}], mentions={"customers": ["한빛물류"]})
    it = ready_bundle(db, settings, [("memo.txt", "한빛물류 회의".encode())], activities=[ActivityItem(draft=draft, files=[1])])
    user_client.post(f"/intake/{it.id}/apply", data=form_from_page(user_client, it), follow_redirects=False)
    a = db.scalar(select(Activity))
    assert [p.name for p in a.contacts] == ["최현수"] and db.scalar(select(Contact).where(Contact.name == "박도윤")) is None
    db.refresh(it)
    assert any("(참석 담당자 1명 연결)" in str(e.get("label")) for e in it.result)


def test_error_rerender_keeps_attendee_choices(user_client, db):
    c, other, choi, lee, kim, o = _world(db)
    data = {"kind": "meeting", "title": "미팅", "raw_text": "최현수 이정민", "customer_ids": [str(c.id)], "contact_field": "1",
            "contact_ids": [str(lee.id)], "date": "nope-date"}
    r = user_client.post("/activities", data={**data, "raw_text": ""})  # 원문이 비면 오류로 다시 그린다
    assert 'name="contact_field" value="1"' in r.text and f'name="contact_ids" value="{lee.id}" checked' in r.text

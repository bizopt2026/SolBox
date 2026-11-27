"""담당자 관리 부담 줄이기 ④: 명함·올리기의 바뀐 정보 제안."""
from sqlalchemy import select

from app import ai_inbox
from app import contact_suggestions as cs
from app.ai.intake_tasks import Contact as PlanContact
from app.models import Contact, ContactSuggestion, Customer, User
from tests.conftest import form_from_page, nloc, ready_bundle

CARD = ("명함.txt", "이정민 부장 010-2222-3333 jm@hanbit.kr".encode())


def _world(db):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    p = Contact(name="이정민", title="차장", phone="010-2222-3333", email="old@hanbit.kr", customer=c)
    db.add(p)
    db.commit()
    return c, p


def _upload(db, settings, user_client, c, **kw):
    base = dict(target_type="customer", target_id=c.id, person="이정민", role="부장", phone="010-2222-3333", email="jm@hanbit.kr", files=[1], reason="명함")
    it = ready_bundle(db, settings, [CARD], contacts=[PlanContact(**{**base, **kw})])
    user_client.post(f"/intake/{it.id}/apply", data=form_from_page(user_client, it), follow_redirects=False)
    db.refresh(it)
    return it


def test_same_person_card_makes_title_and_email_suggestions_once(user_client, db, settings):
    c, p = _world(db)
    for _ in range(3):
        it = _upload(db, settings, user_client, c)
    rows = db.scalars(select(ContactSuggestion).order_by(ContactSuggestion.field)).all()
    assert [(s.field, s.old_value, s.new_value) for s in rows] == [("email", "old@hanbit.kr", "jm@hanbit.kr"), ("title", "차장", "부장")]
    assert len(db.scalars(select(Contact)).all()) == 1
    assert any("이미 있는 담당자" in e.get("reason", "") for e in it.result)


def test_name_only_twin_is_asked_not_created(user_client, db, settings):
    c, p = _world(db)
    _upload(db, settings, user_client, c, phone="010-9999-0000", email="")
    s = db.scalar(select(ContactSuggestion))
    assert s.field == "person" and "010-9999-0000" in s.new_value and len(db.scalars(select(Contact)).all()) == 1
    page = user_client.get("/ai/inbox").text
    assert "같은 사람: 정보 반영" in page and "다른 사람: 새 담당자로 추가" in page
    r = user_client.post(f"/contacts/suggestions/{s.id}/new", data={"back": "/ai/inbox"}, follow_redirects=False)
    assert nloc(r) == "/ai/inbox?notice=contact_suggestion_new"
    twin = db.scalar(select(Contact).where(Contact.id != p.id))
    assert (twin.name, twin.phone, twin.customer_id, twin.origin_type) == ("이정민", "010-9999-0000", c.id, "intake")


def test_name_only_twin_same_person_applies_phone_and_keeps_old_in_note(user_client, db, settings):
    c, p = _world(db)
    _upload(db, settings, user_client, c, phone="010-9999-0000", email="")
    s = db.scalar(select(ContactSuggestion))
    r = user_client.post(f"/contacts/suggestions/{s.id}/apply", follow_redirects=False)
    assert nloc(r) == f"/contacts/{p.id}?notice=contact_suggestion_applied"
    db.refresh(p)
    assert (p.phone, p.title) == ("010-9999-0000", "부장") and "이전 휴대폰 010-2222-3333" in p.note


def test_apply_skips_when_changed_meanwhile_and_apply_all(user_client, db, settings):
    c, p = _world(db)
    _upload(db, settings, user_client, c)
    p.title = "과장"
    db.commit()
    r = user_client.post("/contacts/suggestions/apply-all", data={"back": "/ai/inbox"}, follow_redirects=False)
    assert nloc(r) == "/ai/inbox?notice=contact_suggestion_applied"
    db.refresh(p)
    assert p.title == "과장" and p.email == "old@hanbit.kr, jm@hanbit.kr"  # 직함은 그사이 바뀌어 반영 안 함, 이메일은 덧붙임
    states = {s.field: s.state for s in db.scalars(select(ContactSuggestion))}
    assert states == {"title": "changed", "email": "applied"}


def test_inbox_count_detail_and_dismiss(user_client, db, settings):
    c, p = _world(db)
    _upload(db, settings, user_client, c)
    assert ai_inbox.pending_count(db) == 2
    detail = user_client.get(f"/contacts/{p.id}").text
    assert "바뀐 정보 제안 2" in detail and "직함: 차장 → 부장" in detail
    s = db.scalar(select(ContactSuggestion).where(ContactSuggestion.field == "title"))
    user_client.post(f"/contacts/suggestions/{s.id}/dismiss")
    db.refresh(s)
    assert s.state == "dismissed" and ai_inbox.pending_count(db) == 1
    r = user_client.post(f"/contacts/suggestions/{s.id}/apply", follow_redirects=False)
    assert "notice=contact_suggestion_done" in nloc(r)


def test_card_import_skipped_same_person_still_proposes(user_client, db, settings):
    import io

    from openpyxl import Workbook

    c, p = _world(db)
    wb = Workbook()
    wb.active.append(["이름", "회사", "부서", "직책", "휴대폰", "전화", "이메일"])
    wb.active.append(["이정민", "한빛", "구매팀", "부장", "010-2222-3333", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    r = user_client.get("/intake/cards").text
    import re as _re
    tok = _re.search(r'name="_tok" value="([^"]+)"', r).group(1)
    user_client.post("/intake/cards", data={"_tok": tok, "kind": "customer"}, files={"file": ("cards.xlsx", buf.getvalue())}, follow_redirects=False)
    from app.models import Intake
    it = db.scalar(select(Intake))
    item = next(i for i in it.plan["items"] if i["kind"] == "add_contact")
    assert item["skip_default"] and item["dup_person"] and "제안으로 올려요" in item["skip_note"]
    form = form_from_page(user_client, it)
    if not any(k.startswith("skip_") for k in form):
        form[f"skip_{item['key']}"] = ["on"]
    form.pop("_tok", None)
    user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    s = db.scalar(select(ContactSuggestion))
    assert s is not None and (s.field, s.new_value) == ("title", "구매팀 부장")


def test_person_same_keeps_fields_edited_meanwhile_and_syncs_opportunity(app, user_client, db, settings):
    from app.models import Opportunity

    c, p = _world(db)
    o = Opportunity(name="건", customer_id=c.id, stage="proposing")
    db.add(o)
    db.commit()
    o.contacts = [p]
    db.commit()
    _upload(db, settings, user_client, c, phone="010-9999-0000", email="")
    s = db.scalar(select(ContactSuggestion))
    p.title = "상무"
    db.commit()
    user_client.post(f"/contacts/suggestions/{s.id}/apply")
    db.refresh(p)
    assert p.title == "상무" and p.phone == "010-9999-0000"  # 그사이 고친 직함은 그대로
    assert "이정민 상무" in app.state.mirror.path_for(o).read_text(encoding="utf-8")


def test_person_new_does_not_duplicate_existing(user_client, db, settings):
    c, p = _world(db)
    _upload(db, settings, user_client, c, phone="010-9999-0000", email="")
    s = db.scalar(select(ContactSuggestion))
    db.add(Contact(name="이정민", phone="010-9999-0000", customer=c))
    db.commit()
    user_client.post(f"/contacts/suggestions/{s.id}/new")
    assert len(db.scalars(select(Contact)).all()) == 2


def test_only_unchanged_same_person_card_says_nothing_changed(user_client, db, settings):
    c, p = _world(db)
    it = _upload(db, settings, user_client, c, role="차장", email="old@hanbit.kr")
    assert db.scalar(select(ContactSuggestion)) is None
    assert any("이미 있는 담당자" in str(e.get("reason", "")) for e in it.result)


def test_card_import_only_unchanged_same_person_reports_it(user_client, db, settings):
    import io
    import re as _re

    from openpyxl import Workbook

    from app.models import Intake

    c, p = _world(db)
    wb = Workbook()
    wb.active.append(["이름", "회사", "부서", "직책", "휴대폰", "전화", "이메일"])
    wb.active.append(["이정민", "한빛", "", "차장", "010-2222-3333", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    tok = _re.search(r'name="_tok" value="([^"]+)"', user_client.get("/intake/cards").text).group(1)
    user_client.post("/intake/cards", data={"_tok": tok, "kind": "customer"}, files={"file": ("cards.xlsx", buf.getvalue())}, follow_redirects=False)
    it = db.scalar(select(Intake))
    form = form_from_page(user_client, it)
    form.pop("_tok", None)
    user_client.post(f"/intake/{it.id}/apply", data=form, follow_redirects=False)
    db.refresh(it)
    assert it.status == "applied" and any("바뀐 정보 없음" in str(e.get("reason")) for e in it.result)
    assert it.plan["outcome"]["saved"] == 0 and db.scalar(select(ContactSuggestion)) is None

"""영업 A 단계: 영업기회 등록·수정 폼의 "진행" 구역 (금액·예상 수주일·담당·다음 할 일)."""
import re
from datetime import date

from sqlalchemy import select

from app import money, opportunities as opps
from app.guards import rev_of
from app.models import Customer, Opportunity, OpportunityFieldChange, Team, User
from tests.conftest import make_user


def _customer(db, name="한빛물류"):
    c = Customer(name=name)
    db.add(c)
    db.commit()
    return c


def _me(db):
    return db.scalar(select(User).where(User.username == "kim"))


def _progress(**kw):
    base = {"progress": "1", "amount": "", "expected_close": "", "owner_user_id": "", "next_step": "", "next_step_due": ""}
    base.update(kw)
    return base


def _create(client, c, **kw):
    return client.post("/opportunities", data={"name": "태양광 PPA", "customer_id": str(c.id), **_progress(**kw)}, follow_redirects=False)


def _newest(db):
    db.expire_all()
    return db.scalars(select(Opportunity).order_by(Opportunity.id.desc())).first()


def test_new_form_defaults_owner_to_me_and_groups_by_team(user_client, db):
    c = _customer(db)
    t = Team(name="영업1팀")
    db.add(t)
    db.commit()
    me = _me(db)
    me.team_id = t.id
    make_user(db, "park", name="박하늘")
    db.commit()
    page = user_client.get(f"/opportunities/new?customer_id={c.id}").text
    assert 'name="progress" value="1"' in page
    assert '<optgroup label="영업1팀">' in page and '<optgroup label="팀 없음">' in page
    assert f'<option value="{me.id}" selected>' in page
    for name in ("amount", "expected_close", "owner_user_id", "next_step", "next_step_due"):
        assert f'name="{name}"' in page


def test_create_saves_progress_values(user_client, db):
    c = _customer(db)
    me = _me(db)
    r = _create(user_client, c, amount="3.5억", expected_close="2026-12-20", owner_user_id=str(me.id), next_step="수정 견적 회신", next_step_due="2026-11-28")
    assert r.status_code == 303, r.text
    o = _newest(db)
    assert (o.amount, o.expected_close, o.owner_user_id, o.next_step, o.next_step_due) == (
        350_000_000, date(2026, 12, 20), me.id, "수정 견적 회신", date(2026, 11, 28))


def test_create_without_progress_fields_gets_me_as_owner(user_client, db):
    c = _customer(db)
    r = user_client.post("/opportunities", data={"name": "옛 화면", "customer_id": str(c.id)}, follow_redirects=False)
    assert r.status_code == 303
    assert _newest(db).owner_user_id == _me(db).id


def test_bad_amount_redraws_with_typed_text_and_field_error(user_client, db):
    c = _customer(db)
    r = _create(user_client, c, amount="삼억", next_step="견적")
    assert r.status_code == 400
    assert money.AMOUNT_HELP in r.text and 'value="삼억"' in r.text and 'value="견적"' in r.text
    assert re.search(r'<input id="f-amount" name="amount" type="text" value="삼억"[^>]* aria-invalid="true"', r.text)
    assert db.scalar(select(Opportunity)) is None


def test_bad_date_is_rejected(user_client, db):
    c = _customer(db)
    r = _create(user_client, c, expected_close="2026-13-40")
    assert r.status_code == 400 and opps.BAD_DATE in r.text


def test_unknown_or_inactive_new_owner_is_rejected(user_client, db):
    c = _customer(db)
    gone = make_user(db, "gone", name="퇴사자")
    gone.is_active = False
    db.commit()
    assert _create(user_client, c, owner_user_id="999").status_code == 400
    r = _create(user_client, c, owner_user_id=str(gone.id))
    assert r.status_code == 400 and opps.BAD_OWNER in r.text


def test_inactive_current_owner_is_kept_without_history(user_client, db):
    c = _customer(db)
    gone = make_user(db, "gone", name="퇴사자")
    opp = Opportunity(name="옛 건", customer_id=c.id, owner_user_id=gone.id, amount=100_000_000)
    db.add(opp)
    db.commit()
    gone.is_active = False
    db.commit()
    page = user_client.get(f"/opportunities/{opp.id}/edit").text
    assert f'<option value="{gone.id}" selected>퇴사자 (사용 중지)</option>' in page
    before = db.scalar(select(OpportunityFieldChange.id).order_by(OpportunityFieldChange.id.desc()))
    data = {"name": "옛 건", "customer_id": str(c.id), "_rev": rev_of(opp), **_progress(amount="1억", owner_user_id=str(gone.id), next_step="연락")}
    assert user_client.post(f"/opportunities/{opp.id}", data=data, follow_redirects=False).status_code == 303
    o = _newest(db)
    assert o.owner_user_id == gone.id and o.next_step == "연락"
    fields = list(db.scalars(select(OpportunityFieldChange.field).where(OpportunityFieldChange.id > before)))
    assert fields == ["next_step"]


def test_edit_form_shows_amount_as_edit_text(user_client, db):
    c = _customer(db)
    opp = Opportunity(name="건", customer_id=c.id, amount=123_456_789, next_step_due=date(2026, 11, 23))
    db.add(opp)
    db.commit()
    page = user_client.get(f"/opportunities/{opp.id}/edit").text
    assert 'value="123,456,789"' in page and 'value="2026-11-23"' in page


def test_update_without_progress_marker_keeps_progress_values(user_client, db):
    c = _customer(db)
    opp = Opportunity(name="건", customer_id=c.id, amount=100_000_000, next_step="견적")
    db.add(opp)
    db.commit()
    data = {"name": "건 이름 고침", "customer_id": str(c.id), "_rev": rev_of(opp)}
    assert user_client.post(f"/opportunities/{opp.id}", data=data, follow_redirects=False).status_code == 303
    o = _newest(db)
    assert (o.name, o.amount, o.next_step) == ("건 이름 고침", 100_000_000, "견적")


def test_conflict_detects_amount_changed_elsewhere(user_client, db):
    c = _customer(db)
    opp = Opportunity(name="건", customer_id=c.id, amount=100_000_000)
    db.add(opp)
    db.commit()
    stamp = rev_of(opp)
    opp.amount = 200_000_000
    db.commit()
    data = {"name": "건", "customer_id": str(c.id), "_rev": stamp, **_progress(amount="3억")}
    r = user_client.post(f"/opportunities/{opp.id}", data=data)
    assert r.status_code == 409 and "예상 금액" in r.text
    assert _newest(db).amount == 200_000_000

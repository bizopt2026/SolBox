"""영업 A 단계: 수주·실패로 바꿀 때 사유 받기 (사유 화면, 단계 막대, 되돌리면 비움)."""
from sqlalchemy import select

from app import opportunities as opps
from app.models import Customer, Opportunity, OpportunityFieldChange, OpportunityStageChange
from tests.conftest import nloc


def _opp(db, stage="negotiating"):
    c = Customer(name="한빛물류")
    db.add(c)
    db.commit()
    o = Opportunity(name="태양광 PPA", customer_id=c.id, stage=stage)
    db.add(o)
    db.commit()
    return o


def _fresh(db, o):
    db.expire_all()
    return db.get(Opportunity, o.id)


def test_stage_bar_won_or_lost_goes_to_reason_page_without_changing(user_client, db):
    o = _opp(db)
    for stage in ("won", "lost"):
        r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": stage}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/opportunities/{o.id}/close?to={stage}"
        assert _fresh(db, o).stage == "negotiating"


def test_reason_page_lists_reasons_for_target_stage(user_client, db):
    o = _opp(db)
    page = user_client.get(f"/opportunities/{o.id}/close?to=lost").text
    assert "실패로 바꾸기" in page and 'value="competitor"' in page and "경쟁사 선택" in page and "가격 경쟁력" not in page
    assert f'action="/opportunities/{o.id}/close"' in page and 'name="stage" value="lost"' in page and 'name="note"' in page
    page = user_client.get(f"/opportunities/{o.id}/close?to=won").text
    assert "수주로 바꾸기" in page and "가격 경쟁력" in page
    assert user_client.get(f"/opportunities/{o.id}/close?to=hold", follow_redirects=False).status_code == 303


def test_close_requires_a_reason_of_that_stage(user_client, db):
    o = _opp(db)
    r = user_client.post(f"/opportunities/{o.id}/close", data={"stage": "lost", "reason": "", "note": "메모"})
    assert r.status_code == 400 and opps.REASON_REQUIRED in r.text and "메모" in r.text
    r = user_client.post(f"/opportunities/{o.id}/close", data={"stage": "lost", "reason": "tech"})  # 수주 사유 코드
    assert r.status_code == 400
    r = user_client.post(f"/opportunities/{o.id}/close", data={"stage": "hold", "reason": "other"})
    assert r.status_code == 400
    assert _fresh(db, o).stage == "negotiating"


def test_close_saves_stage_reason_note_and_history(user_client, db):
    o = _opp(db)
    r = user_client.post(f"/opportunities/{o.id}/close", data={"stage": "lost", "reason": "competitor", "note": " A사가 8원 낮게 "}, follow_redirects=False)
    assert r.status_code == 303 and nloc(r) == f"/opportunities/{o.id}?notice=opportunity_stage.lost"
    f = _fresh(db, o)
    assert (f.stage, f.close_reason, f.close_note) == ("lost", "competitor", "A사가 8원 낮게")
    assert db.scalar(select(OpportunityStageChange.to_stage).order_by(OpportunityStageChange.id.desc())) == "lost"
    assert db.scalar(select(OpportunityFieldChange.new_value).where(OpportunityFieldChange.field == "close_reason")) == "competitor"
    page = user_client.get(f"/opportunities/{o.id}").text
    assert "실패 사유" in page and "경쟁사 선택" in page and "A사가 8원 낮게" in page


def test_reopening_clears_reason_but_history_keeps_it(user_client, db):
    o = _opp(db)
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "lost", "reason": "competitor", "note": "메모"})
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "proposing"}, follow_redirects=False)
    assert r.status_code == 303
    f = _fresh(db, o)
    assert (f.stage, f.close_reason, f.close_note) == ("proposing", "", "")
    vals = list(db.scalars(select(OpportunityFieldChange.new_value).where(OpportunityFieldChange.field == "close_reason").order_by(OpportunityFieldChange.id)))
    assert vals == ["competitor", ""]


def test_switching_between_won_and_lost_goes_through_reason_page(user_client, db):
    o = _opp(db, stage="lost")
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "won"}, follow_redirects=False)
    assert r.headers["location"] == f"/opportunities/{o.id}/close?to=won"
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": "trust"})
    f = _fresh(db, o)
    assert (f.stage, f.close_reason) == ("won", "trust")
    assert "수주 사유" in user_client.get(f"/opportunities/{o.id}").text


def test_close_from_a_stale_tab_shows_the_conflict_screen(user_client, db):
    """두 탭에서 수주·실패 사유 화면을 따로 열고 차례로 저장하면 늦은 쪽은 충돌 화면 (베타 점검 2026-11-27)."""
    import re

    o = _opp(db)
    won_page = user_client.get(f"/opportunities/{o.id}/close?to=won").text
    lost_page = user_client.get(f"/opportunities/{o.id}/close?to=lost").text
    rev = lambda page: re.search(r'name="_rev" value="([^"]+)"', page).group(1)  # noqa: E731
    won_reason = re.search(r'name="reason" value="([^"]+)"', won_page).group(1)
    lost_reason = re.search(r'name="reason" value="([^"]+)"', lost_page).group(1)
    r = user_client.post(f"/opportunities/{o.id}/close", data={"stage": "won", "reason": won_reason, "_rev": rev(won_page)}, follow_redirects=False)
    assert r.status_code == 303
    r = user_client.post(f"/opportunities/{o.id}/close", data={"stage": "lost", "reason": lost_reason, "_rev": rev(lost_page)}, follow_redirects=False)
    assert r.status_code == 409
    db.expire_all()
    assert db.get(type(o), o.id).stage == "won"

"""영업 B 단계: 확인 항목 체크·풀기, 단계 막대의 확인 n/m, 앞으로 넘길 때 남은 항목 알림."""
from sqlalchemy import select

from app import notices, opportunity_ai as oai
from app.models import Customer, Opportunity, OpportunityCheck, OpportunityFieldChange
from tests.conftest import nloc


def _opp(db, stage="discovery"):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    o = Opportunity(name="태양광", customer_id=c.id, stage=stage)
    db.add(o)
    db.commit()
    return o


def _items(db, o):
    db.expire_all()
    return sorted(db.scalars(select(OpportunityCheck.item).where(OpportunityCheck.opportunity_id == o.id)))


def test_check_and_uncheck_with_history(user_client, db):
    o = _opp(db)
    r = user_client.post(f"/opportunities/{o.id}/checks", data={"item": "discovery.need", "checked": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/opportunities/{o.id}#checks"
    assert _items(db, o) == ["discovery.need"]
    user_client.post(f"/opportunities/{o.id}/checks", data={"item": "discovery.need", "checked": "1"})  # 다시 눌러도 하나
    assert _items(db, o) == ["discovery.need"]
    user_client.post(f"/opportunities/{o.id}/checks", data={"item": "discovery.need", "checked": "0"})
    assert _items(db, o) == []
    assert [h.field for h in db.scalars(select(OpportunityFieldChange).where(OpportunityFieldChange.field == "check"))] == ["check", "check"]
    assert user_client.post(f"/opportunities/{o.id}/checks", data={"item": "nope", "checked": "1"}).status_code == 400


def test_htmx_toggle_returns_card_fragment(user_client, db):
    o = _opp(db)
    r = user_client.post(f"/opportunities/{o.id}/checks", data={"item": "discovery.budget", "checked": "1"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and 'id="checks"' in r.text and "<html" not in r.text and "1/4" in r.text


def test_detail_shows_current_stage_items_counts_and_leftovers(user_client, db):
    o = _opp(db, stage="proposing")
    oai.set_check(db, o, "proposing.sent", True, None)
    oai.set_check(db, o, "discovery.need", True, None)
    db.commit()
    page = user_client.get(f"/opportunities/{o.id}").text
    card = page.split('id="checks"')[1].split("</section>")[0]
    assert "제안서 전달" in card and "현장 조사·기술 검토 완료" in card and "1/4" in card
    assert "발굴 단계에서 확인하지 않은 항목 3개" in card and "의사결정 구조(실무 담당·결재자) 파악" in card
    assert "확인 1/4" in page.split('class="stagebar"')[1].split("</form>")[0]


def test_closed_opportunity_has_no_check_card(user_client, db):
    o = _opp(db, stage="won")
    assert 'id="checks"' not in user_client.get(f"/opportunities/{o.id}").text


def test_forward_move_reports_leftovers(user_client, db):
    o = _opp(db)
    oai.set_check(db, o, "discovery.need", True, None)
    db.commit()
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "proposing"}, follow_redirects=False)
    assert nloc(r) == f"/opportunities/{o.id}?notice=opportunity_stage_open.discovery.proposing.3"
    assert notices.lookup("opportunity_stage_open.discovery.proposing.3").text == "단계를 '제안 중'으로 바꿨습니다. 발굴 단계의 확인하지 않은 항목 3개가 남았습니다."
    assert notices.lookup("opportunity_stage_open.discovery.nope.3") is None and notices.lookup("opportunity_stage_open.discovery.proposing.x") is None


def test_no_leftover_notice_when_all_done_backward_or_hold(user_client, db):
    o = _opp(db)
    for code, _ in oai.items_of("discovery"):
        oai.set_check(db, o, code, True, None)
    db.commit()
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "proposing"}, follow_redirects=False)
    assert "notice=opportunity_stage.proposing" in nloc(r)
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "discovery"}, follow_redirects=False)
    assert "notice=opportunity_stage.discovery" in nloc(r)
    r = user_client.post(f"/opportunities/{o.id}/stage", data={"stage": "hold"}, follow_redirects=False)
    assert "notice=opportunity_stage.hold" in nloc(r)


def test_forward_move_rule():
    assert oai.forward_move("discovery", "proposing") and oai.forward_move("discovery", "negotiating") and oai.forward_move("proposing", "negotiating")
    assert not oai.forward_move("proposing", "discovery") and not oai.forward_move("negotiating", "won") and not oai.forward_move("hold", "proposing")

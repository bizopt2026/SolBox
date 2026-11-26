"""영업 B 단계: 영업기회 상세의 AI 제안 띠, 브리핑 카드, 이력의 AI 줄."""
from datetime import date

import pytest

from app import opportunity_ai as oai
from app.models import Activity, Customer, Opportunity


def _world(db, suggestions=True):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    o = Opportunity(name="태양광", customer_id=c.id, stage="proposing", amount=800_000_000)
    db.add(o)
    db.commit()
    a = Activity(kind="meeting", date=date(2026, 11, 20), title="2차 미팅", raw_text="r")
    a.opportunities = [o]
    db.add(a)
    db.commit()
    sugs = [
        {"id": 1, "kind": "field", "field": "amount", "item": None, "value": 1_200_000_000, "due": None, "evidence": "총 12억 정도", "activity_id": a.id, "verified": True},
        {"id": 2, "kind": "check", "field": None, "item": "proposing.competitor", "value": None, "due": None, "evidence": "A사도 제안", "activity_id": a.id, "verified": False},
        {"id": 3, "kind": "stage", "field": None, "item": None, "value": "negotiating", "due": None, "evidence": "조정안", "activity_id": a.id, "verified": True},
    ] if suggestions else []
    for s in sugs:
        s.update(state="pending", decided_by=None, decided_at=None)
    brief = {"situation": "규모 확대에 합의했습니다.", "risks": ["A사 경쟁"], "next_actions": ["단가 회신"], "outlook": ""}
    ins = oai.save_insight(db, o, None, brief, sugs, oai.basis_of(o, {a.id: "r"}))
    db.commit()
    return o, ins, a


def test_band_lists_pending_suggestions_with_evidence(user_client, db):
    o, ins, a = _world(db)
    page = user_client.get(f"/opportunities/{o.id}").text
    band = page.split('id="ai-band"')[1].split("</section>")[0]
    assert "AI 제안 3건" in band and "활동 기록 1건" in band
    assert "8억" in band and "12억" in band and "총 12억 정도" in band and f'href="/activities/{a.id}"' in band
    assert "경쟁사·대안 파악" in band and "원문에서 근거를 찾지 못했어요" in band and "제안 중" in band and "협상" in band
    assert f'action="/opportunities/{o.id}/ai/{ins.id}/1/apply"' in band and f'action="/opportunities/{o.id}/ai/{ins.id}/1/dismiss"' in band


def test_band_disappears_when_all_decided(user_client, db):
    o, ins, a = _world(db)
    for sid in (1, 2, 3):
        user_client.post(f"/opportunities/{o.id}/ai/{ins.id}/{sid}/dismiss")
    page = user_client.get(f"/opportunities/{o.id}").text
    assert 'id="ai-band"' not in page and "규모 확대에 합의했습니다." in page


def test_briefing_card(user_client, db):
    o, ins, a = _world(db, suggestions=False)
    page = user_client.get(f"/opportunities/{o.id}").text
    card = page.split('id="brief"')[1].split("</section>")[0]
    assert "규모 확대에 합의했습니다." in card and "A사 경쟁" in card and "단가 회신" in card and "AI 초안" in card and "다시 분석" in card
    assert 'id="ai-band"' not in page


@pytest.mark.ai_gate
def test_review_button_disabled_when_ai_off(user_client, db):
    o, ins, a = _world(db)
    card = user_client.get(f"/opportunities/{o.id}").text.split('id="brief"')[1].split("</section>")[0]
    assert "disabled" in card and 'id="ai-note-brief"' in card


def test_history_marks_ai_lines(user_client, db):
    o, ins, a = _world(db)
    user_client.post(f"/opportunities/{o.id}/ai/{ins.id}/1/apply")
    hist = user_client.get(f"/opportunities/{o.id}").text.split('id="h-hist"')[1].split("</section>")[0]
    assert "AI 제안" in hist and "총 12억 정도" in hist

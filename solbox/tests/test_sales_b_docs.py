"""영업 B 단계: 지식 사본의 확인 항목 줄 (브리핑은 넣지 않는다)."""
from app import opportunity_ai as oai
from app.knowledge import KNOWLEDGE_FORMAT
from app.models import Customer, Opportunity


def test_mirror_lists_checked_items_not_briefing(user_client, db, settings):
    c = Customer(name="한빛")
    db.add(c)
    db.commit()
    o = Opportunity(name="태양광", customer_id=c.id, stage="proposing")
    db.add(o)
    db.commit()
    oai.save_insight(db, o, None, {"situation": "비밀스러운 AI 브리핑 문장"}, [], {})
    db.commit()
    user_client.post(f"/opportunities/{o.id}/checks", data={"item": "proposing.sent", "checked": "1"})
    user_client.post(f"/opportunities/{o.id}/checks", data={"item": "discovery.need", "checked": "1"})
    text = (settings.knowledge_dir / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")
    assert "- 확인 항목: 고객 니즈·문제 확인, 제안서 전달" in text
    assert "비밀스러운 AI 브리핑 문장" not in text
    assert KNOWLEDGE_FORMAT >= 13

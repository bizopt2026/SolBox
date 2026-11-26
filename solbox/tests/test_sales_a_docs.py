"""영업 A 단계: 지식 사본의 진행 칸·사유 줄, 전체 검색의 다음 할 일."""
from datetime import date

from app.knowledge import KNOWLEDGE_FORMAT
from app.models import Customer, Opportunity, Team
from tests.conftest import make_user


def test_mirror_has_progress_and_reason_lines(user_client, db, settings):
    t = Team(name="영업1팀")
    db.add(t)
    db.commit()
    kim = make_user(db, "kim9", name="김영업")
    kim.team_id = t.id
    c = Customer(name="한빛물류")
    db.add(c)
    db.commit()
    r = user_client.post("/opportunities", data={"name": "태양광 PPA", "customer_id": str(c.id), "progress": "1", "amount": "12억",
                                                 "expected_close": "2026-12-20", "owner_user_id": str(kim.id), "next_step": "수정 견적 회신", "next_step_due": "2026-11-28"},
                         follow_redirects=False)
    assert r.status_code == 303
    o = db.query(Opportunity).one()
    user_client.post(f"/opportunities/{o.id}/close", data={"stage": "lost", "reason": "competitor", "note": "A사 선택"})
    text = (settings.knowledge_dir / "opportunities" / f"{o.id}.md").read_text(encoding="utf-8")
    for line in ("- 예상 금액: 12억", "- 예상 수주일: 2026-12-20", "- 담당: 김영업 (영업1팀)", "- 다음 할 일: 수정 견적 회신 (2026-11-28)",
                 "- 실패 사유: 경쟁사 선택", "- 사유 메모: A사 선택"):
        assert line in text, line
    assert KNOWLEDGE_FORMAT >= 12


def test_search_finds_next_step(user_client, db):
    c = Customer(name="한빛물류")
    db.add(c)
    db.commit()
    db.add(Opportunity(name="태양광 PPA", customer_id=c.id, next_step="구조안전진단 업체 섭외", next_step_due=date(2026, 12, 1)))
    db.commit()
    page = user_client.get("/search?q=구조안전진단").text
    assert "태양광 PPA" in page and "다음 할 일" in page

"""UX 개선 (2026-11-26): 홈 "내 할 일", AI 제안함, 관리 메뉴 접기, 목록 걸러 보기, 빈 요약 띠 등."""
import re
from datetime import date, timedelta

from sqlalchemy import select

from app import cowork, guards
from app.models import AIMessage, AIThread, Customer, Initiative, Opportunity, OpportunityInsight, User
from tests.conftest import nloc


def _kim(db):
    return db.scalars(select(User).where(User.username == "kim")).one()


def test_home_work_strip_counts(user_client, db):
    kim = _kim(db)
    db.add_all([Initiative(title="내 과제", owner_user_id=kim.id, due=date.today() - timedelta(days=1)), Initiative(title="남의 과제")])
    db.commit()
    page = user_client.get("/").text
    strip = page.split('aria-label="내 할 일"')[1].split("</nav>")[0]
    cell = strip.split('data-work="initiative"')[1].split("</a>")[0]
    assert "<b>1</b>" in cell and "기한 지남 1" in cell
    assert 'href="/ai/inbox"' in strip and 'href="/initiatives?who=mine"' in strip


def _world(db):
    c = Customer(name="한빛")
    o = Opportunity(name="한빛 ESS", customer=c)
    item = Initiative(title="실증 추진")
    db.add_all([c, o, item])
    db.commit()
    db.add(OpportunityInsight(opportunity_id=o.id, briefing={}, basis={}, suggestions=[
        {"id": 1, "kind": "field", "field": "next_step", "item": None, "value": "견적 보내기", "due": None, "evidence": "e", "activity_id": None,
         "verified": True, "state": "pending", "decided_by": None, "decided_at": None}]))
    t = AIThread(target_type="initiative", target_id=item.id, title="대화")
    db.add(t)
    db.flush()
    m = AIMessage(thread_id=t.id, role="assistant", content="a",
                  suggestions=cowork.clean_suggestions([{"kind": "status", "value": "doing", "evidence": ""}], "initiative", "", "", guards.rev_of(item), item))
    db.add(m)
    db.commit()
    return o, item, t, m


def test_inbox_lists_both_sources_and_returns_after_decisions(user_client, db):
    o, item, t, m = _world(db)
    page = user_client.get("/ai/inbox").text
    assert "다음 할 일: 견적 보내기" in page and "상태: 진행 중" in page and "실증 추진" in page and "한빛 ESS" in page
    home = user_client.get("/").text
    assert "<b>2</b>" in home.split('data-work="ai"')[1].split("</a>")[0]
    r = user_client.post(f"/cowork/threads/{t.id}/messages/{m.id}/suggestions/1/apply", data={"back": "/ai/inbox"}, follow_redirects=False)
    assert nloc(r).startswith("/ai/inbox") and "cowork_applied" in nloc(r)
    ins = db.scalars(select(OpportunityInsight)).one()
    r = user_client.post(f"/opportunities/{o.id}/ai/{ins.id}/1/dismiss", data={"back": "/ai/inbox"}, follow_redirects=False)
    assert nloc(r).startswith("/ai/inbox")
    r = user_client.post(f"/opportunities/{o.id}/ai/{ins.id}/1/dismiss", data={"back": "https://evil.example/"}, follow_redirects=False)
    assert nloc(r).startswith(f"/opportunities/{o.id}")
    assert "반영을 기다리는 AI 제안이 없습니다" in user_client.get("/ai/inbox").text


def test_admin_menu_folded_unless_on_admin_page(admin_client):
    home = admin_client.get("/").text
    fold = home.split('class="navg navg-fold"')[1].split("</nav>")[0]
    assert "<details>" in fold and "/admin/users" in fold
    users = admin_client.get("/admin/users").text
    assert "<details open>" in users.split('class="navg navg-fold"')[1].split("</nav>")[0]


def test_opportunity_list_filters_live_in_one_disclosure(user_client):
    html = user_client.get("/opportunities").text
    more = html.split('<details class="tool-more"')[1].split("</details>")[0]
    assert "멈춘 건" in more and "방향 밖" in more and "정렬" in more
    assert 'open' in user_client.get("/opportunities?stale=1").text.split('<details class="tool-more"')[1].split(">")[0]


def test_empty_progress_band_becomes_one_line(user_client, db):
    c = Customer(name="한빛")
    o = Opportunity(name="빈 건", customer=c)
    db.add_all([c, o])
    db.commit()
    page = user_client.get(f"/opportunities/{o.id}").text
    assert 'class="sumband-empty' in page and 'class="sumband"' not in page
    o.amount = 100000000
    db.commit()
    page = user_client.get(f"/opportunities/{o.id}").text
    assert 'class="sumband"' in page


def test_job_tray_lists_my_recent_jobs_with_links(user_client, db):
    from app.models import AIJob

    kim = _kim(db)
    c = Customer(name="한빛")
    o = Opportunity(name="한빛 ESS", customer=c)
    db.add_all([c, o])
    db.commit()
    db.add_all([AIJob(kind="alignment_check", status="pending", requester_id=kim.id, input={"target_type": "opportunity", "target_id": o.id}),
                AIJob(kind="report_summary", status="done", requester_id=None, input={"report_id": 1})])
    db.commit()
    html = user_client.get("/").text
    tray = html.split('class="jobtray"')[1].split("</details>")[0]
    assert "사업 방향 정렬 점검" in tray and f'href="/opportunities/{o.id}?from=tray"' in tray and "진행 중 1개" in tray
    assert "업무 보고" not in tray  # 남이 시작한 작업은 없다


def test_ai_dock_groups_alignment_and_cowork(user_client, db):
    c = Customer(name="한빛")
    o = Opportunity(name="한빛 ESS", customer=c)
    db.add_all([c, o])
    db.commit()
    dock = user_client.get(f"/opportunities/{o.id}").text.split('class="ai-dock"')[1].split('class="card related')[0]
    assert 'id="alignment"' in dock and 'id="cowork"' in dock and 'href="/ai/inbox"' in dock

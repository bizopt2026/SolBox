"""리서치 상세: 접히는 카드, 분야 탭, 내부 번호 숨기기."""
import re

from conftest import finished_run
from app.models import ResearchReport, Solution
from app.research import humanize_ids


def test_humanize_ids_replaces_known_forms(db):
    s = Solution(name="AI 공조", stage="boxed")
    db.add(s)
    db.commit()
    n = s.id
    assert humanize_ids(db, f"솔루션 (id {n})를 민다") == "솔루션 (AI 공조)를 민다"
    assert humanize_ids(db, f"id {n} 와 ID: {n}, Id:{n}") == "AI 공조 와 AI 공조, AI 공조"
    assert humanize_ids(db, "id 9999 는 없음") == "id 9999 는 없음"
    assert humanize_ids(db, f"valid {n} 그대로, id{n}0 그대로") == f"valid {n} 그대로, id{n}0 그대로"
    assert humanize_ids(db, "") == ""


def test_prompt_forbids_ids_in_body():
    from pathlib import Path
    text = (Path(__file__).parent.parent / "app/ai/prompts/research_suggest.md").read_text(encoding="utf-8")
    assert "솔루션 번호를 쓰지 말고 이름으로 쓴다" in text and "`solution_ids` 에만" in text


def test_run_page_humanizes_suggestion_text(user_client, db, settings):
    run = finished_run(db, settings)
    sug = db.query(ResearchReport).filter_by(run_id=run.id, kind="suggest").one()
    sid = db.query(Solution).first().id
    sug.suggestions = [{**sug.suggestions[1], "title": f"밀기 (id {sid})", "detail": f"id {sid} 를 민다", "reason": f"ID: {sid} 수요"}]
    db.commit()
    page = user_client.get(f"/research/runs/{run.id}").text
    assert f"id {sid}" not in page and f"ID: {sid}" not in page
    assert "밀기 (AI 공조 최적제어)" in page


def test_run_page_cards_are_details_first_three_open(user_client, db, settings):
    run = finished_run(db, settings)
    sug = db.query(ResearchReport).filter_by(run_id=run.id, kind="suggest").one()
    sug.suggestions = [{**sug.suggestions[0], "title": f"제안{i}"} for i in range(5)]
    db.commit()
    page = user_client.get(f"/research/runs/{run.id}").text
    cards = re.findall(r'<details class="trend-card( suggestion)?"( open)?>', page)
    sugs = [c for c in cards if c[0]]
    assert len(sugs) == 5 and [bool(c[1]) for c in sugs] == [True, True, True, False, False]
    assert all(not c[1] for c in cards if not c[0])  # 동향 카드는 접힌 채
    assert "후보 등록" in page and "모두 펼치기" in page and "모두 접기" in page


def test_trend_summary_shows_link_count_and_needs_check(user_client, db, settings):
    run = finished_run(db, settings)
    page = user_client.get(f"/research/runs/{run.id}").text
    assert "링크 1개" in page and "링크 0개" in page
    summary = re.search(r"<summary[^>]*>[^<]*<strong>출처 없는 동향</strong>.*?</summary>", page, re.S).group(0)
    assert "확인 필요" in summary


def test_area_tabs(user_client, db, settings):
    run = finished_run(db, settings)
    page = user_client.get(f"/research/runs/{run.id}").text
    tabs = re.search(r'<div class="tabs" role="tablist">(.*?)</div>', page, re.S).group(1)
    assert 'href="#tech"' in tabs and "기술" in tabs and "2" in tabs
    assert 'href="#policy"' in tabs and "실패" in tabs
    assert tabs.count('aria-selected="true"') == 1 and 'href="#tech" role="tab" aria-selected="true"' in tabs
    assert 'id="tech"' in page and 'id="policy"' in page


def test_insights_come_before_trends_in_area_panel(user_client, db, settings):
    run = finished_run(db, settings)
    page = user_client.get(f"/research/runs/{run.id}").text
    panel = page[page.index('id="tech"'):]
    assert panel.index("설비 운영 고객에 제안 기회") < panel.index("AI 공조 제어 확산")


def test_humanize_ids_drops_reference_right_after_the_name(db):
    s = Solution(name="AI 공조", stage="boxed")
    db.add(s)
    db.commit()
    assert humanize_ids(db, f"AI 공조(id {s.id})로 절감") == "AI 공조로 절감"
    assert humanize_ids(db, f"AI 공조 (id {s.id}) 로") == "AI 공조  로"

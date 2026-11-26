"""베타 AM (2026-11-26): 구성안을 Claude 용 글로 내보내기. 디자인은 사용자가 Claude 에서 PPT 스킬로 만든다."""

from datetime import date

from app import deck
from app.outline_export import export_markdown, open_checks
from tests.conftest import outlined_proposal, refining_proposal

OUTLINE = {"title": "에너지 제안", "slides": [
    {"kind": "cover", "title": "에너지 제안", "subtitle": "평택공장"},
    {"kind": "bullets", "title": "과제가 큽니다", "key_message": "관리가 필요합니다.", "bullets": ["사용량: 실시간으로 못 봅니다", "근거: solutions/1/card.md 참고"],
     "sources": ["solutions/1/card.md"], "needs_check": ["절감률 수치", "확인한 것"], "checked": ["확인한 것"]},
    {"kind": "table", "title": "방식", "key_message": "고르실 수 있습니다.", "columns": ["구분", "A|B"], "rows": [["비용", "없음"]]},
    {"kind": "kpi", "title": "효과", "key_message": "줄었습니다.", "kpis": [{"value": "12", "unit": "%", "label": "절감", "note": "첫해"}]},
    {"kind": "chart", "title": "추이", "key_message": "낮아졌습니다.", "chart_type": "bar", "unit": "백만원", "categories": ["1월", "2월"],
     "series": [{"name": "전", "values": [1200, 1100.5]}]},
    {"kind": "diagram", "diagram_type": "matrix", "title": "위치", "key_message": "앞섭니다.", "axis_x": "비용", "axis_y": "효과",
     "nodes": [{"label": "이번 제안", "detail": "적은 투자", "group": "왼쪽 위"}]},
    {"kind": "steps", "title": "절차", "key_message": "나눠 갑니다.", "steps": [{"name": "진단", "detail": "설비 확인"}]},
    {"kind": "closing", "title": "함께 하겠습니다", "bullets": ["일정 협의"]},
]}


def _text():
    return export_markdown(OUTLINE, deck_title="에너지 제안", target="OO제조", contact=["담당 김대리"], today=date(2026, 11, 26))


def test_export_carries_rules_and_content():
    t = _text()
    assert t.startswith("# 제안서 PPT 만들기: 에너지 제안")
    assert "내용은 바꾸거나 더하지 마세요" in t and "슬라이드에 넣지 마세요" in t and "PPT 스킬" in t
    for size in (deck.T_KPI, deck.T_DISPLAY, deck.T_H1, deck.T_H2, deck.T_BODY, deck.T_SMALL, deck.T_CAPTION):
        assert f" {size}" in t  # 글자 크기 체계를 그대로 알린다
    assert f"#{deck.BRAND}" in t and deck.FONT in t
    assert "- 제안 대상: OO제조" in t and "- 제안일: 2026. 11. 26." in t and "- 장 수: 8장" in t
    assert "### 2. 과제가 큽니다\n- 종류: 글머리\n- 결론: 관리가 필요합니다.\n- 내용:\n  - 사용량: 실시간으로 못 봅니다" in t
    assert "| 구분 | A｜B |" in t and "| 비용 | 없음 |" in t  # 표 칸의 | 는 바꿔 쓴다
    assert "**12%** 절감 (첫해)" in t
    assert "- 그래프: 가로 막대, 단위 백만원" in t and "| 1월 | 1,200 |" in t and "| 2월 | 1,100.5 |" in t
    assert "- 도식: 2×2 위치 (" in t and "첫 항목이 우리 제안입니다" in t
    assert "  - 이번 제안 [왼쪽 위]: 적은 투자" in t and "가로축: 비용 / 세로축: 효과" in t
    assert "  1. 진단: 설비 확인" in t
    assert "  - 담당 김대리" in t  # 마무리 장에 연락처
    assert "## 작성자 확인 사항 (슬라이드에 넣지 마세요)\n\n- 2장: 절감률 수치" in t and "확인한 것" not in t


def test_export_never_carries_internal_paths():
    t = _text()
    assert "card.md" not in t and "solutions/1" not in t and not deck.has_internal_mark(t.split("## 작성자 확인 사항")[0])


def test_open_checks_skip_checked():
    assert open_checks(OUTLINE) == [(2, "절감률 수치")]
    assert open_checks({"slides": [{"needs_check": ["a"], "checked": ["a"]}]}) == []


def test_outline_page_card_and_download(app, user_client, db, settings):
    p = outlined_proposal(db, settings)
    page = user_client.get(f"/proposals/{p.id}/outline").text
    assert "Claude 로 디자인하기" in page and 'id="export-text"' in page and "# 제안서 PPT 만들기:" in page
    assert f'href="/proposals/{p.id}/outline/export"' in page and 'data-copy="#export-text"' in page
    r = user_client.get(f"/proposals/{p.id}/outline/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"].startswith("attachment;") and "_Claude" in r.headers["content-disposition"]
    assert r.text.startswith("# 제안서 PPT 만들기:") and "## 장별 내용" in r.text and "절감률 수치" in r.text
    assert "solutions/1/card.md" not in r.text  # 출처 경로는 싣지 않는다


def test_export_needs_outline(app, user_client, db, settings):
    p = refining_proposal(db, settings)
    assert user_client.get(f"/proposals/{p.id}/outline/export").status_code == 404

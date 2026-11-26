"""베타 AK (2026-11-26): 남은 디자인 다듬기. 2×2 강조·축 끝, 그래프 단위 한 번, 표지 장식·정보 줄, 반 폭 카드·단계 카드의 큰 글자."""

from datetime import date

from pptx import Presentation

from app import deck
from app.deck import build_deck

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
EMU = 914400
KM = "현장 진단부터 바로 시작할 수 있습니다."
LAYOUT = ("슬라이드 밖", "본문 최대 y", "헤더 띠", "#25-b", "#28-b", "#28-c", "#0b", "#14", "#21", "#9", "#15", "#5", "#8", "최소 글자", "면 색", "차트")


def _build(tmp_path, slides, name="d.pptx", **kw):
    stats = {}
    outline = {"title": "스마트 공장 에너지 관리 제안", "slides": [{"kind": "cover", "title": "스마트 공장 에너지 관리 제안", "subtitle": "평택공장 전력 비용 절감"}, *slides]}
    kw.setdefault("customer_name", "OO제조")
    build_deck(outline, tmp_path / name, stats=stats, today=date(2026, 11, 26), **kw)
    return Presentation(tmp_path / name), stats


def _layout(stats):
    return [f for f in stats["quality"] if f["gate"].startswith(LAYOUT)]


def _named(slide, name):
    return [sh for sh in slide.shapes if sh.name == name]


def test_cover_motif_stays_in_card_and_info_row(tmp_path):
    prs, stats = _build(tmp_path, [])
    cover = prs.slides[0]
    card = _named(cover, "표지 카드")[0]
    motif = _named(cover, "표지 장식")
    assert len(motif) == 3
    for sh in motif:
        assert sh.left >= card.left and sh.top >= card.top and sh.left + sh.width <= card.left + card.width and sh.top + sh.height <= card.top + card.height
    title = _named(cover, "표지 제목")[0]
    assert title.width / EMU <= deck.CONTENT_W - 1.2 - deck.COVER_MOTIF_W + 0.001  # 제목 글 폭은 장식 앞까지
    labels = [sh.text_frame.text for sh in _named(cover, "표지 라벨")]
    assert labels == ["제안 대상", "제안일"]
    assert _named(cover, "고객 이름")[0].text_frame.text == "OO제조" and _named(cover, "날짜")[0].text_frame.text == "2026. 11. 26."
    assert _layout(stats) == []


def test_cover_without_customer_has_date_only(tmp_path):
    prs, stats = _build(tmp_path, [], customer_name="")
    assert [sh.text_frame.text for sh in _named(prs.slides[0], "표지 라벨")] == ["제안일"] and not _named(prs.slides[0], "고객 이름")


def test_chart_unit_once(tmp_path):
    slide = {"kind": "chart", "title": "요금이 낮아졌습니다", "key_message": KM, "chart_type": "column", "unit": "백만원",
             "categories": ["1월", "2월", "3월"], "series": [{"name": "도입 전", "values": [420, 410, 430]}, {"name": "도입 후", "values": [400, 380, 370]}]}
    prs, stats = _build(tmp_path, [slide])
    s = prs.slides[1]
    assert [sh.text_frame.text for sh in _named(s, "단위")] == ["단위: 백만원"]
    chart = next(sh for sh in s.shapes if getattr(sh, "has_chart", False))
    xml = chart.chart._chartSpace.xml
    assert "백만원" not in xml  # 라벨·눈금·자료 서식 어디에도 단위가 되풀이되지 않는다
    assert chart.top >= _named(s, "단위")[0].top + _named(s, "단위")[0].height - 0.01 * EMU
    assert _layout(stats) == []
    prs, _ = _build(tmp_path, [{**slide, "unit": ""}], name="n.pptx")
    assert not _named(prs.slides[1], "단위")


def test_matrix_highlight_and_axis_ends(tmp_path):
    slide = {"kind": "diagram", "diagram_type": "matrix", "title": "비용 대비 효과가 큽니다", "key_message": KM, "axis_x": "도입 비용", "axis_y": "절감 효과",
             "nodes": [{"label": "이번 제안", "detail": "적은 투자로 큰 절감", "group": "왼쪽 위"}, {"label": "설비 교체", "detail": "", "group": "오른쪽 위"},
                       {"label": "수동 관리", "detail": "", "group": "왼쪽 아래"}, {"label": "외부 컨설팅", "detail": "", "group": "오른쪽 아래"},
                       {"label": "조명 교체", "detail": "효과 작음", "group": "오른쪽 아래"}]}
    prs, stats = _build(tmp_path, [slide])
    s = prs.slides[1]
    cells = _named(s, "칸")
    assert [str(c.fill.fore_color.rgb) for c in cells] == ["002FA7", "F6F7FB", "F6F7FB", "F6F7FB"]
    boxes = sorted(_named(s, "칸 글"), key=lambda b: (b.top, b.left))
    ours = boxes[0].text_frame.paragraphs
    assert ours[0].runs[0].text == "이번 제안" and str(ours[0].runs[0].font.color.rgb) == "FFFFFF" and ours[1].runs[0].text == "적은 투자로 큰 절감"
    assert ours[0].runs[0].font.size.pt > ours[1].runs[0].font.size.pt  # 이름이 설명보다 크다
    assert boxes[3].text_frame.text == "외부 컨설팅\n조명 교체\n효과 작음"  # 한 칸에 둘이면 글머리
    assert sorted(sh.text_frame.text for sh in _named(s, "축 끝")) == ["낮음", "낮음", "높음", "높음"]
    assert _layout(stats) == []


def test_half_cards_and_steps_use_larger_type_when_few(tmp_path):
    prs, stats = _build(tmp_path, [
        {"kind": "two_column", "title": "비교", "key_message": KM, "left_title": "전", "left": ["손으로 모읍니다"], "right_title": "후", "right": ["자동으로 모읍니다"]},
        {"kind": "steps", "title": "절차", "key_message": KM, "steps": [{"name": "진단", "detail": "설비 확인"}, {"name": "적용", "detail": "한 라인"}, {"name": "확대", "detail": "전 공장"}]},
    ])
    sizes = {r.font.size.pt for sh in _named(prs.slides[1], "목록") for p in sh.text_frame.paragraphs for r in p.runs}
    assert sizes == {18}
    step = _named(prs.slides[2], "단계 글")[0].text_frame.paragraphs
    assert step[0].runs[0].font.size.pt == 20 and step[1].runs[0].font.size.pt == 15
    assert _layout(stats) == []

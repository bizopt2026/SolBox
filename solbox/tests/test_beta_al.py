"""베타 AL (2026-11-26): 글자 크기는 역할별 고정 체계 안에서만. 사용자: "폰트 크기가 일관성이 없는게 가장 큼"."""

import random
from datetime import date

from pptx import Presentation
from pptx.util import Pt

from app import deck, deck_gates
from app.deck import build_deck
from tests.test_beta_af import _random_outline

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
KM = "현장 진단부터 바로 시작할 수 있습니다."
ALL_KINDS = [
    {"kind": "bullets", "title": "과제", "key_message": KM, "bullets": ["사용량 파악: 실시간으로 보지 못합니다", "피크 대응: 수동으로 조정합니다", "원가 부담: 요금이 그대로 반영됩니다"]},
    {"kind": "bullets", "title": "과제 많음", "key_message": KM, "bullets": [f"항목 {i}: 설명 {i}" for i in range(7)]},
    {"kind": "two_column", "title": "비교", "key_message": KM, "left_title": "전", "left": ["수집: 손으로"], "right_title": "후", "right": ["수집: 자동", "보고: 자동"]},
    {"kind": "table", "title": "표", "key_message": KM, "columns": ["구분", "구축형", "임대형"], "rows": [["초기 비용", "있음", "없음"], ["소유", "고객", "공급사"]]},
    {"kind": "steps", "title": "절차", "key_message": KM, "steps": [{"name": "진단", "detail": "설비 확인"}, {"name": "적용", "detail": "한 라인"}]},
    {"kind": "kpi", "title": "효과", "key_message": KM, "kpis": [{"value": "12", "unit": "%", "label": "절감", "note": "첫해"}, {"value": "1,200", "unit": "만원", "label": "연간", "note": ""}]},
    {"kind": "chart", "title": "추이", "key_message": KM, "chart_type": "column", "unit": "백만원", "categories": ["1월", "2월"], "series": [{"name": "전", "values": [4, 5]}]},
    *[{"kind": "diagram", "diagram_type": t, "title": t, "key_message": KM, "center": "플랫폼", "axis_x": "비용", "axis_y": "효과",
       "nodes": [{"label": f"항목 {i}", "detail": "설명 글", "group": g} for i, g in enumerate(["2주", "오른쪽 위", "", "왼쪽 아래"])]}
      for t in ("layers", "hub", "timeline", "matrix", "cycle")],
    {"kind": "diagram", "diagram_type": "before_after", "title": "전후", "key_message": KM, "left_title": "전", "left": ["손으로"], "right_title": "후", "right": ["자동"]},
    {"kind": "closing", "title": "마무리", "key_message": "함께 시작하겠습니다.", "bullets": ["일정 협의"]},
]


def _build(tmp_path, slides, name="d.pptx"):
    stats = {}
    outline = {"title": "시험 제안", "slides": [{"kind": "cover", "title": "시험 제안", "subtitle": "부제"}, *slides]}
    build_deck(outline, tmp_path / name, stats=stats, today=date(2026, 11, 26), customer_name="고객사", contact=["담당: 김대리"])
    return Presentation(tmp_path / name), stats


def _sizes(prs):
    return {int(r.get("sz")) / 100 for s in prs.slides for r in s._element.iter(f"{A}rPr") if r.get("sz")}


def test_scale_is_small_and_ordered():
    assert deck.TYPE_SCALE == (8, 10, 12, 14, 18, 26, 44, 56)
    for sizes in (deck.LIST_SIZES, deck.GRID_SIZES, deck.TABLE_SIZES, deck.STEP_NAME_SIZES, deck.STEP_DETAIL_SIZES, deck.COVER_SIZES, deck.KPI_SIZES, deck.HEAD_SIZES):
        assert set(sizes) <= set(deck.TYPE_SCALE) and len(sizes) <= 3 and list(sizes) == sorted(sizes, reverse=True)
    for pair in (*deck.BLOCK_SIZES, *deck.MATRIX_SIZES, *deck.DETAIL_SIZES, *deck.AXIS_SIZES):
        assert {x for x in pair if x} <= set(deck.TYPE_SCALE)


def test_every_slide_kind_stays_on_the_scale(tmp_path):
    prs, stats = _build(tmp_path, ALL_KINDS)
    assert _sizes(prs) <= set(deck.TYPE_SCALE)
    assert not [f for f in stats["quality"] if f["gate"] == "글자 크기 체계"]


def test_random_decks_stay_on_the_scale(tmp_path):
    rng = random.Random(20261126)
    for i in range(12):
        prs, stats = _build(tmp_path, _random_outline(rng)["slides"][1:], name=f"r{i}.pptx")
        assert _sizes(prs) <= set(deck.TYPE_SCALE), i
        assert not [f for f in stats["quality"] if f["gate"] == "글자 크기 체계"], i


def test_same_role_same_size_across_slides(tmp_path):
    """항목 수가 달라도 글머리 본문은 같은 크기다 (전에는 1개면 16, 5개면 14, 9개면 13 …)."""
    slides = [{"kind": "bullets", "title": f"글머리 {n}", "key_message": KM, "bullets": [f"짧은 항목 {i}" for i in range(n)]} for n in (1, 2, 4, 6, 8)]
    prs, _ = _build(tmp_path, slides)
    body = set()
    for s in list(prs.slides)[1:]:
        for sh in s.shapes:
            if sh.name in ("번호 카드 글", "목록"):
                body |= {r.font.size.pt for p in sh.text_frame.paragraphs for r in p.runs if r.text.startswith("짧은")}
    assert body == {deck.T_BODY}


def test_gate_flags_off_scale_size(tmp_path):
    prs, _ = _build(tmp_path, ALL_KINDS[:1])
    run = next(r for sh in prs.slides[1].shapes if sh.name == "번호 카드 글" for p in sh.text_frame.paragraphs for r in p.runs)
    run.font.size = Pt(15)
    found = [f for f in deck_gates.check_presentation(prs, kinds=["cover", "bullets"]) if f["gate"] == "글자 크기 체계"]
    assert found and found[0]["slide"] == 2 and "15pt" in found[0]["text"] and found[0]["level"] == "warn"

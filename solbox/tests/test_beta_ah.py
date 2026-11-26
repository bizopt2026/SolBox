"""베타 AH (2026-11-26): 제안 PPT 가독성 1차. 결론 띠 강조, `머리말: 설명` 글머리, 부제 없는 장의 본문 끌어올리기."""

from datetime import date
from pathlib import Path

import pytest
from pptx import Presentation

from app import deck
from app.deck import build_deck

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
EMU = 914400
ROOT = Path(__file__).resolve().parents[1]
KM = "현장 진단부터 바로 시작할 수 있습니다."
LAYOUT = ("슬라이드 밖", "본문 최대 y", "헤더 띠", "#25-b", "#28-b", "#28-c", "#13", "#28-W")


def _build(tmp_path, slides, name="d.pptx", **kw):
    stats = {}
    outline = {"title": "시험 제안", "slides": [{"kind": "cover", "title": "시험 제안"}, *slides]}
    build_deck(outline, tmp_path / name, stats=stats, today=date(2026, 11, 26), **kw)
    return Presentation(tmp_path / name), stats


def _shape(slide, name):
    return next(sh for sh in slide.shapes if sh.name == name)


def _layout(stats):
    return [f for f in stats["quality"] if f["gate"].startswith(LAYOUT)]


@pytest.mark.parametrize("text, expected", [
    ("실시간 계측: 설비별 사용량을 1분 단위로 봅니다", ("실시간 계측", "설비별 사용량을 1분 단위로 봅니다")),
    ("연락처：010-0000-0000", ("", "연락처：010-0000-0000")),  # 쌍점 뒤 빈칸이 없으면 나누지 않는다
    ("회의는 10:30 에 시작합니다", ("", "회의는 10:30 에 시작합니다")),
    ("2026: 시작", ("", "2026: 시작")),  # 머리말이 숫자로 끝나면 나누지 않는다
    ("머리말이 열여섯 자를 훌쩍 넘어가는 긴 줄: 설명", ("", "머리말이 열여섯 자를 훌쩍 넘어가는 긴 줄: 설명")),
    ("쌍점 없는 그냥 문장입니다", ("", "쌍점 없는 그냥 문장입니다")),
])
def test_split_head(text, expected):
    assert deck._split_head(text) == expected


def test_so_what_band_is_brand_with_white_bold_text(tmp_path):
    prs, stats = _build(tmp_path, [{"kind": "bullets", "title": "효과", "key_message": KM, "bullets": ["가", "나"]}])
    band = _shape(prs.slides[1], "결론 띠")
    assert str(band.fill.fore_color.rgb) == "002FA7"
    assert band.text_frame.text == "SO WHAT     " + KM
    label, _, message = band.text_frame.paragraphs[0].runs
    assert str(label.font.color.rgb) == "FFFFFF" and label.font.name == deck.FONT_B
    assert str(message.font.color.rgb) == "FFFFFF" and message.font.name == deck.FONT_B and message.font.size.pt == 14
    assert abs(band.top / EMU - 6.20) < 0.001 and abs(band.height / EMU - 0.50) < 0.001
    assert _layout(stats) == []


def test_grid_cards_with_heads(tmp_path):
    items = ["사용량 파악: 설비별 전력 사용량을 실시간으로 봅니다", "피크 대응: 가동 계획을 자동으로 조정합니다", "그냥 문장 한 줄입니다", "보고 업무: 보고서를 자동으로 만듭니다"]
    prs, stats = _build(tmp_path, [{"kind": "bullets", "title": "과제", "key_message": KM, "bullets": items}])
    boxes = [sh for sh in prs.slides[1].shapes if sh.name == "번호 카드 글"]
    assert [b.text_frame.text for b in boxes] == [
        "01  사용량 파악\n설비별 전력 사용량을 실시간으로 봅니다", "02  피크 대응\n가동 계획을 자동으로 조정합니다", "03\n그냥 문장 한 줄입니다", "04  보고 업무\n보고서를 자동으로 만듭니다"]
    num, head = boxes[0].text_frame.paragraphs[0].runs
    detail = boxes[0].text_frame.paragraphs[1].runs[0]
    assert num.font.name == head.font.name == deck.FONT_B and str(head.font.color.rgb) == "222222"
    assert detail.font.name == deck.FONT and str(detail.font.color.rgb) == "3A4654" and head.font.size.pt == detail.font.size.pt + 4
    assert detail.font.size.pt == 16  # 한 줄에 2장인 카드는 16pt 까지 쓴다
    assert _layout(stats) == []


def test_list_cards_keep_colon_and_bold_head(tmp_path):
    items = [f"항목 {i}: 설명 글 {i}" for i in range(8)] + ["머리말 없는 줄"]
    prs, stats = _build(tmp_path, [
        {"kind": "bullets", "title": "목록", "key_message": KM, "bullets": items},
        {"kind": "two_column", "title": "비교", "key_message": KM, "left_title": "전", "left": ["수집: 손으로 모읍니다"], "right_title": "후", "right": ["수집: 자동으로 모읍니다"]},
        {"kind": "closing", "title": "다음 단계", "bullets": ["일정 협의"]}], contact=["담당: 김대리"])
    text = "\n".join(sh.text_frame.text for s in list(prs.slides)[1:] for sh in s.shapes if sh.has_text_frame)
    for line in items + ["수집: 손으로 모읍니다", "수집: 자동으로 모읍니다", "담당: 김대리"]:
        assert line in text  # 쓴 글은 그대로 남는다
    runs = [p.runs for sh in prs.slides[2].shapes if sh.name == "목록" for p in sh.text_frame.paragraphs]
    assert all(r[0].text == "수집:" and r[0].font.name == deck.FONT_B and r[1].font.name == deck.FONT for r in runs)
    assert _layout(stats) == []


def test_body_rises_only_without_subtitle_and_with_one_line_title(tmp_path):
    long_title = "별관에서 먼저 검증하고 성과를 확인한 다음 본관으로 넓혀 가는 단계적 통합 제안"
    slides = [{"kind": kind, "title": title, "subtitle": sub, "key_message": KM, **extra}
              for title, sub in (("짧은 제목", ""), ("짧은 제목", "부제가 있습니다"), (long_title, ""))
              for kind, extra in (("bullets", {"bullets": ["가: 나", "다: 라"]}), ("table", {"columns": ["a", "b"], "rows": [["1", "2"]]}),
                                  ("kpi", {"kpis": [{"value": "1", "unit": "개", "label": "이름", "note": ""}]}))]
    prs, stats = _build(tmp_path, slides)
    for slide, data in zip(list(prs.slides)[1:], slides):
        expected = 1.75 if not data["subtitle"] and data["title"] != long_title else 2.20
        assert deck._body_top(data) == expected
        tops = [sh.top / EMU for sh in slide.shapes if sh.name not in ("배경", "챕터", "대제목", "부제", "결론 띠", "쪽 번호", "출처")]
        assert abs(min(tops) - expected) < 0.001, (data["kind"], data["title"], data["subtitle"], min(tops))
    assert _layout(stats) == []


def test_gate_still_catches_body_in_header_band(tmp_path):
    """본문 위 끝이 1.75 로 내려도, 부제가 있는 장의 본문이 2.20 위로 오면 여전히 오류다."""
    prs, _ = _build(tmp_path, [{"kind": "bullets", "title": "짧은 제목", "subtitle": "부제", "key_message": KM, "bullets": ["가", "나"]}])
    slide = prs.slides[1]
    card = next(sh for sh in slide.shapes if sh.name == "카드")
    card.top = int(1.80 * EMU)
    from app import deck_gates
    found = deck_gates.check_presentation(prs, kinds=["cover", "bullets"])
    assert any(f["gate"] == "헤더 띠 침범" and f["slide"] == 2 for f in found)


def test_outline_prompt_asks_for_head_form():
    prompt = (ROOT / "app" / "ai" / "prompts" / "proposal_outline.md").read_text(encoding="utf-8")
    assert "`머리말: 설명` 꼴" in prompt

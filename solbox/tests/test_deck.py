from datetime import date

import pytest
from pptx import Presentation
from pptx.util import Inches

from app.ai.proposal_tasks import Outline
from app.deck import build_deck, paginate
from app.proposals import clean_outline
from tests.conftest import sample_outline

FONT = "맑은 고딕"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def outline(n, tmp_path):
    return clean_outline(Outline.model_validate(sample_outline(n)), tmp_path)


def _frames(slide):
    for sh in slide.shapes:
        if sh.has_text_frame:
            yield sh.text_frame
        if getattr(sh, "has_table", False) and sh.has_table:
            for row in sh.table.rows:
                for cell in row.cells:
                    yield cell.text_frame


def texts(slide):
    return "\n".join(tf.text for tf in _frames(slide))


def notes(slide):
    return slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""


def tables(slide):
    return [sh.table for sh in slide.shapes if getattr(sh, "has_table", False) and sh.has_table]


def test_builds_all_six_kinds(tmp_path):
    out = tmp_path / "d" / "deck.pptx"
    n = build_deck(outline(8, tmp_path), out, customer_name="한빛병원", today=date(2026, 11, 18))
    prs = Presentation(out)
    assert n == len(prs.slides) == 8
    assert (prs.slide_width, prs.slide_height) == (Inches(13.333), Inches(7.5))
    assert "한빛병원" in texts(prs.slides[0]) and "2026. 11. 18." in texts(prs.slides[0])
    assert "전력비 절감 제안" in texts(prs.slides[0])
    assert "솔루션 구성" in texts(prs.slides[3]) and tables(prs.slides[3])
    assert "착수" in texts(prs.slides[4]) and "3개월" in texts(prs.slides[4])
    assert "감사합니다" in texts(prs.slides[7])


def test_sources_and_needs_check_only_in_notes(tmp_path):
    o = {"title": "제안", "slides": [{"kind": "cover", "title": "제안"},
         {"kind": "bullets", "title": "효과", "key_message": "절감", "bullets": ["전력비 절감"],
          "sources": ["solutions/1/card.md"], "needs_check": ["절감률 수치"]},
         {"kind": "bullets", "title": "노트 없음", "bullets": ["x"]}]}
    build_deck(o, tmp_path / "a.pptx"); prs = Presentation(tmp_path / "a.pptx")
    body, note = texts(prs.slides[1]), notes(prs.slides[1])
    assert "solutions/1/card.md" not in body and "절감률 수치" not in body
    assert "[출처]" in note and "solutions/1/card.md" in note and "[확인 항목]" in note and "절감률 수치" in note
    assert not prs.slides[2].has_notes_slide


def test_bullets_overflow_splits(tmp_path):
    o = {"title": "t", "slides": [{"kind": "cover", "title": "t"},
         {"kind": "bullets", "title": "목록", "key_message": "핵심", "bullets": [f"항목 {i}" for i in range(15)], "needs_check": ["확인"]}]}
    build_deck(o, tmp_path / "b.pptx"); prs = Presentation(tmp_path / "b.pptx")
    assert len(prs.slides) == 3
    assert "항목 9" in texts(prs.slides[1]) and "항목 10" in texts(prs.slides[2]) and "목록 (2)" in texts(prs.slides[2])
    assert "핵심" in texts(prs.slides[1]) and "핵심" not in texts(prs.slides[2])
    assert "확인" in notes(prs.slides[1]) and not prs.slides[2].has_notes_slide


def test_table_overflow_repeats_header(tmp_path):
    rows = [[f"솔루션{i}", f"역할{i}"] for i in range(20)]
    o = {"title": "t", "slides": [{"kind": "cover", "title": "t"},
         {"kind": "table", "title": "표", "columns": ["이름", "역할"], "rows": rows}]}
    build_deck(o, tmp_path / "t.pptx"); prs = Presentation(tmp_path / "t.pptx")
    assert len(prs.slides) == 4
    for i in (1, 2, 3):
        t = tables(prs.slides[i])[0]
        assert [c.text for c in t.rows[0].cells] == ["이름", "역할"]
    assert len(tables(prs.slides[1])[0].rows) == 9 and len(tables(prs.slides[3])[0].rows) == 5
    assert "표 (3)" in texts(prs.slides[3]) and "솔루션19" in texts(prs.slides[3])


def test_table_font_size_by_column_count(tmp_path):
    wide = {"kind": "table", "title": "넓은 표", "columns": [f"c{i}" for i in range(7)], "rows": [[str(i) for i in range(7)]]}
    narrow = {"kind": "table", "title": "좁은 표", "columns": ["a", "b"], "rows": [["1", "2"]]}
    build_deck({"title": "t", "slides": [wide, narrow]}, tmp_path / "w.pptx"); prs = Presentation(tmp_path / "w.pptx")
    assert tables(prs.slides[0])[0].cell(1, 0).text_frame.paragraphs[0].runs[0].font.size.pt == 11
    assert tables(prs.slides[1])[0].cell(1, 0).text_frame.paragraphs[0].runs[0].font.size.pt == 16  # 4열 이하, 6행 이하


def test_two_column_overflow(tmp_path):
    o = {"title": "t", "slides": [{"kind": "two_column", "title": "비교", "key_message": "m",
         "left_title": "현재", "left": [f"왼{i}" for i in range(12)], "right_title": "제안", "right": ["오1"]}]}
    build_deck(o, tmp_path / "c.pptx"); prs = Presentation(tmp_path / "c.pptx")
    assert len(prs.slides) == 2
    assert "왼7" in texts(prs.slides[0]) and "왼8" not in texts(prs.slides[0])
    assert "왼8" in texts(prs.slides[1]) and "왼11" in texts(prs.slides[1]) and "비교 (2)" in texts(prs.slides[1])
    assert "오1" in texts(prs.slides[0])


def test_very_long_text_does_not_fail(tmp_path):
    long = "전력비를 줄이는 방법" * 60
    o = {"title": "t", "slides": [{"kind": "bullets", "title": "긴 글", "bullets": [long, "짧음"]}]}
    build_deck(o, tmp_path / "l.pptx"); prs = Presentation(tmp_path / "l.pptx")
    assert long in texts(prs.slides[0])
    sizes = {r.font.size.pt for tf in _frames(prs.slides[0]) for p in tf.paragraphs for r in p.runs if r.text in (long, "짧음")}
    assert sizes == {16}  # 20pt 에서 두 단계(4pt) 줄인다


def test_bullet_font_sizes(tmp_path):
    def size_for(n):
        o = {"title": "t", "slides": [{"kind": "bullets", "title": "목록", "bullets": [f"b{i}" for i in range(n)]}]}
        build_deck(o, tmp_path / f"s{n}.pptx"); prs = Presentation(tmp_path / f"s{n}.pptx")
        return {r.font.size.pt for tf in _frames(prs.slides[0]) for p in tf.paragraphs for r in p.runs if r.text == "b0"}
    assert size_for(7) == {20} and size_for(8) == {16} and size_for(10) == {16}


def test_font_is_malgun_gothic(tmp_path):
    out = tmp_path / "f.pptx"
    build_deck(outline(8, tmp_path), out, customer_name="한빛병원")
    prs = Presentation(out)
    seen = 0
    for slide in prs.slides:
        for tf in _frames(slide):
            for p in tf.paragraphs:
                for r in p.runs:
                    seen += 1
                    assert r.font.name == FONT
                    ea = r._r.find(f"{A_NS}rPr/{A_NS}ea")
                    assert ea is not None and ea.get("typeface") == FONT
    assert seen > 20


def test_page_numbers_skip_cover(tmp_path):
    out = tmp_path / "p.pptx"
    build_deck(outline(8, tmp_path), out)
    prs = Presentation(out)
    assert not any(sh.has_text_frame and sh.text_frame.text.strip() == "1" for sh in prs.slides[0].shapes)
    for i in range(1, 8):
        nums = [sh.text_frame.text for sh in prs.slides[i].shapes if sh.has_text_frame and sh.text_frame.text.strip() == str(i + 1)]
        assert nums == [str(i + 1)]


def test_empty_outline_raises(tmp_path):
    with pytest.raises(ValueError):
        build_deck({"title": "t", "slides": []}, tmp_path / "x.pptx")
    with pytest.raises(ValueError):
        build_deck({"title": "t"}, tmp_path / "x.pptx")


def test_special_characters(tmp_path):
    title = "A & B <c> \"d\" 'e'\n둘째 줄 😀 \x00\x0b끝"
    o = {"title": "t", "slides": [{"kind": "cover", "title": title}, {"kind": "bullets", "title": title, "key_message": title, "bullets": [title, "줄\n바꿈"],
         "sources": [title], "needs_check": [title]},
         {"kind": "table", "title": title, "columns": [title], "rows": [[title]]},
         {"kind": "steps", "title": title, "steps": [{"name": title, "detail": title}]}]}
    build_deck(o, tmp_path / "s.pptx", customer_name=title)
    prs = Presentation(tmp_path / "s.pptx")
    assert len(prs.slides) == 4
    assert "A & B <c>" in texts(prs.slides[1]) and "😀" in texts(prs.slides[1])
    assert "\x00" not in texts(prs.slides[1]) and "둘째 줄 😀 끝" in texts(prs.slides[1])  # 제어문자는 빠지고 줄바꿈은 남는다


def test_degenerate_slides_fall_back(tmp_path):
    o = {"title": "t", "slides": [{"kind": "table", "title": "빈 표", "key_message": "메시지"},
         {"kind": "steps", "title": "빈 단계", "key_message": "메시지2"},
         {"kind": "mystery", "title": "모름"}, {"kind": "bullets"}]}
    n = build_deck(o, tmp_path / "e.pptx"); prs = Presentation(tmp_path / "e.pptx")
    assert n == len(prs.slides) == 4
    assert "빈 표" in texts(prs.slides[0]) and "메시지" in texts(prs.slides[0])
    assert "메시지2" in texts(prs.slides[1])


def test_paginate_never_empty():
    pages = paginate({"kind": "bullets"})
    assert len(pages) == 1 and pages[0]["kind"] == "bullets"
    assert len(paginate({"kind": "table", "title": "t", "columns": ["a"], "rows": [["1"]] * 17})) == 3
    assert paginate({"kind": "cover", "title": "t"})[0]["title"] == "t"


def test_open_in_default_dir_creates_parent(tmp_path):
    out = tmp_path / "a" / "b" / "deck.pptx"
    build_deck(outline(6, tmp_path), out)
    assert out.exists()


def _table_size(tmp_path, columns, rows):
    build_deck({"title": "t", "slides": [{"kind": "table", "title": "표", "columns": columns, "rows": rows}]}, tmp_path / "ts.pptx")
    prs = Presentation(tmp_path / "ts.pptx")
    cells = [c for row in tables(prs.slides[0])[0].rows for c in row.cells]
    return {r.font.size.pt for c in cells for p in c.text_frame.paragraphs for r in p.runs}


def test_table_font_16_small_14_larger_and_steps_down_when_tall(tmp_path):
    cols4 = ["a", "b", "c", "d"]
    assert _table_size(tmp_path, cols4, [["1"] * 4] * 6) == {16}
    assert _table_size(tmp_path, cols4, [["1"] * 4] * 7) == {14}
    assert _table_size(tmp_path, cols4 + ["e"], [["1"] * 5] * 2) == {14}
    assert _table_size(tmp_path, [f"c{i}" for i in range(7)], [["1"] * 7]) == {11}
    tall = [["가나다 " * 40, "x"]] * 5  # 긴 칸: 16pt 로는 본문 영역을 넘으므로 글자를 줄인다
    assert max(_table_size(tmp_path, ["a", "b"], tall)) < 16


def test_long_table_cells_paginate_before_overflowing(tmp_path):
    from app.deck import BODY_H, _table_row_heights
    rows = [["가나다 " * 60, "x"] for _ in range(8)]
    pages = paginate({"kind": "table", "title": "표", "columns": ["a", "b"], "rows": rows})
    assert len(pages) > 1 and sum(len(p["rows"]) for p in pages) == 8
    for p in pages:
        assert sum(_table_row_heights(p["columns"], p["rows"], 11)) <= BODY_H or len(p["rows"]) == 1


def _body_top(slide, needle):
    return min(sh.top for sh in slide.shapes if sh.has_text_frame and needle in sh.text_frame.text)


def test_body_top_lower_when_key_message_wraps(tmp_path):
    short = "짧은 핵심 메시지입니다"
    long = "이미 본관과 별관 설비를 24시간 상주 운영하고 있어 새 솔루션을 기존 운영 범위 안에서 바로 확장할 수 있습니다"
    slides = []
    for kind, extra in (("bullets", {"bullets": ["본문항목"]}), ("two_column", {"left_title": "L", "left": ["본문항목"], "right_title": "R", "right": ["x"]}),
                        ("table", {"columns": ["a", "b"], "rows": [["본문항목", "y"]]}), ("steps", {"steps": [{"name": "본문항목", "detail": "d"}]})):
        slides += [{"kind": kind, "title": "t", "key_message": short, **extra}, {"kind": kind, "title": "t", "key_message": long, **extra}]
    build_deck({"title": "t", "slides": slides}, tmp_path / "m.pptx")
    prs = Presentation(tmp_path / "m.pptx")
    for i in range(0, 8, 2):
        def top(slide):
            tops = [sh.top for sh in slide.shapes if (sh.has_text_frame and "본문항목" in sh.text_frame.text) or getattr(sh, "has_table", False) and sh.has_table
                    or (sh.shape_type == 1 and sh.top > Inches(2))]
            return min(tops)
        assert top(prs.slides[i + 1]) > top(prs.slides[i]) + Inches(0.2), slides[i]["kind"]
    assert min(sh.top for sh in prs.slides[0].shapes if sh.has_text_frame and "본문항목" in sh.text_frame.text) == Inches(2.2)


def test_two_column_boxes_have_equal_height(tmp_path):
    o = {"kind": "two_column", "title": "비교", "key_message": "m", "left_title": "L", "right_title": "R",
         "left": ["긴 항목입니다 " * 8] * 5, "right": ["짧음"]}
    build_deck({"title": "t", "slides": [o]}, tmp_path / "e.pptx")
    prs = Presentation(tmp_path / "e.pptx")
    boxes = [sh for sh in prs.slides[0].shapes if sh.shape_type == 1 and sh.width > Inches(5) and sh.height > Inches(1)]
    assert len(boxes) == 2 and boxes[0].height == boxes[1].height
    bodies = [sh for sh in prs.slides[0].shapes if sh.has_text_frame and ("긴 항목" in sh.text_frame.text or "짧음" in sh.text_frame.text)]
    assert len(bodies) == 2 and bodies[0].height == bodies[1].height


def test_table_stays_in_body_with_long_key_message(tmp_path):
    from app.deck import BODY_BOTTOM, BODY_H, _table_row_heights
    long = "이미 본관과 별관 설비를 24시간 상주 운영하고 있어 새 솔루션을 기존 운영 범위 안에서 바로 확장할 수 있습니다"
    rows, n = [], 0
    while True:  # 핵심 메시지 없이는 11pt 로 꼭 들어가는 가장 큰 표
        cand = rows + [["가나다 " * 12, "x"]]
        if sum(_table_row_heights(["a", "b"], cand, 11)) > BODY_H or len(cand) > 8:
            break
        rows = cand
    assert rows
    for message in ("", long):
        build_deck({"title": "t", "slides": [{"kind": "table", "title": "표", "key_message": message, "columns": ["a", "b"], "rows": rows}]}, tmp_path / "k.pptx")
        prs = Presentation(tmp_path / "k.pptx")
        for slide in prs.slides:
            for t in [sh for sh in slide.shapes if getattr(sh, "has_table", False) and sh.has_table]:
                assert (t.top + sum(r.height for r in t.table.rows)) <= Inches(BODY_BOTTOM) + 10

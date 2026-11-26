"""베타 AG: 시각 자료가 먼저인 제안서 (핵심 수치·그래프·도식 장), 마침표 없는 제목, 로고 없는 기본 바탕.

수치는 구성안에 적힌 값만 그린다. 이 시험의 수치는 그리는 코드를 누르기 위한 채움 값이고 앱 코드에는 예시 수치가 없다."""

import random
import re
from datetime import date
from pathlib import Path

import pytest
from pptx import Presentation

from app import checks, deck, deck_gates, ontology
from app.ai.proposal_tasks import AISlide, Outline, Slide
from app.deck import build_deck, paginate
from app.proposals import clean_outline, slide_from_form, slide_to_form
from tests.conftest import outlined_proposal

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
EMU = 914400
ROOT = Path(__file__).resolve().parents[1]
KM = "별관에서 먼저 확인합니다."


def _build(tmp_path, slides, name="d.pptx", **kw):
    stats = {}
    kw.setdefault("today", date(2026, 11, 24))
    outline = {"title": "시험 제안", "slides": [{"kind": "cover", "title": "시험 제안"}, *slides]}
    n = build_deck(outline, tmp_path / name, stats=stats, **kw)
    return Presentation(tmp_path / name), stats, n


def _texts(slide):
    return "\n".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame)


def _charts(slide):
    return [sh for sh in slide.shapes if getattr(sh, "has_chart", False)]


def _nodes(n, detail="설명 글", group=""):
    return [{"label": f"항목 {i + 1}", "detail": detail, "group": group} for i in range(n)]


def _kpi(n=3):
    return {"kind": "kpi", "title": "수치", "key_message": KM, "kpis": [{"value": str(10 * (i + 1)), "unit": "분", "label": f"이름 {i + 1}", "note": "설명"} for i in range(n)]}


def _chart(ctype="column", cats=3, series=1, **kw):
    return {"kind": "chart", "title": "그래프", "key_message": KM, "chart_type": ctype, "unit": "주", "categories": [f"항목 {i + 1}" for i in range(cats)],
            "series": [{"name": f"계열 {k + 1}", "values": [i + k + 1 for i in range(cats)]} for k in range(series)], **kw}


def _diagram(dtype, n=4, **kw):
    return {"kind": "diagram", "title": "도식", "key_message": KM, "diagram_type": dtype, "nodes": _nodes(n), **kw}


def _layout(findings):
    names = ("슬라이드 밖", "본문 최대 y", "헤더 띠", "#25-b", "#28-b", "#28-c", "#0b", "#21", "#9", "#15", "#5", "#14", "#8", "최소 글자", "면 색", "차트")
    return [f for f in findings if f["gate"].startswith(names)]


# ---------------------------------------------------------------- 종류·모델

def test_new_kinds_are_registered():
    assert {"kpi": "핵심 수치", "chart": "그래프", "diagram": "도식"}.items() <= ontology.SLIDE_KINDS.items()
    assert list(ontology.SLIDE_KINDS)[:6] == ["cover", "bullets", "two_column", "table", "steps", "kpi"] and list(ontology.SLIDE_KINDS)[-1] == "closing"
    assert set(ontology.DIAGRAM_TYPES) == {"layers", "hub", "before_after", "timeline", "matrix", "cycle"} == set(ontology.DIAGRAM_TYPE_DESCRIPTIONS)
    assert set(ontology.CHART_TYPES) == {"column", "bar", "line", "doughnut"}
    assert ontology.CODE_LISTS["chart_type"].items is ontology.CHART_TYPES and ontology.CODE_LISTS["diagram_type"].items is ontology.DIAGRAM_TYPES
    assert set(ontology.VISUAL_SLIDE_KINDS) == {"two_column", "table", "steps", "kpi", "chart", "diagram"}
    for field in ("kpis", "chart_data", "nodes", "note", "center", "axis_x", "axis_y", "unit", "chart_type", "diagram_type"):
        assert field in ontology.SLIDE_FORM_LABELS


def test_models_accept_new_fields_and_old_outlines():
    s = AISlide(kind="chart", chart_type="bar", categories=["가"], series=[{"name": "기간", "values": [2, "3", None]}], unit="주")
    assert s.series[0].values == [2, "3", None]  # 숫자가 아닌 값은 모델이 아니라 구성안 정리가 가른다
    k = AISlide(kind="kpi", kpis=[{"value": "10", "unit": "분", "label": "주기"}])
    assert k.kpis[0].note == ""
    d = AISlide(kind="diagram", diagram_type="hub", center="가운데", nodes=[{"label": "가"}])
    assert d.nodes[0].group == "" and d.axis_x == ""
    old = Slide(kind="bullets", title="옛 장", bullets=["가"]).model_dump()  # 옛 구성안: 새 칸은 빈 값
    assert old["kpis"] == [] and old["nodes"] == [] and old["series"] == [] and old["note"] == "" and old["checked"] == []


# ---------------------------------------------------------------- 구성안 정리

def _clean(tmp_path, *slides):
    (tmp_path / "solutions" / "1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "solutions" / "1" / "card.md").write_text("x", encoding="utf-8")
    return clean_outline(Outline(title="제목", slides=[AISlide(kind="cover", title="제목."), *[AISlide(**s) for s in slides]]), tmp_path)["slides"]


def test_clean_outline_keeps_valid_visuals_and_strips_title_period(tmp_path):
    src = ["solutions/1/card.md"]
    cover, kpi, chart, dia, ba = _clean(
        tmp_path,
        {"kind": "kpi", "title": "주기가 짧습니다.", "kpis": [{"value": " 10 ", "unit": "분", "label": "주기"}, {"value": "", "label": "값 없음"}], "note": " 기준 ", "sources": src},
        {"kind": "chart", "title": "기간。", "chart_type": "BAR", "categories": ["조사", "연동"], "series": [{"name": "기간", "values": ["2", 2.0]}], "unit": "주", "sources": src},
        {"kind": "diagram", "title": "구성", "diagram_type": "matrix", "axis_x": "가로", "nodes": [{"label": "가", "group": "우상"}, {"label": "", "detail": "이름 없음"}, {"label": "나", "group": "모름"}], "sources": src},
        {"kind": "diagram", "title": "전후", "diagram_type": "before_after", "left": ["전"], "right": ["후"], "nodes": [{"label": "쓰지 않음"}], "sources": src},
    )
    assert cover["title"] == "제목" and kpi["title"] == "주기가 짧습니다" and chart["title"] == "기간"  # 제목 끝 마침표는 뗀다
    assert kpi["kind"] == "kpi" and kpi["kpis"] == [{"value": "10", "unit": "분", "label": "주기", "note": ""}] and kpi["note"] == "기준" and kpi["needs_check"] == []
    assert chart["kind"] == "chart" and chart["chart_type"] == "bar" and chart["series"] == [{"name": "기간", "values": [2, 2]}] and chart["unit"] == "주"
    assert all(isinstance(v, int) for v in chart["series"][0]["values"])
    assert dia["nodes"] == [{"label": "가", "detail": "", "group": "오른쪽 위"}, {"label": "나", "detail": "", "group": ""}] and dia["axis_x"] == "가로"
    assert ba["diagram_type"] == "before_after" and ba["nodes"] == [] and ba["left"] == ["전"]
    assert kpi["diagram_type"] == "" and dia["chart_type"] == "" and dia["unit"] == ""


def test_clean_outline_never_draws_bad_numbers(tmp_path):
    """숫자가 아닌 값·개수가 안 맞는 그래프는 표로 바꾸고 알린다. 출처 없는 수치 장은 늘 근거 문제로 알린다."""
    src = ["solutions/1/card.md"]
    _, bad, short, nosrc, empty, emptykpi = _clean(
        tmp_path,
        {"kind": "chart", "title": "가", "categories": ["조사", "연동"], "series": [{"name": "기간", "values": ["2주", "약 2"]}], "sources": src},
        {"kind": "chart", "title": "나", "categories": ["조사", "연동"], "series": [{"name": "기간", "values": [2]}], "sources": src},
        {"kind": "kpi", "title": "다", "kpis": [{"value": "10", "unit": "분", "label": "주기"}]},
        {"kind": "chart", "title": "라", "sources": src},
        {"kind": "kpi", "title": "마", "bullets": ["글머리로 남는다"], "sources": src},
    )
    assert bad["kind"] == "table" and bad["columns"] == ["항목", "기간"] and bad["rows"] == [["조사", "2주"], ["연동", "약 2"]]
    assert checks.CHART_TO_TABLE_NOTE in bad["needs_check"] and bad["series"] == [] and bad["categories"] == []
    assert short["kind"] == "table" and short["rows"] == [["조사", "2"], ["연동", ""]]
    assert nosrc["kind"] == "kpi" and checks.NO_FIGURE_SOURCE_NOTE in nosrc["needs_check"] and checks.check_kind(checks.NO_FIGURE_SOURCE_NOTE) == "evidence"
    assert empty["kind"] == "bullets" and emptykpi["kind"] == "bullets" and emptykpi["bullets"] == ["글머리로 남는다"]


def test_clean_outline_limits_and_fallbacks(tmp_path):
    src = ["solutions/1/card.md"]
    _, many, unknown, nonodes, manykpi = _clean(
        tmp_path,
        {"kind": "diagram", "title": "가", "diagram_type": "hub", "nodes": [{"label": f"n{i}"} for i in range(8)], "sources": src},
        {"kind": "diagram", "title": "나", "diagram_type": "pyramid", "nodes": [{"label": "가", "detail": "설명"}], "sources": src},
        {"kind": "diagram", "title": "다", "diagram_type": "cycle", "sources": src},
        {"kind": "kpi", "title": "라", "kpis": [{"value": str(i), "label": f"k{i}"} for i in range(6)], "sources": src},
    )
    assert len(many["nodes"]) == 6 and any("n6, n7" in t for t in many["needs_check"])
    assert unknown["kind"] == "diagram" and unknown["diagram_type"] == "layers"
    assert nonodes["kind"] == "bullets" and nonodes["diagram_type"] == ""
    assert len(manykpi["kpis"]) == 4 and any("k4, k5" in t for t in manykpi["needs_check"])


def test_author_notes_move_out_of_new_fields(tmp_path):
    """작성자에게 하는 말은 새 칸에서도 글머리와 같은 규칙으로 확인할 점으로 옮긴다."""
    src = ["solutions/1/card.md"]
    _, kpi, dia, chart, gone = _clean(
        tmp_path,
        {"kind": "kpi", "title": "가", "kpis": [{"value": "XX%", "label": "절감률"}, {"value": "10", "unit": "분", "label": "주기", "note": "수치 확인 필요"}], "note": "금액 미정", "sources": src},
        {"kind": "diagram", "title": "나", "diagram_type": "hub", "center": "확인 필요", "nodes": [{"label": "연동", "detail": "담당자 확인 필요"}, {"label": "TODO: 항목 추가"}], "sources": src},
        {"kind": "chart", "title": "다", "categories": ["조사", "확인 필요"], "series": [{"name": "기간", "values": [2, 9]}], "note": "실제 값으로 교체", "sources": src},
        {"kind": "kpi", "title": "라", "kpis": [{"value": "○○", "label": "건수"}], "sources": src},
    )
    assert kpi["kpis"] == [{"value": "10", "unit": "분", "label": "주기", "note": ""}] and kpi["note"] == ""
    assert {"XX% 절감률", "수치 확인 필요", "금액 미정"} <= set(kpi["needs_check"])
    assert dia["nodes"] == [{"label": "연동", "detail": "", "group": ""}] and dia["center"] == ""
    assert {"담당자 확인 필요", "TODO: 항목 추가", "확인 필요"} <= set(dia["needs_check"])
    assert chart["categories"] == ["조사"] and chart["series"] == [{"name": "기간", "values": [2]}] and chart["note"] == ""  # 항목과 그 자리의 값을 함께 뺀다
    assert gone["kind"] == "bullets" and gone["kpis"] == [] and "○○ 건수" in gone["needs_check"]  # 그릴 것이 남지 않으면 빈 수치 장을 만들지 않는다


# ---------------------------------------------------------------- 편집 폼

FORMS = [
    ("kpi", {"title": "수치", "key_message": "m", "kpis": "10 | 분 | 측정 주기 | 무선 측정\n약 3 | 년 | 배터리\n50", "note": "기준", "sources": "a.md"}),
    ("chart", {"title": "그래프", "chart_type": "bar", "unit": "주", "chart_data": "항목 | 도입 전 | 도입 후\n조사 | 2 | 1.5\n연동 | 1,200 | -3", "note": "기준"}),
    ("diagram", {"title": "층", "diagram_type": "layers", "nodes": "플랫폼 | 수집, 분석, 알림\n사용자", "note": "주"}),
    ("diagram", {"title": "중심", "diagram_type": "hub", "center": "솔루션", "nodes": "기능 | 설명"}),
    ("diagram", {"title": "일정", "diagram_type": "timeline", "nodes": "조사 | 현장 확인 | 2주\n연동 |  | 2주"}),
    ("diagram", {"title": "위치", "diagram_type": "matrix", "axis_x": "가로", "axis_y": "세로", "nodes": "우리 | 설명 | 오른쪽 위\n다른 것 |  | 좌하\n자리 없음"}),
    ("diagram", {"title": "순환", "diagram_type": "cycle", "center": "운영", "nodes": "측정\n알림\n점검"}),
    ("diagram", {"title": "전후", "diagram_type": "before_after", "left_title": "지금", "left": "가\n나", "right_title": "도입 뒤", "right": "다"}),
]


@pytest.mark.parametrize("kind,form", FORMS)
def test_form_round_trip_for_new_kinds(kind, form):
    slide = slide_from_form(kind, form)
    again = slide_from_form(kind, {**slide_to_form(slide), "sources": "\n".join(slide["sources"])})
    assert again == slide
    assert slide_to_form(again) == slide_to_form(slide)


def test_form_parses_line_formats():
    kpi = slide_from_form("kpi", FORMS[0][1])
    assert kpi["kpis"] == [{"value": "10", "unit": "분", "label": "측정 주기", "note": "무선 측정"}, {"value": "약 3", "unit": "년", "label": "배터리", "note": ""},
                           {"value": "50", "unit": "", "label": "", "note": ""}] and kpi["note"] == "기준"
    chart = slide_from_form("chart", FORMS[1][1])
    assert chart["categories"] == ["조사", "연동"] and chart["series"] == [{"name": "도입 전", "values": [2, 1200]}, {"name": "도입 후", "values": [1.5, -3]}]
    assert slide_to_form(chart)["chart_data"] == "항목 | 도입 전 | 도입 후\n조사 | 2 | 1.5\n연동 | 1200 | -3"
    matrix = slide_from_form("diagram", FORMS[5][1])
    assert [n["group"] for n in matrix["nodes"]] == ["오른쪽 위", "왼쪽 아래", ""] and matrix["axis_y"] == "세로"
    timeline = slide_from_form("diagram", FORMS[4][1])
    assert timeline["nodes"][1] == {"label": "연동", "detail": "", "group": "2주"} and slide_to_form(timeline)["nodes"] == "조사 | 현장 확인 | 2주\n연동 |  | 2주"
    ba = slide_from_form("diagram", FORMS[7][1])
    assert ba["left"] == ["가", "나"] and ba["right_title"] == "도입 뒤" and ba["nodes"] == []
    # 그 종류의 칸만 읽는다
    plain = slide_from_form("bullets", {**FORMS[0][1], **FORMS[1][1], **FORMS[5][1], "bullets": "가"})
    assert plain["kpis"] == [] and plain["series"] == [] and plain["nodes"] == [] and plain["note"] == "" and plain["axis_x"] == ""
    assert slide_from_form("diagram", {"diagram_type": "layers", "center": "x", "axis_x": "y", "left": "z"})["center"] == ""
    assert slide_to_form({"kind": "bullets", "title": "옛 장", "bullets": ["가"]})["kpis"] == ""  # 옛 구성안에는 새 키가 없다


@pytest.mark.parametrize("kind,form,message", [
    ("kpi", {"kpis": " | 분 | 이름"}, "맨 앞 칸에 값"),
    ("kpi", {"kpis": "1 | 2 | 3 | 4 | 5"}, "네 개까지"),
    ("kpi", {"kpis": "1\n2\n3\n4\n5"}, "4개까지"),
    ("chart", {"chart_data": "조사 | 2\n연동 | 3"}, "이름 줄"),
    ("chart", {"chart_data": "항목"}, "계열 이름"),
    ("chart", {"chart_data": "항목 | 기간\n조사 | 2주"}, "숫자가 아닌 값"),
    ("chart", {"chart_data": "항목 | 기간\n조사 | 2 | 3"}, "값이 1개여야"),
    ("chart", {"chart_data": "항목 | 기간\n | 2"}, "항목 이름"),
    ("chart", {"chart_data": "항목 | 기간"}, "그래프 값이 없습니다"),
    ("chart", {"chart_data": "항목 | a | b | c | d | e\n조사 | 1 | 2 | 3 | 4 | 5"}, "4개까지"),
    ("chart", {"chart_type": "pie", "chart_data": "항목 | 기간\n조사 | 2"}, "그래프 종류"),
    ("diagram", {"diagram_type": "pyramid", "nodes": "가"}, "도식 종류"),
    ("diagram", {"diagram_type": "hub", "nodes": "\n".join(f"n{i}" for i in range(7))}, "6개까지"),
    ("diagram", {"diagram_type": "layers", "nodes": " | 설명"}, "이름을 쓰세요"),
    ("diagram", {"diagram_type": "layers", "nodes": "a | b | c | d"}, "세 개까지"),
    ("diagram", {"diagram_type": "matrix", "nodes": "가 | 설명 | 가운데"}, "왼쪽 위"),
])
def test_form_errors(kind, form, message):
    with pytest.raises(ValueError, match=message):
        slide_from_form(kind, form)


def test_outline_editor_saves_new_kinds(user_client, db, settings):
    p = outlined_proposal(db, settings)
    url = f"/proposals/{p.id}/outline/slides/1"
    for kind, form in FORMS:
        assert user_client.post(url, data={"kind": kind, **form}).status_code == 200, (kind, form)
        db.expire_all()
        saved = db.get(type(p), p.id).outline["slides"][1]
        assert saved == {**slide_from_form(kind, form), "checked": []}
    page = user_client.get(f"/proposals/{p.id}/outline?open=1").text
    for needle in ('name="kpis"', 'name="chart_data"', 'name="chart_type"', 'name="diagram_type"', 'name="nodes"', 'name="center"', 'name="axis_x"', 'name="note"',
                   'data-kinds="kpi"', 'data-kinds="two_column diagram" data-dtypes="before_after"', "값 | 단위 | 이름 | 설명", "이름 | 설명 | 묶음", "도입 전후 도식", "<h3>지금</h3>"):
        assert needle in page, needle
    for name in ontology.DIAGRAM_TYPES.values():
        assert name in page
    r = user_client.post(url, data={"kind": "chart", "title": "x", "chart_data": "항목 | 기간\n조사 | 두 주"})
    assert r.status_code == 400 and "숫자가 아닌 값" in r.text
    assert user_client.post(url, data={"kind": "kpi", "kpis": " | 분"}).status_code == 400
    assert user_client.post(url, data={"kind": "diagram", "diagram_type": "matrix", "nodes": "가 |  | 한가운데"}).status_code == 400
    db.expire_all()
    assert db.get(type(p), p.id).outline["slides"][1]["kind"] == "diagram"  # 오류가 난 저장은 아무것도 바꾸지 않는다
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "data-dtypes" in js and ".dtype-select" in js


# ---------------------------------------------------------------- 그리기

def test_titles_never_end_with_a_period(tmp_path):
    outline = {"title": "t", "slides": [{"kind": "cover", "title": "표지 제목입니다."}, {"kind": "bullets", "title": "전력비를 줄입니다.", "key_message": KM, "bullets": ["가"]},
                                        _diagram("layers") | {"title": "구성은 네 층입니다。"}, {"kind": "steps", "title": "끝이 물음표인가?", "key_message": KM, "steps": [{"name": "가", "detail": ""}]},
                                        {"kind": "closing", "title": "감사합니다."}]}
    stats = {}
    build_deck(outline, tmp_path / "d.pptx", stats=stats, today=date(2026, 11, 24))
    prs = Presentation(tmp_path / "d.pptx")
    titles = [next(sh.text_frame.paragraphs[0].text for sh in s.shapes if sh.name in ("대제목", "표지 제목")) for s in prs.slides]
    assert titles == ["표지 제목입니다", "전력비를 줄입니다", "구성은 네 층입니다", "끝이 물음표인가?", "감사합니다"]
    assert not [f for f in stats["quality"] if f["gate"] == "#11 대제목 마침표"]
    assert deck._headline_text("가. ") == "가" and deck._headline_text("v1.2") == "v1.2" and deck._headline_text("...") == ""
    # 손으로 마침표를 넣은 파일은 검사가 알린다 (경고)
    head = next(sh for sh in prs.slides[1].shapes if sh.name == "대제목")
    head.text_frame.paragraphs[0].runs[0].text = "전력비를 줄입니다."
    found = [f for f in deck_gates.check_presentation(prs) if f["gate"] == "#11 대제목 마침표"]
    assert [(f["slide"], f["level"]) for f in found] == [(2, "warn")]


def test_no_background_image_by_default(tmp_path, settings):
    assert settings.deck_background is None
    prs, stats, _ = _build(tmp_path, [_kpi(), _chart(), _diagram("hub")])
    for slide in prs.slides:
        assert not [sh for sh in slide.shapes if sh.shape_type == 13 or sh.name == "배경"]  # 그림 없음 (13 = PICTURE)
        assert slide.background.fill.fore_color.rgb == deck.PAGE_BG
    assert not [f for f in stats["quality"] if f["gate"].startswith("#6 ")]
    example = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert not re.search(r"^deck_background", example, re.M)


def test_kpi_slide(tmp_path):
    prs, stats, n = _build(tmp_path, [_kpi(3)])
    slide = prs.slides[1]
    boxes = [sh for sh in slide.shapes if sh.name == "핵심 수치"]
    assert len(boxes) == 3 and len([sh for sh in slide.shapes if sh.name == "카드"]) == 3 and len([sh for sh in slide.shapes if sh.name == "강조 막대"]) == 3
    first = boxes[0].text_frame.paragraphs[0].runs
    assert first[0].text == "10" and first[0].font.size.pt >= 40 and str(first[0].font.color.rgb) == "001C64" and first[0].font.name == "나눔스퀘어_ac Bold"
    assert first[1].text.strip() == "분" and first[1].font.size.pt < first[0].font.size.pt
    assert "이름 1" in _texts(slide) and "설명" in _texts(slide) and any(sh.name == "결론 띠" for sh in slide.shapes)
    assert _layout(stats["quality"]) == []
    assert [len(p["kpis"]) for p in paginate(_kpi(6))] == [4, 2]


@pytest.mark.parametrize("ctype,cats,series", [("column", 3, 1), ("column", 5, 3), ("bar", 2, 1), ("bar", 5, 2), ("line", 6, 2), ("doughnut", 2, 1), ("doughnut", 4, 1)])
def test_native_chart_with_data_labels(tmp_path, ctype, cats, series):
    prs, stats, _ = _build(tmp_path, [_chart(ctype, cats, series, note="기준: 시험 값")])
    slide = prs.slides[1]
    (frame,) = _charts(slide)  # 그림이 아니라 파워포인트에서 고칠 수 있는 진짜 그래프
    chart = frame.chart
    root = chart._chartSpace
    tag = {"column": "barChart", "bar": "barChart", "line": "lineChart", "doughnut": "doughnutChart"}[ctype]
    assert root.find(f".//{C}{tag}") is not None and frame.name == "그래프"
    assert len(chart.plots[0].series) == series and len(list(chart.plots[0].categories)) == cats
    values = sorted(v for s in chart.plots[0].series for v in s.values)
    assert values == sorted(float(i + k + 1) for k in range(series) for i in range(cats))  # 구성안의 값 그대로
    labels = root.find(f".//{C}dLbls")
    assert labels is not None and labels.find(f"{C}showPercent" if ctype == "doughnut" else f"{C}showVal").get("val") == "1"
    assert chart.has_title is False and chart.has_legend is (series > 1)
    faces = {f.get("typeface") for t in ("latin", "ea", "cs") for f in root.iter(f"{A}{t}")}
    assert faces and faces <= {"나눔스퀘어_ac", "나눔스퀘어_ac Bold"}
    assert not [r for r in root.iter(f"{A}defRPr") if r.get("b") == "1"]
    colors = {c.get("val") for c in root.iter(f"{A}srgbClr")}
    assert colors <= deck_gates.PALETTE and "FF7A00" not in colors
    if ctype != "doughnut":
        assert root.find(f".//{C}catAx/{C}majorGridlines") is None and root.find(f".//{C}valAx/{C}majorGridlines") is not None
        assert {t.get("val") for t in root.iter(f"{C}majorTickMark")} == {"none"}
        assert root.find(f".//{C}valAx/{C}numFmt").get("formatCode") == "#,##0"  # 베타 AK: 단위는 눈금·라벨에 되풀이하지 않고
        assert "단위: 주" in _texts(slide)  # 그래프 위 한 줄에 한 번만
    else:
        assert root.find(f".//{C}holeSize").get("val") == "56"
    assert root.find(f"{C}roundedCorners").get("val") == "0"
    x0, y0, x1, y1 = frame.left / EMU, frame.top / EMU, (frame.left + frame.width) / EMU, (frame.top + frame.height) / EMU
    assert x0 >= deck.MARGIN - 0.01 and x1 <= deck.RIGHT + 0.01 and y0 >= deck.BODY_Y_TIGHT - 0.01 and y1 <= deck.BODY_BOTTOM + 0.01
    assert "기준: 시험 값" in _texts(slide)
    if series == 1 and cats <= 6:  # 값 요약 카드: 그래프에 든 값 그대로
        shown = [sh.text_frame.text for sh in slide.shapes if sh.name == "값"]
        assert [t.split()[0] for t in shown] == [str(i + 1) for i in range(cats)]
    assert _layout(stats["quality"]) == [], stats["quality"]


def test_chart_degrades_instead_of_overflowing(tmp_path):
    big = _chart("doughnut", 7, 1)
    pages = paginate(big)
    assert [p["chart_type"] for p in pages] == ["bar"] and len(pages[0]["categories"]) == 7  # 도넛 5조각 이상은 가로 막대로
    assert [len(p["categories"]) for p in paginate(_chart("column", 23, 1))] == [10, 10, 3]
    pages = paginate(_chart("bar", 9, 2))
    assert [len(p["categories"]) for p in pages] == [8, 1] and [len(s["values"]) for s in pages[1]["series"]] == [1, 1]
    assert pages[1]["title"] == "그래프 (2)" and pages[1]["key_message"] == ""
    assert deck._chart_plan(_chart("doughnut", 3, 2))[0] == "bar"
    assert deck._chart_plan({**_chart(), "series": [{"name": "x", "values": [1, "둘", 3]}]}) is None  # 숫자가 아닌 값: 짐작해 그리지 않는다
    assert deck._chart_plan({**_chart(), "series": [{"name": "x", "values": [1, 2]}]}) is None
    prs, stats, n = _build(tmp_path, [{**_chart(), "series": [{"name": "x", "values": [1, "둘", 3]}]}, _chart("column", 23, 1), {"kind": "chart", "title": "빈 그래프", "key_message": KM}])
    assert n == 6 and not _charts(prs.slides[1]) and len([s for s in prs.slides if _charts(s)]) == 3
    assert _layout(stats["quality"]) == []


DIAGRAM_SHAPES = {"layers": "층 이름", "hub": "중심 원", "timeline": "일정 막대", "matrix": "칸", "cycle": "순환 화살표"}


@pytest.mark.parametrize("dtype", list(DIAGRAM_SHAPES))
def test_diagram_types_draw_their_shapes(tmp_path, dtype):
    n = 4
    data = _diagram(dtype, n, center="가운데 이름", axis_x="가로축", axis_y="세로축", note="덧붙이는 말")
    if dtype == "timeline":
        for i, node in enumerate(data["nodes"]):
            node["group"] = f"{i + 1}주"
    prs, stats, _ = _build(tmp_path, [data])
    slide = prs.slides[1]
    names = [sh.name for sh in slide.shapes]
    text = _texts(slide)
    assert names.count(DIAGRAM_SHAPES[dtype]) == {"layers": n, "hub": 1, "timeline": n, "matrix": 4, "cycle": n}[dtype]
    for i in range(n):
        assert f"항목 {i + 1}" in text
    assert "설명 글" in text and "덧붙이는 말" in text and "결론 띠" in names and "대제목" in names
    if dtype in ("hub", "cycle"):
        assert "가운데 이름" in text
    if dtype == "matrix":
        assert "가로축" in text and "세로축" in text and names.count("가로축") == 1
    if dtype == "timeline":
        assert [sh.text_frame.text for sh in slide.shapes if sh.name == "기간"] == ["1주", "2주", "3주", "4주"]
        bars = [sh for sh in slide.shapes if sh.name == "일정 막대"]
        assert [round(b.width / EMU, 1) for b in bars] == sorted(round(b.width / EMU, 1) for b in bars) and bars[0].width < bars[-1].width  # 기간 비율대로
        assert [b.top for b in bars] == sorted(b.top for b in bars) and [b.left for b in bars] == sorted(b.left for b in bars)
    if dtype == "layers":
        assert names.count("흐름 화살표") == n - 1
    if dtype == "hub":
        assert names.count("연결선") == n and names.count("도식 상자") == n
    assert _layout(stats["quality"]) == [], stats["quality"]


def test_layers_chips_and_before_after(tmp_path):
    layers = _diagram("layers", 2)
    layers["nodes"][0]["detail"] = "수집, 분석, 알림"
    ba = {"kind": "diagram", "title": "도입 전후", "key_message": KM, "diagram_type": "before_after", "left": ["수동 점검", "고장 뒤 대응"], "right": ["자동 감시"], "right_title": "도입 뒤"}
    prs, stats, _ = _build(tmp_path, [layers, ba])
    chips = [sh.text_frame.text for sh in prs.slides[1].shapes if sh.name == "칩"]
    assert chips == ["수집", "분석", "알림"] and "설명 글" in _texts(prs.slides[1])
    names = [sh.name for sh in prs.slides[2].shapes]
    assert names.count("전후 화살표") == 1 and names.count("카드") == 2 and names.count("머리 표시") == 2
    text = _texts(prs.slides[2])
    assert "도입 전" in text and "도입 뒤" in text and "수동 점검" in text and "자동 감시" in text  # 왼쪽 제목이 비면 "도입 전"
    assert _layout(stats["quality"]) == []
    long = {**ba, "left": ["긴 줄 " * 30] * 9, "right": ["가"]}
    pages = paginate(long)
    assert len(pages) >= 2 and sum(len(p["left"]) for p in pages) == 9 and pages[1]["right"] == []
    assert [len(p["nodes"]) for p in paginate(_diagram("layers", 12))] == [5, 5, 2] and [len(p["nodes"]) for p in paginate(_diagram("cycle", 7))] == [6, 1]


def test_matrix_places_items_and_highlights_first(tmp_path):
    data = _diagram("matrix", 0, axis_x="가로", axis_y="세로")
    data["nodes"] = [{"label": "우리 제안", "detail": "", "group": "오른쪽 아래"}, {"label": "다른 것", "detail": "", "group": "왼쪽 위"}, {"label": "자리 없음", "detail": "", "group": ""}]
    prs, stats, _ = _build(tmp_path, [data])
    cells = [sh for sh in prs.slides[1].shapes if sh.name == "칸"]
    fills = [str(sh.fill.fore_color.rgb) for sh in cells]
    assert fills == ["F6F7FB", "F6F7FB", "F6F7FB", "002FA7"]  # 첫 항목이 든 칸(오른쪽 아래)만 강조 (베타 AK: 브랜드 면·흰 글자)
    boxes = sorted((sh for sh in prs.slides[1].shapes if sh.name == "칸 글"), key=lambda sh: (sh.top, sh.left))
    assert [b.text_frame.text for b in boxes] == ["다른 것", "자리 없음", "우리 제안"]  # 자리 없는 셋째 항목은 순서대로 셋째 칸(왼쪽 아래)
    assert _layout(stats["quality"]) == []


def test_random_sizes_stay_inside_the_slide(tmp_path):
    """새 장 종류: 항목 수·글 길이를 아무렇게나 줘도 슬라이드·본문 띠 밖으로 나가지 않고, 넘치면 쪽을 나누거나 글자를 줄인다."""
    rnd = random.Random(20261124)
    words = ["설비", "운영", "절감", "예지보전", "센서", "플랫폼", "게이트웨이와 연동", "관리자 앱 알림", "A", "BAS 연동 방식 확인"]

    def text(lo, hi):
        return " ".join(rnd.choice(words) for _ in range(rnd.randint(lo, hi)))

    slides = []
    for _ in range(40):
        km = text(2, 40) + "입니다" if rnd.random() < 0.8 else ""
        note = text(1, 30) if rnd.random() < 0.4 else ""
        pick = rnd.choice(["kpi", "chart", "layers", "hub", "timeline", "matrix", "cycle", "before_after"])
        base = {"title": text(1, 8), "key_message": km, "note": note}
        if pick == "kpi":
            slides.append({**base, "kind": "kpi", "kpis": [{"value": rnd.choice(["7", "1,234,567", "약 3", text(1, 4)]), "unit": rnd.choice(["", "분", "개월"]), "label": text(0, 6), "note": text(0, 25)}
                                                           for _ in range(rnd.randint(1, 9))]})
        elif pick == "chart":
            cats, ser = rnd.randint(1, 26), rnd.randint(1, 5)
            slides.append({**base, "kind": "chart", "chart_type": rnd.choice(["column", "bar", "line", "doughnut", "nope"]), "unit": rnd.choice(["", "주", "%"]),
                           "categories": [text(1, 5) for _ in range(cats)],
                           "series": [{"name": text(0, 4), "values": [rnd.choice([0, 3, 12.5, 1500000, -4]) for _ in range(cats)]} for _ in range(ser)]})
        elif pick == "before_after":
            slides.append({**base, "kind": "diagram", "diagram_type": pick, "left_title": text(0, 12), "right_title": text(0, 3),
                           "left": [text(1, 30) for _ in range(rnd.randint(0, 12))], "right": [text(1, 12) for _ in range(rnd.randint(0, 12))]})
        else:
            slides.append({**base, "kind": "diagram", "diagram_type": pick, "center": text(0, 10), "axis_x": text(0, 10), "axis_y": text(0, 10),
                           "nodes": [{"label": text(1, 8), "detail": rnd.choice(["", text(1, 40), "가, 나, 다", ", ".join(text(1, 2) for _ in range(9))]),
                                      "group": rnd.choice(["", "2주", "오른쪽 위", "왼쪽 아래", text(1, 6)])} for _ in range(rnd.randint(1, 14))]})
    prs, stats, n = _build(tmp_path, slides)
    assert n == len(prs.slides) >= 41
    bad = [f for f in stats["quality"] if f["gate"].startswith(("슬라이드 밖", "본문 최대 y", "헤더 띠", "#25-b", "#28-b", "#28-c", "#0b", "#5", "#14", "#8", "최소 글자", "#9", "#15", "#21", "차트"))]
    assert bad == [], bad[:5]
    for slide in list(prs.slides)[1:]:
        band = next((sh for sh in slide.shapes if sh.name == "결론 띠"), None)
        limit = min(deck.BODY_BOTTOM, band.top / EMU - deck.BAND_GAP) if band is not None else deck.BODY_BOTTOM
        for sh in slide.shapes:
            x0, y0, x1, y1 = sh.left / EMU, sh.top / EMU, (sh.left + sh.width) / EMU, (sh.top + sh.height) / EMU
            assert -0.011 <= x0 and x1 <= deck.SLIDE_W + 0.011 and -0.011 <= y0 and y1 <= deck.SLIDE_H + 0.011, (sh.name, x0, y0, x1, y1)
            if sh.name not in ("챕터", "대제목", "부제", "결론 띠", "쪽 번호", "출처"):
                assert y0 >= deck.BODY_Y_TIGHT - 0.011 and y1 <= limit + 0.011, (sh.name, y0, y1, limit)
                assert x0 >= deck.MARGIN - 0.011 and x1 <= deck.RIGHT + 0.011, (sh.name, x0, x1)


def test_broken_shapes_do_not_break_the_deck(tmp_path):
    """손으로 고친 구성안: 키가 빠지거나 모양이 틀려도 만든다 (그릴 수 없는 것은 그리지 않는다)."""
    slides = [{"kind": "kpi", "title": "가", "kpis": ["글", {"label": "값 없음"}, None]}, {"kind": "chart", "title": "나", "categories": "글", "series": [3, {"values": "x"}]},
              {"kind": "diagram", "title": "다", "diagram_type": None, "nodes": [None, "글", {"detail": "이름 없음"}, {"label": "하나"}]},
              {"kind": "diagram", "title": "라", "diagram_type": "matrix", "nodes": [{"label": "가", "group": 3}]}, {"kind": "diagram", "title": "마", "diagram_type": "before_after"},
              {"kind": "chart", "title": "바", "categories": ["가", "나"], "series": [{"name": None, "values": [True, 2]}]}]
    prs, stats, n = _build(tmp_path, slides)
    assert n == 7 and "하나" in _texts(prs.slides[3]) and not [s for s in prs.slides if _charts(s)]
    assert isinstance(stats["quality"], list)


# ---------------------------------------------------------------- 고객용·내부용

def test_customer_deck_scrubs_new_fields(tmp_path):
    path = "solutions/3/card.md"
    slides = [
        {"kind": "kpi", "title": "수치", "key_message": KM, "note": f"근거 {path}", "kpis": [{"value": "10", "unit": "분", "label": "주기", "note": ""},
                                                                                     {"value": "5", "unit": "건", "label": f"출처 {path}", "note": ""}, {"value": "7", "unit": "", "label": "남는 것", "note": "customers/12 참고"}]},
        {"kind": "chart", "title": "그래프", "key_message": KM, "unit": "주", "note": "activities/2026/1.md", "categories": ["조사", f"{path} 값", "시운전"],
         "series": [{"name": "기간", "values": [2, 9, 4]}, {"name": "opportunities/2.md", "values": [1, 8, 3]}]},
        {"kind": "diagram", "title": "도식", "key_message": KM, "diagram_type": "hub", "center": path, "axis_x": "", "nodes": [{"label": "연동", "detail": "설명", "group": ""}, {"label": "내부", "detail": path, "group": ""},
                                                                                                                         {"label": path, "detail": "", "group": ""}, {"label": "기간", "detail": "", "group": "partners/4.md"}]},
        {"kind": "diagram", "title": "전후", "key_message": KM, "diagram_type": "before_after", "left": ["수동", f"근거={path}"], "right": ["자동"]},
    ]
    stats = {}
    outline = {"title": "t", "slides": [{"kind": "cover", "title": "t"}, *slides]}
    build_deck(outline, tmp_path / "c.pptx", audience="customer", stats=stats, today=date(2026, 11, 24))
    prs = Presentation(tmp_path / "c.pptx")
    whole = "\n".join(_texts(s) for s in prs.slides)
    chart = _charts(prs.slides[2])[0].chart
    chart_text = "\n".join(t.text or "" for t in chart._chartSpace.iter() if t.tag in (f"{C}v", f"{A}t"))
    book = chart.part.chart_workbook.xlsx_part.blob
    assert "solutions/3" not in whole + chart_text and "customers/12" not in whole and "partners/4" not in whole and "opportunities/2" not in chart_text and "activities/2026" not in whole
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(book)) as z:
        sheets = b"".join(z.read(n) for n in z.namelist()).decode("utf-8", "ignore")
    assert "solutions/3" not in sheets and "opportunities/2" not in sheets  # 그래프에 딸린 엑셀 자료에도 남지 않는다
    assert list(chart.plots[0].categories) == ["조사", "시운전"] and [list(s.values) for s in chart.plots[0].series] == [[2.0, 4.0], [1.0, 3.0]]  # 항목과 그 자리의 값을 함께 뺀다
    assert "주기" in whole and "남는 것" not in whole and "연동" in whole and "수동" in whole and "자동" in whole
    assert stats["removed"] == 2 + 1 + 1 + 1 + 1 + 3 + 1 + 1  # 수치 2 + 주석 1 / 항목 1 + 계열 이름 1 + 주석 1 / 항목 3 + 가운데 1 / 전후 1
    assert all(not s.has_notes_slide or not s.notes_slide.notes_text_frame.text for s in prs.slides)
    # 내부 검토용은 그대로 그리고 노트에 출처·확인 항목을 적는다
    inner = {"title": "t", "slides": [{"kind": "cover", "title": "t"}, {**slides[0], "sources": [path], "needs_check": ["주기 값 확인"]}]}
    build_deck(inner, tmp_path / "i.pptx", audience="internal", source_labels={path: "AI 공조 최적제어"}, today=date(2026, 11, 24))
    slide = Presentation(tmp_path / "i.pptx").slides[1]
    assert "남는 것" in _texts(slide) and "Source: AI 공조 최적제어" in _texts(slide)
    assert "AI 공조 최적제어" in slide.notes_slide.notes_text_frame.text and "주기 값 확인" in slide.notes_slide.notes_text_frame.text


# ---------------------------------------------------------------- 품질 검사

def test_gate_warns_when_few_slides_are_visual(tmp_path):
    text_only = [{"kind": "bullets", "title": f"글 {i}", "key_message": KM, "bullets": ["가", "나"]} for i in range(3)]
    _, stats, _ = _build(tmp_path, [*text_only, _diagram("hub"), {"kind": "closing", "title": "끝"}])
    found = [f for f in stats["quality"] if f["gate"] == "시각 자료 비율"]
    assert len(found) == 1 and found[0]["level"] == "warn" and found[0]["slide"] is None and "본문 4장" in found[0]["text"] and "1장" in found[0]["text"]
    _, stats, _ = _build(tmp_path, [*text_only[:2], _diagram("hub"), _kpi(), {"kind": "closing", "title": "끝"}], name="ok.pptx")
    assert not [f for f in stats["quality"] if f["gate"] == "시각 자료 비율"]  # 절반이면 된다
    out = []
    deck_gates._check_visual_ratio(["cover", "closing"], out)
    deck_gates._check_visual_ratio(None, out)
    assert out == []


def test_chart_gates_catch_a_bad_chart(tmp_path):
    from pptx.dml.color import RGBColor

    prs, stats, _ = _build(tmp_path, [_chart("column", 3, 1), _chart("doughnut", 3, 1)])
    assert not [f for f in stats["quality"] if f["gate"].startswith(("#8 ", "차트", "#5b", "도넛"))]
    chart = _charts(prs.slides[1])[0].chart
    chart.plots[0].has_data_labels = False
    chart.category_axis.has_major_gridlines = True
    chart.has_title = True
    chart.chart_title.text_frame.text = "제목"
    chart.plots[0].series[0].format.fill.fore_color.rgb = RGBColor(0xE6, 0x00, 0x7E)
    chart.font.bold = True
    gates = {f["gate"] for f in deck_gates.check_presentation(prs) if f["slide"] == 2}
    assert {"#8 차트 수치 미표기", "차트 눈금", "차트 제목", "차트 색 위계", "#5b-3 차트 굵기 플래그"} <= gates


# ---------------------------------------------------------------- 프롬프트·문서

def test_prompt_plans_the_deck_visually():
    prompt = (ROOT / "app" / "ai" / "prompts" / "proposal_outline.md").read_text(encoding="utf-8")
    for needle in ("글보다 그림이 먼저", "`kpi` (핵심 수치)", "`chart` (그래프)", "`diagram` (도식)", "`layers`", "`hub`", "`before_after`", "`timeline`", "`matrix`", "`cycle`",
                   "2장까지만", "절반 이상", "읽은 파일에 실제로 적혀 있는 값만", "도식(`diagram`)으로 보여 주세요", "`needs_check` 에 적으세요", "반드시 `sources`",
                   "제목은 마침표로 끝내지 않습니다", "아홉 가지", "자료에 없는 수치, 스펙, 실적을 쓰지 마세요", "작성자에게 하는 말"):
        assert needle in prompt, needle
    assert "자동으로 붙습니다" not in prompt and "여섯 가지" not in prompt
    for kind in ontology.SLIDE_KINDS:
        assert f'"kind": "{kind}"' in prompt
    assert not re.search(r'"values": \[[1-9]', prompt)  # 예시에 진짜처럼 보이는 수치를 넣지 않는다


def test_docs_describe_beta_ag():
    rules = (ROOT / "docs" / "rules" / "제안서와-PPT.md").read_text(encoding="utf-8")  # 묶음 규칙은 CLAUDE.md 에서 영역 파일로 옮겼다
    section = rules[rules.index("## 베타 AG 묶음이 바꾼 규칙"):rules.index("## 베타 AH 묶음이 바꾼 규칙")]  # 베타 AH 가 뒤에 이어진다
    assert "\n## " not in section[3:], "베타 AG 와 AH 사이에 다른 구역이 생기면 끝을 다시 정한다"
    for needle in ("마침표", "로고", "deck_background", "kpi", "chart", "diagram", "시각 자료 비율", "값 | 단위 | 이름 | 설명", "이름 | 설명 | 묶음", "test_beta_ag.py"):
        assert needle in section, needle
    onto = (ROOT / "docs" / "ontology.md").read_text(encoding="utf-8")
    for needle in ("핵심 수치", "도식 종류", "그래프 종류", "시각 자료가 먼저"):
        assert needle in onto, needle
    guide = (ROOT / "docs" / "운영가이드.md").read_text(encoding="utf-8")
    assert "로고가 없는" in guide and "핵심 수치" in guide
    for doc in (rules, (ROOT / "CLAUDE.md").read_text(encoding="utf-8"), onto, guide, (ROOT / "config.example.toml").read_text(encoding="utf-8"), (ROOT / "README.md").read_text(encoding="utf-8")):
        assert "Background_paperlogy" not in doc and "로고는 그림 오른쪽 위" not in doc
    helps = (ROOT / "app" / "templates" / "help.html").read_text(encoding="utf-8")
    for needle in ("핵심 수치", "그래프", "도식"):
        assert needle in helps, needle

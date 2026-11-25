"""베타 AF: 제안서 PPT 디자인(paperlogy-ppt 디자인 시스템, 강의·마케팅 모드)과 품질 검사(`app.deck_gates`)."""

import random
import re
from datetime import date
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from app import deck, deck_gates
from app.config import load_settings
from app.deck import build_deck, paginate
from tests.conftest import outlined_proposal, sample_outline

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
FACES = {"나눔스퀘어_ac", "나눔스퀘어_ac Bold"}
PALETTE = {"002FA7", "001C64", "4D6DC1", "99ACDB", "D9E0F2", "EAEEF8", "FF7A00", "FFFFFF", "000000", "222222", "3A4654", "5A6678", "8A94A2",
           "5F6B78", "EFF2F6", "F4F6F9", "B7BDC8", "D5DAE3", "EEF0F4", "F6F7FB"}
EMU = 914400
TOL = 0.011


def _build(tmp_path, outline, name="d.pptx", **kw):
    stats = {}
    kw.setdefault("today", date(2026, 11, 24))
    n = build_deck(outline, tmp_path / name, stats=stats, **kw)
    return Presentation(tmp_path / name), stats, n


def _texts(slide):
    out = []
    for sh in slide.shapes:
        if sh.has_text_frame:
            out.append(sh.text_frame.text)
        if getattr(sh, "has_table", False) and sh.has_table:
            out += [c.text_frame.text for row in sh.table.rows for c in row.cells]
    return "\n".join(out)


def _notes(slide):
    return slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""


def _box(sh):
    h = sh.height
    if getattr(sh, "has_table", False) and sh.has_table:
        h = sum(r.height for r in sh.table.rows)
    return sh.left / EMU, sh.top / EMU, (sh.left + sh.width) / EMU, (sh.top + h) / EMU


def _layout_gates(findings):
    """배치 검사 결과만 (문구 검사는 뺀다)."""
    names = ("슬라이드 밖", "본문 최대 y", "헤더 띠", "#25-b", "#28-b", "#28-c", "#28-W", "#0b", "#21", "#9", "#15", "#5", "#14", "#8f", "최소 글자")
    return [f for f in findings if f["gate"].startswith(names)]


# ---------------------------------------------------------------- 토큰: 글꼴·굵기·색·윤곽선

def test_faces_no_bold_palette_and_outline(tmp_path):
    prs, stats, _ = _build(tmp_path, sample_outline(8), customer_name="한빛병원", contact=["담당: 김대리", "02-000-0000"])
    runs = 0
    for slide in prs.slides:
        root = slide._element
        for rpr in root.iter(f"{A}rPr"):
            runs += 1
            assert rpr.get("b") == "0" and rpr.get("i") != "1"  # 굵기는 속성이 아니라 페이스로
            faces = {rpr.find(f"{A}{tag}").get("typeface") for tag in ("latin", "ea", "cs")}
            assert len(faces) == 1 and faces <= FACES
            color = rpr.find(f"{A}solidFill/{A}srgbClr").get("val")
            assert color != "FF7A00"  # 강조 오렌지는 글자에 쓰지 않는다
            ln = rpr.find(f"{A}ln")
            if color == "FFFFFF":
                assert ln is None  # 흰 글자만 윤곽선 없음 (LibreOffice 계열이 흰 글자를 지운다)
            else:
                assert rpr[0] is ln and ln.get("w") == "12700"  # 1pt
                clr = ln.find(f"{A}solidFill/{A}srgbClr")
                assert clr.get("val") == color and clr.find(f"{A}alpha").get("val") == "0"  # 투명도 100%, 색은 글자색
        assert {c.get("val") for c in root.iter(f"{A}srgbClr")} <= PALETTE
    assert runs > 30
    bold = [r for s in prs.slides for sh in s.shapes if sh.has_text_frame and sh.name == "대제목" for p in sh.text_frame.paragraphs for r in p.runs]
    assert bold and all(r.font.name == "나눔스퀘어_ac Bold" and r.font.size.pt == 26 for r in bold)
    assert stats["quality"] is not None and not _layout_gates(stats["quality"])


def test_tokens_match_design_system():
    assert (deck.FONT, deck.FONT_B) == ("나눔스퀘어_ac", "나눔스퀘어_ac Bold")
    assert [str(c) for c in (deck.BRAND, deck.BRAND_DEEP, deck.ACCENT, deck.INK, deck.TEXT, deck.MUTE, deck.CAPTION, deck.SOURCE, deck.SURFACE2)] == [
        "002FA7", "001C64", "FF7A00", "222222", "3A4654", "5A6678", "8A94A2", "5F6B78", "EFF2F6"]
    assert (deck.SLIDE_W, deck.SLIDE_H, deck.MARGIN, deck.CONTENT_W) == (13.333, 7.5, 0.422, 12.49)
    assert (deck.CHAPTER_Y, deck.HEAD_Y, deck.SUB_Y, deck.BODY_Y, deck.BODY_BOTTOM, deck.BAND_Y, deck.BAND_H, deck.STRIP_Y) == (0.306, 0.851, 1.472, 2.20, 6.05, 6.20, 0.40, 6.85)
    assert {str(deck.PAGE_BG)} <= deck_gates.PALETTE and {str(c) for c in (deck.GRID_GREY, deck.HAIRLINE, deck.BRAND_T3, deck.BRAND_PALE)} <= deck_gates.PALETTE


# ---------------------------------------------------------------- 헤더·띠·쪽 번호

def test_header_band_and_page_number(tmp_path):
    o = {"title": "전력비 절감 제안", "slides": [
        {"kind": "cover", "title": "전력비 절감 제안", "subtitle": "고객사 귀중"},
        {"kind": "bullets", "title": "전력비를 연 12% 줄입니다", "key_message": "설비를 바꾸지 않고 줄입니다.", "bullets": ["가", "나", "다"]},
        {"kind": "bullets", "title": "끝이 물음표인가?", "bullets": ["가"]}]}
    prs, stats, _ = _build(tmp_path, o)
    cover, s2, s3 = prs.slides
    by = {sh.name: sh for sh in s2.shapes}
    assert by["챕터"].text_frame.text == "02 · 전력비 절감 제안"
    assert by["대제목"].text_frame.text == "전력비를 연 12% 줄입니다"  # 제목은 마침표로 끝내지 않는다 (베타 AG, 사용자 결정 2026-11-24)
    assert next(sh for sh in s3.shapes if sh.name == "대제목").text_frame.text == "끝이 물음표인가?"
    for name, (x, y, w, h) in {"챕터": (0.422, 0.306, 8.0, 0.3), "대제목": (0.422, 0.851, 12.262, 0.55), "결론 띠": (0.422, 6.20, 12.49, 0.40),
                               "쪽 번호": (12.45, 6.85, 0.5, 0.3)}.items():
        sh = by[name]
        got = (sh.left / EMU, sh.top / EMU, sh.width / EMU, sh.height / EMU)
        assert all(abs(a - b) < 0.001 for a, b in zip(got, (x, y, w, h))), (name, got)
    assert by["결론 띠"].text_frame.text == "SO WHAT     설비를 바꾸지 않고 줄입니다."
    label = by["결론 띠"].text_frame.paragraphs[0].runs[0]
    assert label._r.find(f"{A}rPr").get("spc") == "200"  # 영문 대문자 라벨만 자간 +2
    assert by["챕터"].text_frame.paragraphs[0].runs[0]._r.find(f"{A}rPr").get("spc") is None  # 한글이 섞이면 0
    # 쪽 번호는 자동 슬라이드 번호 필드. 표지에는 없다
    assert not cover._element.findall(f".//{A}fld")
    for i, slide in enumerate((s2, s3), start=2):
        fields = slide._element.findall(f".//{A}fld")
        assert len(fields) == 1 and fields[0].get("type") == "slidenum" and fields[0].find(f"{A}t").text == str(i)
        assert fields[0].find(f"{A}rPr").get("sz") == "800"
    assert not any(sh.name == "결론 띠" for sh in s3.shapes)
    assert [f["gate"] for f in stats["quality"] if f["slide"] == 3] == ["#13 SO WHAT 띠"]
    assert not [f for f in stats["quality"] if f["slide"] == 2]
    # 카드는 선 없이 그림자만, 모든 글상자는 세로 가운데
    card = next(sh for sh in s2.shapes if sh.name == "카드")
    sp_pr = card._element.spPr
    assert sp_pr.find(f"{A}ln/{A}noFill") is not None and sp_pr.find(f"{A}effectLst/{A}outerShdw/{A}srgbClr/{A}alpha").get("val") == "5000"
    for slide in prs.slides:
        for body_pr in slide._element.iter(f"{A}bodyPr"):
            assert body_pr.get("anchor") == "ctr" and body_pr.get("tIns") == body_pr.get("bIns")


def test_long_headline_and_key_message_stay_in_their_bands(tmp_path):
    title = "별관에서 먼저 검증하고 성과를 확인한 다음 본관으로 단계적으로 넓혀 가는 통합 제안의 전체 그림"
    message = "이미 본관과 별관 설비를 24시간 상주 운영하고 있어 새 솔루션을 기존 운영 범위 안에서 바로 확장할 수 있습니다 " * 3
    prs, stats, _ = _build(tmp_path, {"title": "t", "slides": [{"kind": "bullets", "title": title, "key_message": message, "bullets": ["가", "나"]}]})
    by = {sh.name: sh for sh in prs.slides[0].shapes}
    head, band = by["대제목"], by["결론 띠"]
    assert (head.top + head.height) / EMU <= 2.20 - 0.2
    assert abs((band.top + band.height) / EMU - 6.60) < 0.001 and band.height / EMU > 0.40  # 띠는 위로 자란다
    assert all(_box(sh)[3] <= band.top / EMU - 0.15 + TOL for sh in prs.slides[0].shapes if sh.name == "카드")
    gates = {f["gate"] for f in stats["quality"]}
    assert "헤드라인 30자" in gates and not _layout_gates(stats["quality"])


# ---------------------------------------------------------------- 배치: 무작위 구성안이 띠를 넘지 않는다

WORDS = ["전력비", "절감", "공조", "최적제어", "예지보전", "센서", "24시간", "상주", "운영", "BAS", "연동", "검증합니다", "확대합니다", "고장", "징후를", "미리",
         "알립니다", "수술실", "냉방", "중단", "예방", "ROI", "3개월", "KT", "AI", "설비", "기준을", "합의한", "뒤", "진행합니다"]


def _sentence(rng, lo, hi):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(lo, hi)))


def _random_outline(rng):
    hi = rng.choice([3, 6, 12, 30])
    slides = [{"kind": "cover", "title": _sentence(rng, 2, 14), "subtitle": _sentence(rng, 0, 8)}]
    for _ in range(rng.randint(3, 7)):
        kind = rng.choice(["bullets", "two_column", "table", "steps", "closing"])
        s = {"kind": kind, "title": _sentence(rng, 1, 10), "key_message": _sentence(rng, 0, 25) if rng.random() < 0.8 else ""}
        if kind in ("bullets", "closing"):
            s["bullets"] = [_sentence(rng, 1, hi) for _ in range(rng.randint(0, 14))]
        elif kind == "two_column":
            s.update(left_title=_sentence(rng, 0, 6), right_title=_sentence(rng, 0, 6),
                     left=[_sentence(rng, 1, hi) for _ in range(rng.randint(0, 11))], right=[_sentence(rng, 1, hi) for _ in range(rng.randint(0, 11))])
        elif kind == "table":
            cols = rng.randint(1, 8)
            s.update(columns=[_sentence(rng, 1, 2) for _ in range(cols)], rows=[[_sentence(rng, 1, max(2, hi // 2)) for _ in range(cols)] for _ in range(rng.randint(0, 20))])
        else:
            s["steps"] = [{"name": _sentence(rng, 1, 4), "detail": _sentence(rng, 0, hi)} for _ in range(rng.randint(0, 9))]
        slides.append(s)
    return {"title": _sentence(rng, 1, 8), "slides": slides}


@pytest.mark.slow
def test_random_outlines_stay_inside_slide_and_body(tmp_path):
    rng = random.Random(20261124)
    kinds_seen = set()
    for i in range(60):
        o = _random_outline(rng)
        audience = "customer" if i % 2 else "internal"
        prs, stats, n = _build(tmp_path, o, name=f"r{i}.pptx", audience=audience, customer_name="한빛종합병원", contact=["담당: 김대리", "02-000-0000", "kim@example.com"])
        assert n == len(prs.slides)
        pages = [p for s in o["slides"] for p in paginate(s if s["kind"] != "closing" else deck._with_contact(s, ["담당: 김대리", "02-000-0000", "kim@example.com"]))]
        assert len(pages) == n
        for slide, page in zip(prs.slides, pages):
            kinds_seen.add(page["kind"])
            band = next((sh for sh in slide.shapes if sh.name == "결론 띠"), None)
            limit = min(6.05, band.top / EMU - 0.15) if band is not None else 6.05
            for sh in slide.shapes:
                x1, y1, x2, y2 = _box(sh)
                assert -TOL <= x1 and -TOL <= y1 and x2 <= 13.333 + TOL and y2 <= 7.5 + TOL, (i, page["kind"], sh.name)
                if page["kind"] != "cover" and sh.name not in ("결론 띠", "배경", "챕터", "대제목", "부제", "쪽 번호", "출처"):
                    assert y1 >= 2.20 - TOL and y2 <= limit + TOL, (i, page["kind"], sh.name, y1, y2, limit)
            if page["kind"] != "cover":
                assert len(slide._element.findall(f".//{A}fld")) == 1
        bad = _layout_gates(stats["quality"])
        assert not bad, (i, bad[:3])
    assert kinds_seen == {"cover", "bullets", "two_column", "table", "steps", "closing"}


def test_pagination_by_height_keeps_every_item(tmp_path):
    long = "설비를 멈추지 않고 별관 냉동기와 펌프 모터에 바로 달아 고장 징후를 미리 잡고 관리자 앱으로 알립니다 " * 4
    cases = [
        {"kind": "bullets", "title": "t", "key_message": "m", "bullets": [f"{i} {long}" for i in range(9)]},
        {"kind": "two_column", "title": "t", "left": [f"L{i} {long}" for i in range(7)], "right": [f"R{i} 짧음" for i in range(3)]},
        {"kind": "steps", "title": "t", "steps": [{"name": f"단계 {i}", "detail": long} for i in range(6)]},
    ]
    for case in cases:
        pages = paginate(case)
        assert len(pages) > 1 and pages[1]["title"] == "t (2)" and not pages[1].get("key_message")
        for key in ("bullets", "left", "right", "steps"):
            if case.get(key):
                assert [x for p in pages for x in p[key]] == case[key]
    prs, stats, _ = _build(tmp_path, {"title": "t", "slides": cases})
    assert not _layout_gates(stats["quality"])
    # 짧은 글은 개수 한도 그대로: 글머리 10, 2단 한쪽 8, 단계 6, 표 8행
    assert [len(p["bullets"]) for p in paginate({"kind": "bullets", "bullets": [f"b{i}" for i in range(23)]})] == [10, 10, 3]
    assert [len(p["steps"]) for p in paginate({"kind": "steps", "steps": [{"name": f"s{i}", "detail": "d"} for i in range(8)]})] == [6, 2]
    assert [len(p["left"]) for p in paginate({"kind": "two_column", "left": [f"l{i}" for i in range(9)], "right": ["r"]})] == [8, 1]


def test_layout_by_bullet_count(tmp_path):
    slides = [{"kind": "bullets", "title": f"글머리 {n}개", "key_message": "핵심입니다.", "bullets": [f"항목 {i}" for i in range(n)]} for n in (1, 2, 3, 4, 5, 6, 7, 10)]
    prs, _, _ = _build(tmp_path, {"title": "t", "slides": slides})
    cards = [len([sh for sh in s.shapes if sh.name == "카드"]) for s in prs.slides]
    assert cards == [1, 2, 3, 4, 5, 6, 1, 1]  # 6개까지는 번호 카드, 그보다 많으면 목록 카드 하나
    assert "01" in _texts(prs.slides[2]) and "03" in _texts(prs.slides[2]) and "01" not in _texts(prs.slides[6])
    lists = [sh for sh in prs.slides[7].shapes if sh.name == "목록"]
    assert len(lists) == 2 and lists[0].top == lists[1].top  # 카드 안 두 줄
    for s in prs.slides:  # 큰 카드의 좌우 끝은 공통 마진
        xs = [(sh.left / EMU, (sh.left + sh.width) / EMU) for sh in s.shapes if sh.name == "카드"]
        assert abs(min(a for a, _ in xs) - 0.422) < 0.001 and abs(max(b for _, b in xs) - 12.912) < 0.001


def test_steps_and_closing_shapes(tmp_path):
    o = {"title": "t", "slides": [
        {"kind": "steps", "title": "일정", "key_message": "m", "steps": [{"name": "착수", "detail": "1개월"}, {"name": "구축", "detail": "3개월"}, {"name": "운영", "detail": ""}]},
        {"kind": "closing", "title": "다음 단계", "key_message": "별관에서 먼저 확인합니다.", "bullets": ["일정 확정"]},
        {"kind": "closing", "title": "감사합니다"}]}
    prs, stats, _ = _build(tmp_path, o, contact=["담당: 김대리"])
    steps, closing, thanks = prs.slides
    dots = [sh for sh in steps.shapes if sh.name == "번호 원"]
    assert [d.text_frame.text for d in dots] == ["1", "2", "3"] and len([sh for sh in steps.shapes if sh.name == "흐름 화살표"]) == 2
    assert steps._element.findall(f".//{A}tailEnd")[0].get("type") == "triangle"
    names = {sh.name for sh in closing.shapes}
    assert "강조 카드" in names and "결론 띠" not in names and "담당: 김대리" in _texts(closing) and "별관에서 먼저 확인합니다." in _texts(closing)
    assert "감사합니다" in _texts(thanks) and "감사합니다." not in _texts(thanks) and "담당: 김대리" in _texts(thanks)  # 연락처가 붙으면 보통의 마무리 장
    bare, _, _ = _build(tmp_path, {"title": "t", "slides": [o["slides"][2]]}, name="bare.pptx")
    assert "감사합니다" in _texts(bare.slides[0]) and {sh.name for sh in bare.slides[0].shapes} == {"강조 막대", "대제목", "쪽 번호"}
    assert not [f for f in stats["quality"] if f["gate"].startswith("#13")]  # 마무리 장은 결론 띠가 없어도 된다


# ---------------------------------------------------------------- 배경 설정

def _picture(path: Path) -> Path:
    Image.new("RGB", (320, 180), (250, 250, 250)).save(path)
    return path


def test_background_setting_on_and_off(tmp_path):
    o = sample_outline(6)
    plain, stats, _ = _build(tmp_path, o, name="plain.pptx")
    for slide in plain.slides:
        assert not [sh for sh in slide.shapes if sh.shape_type == 13]
        assert slide._element.find(f".//{A}solidFill/{A}srgbClr").get("val") == "F6F7FB"  # 그림이 없으면 옅은 회색 바탕
    with_bg, stats_bg, _ = _build(tmp_path, o, name="bg.pptx", background=_picture(tmp_path / "bg.png"))
    for slide in with_bg.slides:
        first = slide.shapes[0]
        assert first.name == "배경" and first.shape_type == 13 and (first.left, first.top, first.width, first.height) == (0, 0, with_bg.slide_width, with_bg.slide_height)
    assert len({sh.image.sha1 for s in with_bg.slides for sh in s.shapes if sh.shape_type == 13}) == 1
    assert not [f for f in stats_bg["quality"] if f["gate"].startswith("#6")] and not _layout_gates(stats_bg["quality"])
    # 없는 파일·그림이 아닌 파일: 만들기는 그대로 되고 검사 결과에 한 줄
    (tmp_path / "not-image.jpg").write_text("그림이 아님", encoding="utf-8")
    for bad in (tmp_path / "missing.jpg", tmp_path / "not-image.jpg"):
        prs, st, n = _build(tmp_path, o, name="bad.pptx", background=bad)
        assert n == 6 and not [sh for s in prs.slides for sh in s.shapes if sh.shape_type == 13]
        assert [f["gate"] for f in st["quality"] if f["slide"] is None] == ["#6 배경 이미지"]


def test_deck_background_config(tmp_path):
    base = 'data_dir = "data"\nsecret_key = "0123456789abcdef0123456789abcdef"\n'
    cfg = tmp_path / "config.toml"
    cfg.write_text(base, encoding="utf-8")
    assert load_settings(cfg).deck_background is None
    cfg.write_text(base + 'deck_background = "bg/deck.jpg"\n', encoding="utf-8")
    assert load_settings(cfg).deck_background == tmp_path.resolve() / "bg" / "deck.jpg"
    cfg.write_text(base + 'deck_background = ""\n', encoding="utf-8")
    assert load_settings(cfg).deck_background is None
    example = (Path(__file__).resolve().parent.parent / "config.example.toml").read_text(encoding="utf-8")
    assert re.search(r"^# deck_background = ", example, re.M) and not re.search(r"^deck_background", example, re.M)


def test_route_passes_background_and_shows_quality(user_client, db, settings, tmp_path):
    settings.deck_background = _picture(tmp_path / "bg.png")
    p = outlined_proposal(db, settings)
    slides = [dict(s) for s in p.outline["slides"]]
    slides[1] = {**slides[1], "title": "현황 — 과제 ①"}
    p.outline = {**p.outline, "slides": slides}
    db.commit()
    assert user_client.post(f"/proposals/{p.id}/deck", follow_redirects=False).status_code == 303
    db.refresh(p)
    path = settings.decks_dir / str(p.id) / p.deck_name
    for f in (path, path.with_name(path.stem + "-internal.pptx")):
        assert all(s.shapes[0].name == "배경" for s in Presentation(f).slides)
    gates = [f["gate"] for f in p.deck_quality]
    assert "#1 줄표" in gates and "#2 원형숫자" in gates
    page = user_client.get(f"/proposals/{p.id}").text
    assert f"품질 검사: 경고 {len(p.deck_quality)}건" in page and "줄표(— –)가 있습니다" in page and "2쪽 · " in page
    # 고치고 다시 만들면 사라진다
    slides[1] = {**slides[1], "title": "현황과 과제"}
    p.outline = {**p.outline, "slides": slides}
    db.commit()
    user_client.post(f"/proposals/{p.id}/deck")
    db.refresh(p)
    assert not [g for g in (f["gate"] for f in p.deck_quality) if g.startswith(("#1 ", "#2 "))]
    # 옛 제안 건(검사 결과 없음)은 줄이 없다
    p.deck_quality = None
    db.commit()
    assert "품질 검사" not in user_client.get(f"/proposals/{p.id}").text
    p.deck_quality = []
    db.commit()
    assert "품질 검사: 경고 없음" in user_client.get(f"/proposals/{p.id}").text


# ---------------------------------------------------------------- 품질 검사

def test_gates_report_a_bad_outline_without_blocking(tmp_path):
    bad = {"title": "나쁜 예", "slides": [
        {"kind": "cover", "title": "나쁜 예"},
        {"kind": "bullets", "title": "솔직히 말하면 — 이건 정말 너무너무 길게 늘여 쓴 대제목이라서 서른 자를 훌쩍 넘습니다", "key_message": "",
         "bullets": ["① 첫째 항목", "둘째 😀 항목", "근본적으로 되어지는 구조", "「낫표」 쓰기", "정말입니다!!"]},
        {"kind": "bullets", "title": "대조", "key_message": "비용이 아니라 투자입니다.", "bullets": ["단순 교체가 아니라 전환입니다", "지출이 아니라 절감입니다", "사실은 그렇습니다"]},
        {"kind": "bullets", "title": "넘침", "key_message": "m", "bullets": ["가나다라 " * 1500]},
    ]}
    prs, stats, n = _build(tmp_path, bad)
    assert n == 4 and len(prs.slides) == 4  # 막지 않는다
    found = {(f["slide"], f["gate"]) for f in stats["quality"]}
    for expected in [(2, "#1 줄표"), (2, "#2 원형숫자"), (2, "#3 이모지"), (2, "카피 · 솔직히 류"), (2, "카피 · 군더더기 부사"), (2, "카피 · 이중피동"), (2, "문장부호"),
                     (2, "카피 · 느낌표 남발"), (2, "헤드라인 30자"), (2, "#13 SO WHAT 띠"), (None, "카피 · 대조말투 남발"),
                     (None, "카피 · 군더더기 표현"), (4, "#28-W 글자 넘침")]:
        assert expected in found, expected
    assert {f["level"] for f in stats["quality"]} == {"error", "warn"} and stats["quality"][0]["level"] == "error"
    assert deck_gates.counts(stats["quality"])["total"] == len(stats["quality"]) and deck_gates.counts(None) == {"total": 0, "errors": 0, "warns": 0}
    good, good_stats, _ = _build(tmp_path, {"title": "좋은 예", "slides": [
        {"kind": "cover", "title": "좋은 예"},
        {"kind": "bullets", "title": "전력비를 줄입니다", "key_message": "설비 교체 없이 전력비를 줄입니다.", "bullets": ["운전 최적화", "고장 예방"], "sources": [], "needs_check": []},
        # 베타 AG: 본문 장의 절반 이상이 시각 자료 장이어야 "시각 자료 비율" 경고가 없다
        {"kind": "steps", "title": "두 단계로 도입합니다", "key_message": "별관에서 먼저 확인합니다.", "steps": [{"name": "시범", "detail": "별관"}, {"name": "확대", "detail": "본관"}]},
        {"kind": "closing", "title": "감사합니다"}]}, name="good.pptx")
    assert good_stats["quality"] == []


def test_gates_catch_design_violations_in_a_hand_made_deck(tmp_path):
    """검사기 자체: 규칙을 어긴 프레젠테이션을 손으로 만들어 넣는다."""
    from pptx.dml.color import RGBColor
    from pptx.util import Pt

    from app.pptx_template import blank_presentation

    prs = blank_presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(5.5), Inches(4), Inches(1.2))  # 본문 최대 y 를 넘고 세로 가운데가 아니다
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "맑은 고딕 굵게"
    run.font.name, run.font.bold, run.font.size = "맑은 고딕", True, Pt(6)
    run.font.color.rgb = RGBColor(0xFF, 0x7A, 0x00)
    shape = slide.shapes.add_shape(1, Inches(12), Inches(3), Inches(2), Inches(1))  # 슬라이드 밖 + 팔레트 밖 파랑 + 테두리
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(0x3B, 0x5B, 0xA9)
    gates = {f["gate"] for f in deck_gates.check_presentation(prs)}
    assert gates >= {"#5b-1 승인 외 폰트", "#5b-2 굵게 속성", "#5c-2 윤곽선 없는 텍스트 런", "최소 글자 8pt", "#8f 오렌지 글자색", "#14 팔레트 밖 파란 계열",
                     "슬라이드 밖 도형", "본문 최대 y 6.05", "#28-b 세로 가운데 아님", "#4 자동 슬라이드번호"}
    prs.slide_width = Inches(10)
    assert "슬라이드 크기" in {f["gate"] for f in deck_gates.check_presentation(prs)}


def test_gate_failure_never_blocks_the_file(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("검사기 고장")
    monkeypatch.setattr(deck_gates, "check_presentation", boom)
    prs, stats, n = _build(tmp_path, sample_outline(6))
    assert n == 6 and len(prs.slides) == 6 and stats["quality"] is None and stats["removed"] == 0
    assert not list(tmp_path.glob("*.part"))


# ---------------------------------------------------------------- 고객용 / 내부 검토용

def test_customer_deck_has_no_notes_or_internal_paths_and_internal_keeps_notes(tmp_path):
    o = {"title": "제안", "slides": [
        {"kind": "cover", "title": "제안"},
        {"kind": "bullets", "title": "효과", "key_message": "전력비를 줄입니다.", "bullets": ["전력비 절감", "근거: solutions/1/card.md 참고", "data/app.db 백업"],
         "sources": ["solutions/1/card.md", "activities/2026/1.md"], "needs_check": ["절감률 수치", "확인한 것"], "checked": ["확인한 것"]},
        {"kind": "table", "title": "표", "key_message": "m", "columns": ["항목", "knowledge/solutions/3/card.md"], "rows": [["가", "나"], ["opportunities/2.md", "다"]]},
        {"kind": "closing", "title": "다음 단계", "bullets": ["일정 확정"]}]}
    labels = {"solutions/1/card.md": "AI 공조 최적제어"}
    customer, cst, _ = _build(tmp_path, o, name="c.pptx", audience="customer", source_labels=labels, contact=["담당: 김대리", "kim@example.com"])
    internal, ist, _ = _build(tmp_path, o, name="i.pptx", audience="internal", source_labels=labels, contact=["담당: 김대리", "kim@example.com"])
    assert cst["removed"] == 4 and ist["removed"] == 0
    for slide in customer.slides:
        assert not slide.has_notes_slide
        text = _texts(slide)
        assert not deck.has_internal_mark(text) and "card.md" not in text and "app.db" not in text and "Source:" not in text and "절감률 수치" not in text
    assert "전력비 절감" in _texts(customer.slides[1]) and "kim@example.com" in _texts(customer.slides[3])
    note = _notes(internal.slides[1])
    assert "[출처]\nAI 공조 최적제어\nactivities/2026/1.md" in note and "[확인 항목]\n절감률 수치" in note and "[확인함]\n확인한 것" in note
    body = _texts(internal.slides[1])
    assert "근거: solutions/1/card.md 참고" in body and "절감률 수치" not in body
    source = next(sh for sh in internal.slides[1].shapes if sh.name == "출처")
    assert source.text_frame.text == "Source: AI 공조 최적제어"  # 이름을 아는 출처만 슬라이드에, 경로는 노트에만
    assert abs(source.left / EMU - 0.535) < 0.001 and abs(source.top / EMU - 6.85) < 0.001
    assert not [sh for sh in internal.slides[2].shapes if sh.name == "출처"] and not internal.slides[3].has_notes_slide
    assert "kim@example.com" in _texts(internal.slides[3])
    # 고객용 챕터 라벨에도 내부 경로가 든 제목은 쓰지 않는다
    leak, _, _ = _build(tmp_path, {"title": "solutions/3/card.md 정리", "slides": [{"kind": "bullets", "title": "장", "bullets": ["가"]}]}, name="l.pptx", audience="customer")
    assert next(sh for sh in leak.slides[0].shapes if sh.name == "챕터").text_frame.text == "01"


def test_build_deck_uses_blank_template_and_saves_atomically(tmp_path, monkeypatch):
    source = (Path(deck.__file__)).read_text(encoding="utf-8")
    assert "blank_presentation()" in source and not re.search(r"(?<![_\w])Presentation\(", source)  # 베타 AC: 설치 폴더의 빈 서식을 읽지 않는다
    out = tmp_path / "x" / "deck.pptx"
    calls = []
    real = deck.replace_retry
    monkeypatch.setattr(deck, "replace_retry", lambda src, dst, *a, **k: (calls.append((Path(src).name, Path(dst).name)), real(src, dst))[1])
    assert build_deck(sample_outline(6), out) == 6
    assert calls == [("deck.pptx.part", "deck.pptx")] and out.exists() and not out.with_name("deck.pptx.part").exists()


# ---------------------------------------------------------------- 프롬프트·문서

def test_prompt_and_docs_carry_the_copy_rules():
    root = Path(__file__).resolve().parent.parent
    prompt = (root / "app" / "ai" / "prompts" / "proposal_outline.md").read_text(encoding="utf-8")
    for needle in ("30자 안쪽", "존댓말 평서문", "줄표", "원형숫자", "이모지", "자료에 없는 수치", "needs_check"):
        assert needle in prompt
    claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## 베타 AF 묶음이 바꾼 규칙" in claude and claude.index("## 베타 AF 묶음이 바꾼 규칙") < claude.index("## 문서")
    for needle in ("강의·마케팅 모드", "deck_background", "humanize-korean", "pptx-quality-gates", "나눔스퀘어_ac", "deck_gates"):
        assert needle in claude
    guide = (root / "docs" / "운영가이드.md").read_text(encoding="utf-8")
    assert "나눔스퀘어_ac" in guide and "deck_background" in guide
    ontology_doc = (root / "docs" / "ontology.md").read_text(encoding="utf-8")
    assert "paperlogy-ppt" in ontology_doc and "2026-11-24" in ontology_doc


def test_skill_material_is_not_copied_into_the_repo():
    """디자인 시스템의 그림·스크립트·문서는 저장소에 넣지 않는다 (토큰과 규칙만 우리 코드로 다시 썼다)."""
    root = Path(__file__).resolve().parent.parent
    names = {p.name for p in root.rglob("*") if p.is_file() and ".git" not in p.parts and "data" not in p.parts}
    assert not names & {"Background_paperlogy.jpg", "ppt_lint.py", "brandlogy_lint.py", "writing_lint.py", "paperlogy-ppt-template.js", "DESIGN-SYSTEM.md", "GATES.md"}

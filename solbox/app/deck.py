"""구성안(dict) → .pptx. 템플릿 없이 빈 레이아웃 위에 고정 디자인으로 직접 그린다.

입력은 `proposals.clean_outline` / `slide_from_form` 이 만드는 형태이지만, 키가 빠져 있어도 견딘다.

디자인(베타 AF)은 paperlogy-ppt 디자인 시스템의 강의·마케팅 모드를 python-pptx 로 옮긴 것이다 (제안 덱은 결재란에 사인받는 문서가 아니다):
- 색은 승인 팔레트만(클라인 블루 + 강조 오렌지 1색 + 그레이), 글꼴은 `나눔스퀘어_ac` 하나. 굵게는 굵게 속성이 아니라 `나눔스퀘어_ac Bold` 페이스로 준다.
- 세로 띠(밴드)는 장마다 같은 자리다: 챕터 라벨 0.306 → 대제목 0.851 → 본문 카드 2.20~6.05 → 결론 띠 6.20 → 출처·쪽 번호 6.85.
- 카드는 선 없이 옅은 그림자만, 모든 글은 세로 가운데 정렬, 모든 글자에 1pt·투명도 100% 윤곽선.
만든 뒤 `app.deck_gates` 가 같은 규칙으로 검사한다 (막지는 않고 결과만 돌려준다).
"""

import bisect
import copy
import io
import logging
import math
import re
import unicodedata
import uuid
from datetime import date
from pathlib import Path

from lxml import etree

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from app import ontology
from app.pptx_template import blank_presentation

log = logging.getLogger(__name__)

# --- 디자인 토큰: 글꼴 (굵기는 페이스로 가른다) ---
FONT = "나눔스퀘어_ac"
FONT_B = "나눔스퀘어_ac Bold"

# --- 디자인 토큰: 색 (승인 팔레트. 이 밖의 색을 즉석으로 만들지 않는다) ---
BRAND = RGBColor(0x00, 0x2F, 0xA7)       # 메인 잉크: 챕터 라벨, 결론 띠 라벨, 번호 원
BRAND_DEEP = RGBColor(0x00, 0x1C, 0x64)  # 큰 숫자
ACCENT = RGBColor(0xFF, 0x7A, 0x00)      # 강조 1색. 글자에는 쓰지 않는다 (도형에만)
BRAND_T3 = RGBColor(0x99, 0xAC, 0xDB)
BRAND_T4 = RGBColor(0xD9, 0xE0, 0xF2)
BRAND_PALE = RGBColor(0xEA, 0xEE, 0xF8)
INK = RGBColor(0x22, 0x22, 0x22)         # 대제목, 본문 핵심 글자
TEXT = RGBColor(0x3A, 0x46, 0x54)        # 부제, 보조 본문
MUTE = RGBColor(0x5A, 0x66, 0x78)        # 설명문, 비교군
CAPTION = RGBColor(0x8A, 0x94, 0xA2)     # 쪽 번호
SOURCE = RGBColor(0x5F, 0x6B, 0x78)      # 출처 줄
WHITE = RGBColor(0xFF, 0xFF, 0xFF)       # 카드 면
SURFACE2 = RGBColor(0xEF, 0xF2, 0xF6)    # 결론 띠, 표 머리글
GRID_GREY = RGBColor(0xD5, 0xDA, 0xE3)   # 표 가로줄, 구분선
HAIRLINE = RGBColor(0xEE, 0xF0, 0xF4)    # 카드 안 얇은 구분선
PAGE_BG = RGBColor(0xF6, 0xF7, 0xFB)     # 배경 그림이 없을 때의 바탕 (흰 카드가 묻히지 않는 가장 옅은 승인 회색)

# --- 좌표 (인치). 16:9, 세로 띠는 고정이다 ---
SLIDE_W, SLIDE_H = 13.333, 7.5
MARGIN = 0.422                       # 좌 마진. 오른쪽 끝은 12.912
CONTENT_W = 12.49
RIGHT = MARGIN + CONTENT_W
CHAPTER_Y, CHAPTER_W, CHAPTER_H = 0.306, 8.0, 0.3
HEAD_Y, HEAD_W, HEAD_H = 0.851, 12.262, 0.55
SUB_Y, SUB_H = 1.472, 0.303
BODY_Y = 2.20
BODY_BOTTOM = 6.05                   # 본문 콘텐츠 최대 y
BODY_H = BODY_BOTTOM - BODY_Y
BAND_Y, BAND_H = 6.20, 0.40          # 결론(SO WHAT) 띠. 위 카드와 0.15 띄운다
BAND_GAP = 0.15
STRIP_Y, STRIP_H = 6.85, 0.3         # 출처 · 쪽 번호
SOURCE_X, SOURCE_W = 0.535, 11.0
NUM_X, NUM_W = 12.45, 0.5
GAP_X, GAP_Y = 0.20, 0.15            # 카드 사이
CARD_PAD = 0.30                      # 카드 안 좌우 여백
CARD_PAD_V = 0.20                    # 카드 안 위아래 여백 (아래는 0.18 이상)
RADIUS, RADIUS_SMALL, RADIUS_BAND = 0.18, 0.14, 0.06
TABLE_PAD = 0.20
TABLE_H = BODY_H - 2 * TABLE_PAD     # 표가 쓸 수 있는 높이
STEP_TRACK_H = 0.55                  # 단계 장: 번호 원과 화살표가 놓이는 윗줄
STEP_DOT = 0.36

# --- 글자 크기 (pt) ---
HEAD_PT = 26
HEAD_SIZES = (26, 22, 20, 18, 16)
SUB_PT = 12
LABEL_PT = 10                        # 챕터 라벨, 결론 띠 라벨
BAND_PT = 11
STRIP_PT = 8
CARD_HEAD_PT = 14
LIST_SIZES = (14, 13, 12, 11, 10)    # 카드 안 목록
GRID_SIZES = (16, 14, 13, 12, 11)    # 번호 카드 안 글
TABLE_SIZES = (12, 11, 10, 9)
STEP_NAME_SIZES = (16, 14, 13, 12)
STEP_DETAIL_SIZES = (13, 12, 11, 10)
COVER_SIZES = (44, 40, 36, 32, 28, 24)
MIN_FONT_PT = 8                      # 강의·마케팅 모드의 최소 글자

# --- 넘침 규칙 ---
MAX_BULLETS_PER_SLIDE = 10
MAX_TABLE_ROWS = 8
MAX_COLUMN_ITEMS = 8
MAX_STEPS = 6
MAX_GRID_ITEMS = 6                   # 이보다 많은 글머리는 번호 카드 대신 목록 카드로 그린다

_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _clean(value) -> str:
    """XML 에 쓸 수 없는 문자를 없앤다. 줄바꿈은 \\n 하나로 맞춘다."""
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _ILLEGAL_XML.sub("", text)


def _list(slide: dict, key: str) -> list:
    value = slide.get(key)
    return list(value) if isinstance(value, (list, tuple)) else []


# ---------------------------------------------------------------- 줄 수 어림

NATURAL_LINE = 1.25  # 글꼴이 정한 한 줄 높이 어림 (em). 나눔스퀘어는 약 1.1 이고, 글꼴이 없어 바뀌어 보이는 PC 를 생각해 넉넉히 잡는다
BODY_LS = 1.3        # 본문 줄 간격 배수 (본문 행간은 글자 크기의 약 1.45배)
HEAD_LS = 1.0        # 대제목은 글꼴 기본 줄 간격 (약 1.2배)
LINE_SPACING = NATURAL_LINE * BODY_LS


def _char_em(ch: str) -> float:
    if ch == " ":
        return 0.3
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0  # 나눔스퀘어의 한글 폭은 0.91em: 조금 넉넉히
    return 0.62


def _wrap_count(line: str, avail: float) -> int:
    """한 줄(줄바꿈 없는 글)이 `avail`(em) 폭에서 몇 줄이 되는지: 낱말(어절) 단위로 끊고, 한 줄보다 긴 낱말만 글자에서 끊는다."""
    lines, used = 1, 0.0
    for word in line.split(" "):
        w = sum(_char_em(ch) for ch in word)
        gap = 0.3 if used else 0.0
        if used + gap + w <= avail:
            used += gap + w
            continue
        if w <= avail:
            lines, used = lines + 1, w
            continue
        for ch in word:  # 한 줄보다 긴 낱말
            c = _char_em(ch)
            if used + c > avail and used:
                lines, used = lines + 1, 0.0
            used += c
    return lines


def _est_lines(text, pt: float, width_in: float) -> int:
    """`text` 가 폭 `width_in`(인치) 에 `pt` 크기로 들어갈 때 줄 수를 어림한다. 줄바꿈 문자는 새 줄이다."""
    avail = max(width_in, 0.3) * 72 / pt
    return sum(_wrap_count(line, avail) for line in _clean(text).split("\n"))


def _line_h(pt: float, ls: float = BODY_LS) -> float:
    return pt * NATURAL_LINE * ls / 72


def _band_height(message: str) -> float:
    """결론 띠의 높이. 한 줄이면 0.40 이고, 글이 길어 줄이 늘면 위로 자란다 (아래 끝 6.60 은 그대로)."""
    lines = _est_lines("SO WHAT      " + message, BAND_PT, CONTENT_W - 2 * CARD_PAD)
    return BAND_H if lines <= 1 else 0.16 + lines * _line_h(BAND_PT, 1.0) + 0.04


def _has_band(slide: dict) -> bool:
    return slide.get("kind") not in ("cover", "closing") and bool(_clean(slide.get("key_message")).strip())


def _message_shift(slide: dict) -> float:
    """핵심 메시지(결론 띠)가 여러 줄일 때 본문이 줄어드는 만큼(인치). 띠가 위로 자라므로 본문 아래 끝이 그만큼 올라간다."""
    if not _has_band(slide):
        return 0.0
    return _band_height(_clean(slide.get("key_message")).strip()) - BAND_H


def _table_font(columns: list, rows: list) -> int:
    return 9 if len(columns) > 6 else 12 if len(columns) <= 4 and len(rows) <= 6 else 11


def _col_weights(columns: list, rows: list) -> list[float]:
    """표의 열 폭 비율(합 1): 열마다 가장 긴 칸의 글 길이에 맞추되 5~30em 사이로 묶는다 (짧은 이름 열은 좁게, 설명 열은 넓게).
    쪽을 나눠도 모든 쪽이 같은 비율을 쓰도록 `paginate` 가 원래 표 전체로 한 번 계산해 쪽마다 `_col_weights` 로 싣는다."""
    count = max(len(columns), 1)
    longest = [0.0] * count
    for cells in [columns, *rows]:
        cells = list(cells) if isinstance(cells, (list, tuple)) else [cells]
        for c, cell in enumerate(cells[:count]):
            longest[c] = max(longest[c], max(sum(_char_em(ch) for ch in line) for line in _clean(cell).split("\n")))
    raw = [min(30.0, max(5.0, v)) for v in longest]
    return [v / sum(raw) for v in raw]


def _weights_for(columns: list, rows: list, weights=None) -> list[float]:
    count = max(len(columns), 1)
    ok = isinstance(weights, (list, tuple)) and len(weights) == count and all(isinstance(w, (int, float)) and w > 0 for w in weights)
    return [w / sum(weights) for w in weights] if ok else _col_weights(columns, rows)


def _table_row_heights(columns: list, rows: list, pt: float, weights=None) -> list[float]:
    """머리글 + 각 행의 높이(인치). 칸 안 글이 줄바꿈되는 만큼 키운다. `weights` 는 열 폭 비율(없으면 이 행들로 계산)."""
    count = max(len(columns), 1)
    widths = [(CONTENT_W - 2 * TABLE_PAD) * w - 0.24 for w in _weights_for(columns, rows, weights)]
    base = _line_h(pt, 1.0) + 0.16
    heights = []
    for cells in [columns, *rows]:
        cells = list(cells) if isinstance(cells, (list, tuple)) else [cells]
        lines = max([_est_lines(c, pt, widths[i]) for i, c in enumerate(cells[:count])] or [1])
        heights.append(max(base, lines * _line_h(pt, 1.0) + 0.12))
    return heights


def _fit_table_font(columns: list, rows: list, avail: float = TABLE_H, weights=None) -> tuple[int, list[float]]:
    """처음 글자 크기에서 표가 카드를 넘으면 12 → 11 → 10 → 9 로 내린다."""
    start = _table_font(columns, rows)
    sizes = [s for s in TABLE_SIZES if s <= start] or [TABLE_SIZES[-1]]
    for pt in sizes:
        heights = _table_row_heights(columns, rows, pt, weights)
        if sum(heights) <= avail:
            return pt, heights
    return sizes[-1], _table_row_heights(columns, rows, sizes[-1], weights)


def _table_chunks(columns: list, rows: list, first_shift: float = 0.0) -> list[list]:
    """표 행을 장별로 나눈다. 장당 MAX_TABLE_ROWS 행 이하이고, 가장 작은 글자(9pt)로도 카드 안에 들어가게 한다."""
    small = TABLE_SIZES[-1]
    weights = _col_weights(columns, rows)
    chunks, current, used = [], [], 0.0
    budget = TABLE_H - first_shift  # 첫 장만 결론 띠가 여러 줄이면 그만큼 좁다
    head = _table_row_heights(columns, [], small, weights)[0]
    for row in rows:
        h = _table_row_heights(columns, [row], small, weights)[1]
        if current and (len(current) >= MAX_TABLE_ROWS or head + used + h > budget):
            chunks.append(current)
            current, used, budget = [], 0.0, TABLE_H
        current.append(row)
        used += h
    return chunks + [current] if current or not chunks else chunks


# ---------------------------------------------------------------- 목록·카드 높이 어림

def _indent_in(pt: float) -> float:
    return pt * 1.1 / 72


def _list_need(items: list, pt: float, width: float) -> tuple[float, float]:
    """글머리 목록의 (글 높이, 항목 사이를 가장 좁게 뒀을 때의 전체 높이)."""
    text_w = width - _indent_in(pt)
    text_h = sum(_est_lines(i, pt, text_w) * _line_h(pt) for i in items)
    return text_h, text_h + max(len(items) - 1, 0) * pt * 0.4 / 72


def _list_fit(items: list, width: float, height: float, sizes=LIST_SIZES) -> tuple[int, float, bool]:
    """목록이 (폭, 높이) 안에 들어가는 가장 큰 글자 크기와 항목 사이 간격(pt), 들어가는지 여부.
    항목이 적으면 간격을 벌려 카드 아래가 비지 않게 한다 (간격은 글자 크기의 1.2배까지)."""
    for pt in sizes:
        text_h, need = _list_need(items, pt, width)
        if need <= height:
            gap = pt * 0.4
            if len(items) > 1:
                gap = max(gap, min(pt * 1.2, (height * 0.86 - text_h) / (len(items) - 1) * 72))
            return pt, gap, True
    pt = sizes[-1]
    return pt, pt * 0.4, False


def _list_area(slide: dict) -> tuple[float, float]:
    """글머리·마무리 장에서 목록 카드 하나가 쓰는 (글 폭, 글 높이). 쪽을 나눌 때와 그릴 때 같은 값을 쓴다."""
    width = CONTENT_W - 2 * CARD_PAD
    if slide.get("kind") == "closing" and _clean(slide.get("key_message")).strip():
        width = _CLOSING_RIGHT_W - 2 * CARD_PAD
    return width, BODY_H - 2 * CARD_PAD_V - _message_shift(slide)


_CLOSING_LEFT_W = 4.95               # 마무리 장: 좁은 강조 카드(왼쪽) + 넓은 목록 카드(오른쪽), 5:7
_CLOSING_RIGHT_W = CONTENT_W - _CLOSING_LEFT_W - GAP_X
_COLUMN_W = (CONTENT_W - GAP_X) / 2  # 좌우 비교의 카드 폭 (좌우 1:1)
_COLUMN_HEAD_H = 0.72                # 2단 카드의 제목 줄 높이 (구분선까지)


def _column_area(slide: dict) -> tuple[float, float]:
    return _COLUMN_W - 2 * CARD_PAD, BODY_H - _COLUMN_HEAD_H - CARD_PAD_V - _message_shift(slide)


def _height_chunks(items: list, limit: int, width: float, height: float, first_shift: float = 0.0) -> list[list]:
    """목록을 장별로 나눈다: 장당 `limit` 개 이하이고, 가장 작은 글자로도 카드 높이에 들어가게 한다. 첫 장은 `first_shift` 만큼 낮다."""
    small = LIST_SIZES[-1]
    chunks, current = [], []
    budget = height - first_shift
    for item in items:
        if current and (len(current) >= limit or _list_need(current + [item], small, width)[1] > budget):
            chunks.append(current)
            current, budget = [], height
        current.append(item)
    return chunks + [current] if current or not chunks else chunks


def _step_width(n: int) -> float:
    return (CONTENT_W - GAP_X * (n - 1)) / max(n, 1)


def _steps_fit(steps: list, height: float) -> tuple[int, int, bool]:
    """단계 카드들이 한 줄에 놓일 때 (이름 크기, 설명 크기, 다 들어가는지). 카드가 좁아질수록 글이 길어진다."""
    n = len(steps)
    text_w = _step_width(n) - 2 * 0.22
    names = [_clean(s.get("name")) for s in steps]
    details = [_clean(s.get("detail")) for s in steps]
    for name_pt, detail_pt in zip(STEP_NAME_SIZES, STEP_DETAIL_SIZES):
        if n > 5 and name_pt > 14:
            continue
        need = max(_est_lines(a, name_pt, text_w) * _line_h(name_pt, 1.0) + 0.12 + (_est_lines(b, detail_pt, text_w) * _line_h(detail_pt) if b else 0)
                   for a, b in zip(names, details))
        if need <= height:
            return name_pt, detail_pt, True
    return STEP_NAME_SIZES[-1], STEP_DETAIL_SIZES[-1], False


def _step_chunks(steps: list, first_shift: float = 0.0) -> list[list]:
    """단계를 장별로 나눈다: 장당 MAX_STEPS 개 이하이고, 한 줄에 놓았을 때 설명이 카드에 들어가는 만큼만 담는다."""
    base = BODY_H - STEP_TRACK_H - 2 * CARD_PAD_V
    chunks, current, height = [], [], base - first_shift
    for step in steps:
        if current and (len(current) >= MAX_STEPS or not _steps_fit(current + [step], height)[2]):
            chunks.append(current)
            current, height = [], base
        current.append(step)
    return chunks + [current] if current or not chunks else chunks


# ---------------------------------------------------------------- 나누기

def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def paginate(slide: dict) -> list[dict]:
    """넘치는 장을 여러 장으로 나눈다. 빈 목록은 돌려주지 않는다.

    나뉜 장은 제목에 `(2)`, `(3)` … 이 붙고, key_message / sources / needs_check 는 첫 장에만 남는다.
    개수 한도(글머리 10, 표 8행, 2단 한쪽 8, 단계 6)와 함께 글 높이도 본다: 가장 작은 글자로도 본문 카드(아래 끝 6.05)에 들어가는 만큼만 한 장에 담는다.
    """
    kind = slide.get("kind")
    shift = _message_shift(slide)
    if kind == "table":
        weights = _col_weights(_list(slide, "columns"), _list(slide, "rows"))
        parts = [{"rows": c, "_col_weights": weights} for c in _table_chunks(_list(slide, "columns"), _list(slide, "rows"), shift)]
    elif kind == "two_column":
        width, height = _column_area({**slide, "key_message": ""})
        left = _height_chunks(_list(slide, "left"), MAX_COLUMN_ITEMS, width, height, shift)
        right = _height_chunks(_list(slide, "right"), MAX_COLUMN_ITEMS, width, height, shift)
        count = max(len(left), len(right))
        parts = [{"left": left[i] if i < len(left) else [], "right": right[i] if i < len(right) else []} for i in range(count)]
    elif kind == "steps":
        steps = _list(slide, "steps")
        dicts = [s for s in steps if isinstance(s, dict)]
        parts = [{"steps": c} for c in (_step_chunks(dicts, shift) if dicts else _chunks(steps, MAX_STEPS))]
    elif kind in ("bullets", "closing"):
        width, height = _list_area({**slide, "key_message": ""} if kind == "bullets" else slide)
        parts = [{"bullets": c} for c in _height_chunks(_list(slide, "bullets"), MAX_BULLETS_PER_SLIDE, width, height, shift)]
    else:
        return [copy.deepcopy(slide)]
    result = []
    for i, part in enumerate(parts):
        page = copy.deepcopy(slide)
        page.update(part)
        if i:
            page["title"] = f"{slide.get('title') or ''} ({i + 1})".strip()
            page["key_message"] = ""
            page["sources"] = []
            page["needs_check"] = []
            page["checked"] = []
        result.append(page)
    return result


# ---------------------------------------------------------------- 글자 도우미

def _style_run(run, size: float, *, bold=False, color=INK, spacing: float = 0):
    """굵기는 굵게 속성이 아니라 페이스(`FONT_B`)로 준다: 나눔스퀘어는 굵기마다 다른 글꼴이라 속성으로 주면 합성 굵기가 덧칠된다.
    글자에 두께 1pt·투명도 100% 윤곽선을 넣는다 (지금은 보이지 않고, 나중에 투명도만 내려 가독성을 보정할 수 있게 자리를 잡아 둔다). 흰 글자만 예외."""
    face = FONT_B if bold else FONT
    font = run.font
    font.size = Pt(size)
    font.bold = False  # 표 서식 같은 바깥 굵게가 물려 들어오지 않게 꺼 둔다
    font.color.rgb = color
    font.name = face
    rpr = run._r.get_or_add_rPr()
    if spacing:
        rpr.set("spc", str(int(spacing * 100)))
    for tag in ("ea", "cs"):
        for old in rpr.findall(f"{{{_A}}}{tag}"):
            rpr.remove(old)
    latin = rpr.find(f"{{{_A}}}latin")
    ea = etree.Element(f"{{{_A}}}ea")
    ea.set("typeface", face)
    cs = etree.Element(f"{{{_A}}}cs")
    cs.set("typeface", face)
    latin.addnext(ea)
    ea.addnext(cs)
    for old in rpr.findall(f"{{{_A}}}ln"):
        rpr.remove(old)
    if color == WHITE:
        return  # 흰 글자(진한 카드 위)에는 윤곽선을 넣지 않는다: LibreOffice 계열 보기 프로그램이 투명 윤곽선을 불투명하게 그려 흰 글자를 통째로 지운다
    ln = etree.Element(f"{{{_A}}}ln")  # 윤곽선은 rPr 의 첫 자식이어야 한다. 색은 글자색을 따른다
    ln.set("w", "12700")
    clr = etree.SubElement(etree.SubElement(ln, f"{{{_A}}}solidFill"), f"{{{_A}}}srgbClr")
    clr.set("val", str(color))
    etree.SubElement(clr, f"{{{_A}}}alpha").set("val", "0")
    rpr.insert(0, ln)


def _write(paragraph, text, size, *, bold=False, color=INK, spacing: float = 0):
    """문단에 글자를 쓴다. 줄바꿈은 같은 문단 안의 줄 바꿈이 된다."""
    for i, line in enumerate(_clean(text).split("\n")):
        if i:
            paragraph.add_line_break()
        run = paragraph.add_run()
        run.text = line
        _style_run(run, size, bold=bold, color=color, spacing=spacing)


def _spacing_for(text: str, value: float) -> float:
    """양수 자간은 영문 대문자 라벨에만 준다. 한글이 섞인 라벨은 0."""
    return 0 if any(unicodedata.east_asian_width(ch) in ("W", "F") for ch in text) else value


def _bullet(paragraph, size, color=BRAND):
    ppr = paragraph._p.get_or_add_pPr()
    indent = int(Pt(size) * 1.1)
    ppr.set("marL", str(indent))
    ppr.set("indent", str(-indent))
    clr = etree.SubElement(ppr, f"{{{_A}}}buClr")
    etree.SubElement(clr, f"{{{_A}}}srgbClr").set("val", str(color))
    char = etree.SubElement(ppr, f"{{{_A}}}buChar")
    char.set("char", "•")


def _frame(tf, *, pad_x: float = 0.0, pad_y: float = 0.0):
    """글상자 공통: 줄바꿈, 세로 가운데 정렬, 위아래 여백은 늘 같게."""
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = Inches(pad_x)
    tf.margin_top = tf.margin_bottom = Inches(pad_y)
    tf.auto_size = MSO_AUTO_SIZE.NONE
    return tf


def _textbox(slide, x, y, w, h, *, name: str = "글"):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    box.name = name
    _frame(box.text_frame)
    return box


def _shrink(tf, ratio: float):
    """가장 작은 글자로도 상자에 들어가지 않는 글(아주 긴 한 항목): 글자를 비율로 줄여 넣는다 (25% 아래로는 줄이지 않는다)."""
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    fit = tf._txBody.find(f"{{{_A}}}bodyPr/{{{_A}}}normAutofit")
    if fit is not None:
        fit.set("fontScale", str(int(max(0.25, min(1.0, ratio)) * 100000)))


def _fill_list(tf, items, size, gap, *, color=INK):
    last = len(items) - 1
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = BODY_LS
        p.space_after = Pt(gap if i < last else 0)
        _write(p, item, size, color=color)
        _bullet(p, size)


def _plain(shape):
    """테마의 그림자·윤곽 참조를 떼어 납작한 도형으로 둔다."""
    style = shape._element.find(f"{{{_P}}}style")
    if style is not None:
        shape._element.remove(style)
    return shape


def _rect(slide, x, y, w, h, fill, *, shape=MSO_SHAPE.RECTANGLE, name: str = "도형"):
    shp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.name = name
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.fill.background()  # 선 없음
    return _plain(shp)


def _card(slide, x, y, w, h, *, fill=WHITE, radius=RADIUS, shadow=True, name: str = "카드"):
    """카드: 둥근 사각형, 선 없이 아주 옅은 그림자만 (투명도 5%, 흐림 12pt, 아래로 2pt)."""
    shp = _rect(slide, x, y, w, h, fill, shape=MSO_SHAPE.ROUNDED_RECTANGLE, name=name)
    shp.adjustments[0] = min(0.5, radius / max(min(w, h), 0.01))
    if shadow:
        effects = etree.SubElement(shp._element.spPr, f"{{{_A}}}effectLst")
        outer = etree.SubElement(effects, f"{{{_A}}}outerShdw")
        for key, value in (("blurRad", "152400"), ("dist", "25400"), ("dir", "5400000"), ("algn", "t"), ("rotWithShape", "0")):
            outer.set(key, value)
        clr = etree.SubElement(outer, f"{{{_A}}}srgbClr")
        clr.set("val", "000000")
        etree.SubElement(clr, f"{{{_A}}}alpha").set("val", "5000")
    return shp


def _line(slide, x1, y1, x2, y2, color=HAIRLINE, pt: float = 1.0, *, arrow=False, name: str = "구분선"):
    ln = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    ln.name = name
    _plain(ln)
    ln.line.color.rgb = color
    ln.line.width = Pt(pt)
    if arrow:
        tail = etree.SubElement(ln.line._get_or_add_ln(), f"{{{_A}}}tailEnd")
        tail.set("type", "triangle")
    return ln


# ---------------------------------------------------------------- 그리기: 공통 띠

def _headline_text(title: str) -> str:
    """대제목은 마침표로 끝낸다 (강의·마케팅 모드). `.?!` 로 끝나면 그대로."""
    title = title.strip()
    return title if not title or title[-1] in ".?!" else title + "."


def _header(slide, data: dict, number: int, deck_title: str = "") -> None:
    """헤더 3요소: 챕터 라벨(`NN · 제안서 제목`) + 대제목(26pt) + 부제(12pt, 있을 때만). 자리는 장마다 같다."""
    label = f"{number:02d}" + (f" · {deck_title}" if deck_title else "")
    box = _textbox(slide, MARGIN, CHAPTER_Y, CHAPTER_W, CHAPTER_H, name="챕터")
    _write(box.text_frame.paragraphs[0], label, LABEL_PT, bold=True, color=BRAND, spacing=_spacing_for(label, 2))
    title = _headline_text(_clean(data.get("title")))
    subtitle = _clean(data.get("subtitle")).strip() if data.get("kind") != "cover" else ""
    if title:
        # 한 줄이면 0.55 높이 그대로. 두 줄이면 (부제가 없을 때) 빈 부제 자리까지 내려 쓴다. 그래도 넘치면 글자를 줄인다.
        max_lines = 1 if subtitle else 2
        size = next((pt for pt in HEAD_SIZES if _est_lines(title, pt, HEAD_W) <= max_lines), HEAD_SIZES[-1])
        lines = _est_lines(title, size, HEAD_W)
        height = HEAD_H if lines <= 1 else lines * _line_h(size, HEAD_LS) + 0.06
        limit = (SUB_Y - 0.03 if subtitle else BODY_Y - 0.25) - HEAD_Y
        box = _textbox(slide, MARGIN, HEAD_Y, HEAD_W, min(height, limit), name="대제목")
        _write(box.text_frame.paragraphs[0], title, size, bold=True, color=INK)
        if height > limit:
            _shrink(box.text_frame, limit / height)
    if subtitle:
        box = _textbox(slide, MARGIN, SUB_Y, HEAD_W, SUB_H, name="부제")
        _write(box.text_frame.paragraphs[0], subtitle, SUB_PT, color=TEXT)
        if _est_lines(subtitle, SUB_PT, HEAD_W) > 1:
            _shrink(box.text_frame, 1 / _est_lines(subtitle, SUB_PT, HEAD_W))


def _so_what(slide, message: str) -> float:
    """결론 띠: 풀폭, 가로·세로 가운데. 띠의 위 끝 y 를 돌려준다."""
    height = _band_height(message)
    y = BAND_Y + BAND_H - height
    band = _card(slide, MARGIN, y, CONTENT_W, height, fill=SURFACE2, radius=RADIUS_BAND, shadow=False, name="결론 띠")
    tf = _frame(band.text_frame, pad_x=CARD_PAD, pad_y=0.0)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    _write(p, "SO WHAT", LABEL_PT, bold=True, color=BRAND, spacing=2)
    _write(p, "     ", BAND_PT, color=INK)
    _write(p, message, BAND_PT, color=INK)
    return y


def _body_bottom(slide, data: dict) -> float:
    """결론 띠를 그리고(핵심 메시지가 있을 때) 본문이 쓸 수 있는 아래 끝 y 를 돌려준다."""
    if not _has_band(data):
        return BODY_BOTTOM
    return min(BODY_BOTTOM, _so_what(slide, _clean(data.get("key_message")).strip()) - BAND_GAP)


def _page_number(slide, number: int):
    """쪽 번호는 고정 글자가 아니라 자동 슬라이드 번호 필드로 넣는다 (다른 파일에 붙여 넣어도 번호가 맞는다). 서식은 필드에 직접 준다."""
    box = _textbox(slide, NUM_X, STRIP_Y, NUM_W, STRIP_H, name="쪽 번호")
    p = box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    _write(p, str(number), STRIP_PT, color=CAPTION)
    run = p._p.find(f"{{{_A}}}r")
    fld = etree.Element(f"{{{_A}}}fld")
    fld.set("id", "{" + str(uuid.uuid5(uuid.NAMESPACE_OID, f"solbox-slidenum-{number}")).upper() + "}")
    fld.set("type", "slidenum")
    run.addprevious(fld)
    for child in list(run):
        fld.append(child)  # rPr, t 순서 그대로
    p._p.remove(run)


def _source_line(slide, data: dict, source_labels: dict[str, str] | None):
    """출처 줄 (내부 검토용만): 근거로 읽은 기록의 이름. 이름을 모르는 경로는 슬라이드에 쓰지 않는다 (노트에는 그대로 있다)."""
    labels = source_labels or {}
    names = []
    for s in _list(data, "sources"):
        name = _clean(labels.get(s)).strip() if isinstance(s, str) else ""
        if name and name != s and name not in names:
            names.append(name)
    if not names:
        return
    text, shown = "", 0
    for name in names:  # 한 줄에 들어가는 만큼만
        candidate = f"{text}, {name}" if text else f"Source: {name}"
        if shown and _est_lines(candidate + " 외 99건", STRIP_PT, SOURCE_W) > 1:
            break
        text, shown = candidate, shown + 1
    if shown < len(names):
        text += f" 외 {len(names) - shown}건"
    box = _textbox(slide, SOURCE_X, STRIP_Y, SOURCE_W, STRIP_H, name="출처")
    _write(box.text_frame.paragraphs[0], text, STRIP_PT, color=SOURCE)
    if _est_lines(text, STRIP_PT, SOURCE_W) > 1:
        _shrink(box.text_frame, 1 / _est_lines(text, STRIP_PT, SOURCE_W))


def _background(slide, prs, image: bytes | None):
    """배경: 설정한 그림이 있으면 장 전체에 깔고(맨 아래), 없으면 옅은 회색 바탕."""
    if image:
        pic = slide.shapes.add_picture(io.BytesIO(image), 0, 0, prs.slide_width, prs.slide_height)
        pic.name = "배경"
        return
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = PAGE_BG


def load_background(path) -> bytes | None:
    """배경 그림 파일을 읽는다. 설정이 없거나 파일이 없거나 그림으로 읽을 수 없으면 None (제안서는 그림 없이 만든다)."""
    if not path:
        return None
    try:
        data = Path(path).read_bytes()
        from pptx.parts.image import Image

        Image.from_blob(data).content_type  # 그림이 아니면 여기서 오류
        return data
    except Exception:  # noqa: BLE001  (배경은 꾸밈이다: 어떤 오류든 제안서 만들기를 막지 않는다)
        log.warning("제안서 배경 그림을 읽지 못해 그림 없이 만듭니다: %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------- 그리기: 종류별

def _draw_cover(slide, data: dict, customer_name: str, today: date):
    label = _textbox(slide, MARGIN, CHAPTER_Y, CHAPTER_W, CHAPTER_H, name="챕터")
    _write(label.text_frame.paragraphs[0], "PROPOSAL", LABEL_PT, bold=True, color=BRAND, spacing=2)
    x, y, w, h = MARGIN, 1.45, CONTENT_W, 3.85
    _card(slide, x, y, w, h, fill=BRAND, name="표지 카드")
    _rect(slide, x + 0.6, y + 0.5, 0.46, 0.055, ACCENT, name="강조 막대")
    title = _clean(data.get("title"))
    subtitle = _clean(data.get("subtitle"))
    text_w, text_h = w - 1.2, h - 1.25
    sub_pt = 16
    sub_h = (_est_lines(subtitle, sub_pt, text_w) * _line_h(sub_pt, 1.0) + 0.2) if subtitle else 0.0
    size = next((pt for pt in COVER_SIZES if _est_lines(title, pt, text_w) * _line_h(pt, 1.0) + sub_h <= text_h), COVER_SIZES[-1])
    need = _est_lines(title, size, text_w) * _line_h(size, 1.0) + sub_h
    box = _textbox(slide, x + 0.6, y + 0.75, text_w, text_h, name="표지 제목")
    tf = box.text_frame
    p = tf.paragraphs[0]
    _write(p, title, size, bold=True, color=WHITE)
    if subtitle:
        p = tf.add_paragraph()
        p.space_before = Pt(14)
        _write(p, subtitle, sub_pt, color=WHITE)
    if need > text_h:
        _shrink(tf, text_h / need)
    name = _clean(customer_name).strip()
    if name:
        box = _textbox(slide, MARGIN, 5.6, HEAD_W, 0.42, name="고객 이름")
        _write(box.text_frame.paragraphs[0], name, 18, bold=True, color=INK)
        if _est_lines(name, 18, HEAD_W) > 1:
            _shrink(box.text_frame, 1 / _est_lines(name, 18, HEAD_W))
    box = _textbox(slide, MARGIN, 6.08, HEAD_W, 0.3, name="날짜")
    _write(box.text_frame.paragraphs[0], f"{today.year}. {today.month}. {today.day}.", SUB_PT, color=MUTE)


def _list_card(slide, x, y, w, h, items: list, *, columns: int = 1, name: str = "목록"):
    """흰 카드 하나에 글머리 목록. `columns=2` 면 카드 안을 얇은 세로선으로 나눠 두 줄로 쓴다."""
    _card(slide, x, y, w, h)
    inner_h = h - 2 * CARD_PAD_V
    if columns == 2:
        half = math.ceil(len(items) / 2)
        col_w = (w - 2 * CARD_PAD - 2 * CARD_PAD) / 2
        groups = [items[:half], items[half:]]
        fits = [_list_fit(g, col_w, inner_h) for g in groups]
        size, gap = min(f[0] for f in fits), min(f[1] for f in fits)
        _line(slide, x + w / 2, y + CARD_PAD_V + 0.05, x + w / 2, y + h - CARD_PAD_V - 0.05, GRID_GREY, 0.75)
        for i, group in enumerate(groups):
            box = _textbox(slide, x + CARD_PAD + i * (col_w + 2 * CARD_PAD), y + CARD_PAD_V, col_w, inner_h, name=name)
            _fill_list(box.text_frame, group, size, gap)
        return
    width = w - 2 * CARD_PAD
    size, gap, ok = _list_fit(items, width, inner_h)
    box = _textbox(slide, x + CARD_PAD, y + CARD_PAD_V, width, inner_h, name=name)
    _fill_list(box.text_frame, items, size, gap)
    if not ok:
        _shrink(box.text_frame, inner_h / _list_need(items, size, width)[1])


_GRID_ROWS = {1: (1,), 2: (2,), 3: (3,), 4: (2, 2), 5: (3, 2), 6: (3, 3)}  # 글머리 개수 → 줄마다 놓는 카드 수


def _grid_plan(items: list, height: float):
    """글머리 1~6개를 번호 카드로 놓을 수 있으면 (줄 배치, 번호 크기, 글 크기)를, 글이 길어 카드에 안 들어가면 None."""
    rows = _GRID_ROWS.get(len(items))
    if not rows:
        return None
    card_h = (height - GAP_Y * (len(rows) - 1)) / len(rows)
    if card_h < 1.2:
        return None
    num_pt = 22 if len(rows) == 1 else 16
    num_h = _line_h(num_pt, 1.0) + 0.1
    inner_h = card_h - 2 * CARD_PAD_V
    for pt in GRID_SIZES:
        if len(rows) > 1 and pt > 14:
            continue
        index, ok = 0, True
        for count in rows:
            text_w = (CONTENT_W - GAP_X * (count - 1)) / count - 2 * CARD_PAD
            for item in items[index:index + count]:
                ok = ok and num_h + _est_lines(item, pt, text_w) * _line_h(pt) <= inner_h
            index += count
        if ok:
            return rows, num_pt, pt
    return None


def _draw_grid(slide, items: list, top: float, bottom: float, plan):
    """번호 카드: 한 줄에 2~3장씩. 카드마다 큰 번호(01, 02 …)와 글 한 덩어리. 마지막 줄 카드가 적으면 그만큼 넓어진다."""
    rows, num_pt, pt = plan
    card_h = (bottom - top - GAP_Y * (len(rows) - 1)) / len(rows)
    index = 0
    for r, count in enumerate(rows):
        card_w = (CONTENT_W - GAP_X * (count - 1)) / count
        y = top + r * (card_h + GAP_Y)
        for c in range(count):
            x = MARGIN + c * (card_w + GAP_X)
            _card(slide, x, y, card_w, card_h, radius=RADIUS if len(rows) == 1 else RADIUS_SMALL)
            box = _textbox(slide, x + CARD_PAD, y + CARD_PAD_V, card_w - 2 * CARD_PAD, card_h - 2 * CARD_PAD_V, name="번호 카드 글")
            tf = box.text_frame
            p = tf.paragraphs[0]
            p.space_after = Pt(6)
            _write(p, f"{index + 1:02d}", num_pt, bold=True, color=BRAND_DEEP if len(rows) == 1 else BRAND)
            p = tf.add_paragraph()
            p.line_spacing = BODY_LS
            _write(p, items[index], pt, color=INK)
            index += 1


def _draw_list_body(slide, items: list, top: float, bottom: float, *, numbered: bool = True, x: float = MARGIN, w: float = CONTENT_W):
    """글머리 본문: 6개 이하이고 짧으면 번호 카드, 그보다 많거나 길면 목록 카드 하나 (6개 이상이고 반 폭에 들어가면 두 줄)."""
    height = bottom - top
    plan = _grid_plan(items, height) if numbered and w == CONTENT_W else None
    if plan:
        _draw_grid(slide, items, top, bottom, plan)
        return
    inner_h = height - 2 * CARD_PAD_V
    columns = 1
    if len(items) >= 6:
        half = math.ceil(len(items) / 2)
        col_w = (w - 4 * CARD_PAD) / 2
        fits = [_list_fit(g, col_w, inner_h, LIST_SIZES[:4]) for g in (items[:half], items[half:])]
        columns = 2 if all(f[2] for f in fits) else 1
    _list_card(slide, x, top, w, height, items, columns=columns)


def _draw_bullets(slide, data: dict, number: int, deck_title: str):
    items = _list(data, "bullets")
    _header(slide, data, number, deck_title)
    bottom = _body_bottom(slide, data)
    if items:
        _draw_list_body(slide, items, BODY_Y, bottom)


def _draw_closing(slide, data: dict, number: int, deck_title: str):
    """마무리: 핵심 메시지는 결론 띠가 아니라 진한 강조 카드에 크게 쓰고, 다음 단계·연락처는 옆 목록 카드에 쓴다."""
    items = _list(data, "bullets")
    message = _clean(data.get("key_message")).strip()
    if not items and not message:
        _rect(slide, 0.95, 2.95, 0.46, 0.055, ACCENT, name="강조 막대")
        title = _clean(data.get("title"))
        size = next((pt for pt in (40, 36, 32, 28, 24) if _est_lines(title, pt, 11.4) * _line_h(pt, 1.0) <= 2.4), 24)
        box = _textbox(slide, 0.95, 3.2, 11.4, max(0.9, _est_lines(title, size, 11.4) * _line_h(size, 1.0) + 0.1), name="대제목")
        _write(box.text_frame.paragraphs[0], title, size, bold=True, color=INK)
        return
    _header(slide, data, number, deck_title)
    x, w = MARGIN, CONTENT_W
    if message:
        dark_w = _CLOSING_LEFT_W if items else CONTENT_W
        _card(slide, MARGIN, BODY_Y, dark_w, BODY_H, fill=BRAND, name="강조 카드")
        text_w, text_h = dark_w - 2 * 0.4, BODY_H - 2 * 0.4
        size = next((pt for pt in (22, 20, 18, 16, 14, 12) if _est_lines(message, pt, text_w) * _line_h(pt, 1.15) <= text_h), 12)
        box = _textbox(slide, MARGIN + 0.4, BODY_Y + 0.4, text_w, text_h, name="강조 글")
        p = box.text_frame.paragraphs[0]
        p.line_spacing = 1.15
        _write(p, message, size, bold=True, color=WHITE)
        need = _est_lines(message, size, text_w) * _line_h(size, 1.15)
        if need > text_h:
            _shrink(box.text_frame, text_h / need)
        x, w = MARGIN + dark_w + GAP_X, _CLOSING_RIGHT_W
    if items:
        _draw_list_body(slide, items, BODY_Y, BODY_BOTTOM, numbered=False, x=x, w=w)


def _draw_two_column(slide, data: dict, number: int, deck_title: str):
    """좌우 비교: 같은 폭 카드 둘. 제목 앞 작은 네모로 가른다 (왼쪽은 비교군 회색, 오른쪽은 브랜드 블루). 두 카드는 높이와 글자 크기를 같이 쓴다."""
    _header(slide, data, number, deck_title)
    bottom = _body_bottom(slide, data)
    height = bottom - BODY_Y
    sides = {side: _list(data, side) for side in ("left", "right")}
    text_w = _COLUMN_W - 2 * CARD_PAD
    inner_h = height - _COLUMN_HEAD_H - CARD_PAD_V
    fits = {side: _list_fit(items, text_w, inner_h) for side, items in sides.items() if items}
    size = min((f[0] for f in fits.values()), default=LIST_SIZES[0])
    gap = min((f[1] for f in fits.values()), default=size * 0.4)
    for i, side in enumerate(("left", "right")):
        x = MARGIN + i * (_COLUMN_W + GAP_X)
        items = sides[side]
        _card(slide, x, BODY_Y, _COLUMN_W, height)
        head = _clean(data.get(f"{side}_title")).strip()
        if head:
            _rect(slide, x + CARD_PAD, BODY_Y + 0.29, 0.14, 0.14, BRAND if i else MUTE, name="머리 표시")
            head_w = text_w - 0.26
            head_pt = next((pt for pt in (CARD_HEAD_PT, 13, 12, 11) if _est_lines(head, pt, head_w) <= 1), 11)
            box = _textbox(slide, x + CARD_PAD + 0.26, BODY_Y + 0.18, head_w, 0.36, name="카드 제목")
            _write(box.text_frame.paragraphs[0], head, head_pt, bold=True, color=INK)
            if _est_lines(head, head_pt, head_w) > 1:
                _shrink(box.text_frame, 1 / _est_lines(head, head_pt, head_w))
            _line(slide, x + CARD_PAD, BODY_Y + 0.62, x + _COLUMN_W - CARD_PAD, BODY_Y + 0.62)
        if items:
            box = _textbox(slide, x + CARD_PAD, BODY_Y + _COLUMN_HEAD_H, text_w, inner_h, name="목록")
            _fill_list(box.text_frame, items, size, gap)
            if not fits[side][2]:
                _shrink(box.text_frame, inner_h / _list_need(items, size, text_w)[1])


def _cell_border(cell, bottom: bool):
    """표 칸: 세로줄과 바깥 테두리는 없애고, 행 사이 가로줄만 아주 옅게 긋는다."""
    tc_pr = cell._tc.get_or_add_tcPr()
    for tag in ("lnL", "lnR", "lnT", "lnB"):
        for old in tc_pr.findall(f"{{{_A}}}{tag}"):
            tc_pr.remove(old)
    made = []
    for tag in ("lnL", "lnR", "lnT", "lnB"):
        ln = etree.Element(f"{{{_A}}}{tag}")
        if tag == "lnB" and bottom:
            ln.set("w", "6350")
            etree.SubElement(etree.SubElement(ln, f"{{{_A}}}solidFill"), f"{{{_A}}}srgbClr").set("val", str(GRID_GREY))
        else:
            ln.set("w", "0")
            etree.SubElement(ln, f"{{{_A}}}noFill")
        made.append(ln)
    for i, ln in enumerate(made):  # 테두리는 tcPr 안에서 채우기보다 앞에 온다
        tc_pr.insert(i, ln)


_NO_STYLE_TABLE = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"  # 파워포인트의 "스타일 없음, 눈금 없음" 표 서식


def _draw_table(slide, data: dict, number: int, deck_title: str):
    """표: 흰 카드 위에 선이 거의 없는 표. 머리글만 옅은 회색 면이고, 행이 적으면 행 높이를 늘려 카드 가운데에 놓는다."""
    columns, rows = _list(data, "columns"), _list(data, "rows")
    _header(slide, data, number, deck_title)
    bottom = _body_bottom(slide, data)
    card_h = bottom - BODY_Y
    avail = card_h - 2 * TABLE_PAD
    count = len(columns)
    weights = _weights_for(columns, rows, data.get("_col_weights"))
    size, heights = _fit_table_font(columns, rows, avail, weights)
    total = sum(heights)
    if total < avail:  # 행이 적다: 행마다 0.62 까지 늘려 카드가 비어 보이지 않게
        scale = min(avail / total, max(1.0, 0.62 / min(heights)))
        heights = [h * scale for h in heights]
        total = sum(heights)
    _card(slide, MARGIN, BODY_Y, CONTENT_W, card_h)
    table_w = CONTENT_W - 2 * TABLE_PAD
    top = BODY_Y + TABLE_PAD + max(0.0, (avail - total) / 2)
    frame = slide.shapes.add_table(len(rows) + 1, count, Inches(MARGIN + TABLE_PAD), Inches(top), Inches(table_w), Inches(total))
    frame.name = "표"
    table = frame.table
    table.first_row = table.horz_banding = False
    style_id = frame._element.find(f".//{{{_A}}}tableStyleId")
    if style_id is not None:
        style_id.text = _NO_STYLE_TABLE
    for c in range(count):
        table.columns[c].width = Emu(int(Inches(table_w) * weights[c]))
    for r in range(len(rows) + 1):
        table.rows[r].height = Inches(heights[r])
        cells = columns if r == 0 else rows[r - 1]
        cells = list(cells) if isinstance(cells, (list, tuple)) else [cells]
        for c in range(count):
            cell = table.cell(r, c)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = cell.margin_right = Inches(0.12)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            if r == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = SURFACE2
            else:
                cell.fill.background()
            _cell_border(cell, bottom=0 < r < len(rows))
            tf = cell.text_frame
            tf.word_wrap = True
            _write(tf.paragraphs[0], cells[c] if c < len(cells) else "", size, bold=r == 0, color=INK if r == 0 else TEXT)


def _draw_steps(slide, data: dict, number: int, deck_title: str):
    """단계: 윗줄에 번호 원과 화살표(흐름), 아랫줄에 단계 카드. 면을 채운 카드는 아랫줄 한 층뿐이다."""
    steps = [s for s in _list(data, "steps") if isinstance(s, dict)]
    _header(slide, data, number, deck_title)
    bottom = _body_bottom(slide, data)
    n = len(steps)
    width = _step_width(n)
    card_y = BODY_Y + STEP_TRACK_H
    card_h = bottom - card_y
    inner_h = card_h - 2 * CARD_PAD_V
    name_pt, detail_pt, ok = _steps_fit(steps, inner_h)
    dot_y = BODY_Y + 0.04
    pad = 0.22
    for i, step in enumerate(steps):
        x = MARGIN + i * (width + GAP_X)
        dot = _rect(slide, x + pad - 0.02, dot_y, STEP_DOT, STEP_DOT, BRAND, shape=MSO_SHAPE.OVAL, name="번호 원")
        tf = _frame(dot.text_frame)
        tf.word_wrap = False
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        _write(p, str(i + 1), 11, bold=True, color=WHITE)
        if i < n - 1:  # 화살표는 다음 번호 원 바로 앞에서 멈춘다
            _line(slide, x + pad + STEP_DOT + 0.1, dot_y + STEP_DOT / 2, x + width + GAP_X + pad - 0.14, dot_y + STEP_DOT / 2, BRAND_T3, 1.0, arrow=True, name="흐름 화살표")
        _card(slide, x, card_y, width, card_h, radius=RADIUS if n <= 3 else RADIUS_SMALL)
        box = _textbox(slide, x + pad, card_y + CARD_PAD_V, width - 2 * pad, inner_h, name="단계 글")
        tf = box.text_frame
        p = tf.paragraphs[0]
        p.space_after = Pt(8)
        _write(p, step.get("name"), name_pt, bold=True, color=INK)
        detail = _clean(step.get("detail"))
        if detail:
            p = tf.add_paragraph()
            p.line_spacing = BODY_LS
            _write(p, detail, detail_pt, color=TEXT)
        if not ok:
            text_w = width - 2 * pad
            need = _est_lines(step.get("name"), name_pt, text_w) * _line_h(name_pt, 1.0) + 0.12 + _est_lines(detail, detail_pt, text_w) * _line_h(detail_pt)
            if need > inner_h:
                _shrink(tf, inner_h / need)


def mirror_folders() -> tuple[str, ...]:
    """지식 사본의 맨 위 폴더 이름들 (옛 형식 포함). 예: solutions, opportunities"""
    names = {ontology.mirror_folder(t.code) for t in ontology.TYPES.values() if t.mirror_path or t.mirror_dir_format}
    return tuple(sorted(names | set(ontology.legacy_mirror_folders())))


# 고객용 파일에 남으면 안 되는 내부 표시: 지식 사본 경로 모양 (`opportunities/2.md`, `solutions/3/card.md`, `customers/12`).
# 폴더 이름은 온톨로지에서 읽고(대소문자 무시), 폴더 뒤에 `/<번호…>`(구분자는 `/` `\` `／`, `//`·`/./` 도 한 구분자) 가 오는 낱말을 본다.
# 먼저 글을 NFKC 로 맞추고(전각 글자·`／`), 보이지 않는 글자(유니코드 분류 Cf 전부·변형 선택자·`͏`·한글 채움 글자)를 지우고, 슬래시를 닮은 글자(`∕ ⁄ ⧸ ╱`)와 역슬래시를 닮은 글자(`∖ ⧵`),
# 개체 참조(`&#47;`·`&#047;`·`&#x02F;`·`&#47`·`&sol;`·`&#92;`·`&#x5c;`·`&bsol;`·`&amp;#47;`)를 구분자로, `%2F`·`%5C`(두 번 이상 부호화한 `%252F`·`%25252F` 포함)를 구분자로 되돌린 뒤 본다. 지우는 쪽으로 기운다:
# - 번호 바로 뒤: 수량 단위만 남긴다. 센 단위(`개사 개 건 종 명 곳 년 월 일 회 배 억 천 원 단계 시간 …`, `KB MB GB Mbps Gbps`·`K`)는 낱말 끝이거나 조사·접미(`가 이 을 를 의 에 에서 에는 로 와 도 만 까지 간 씩 급 …`)가 붙어도 수량이다
#   (`partners/2개사가`, `customers/100명의`, `solutions/3개를`, `projects/6개월의`, `research/2026년에는`, `solutions/100MB`). 약한 단위(`만 인 대 위 등 분 차 조 % G D M x` 서수)는 낱말 끝일 때만 수량이고 한 글자 조사 단위(`만 인 등`)가 홀로 오면 경로다.
#   그 밖의 글자(조사 `에서 를 의 과`, `번`·`호`, `_v2`, `a`, `조차`, `%a`, `%의`)가 붙으면 경로로 본다. 단위 뒤에 확장자(`.md`)가 오면 단위가 아니다.
# - 폴더 앞 머리말(공백 앞까지)에 `knowledge/` 마디가 있으면 무엇이 앞에 붙어도(`file:///`, `data.bak/`, `%APPDATA%\`) 사본 경로다. `https://`·`http://` 주소만 예외.
# - 머리말이 비었거나 `근거=`·`→`·`※`·`*`·`#`·따옴표·괄호·한글 같은 표지로 끝나면 사본 경로다. `출처:/`·`./`·`../`·`/`·`C:/`·`~/`·`$VAR/`·`%VAR%\`·`\\서버\`·`file://` 로 시작하는
#   경로도 그렇다 (이런 닻 있는 머리말에는 주소 판정을 하지 않는다: `~/backup.old/`, `C:/Users/kim.cs/`).
# - 닻 없는 머리말은 첫 마디만 주소로 본다(`www.acme.co.kr/customers/12`, `x.com/…`, `192.168.0.10/…`, `localhost:8443/…`: 남김). 첫 마디가 일반 폴더 이름이면
#   `intranet/solutions/3` 처럼 확장자가 없을 때는 남기고, 번호 뒤에 확장자(1~10자: `.md`·`.markdown`)나 `/card.md` 꼴이 오면(`data/solutions/3.md`) 파일 경로로 보고 지운다. 머리말에 `..`·`.` 마디가 끼면(`solutions/../solutions/3`) 지운다.
# - 낱말(공백 없는 덩어리) 하나를 한 번만 훑어 머리말·꼬리 사실을 모은 뒤 후보마다 이진 탐색으로 묻는다: 낱말이 아무리 길어도 선형이고 길이 한도가 없다.
# - 영문·숫자에 붙은 것(`a-solutions/3`)은 남긴다.
# - 번호 뒤가 띄어쓰기인 낱말(`solutions/3 단계로 구성`, `research/2026 보고서 인용`, `activities/2026 계획`)은 맨 경로와 구별할 수 없어 지운다 (안전한 쪽).
_SEP = r"[/\\／]"
_FOLDER_AT = re.compile(r"(?:" + "|".join(map(re.escape, mirror_folders())) + rf"){_SEP}(?:\.?{_SEP})*(?:undated{_SEP}(?:\.?{_SEP})*)?(\d+(?:,\d{{3}})*)", re.I)  # `activities/undated/12.md`(날짜 없는 활동의 사본 모양)도 번호 앞에 둔다  # `customers/1,200명` 의 천 단위 쉼표도 번호의 일부
_KNOWLEDGE_SEG = re.compile(rf"(?<![\w.\-])knowledge{_SEP}", re.I)
_URL_HEAD = re.compile(r"https?://", re.I)
_FILE_HEAD = re.compile(r"(?<![\w])file:[/\\]{2,3}", re.I)
_HOST_SEG = re.compile(r"^(?:[\w\-]+(?:\.[\w\-]+)*\.[A-Za-z]{2,}|\d{1,3}(?:\.\d{1,3}){3}|[\w.\-]+:\d+)$")
_JUNK_CH = re.compile(r"[=;,·→※*#‘’“”「」『』…(\[{<|\"'`]")
_LABEL_LEAD = re.compile(r"^(?![A-Za-z]:(?:" + _SEP + r"|$))(?![\w.\-]+:\d+(?:" + _SEP + r"|$))[^/\\／]*?:")
_SEP_CH = re.compile(_SEP)
_DOT_SEG = re.compile(rf"(?<={_SEP})\.{{1,2}}(?={_SEP})")
_EXT = re.compile(r"\.[A-Za-z0-9]{1,10}(?![A-Za-z0-9])")  # `.md`·`.json`·`.markdown`: 파일 확장자 꼴
_TOKEN = re.compile(r"\S+")
# 슬래시·역슬래시를 닮은 글자와 보이지 않는 글자: 글을 맞출 때 슬래시로 되돌리거나 지운다.
# 지우는 것: 서식 글자 전부(유니코드 분류 Cf: 영폭 글자·방향 표지·`⁡`·`؜`·`᠎`·태그 글자 …), 변형 선택자(`️`), 결합 낱말 이음 글자(`͏`), 한글 채움 글자.
_SLASH_LIKE = re.compile("[∕⁄⧸╱⟋⫽⹊∖⧵⧹╲⸺⫻⧷]")
_INVISIBLE = re.compile(
    "[­͏؀-؅؜۝܏࢐࢑࣢ᅟᅠ឴឵᠋-᠏​-‏‪-‮⁠-⁯"
    "ㅤ︀-️﻿ﾠ￹-￻\U000110bd\U000110cd\U00013430-\U0001343f\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0001\U000e0020-\U000e007f\U000e0100-\U000e01ef]"
)
_ENTITY_AMP = re.compile(r"&(?:amp;)+", re.I)
_SLASH_ENTITY = re.compile(r"&(?:#0*47(?!\d)|#x0*2f(?![0-9a-f])|sol(?![A-Za-z0-9]));?|&(?:#0*92(?!\d)|#x0*5c(?![0-9a-f])|bsol(?![A-Za-z0-9]));?", re.I)
_ENC_RUN = re.compile(r"%(?:25)+", re.I)
_DIGIT_ENTITY = re.compile(r"&(?:#0*(?P<dec>4[89]|5[0-7])(?!\d)|#[xX]0*3(?P<hex>[0-9])(?![0-9a-fA-F]));?")  # `&#51;`·`&#x33;` = 3
_DIGIT_PERCENT = re.compile(r"%3(?P<d>[0-9])")  # `%33` = 3
# 번호 뒤에 붙어도 낱말이 아닌 단위: `3.5배`·`3~5종`·`12%`·`5G`·`3rd`·`3단계`·`24시간`·`30여곳`·`100MB`·`10Gbps`.
# 센 단위(`_UNIT_STRONG`: 개사 개 건 종 명 곳 년 월 일 회 배 억 천 원 단계 시간 …, 영문 `KB MB GB Mbps Gbps`·`K`)는 낱말 끝이거나 뒤에 조사·접미(`가 이 을 를 의 에 에서 에는 로 와 도 만 까지 간 씩 급 …`)가 붙어도 수량이다:
# `partners/2개사가`·`customers/100명의`·`solutions/3개를`·`projects/6개월의`·`research/2026년에는`·`solutions/100MB`. 약한 단위(`_UNIT_WEAK`: 만 인 대 위 등 분 차 조 `%` `G D M x` 서수)는 낱말 끝일 때만 수량이다
# (`solutions/3대로`·`3조차`·`3%a`·`3%의`·`3만이` 는 경로). 약한 단위 뒤에 다른 단위를 더 잇지 않는다(`조`+`차` 가 `조차` 로 읽히지 않게). 예외: 센 단위 앞의 `만`(`1만명`, `3천만원`).
# 조사와 같은 글자인 한 글자 단위(`만 인 등`)가 낱말 끝에 홀로 오면(`solutions/3만 참고`) 수량인지 가를 수 없어 경로로 지운다. 단위 뒤에 확장자가 오면(`3개.md`) 경로다.
_UNIT_WORDS = r"개사|개월|개소|개씩|가지|인당|단계|분기|시간|세대|차년도|년차|배속|여곳|여개사|여건|여명|억원대|억대|주차|일차|회차"
_UNIT_ENG = r"(?:[KMGT]?[Bb]ps|[KMGT]B|[kKmMgGtT]b|[kKmMgG]?Hz)"
_UNIT_STRONG = r"(?:" + _UNIT_WORDS + "|" + _UNIT_ENG + r"|[개건종명곳억천백원년월일회배점사社주Kk])"
_UNIT_WEAK = r"(?:[만인대위등분차조%GDMxX]|(?i:st|nd|rd|th))"
_PARTICLE = (
    r"(?:에서는|에서도|에서|에는|에도|에게|까지|부터|이나|으로|이며|이고|이다|입니다|인데|이라고|라고|처럼|보다|마다|만에|밖에|이상|이하|이내|미만|정도|짜리"
    r"|쯤|씩|간|급|째|별|이|가|은|는|을|를|의|에|로|와|과|도|만|나|며|고|인|당)"
)
# 다른 단위와 이어 쓰지 않는 단위(`3안`·`3편`·`3자 협력`·`2중화`·`3MM`·`24x7`): 낱말 끝이거나 조사가 붙을 때만
_UNIT_LONE = r"(?:안|편|자|중화|중|MM|[xX×]\d+)"
_UNIT_TAIL = re.compile(
    r"^(?:[.~\-–]\d+)*(?:(?P<solo>" + _UNIT_WEAK + r")(?!\w)|(?P<lone>" + _UNIT_LONE + r")(?:(?!\w)|" + _PARTICLE + r"(?!\w))"
    r"|(?P<chain>(?:만(?=" + _UNIT_STRONG + r")|" + _UNIT_STRONG + r")+)(?:(?!\w)|(?P<particle>" + _PARTICLE + r")(?!\w)))"
)
_LONE_PARTICLE_UNITS = frozenset("만인등")
_SCAN_CAP = 300  # 번호 뒤 단위 줄을 이만큼까지만 본다 (그보다 긴 단위 줄은 단위로 보지 않는다)


def _normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = _INVISIBLE.sub("", t)
    t = _SLASH_LIKE.sub("/", _SLASH_ENTITY.sub("/", _ENTITY_AMP.sub("&", t)))
    # 여러 번 부호화(`%252F`, `%25252F` …): `%25…25` 덩어리가 `2f`·`5c` 앞이면 `%` 하나로. 덩어리를 한 번씩만 훑는다(선형).
    t = _ENC_RUN.sub(lambda m: "%" if t[m.end():m.end() + 2].lower() in ("2f", "5c") else m.group(0), t)
    t = re.sub(r"%2f", "/", re.sub(r"%5c", lambda _m: chr(92), t, flags=re.I), flags=re.I)
    t = _DIGIT_ENTITY.sub(lambda m: chr(int(m.group("dec"))) if m.group("dec") else m.group("hex"), t)
    return _DIGIT_PERCENT.sub(lambda m: m.group("d"), t)


def _unit_tail_len(rest: str) -> int | None:
    """번호 바로 뒤 `rest` 가 수량 단위(와 조사)로 시작하면 그 길이, 아니면 None."""
    m = _UNIT_TAIL.match(rest[:_SCAN_CAP])
    if not m or m.end() >= _SCAN_CAP:
        return None
    if m.group("solo") in _LONE_PARTICLE_UNITS:
        return None
    return m.end()


class _Token:
    """공백 없는 한 낱말과, 그 안의 여러 사본 경로 후보가 함께 쓰는 앞·뒤 사실. 낱말을 한 번만 훑어 두고 후보마다 이진 탐색으로 묻는다 (후보가 많아도 선형)."""

    def __init__(self, tok: str):
        self.tok = tok
        self.url_end = next((m.end() for m in _URL_HEAD.finditer(tok)), len(tok) + 1)
        self.knowledge_end = next((m.end() for m in _KNOWLEDGE_SEG.finditer(tok)), len(tok) + 1)
        self.file_end = next((m.end() for m in _FILE_HEAD.finditer(tok)), len(tok) + 1)
        i = tok.find("://")
        self.scheme_end = i + 3 if i >= 0 else len(tok) + 1
        self.junk = [m.start() for m in _JUNK_CH.finditer(tok)]
        self.seps = [m.start() for m in _SEP_CH.finditer(tok)]
        self.dots = [m.start() for m in _DOT_SEG.finditer(tok)]
        self.exts = [m.start() for m in _EXT.finditer(tok)]
        self._first: dict[int, str] = {}

    def has_ext_from(self, pos: int) -> bool:
        return bisect.bisect_left(self.exts, pos) < len(self.exts)

    def file_tail(self, e: int) -> bool:
        """`tok[e:]` 가 바로 확장자(`.md`)이거나, 구분자 뒤 마디에 확장자가 있는 파일 꼴(`/card.md`)인가. 같은 마디의 먼 곳 `(kt.com)` 은 보지 않는다."""
        tok = self.tok
        return _EXT.match(tok, e) is not None or (e < len(tok) and _SEP_CH.match(tok[e]) is not None and self.has_ext_from(e + 1))

    def first_segment(self, j: int) -> str:
        """머리말(`tok[j:]`, 마지막 표지 글자 뒤) 의 첫 마디. 이름표(`출처:`)는 뗀다."""
        if j not in self._first:
            k = bisect.bisect_left(self.seps, j)
            head = _LABEL_LEAD.sub("", self.tok[j:self.seps[k] + 1])
            self._first[j] = re.split(_SEP, head)[0]
        return self._first[j]

    def is_path(self, start: int, end: int) -> bool:
        """`tok[start:end]` 가 폴더/번호일 때, 앞 머리말과 뒤 꼬리를 보고 사본 경로인지 가린다."""
        tok = self.tok
        # 번호 바로 뒤에 단위가 붙었으면 수량이다 (`partners/2개사`, `solutions/5G/LTE`, `customers/12%`). 단위 뒤에 확장자가 오면 경로다.
        n = _unit_tail_len(tok[end:end + _SCAN_CAP])
        if self.url_end <= start:
            return False
        if self.knowledge_end <= start:
            return True  # 사본 폴더 안에서는 단위가 붙어도 수량이 아니다 (`knowledge/solutions/3일`)
        if n is not None and not self.file_tail(end + n):
            return False
        if self.file_end <= start:
            return True  # `file://` 은 닻이다 (호스트 이름이 올 수 있어도 내 컴퓨터의 사본)
        if self.scheme_end <= start:
            return False
        if start == 0:
            return True
        last = tok[start - 1]
        if re.match(r"[A-Za-z0-9_]", last):
            return False  # `a-solutions/3`, `mysolutions/3`: 낱말에 붙은 것
        if last == "-" and start >= 2 and re.match(r"[A-Za-z0-9_]", tok[start - 2]):
            return False
        if not _SEP_CH.match(last):
            return True  # 표지(`근거=`, `→`, `※`, 따옴표, 한글 …)로 끝남
        li = bisect.bisect_left(self.junk, start) - 1
        j = self.junk[li] + 1 if li >= 0 else 0
        first = self.first_segment(j)
        anchored = first in ("", ".", "..", "~") or first[0] in "$%" or re.match(r"^[A-Za-z]:$", first) is not None
        if anchored:
            return True
        di = bisect.bisect_left(self.dots, j)
        if di < len(self.dots) and self.dots[di] < start:
            return True  # `intranet/../solutions/3`: 위로 가는 마디가 끼었다
        if _HOST_SEG.match(first):
            return False  # 주소 (닻 없는 머리말의 첫 마디만 본다)
        # 일반 폴더 이름(`intranet/`): 번호만 있으면 남기고 `3.md`·`3/card.md` 면 파일 경로
        return self.file_tail(end + (n or 0))  # 단위 뒤의 확장자(`3개.md`)·`/card.md` 도 파일 경로다


# 번호 없는 사본·데이터 파일 (베타 K, L): 앱이 쓰는 이름은 `knowledge/solutions/<번호>/card.md` 와 `data/app.db` 다. `knowledge/index.md`·`data/solbox.db` 는 앱이 쓰지 않지만 사본·DB 파일처럼 보이는 이름이라 같이 뺀다 (둘 다 일반 글과 겹칠 일이 드물다).
# `docs/knowledge/guide.md`·`Obsidian vault(knowledge/notes.md)`·`kt.com/data/app.db`·`/var/lib/app.db`·`node_modules/app.db` 같은 일반 글은 둔다.
# 파일 이름 뒤에는 영문·숫자·`_` 만 아니면 된다 (`knowledge/index.md의 목록`·`data/app.db를 백업` 의 한글 조사).
_MIRROR_FILE = re.compile(
    rf"(?<![\w.\-])knowledge{_SEP}(?:(?:" + "|".join(map(re.escape, mirror_folders())) + rf"){_SEP})?(?:index|card)\.md(?![A-Za-z0-9_])", re.I)
_DB_FILE = re.compile(rf"(?<![\w.\-])data{_SEP}(?:app|solbox)\.db(?![A-Za-z0-9_])", re.I)
# 데이터 폴더의 그 밖의 모양 (앱이 쓰는 것만): 작업 폴더(`intake_work/<번호>/…`·`research_work/…`), 올린 파일(`uploads/solutions|intake/<번호>/…`), 제안서 파일(`decks/<번호>/deck-<날짜>-<시각>-<6자>[-internal].pptx`),
# 백업(`backups/<날짜>-<시각>[.partial]/…`), 로그(`logs/app.log[.N]`), 옛 DB 사본(`app.db.before-migrate-…`). 일반 낱말(`models/`·`logs/`·`uploads/` 만 있는 글)은 둔다.
_DATA_SHAPE = re.compile(
    rf"(?<![\w.\-])(?:(?:intake_work|research_work){_SEP}|uploads{_SEP}(?:solutions|intake){_SEP}[^\s/\\／]|decks{_SEP}\d+{_SEP}"
    rf"|backups{_SEP}\d{{8}}-\d{{6}}(?![A-Za-z0-9_])|logs{_SEP}app\.log(?:\.\d+)?(?![A-Za-z0-9_])"
    rf"|data{_SEP}(?:uploads{_SEP}(?:solutions|intake)|decks{_SEP}\d+)(?![A-Za-z0-9_]))"  # 베타 M: `data/` 가 붙은 이 둘은 끝에서 끝나도(`data/decks/7`·`data/uploads/solutions`) 앱의 폴더다
    r"|(?<![\w.\-])deck-\d{8}-\d{6}-[0-9a-f]{6}(?:-internal)?\.pptx(?![A-Za-z0-9_])", re.I)
# `data/models/` 는 앱의 모델 폴더 이름(`MODEL_NAMES`·HF 캐시 `models--…`·`.hf_home`)이 이어질 때만 (`open data/models/API 제공`·`MLOps: data/models/serving` 은 고객 글).
_DATA_MODELS = re.compile(
    rf"(?<![\w.\-])data{_SEP}models{_SEP}(?:\.hf_home|models--|(?:tiny|base|small|medium|large-v3-turbo|large-v3)(?![A-Za-z0-9_]))", re.I)
# 그 밖의 `data/<폴더>/` 는 머리말에 `solbox` 마디가 있을 때만 (`solbox/data/logs/x`; 닻 있는 `D:\data\logs\`·`/data/models/` 는 고객 서버 글일 수 있다)
_DATA_SUBDIR = re.compile(rf"(?<![\w.\-])data{_SEP}(?:logs|models|uploads|decks|backups|intake_work|research_work){_SEP}", re.I)
# 앱이 옛 DB 를 옮기기 전에 만드는 사본 (`app.db.before-migrate-<날짜>-<시각>[-N]`, migrate.py)
_DB_BEFORE_MIGRATE = re.compile(r"(?<![\w.\-])app\.db\.before-migrate-\d{8}-\d{6}(?:-\d+)?(?![A-Za-z0-9_])", re.I)
# 주소: `http(s)://` 와, 실제 원격·저장소 주소 모양인 `scheme://…` (아래 `_URL_SCHEMES` 허용 목록). 한 글자 `C://`·`x://`·`sqlite`·`file`·모르는 낱말(`path://`·`note://`)은 주소가 아니라 경로 흉내다 (베타 M).
# 주소의 끝은 `,`·`;`·한글·공백에서 끝난다 (`https://kt.com,knowledge/index.md` 의 파일은 주소 밖)
_URL_SCHEMES = (r"s3a?|gs|hdfs|abfss?|wasbs?|ftps?|sftp|smb|nfs|jdbc|mqtts?|wss?|rtsp|rtmp|ssh|git|redis|rediss|amqps?|kafka|mongodb(?:\+srv)?|postgres(?:ql)?|mysql|mariadb|oracle|tcp|udp|opc\.tcp"
                r"|modbus(?:\+tcp)?|coaps?|nats|cassandra|clickhouse|elasticsearch|dbfs|webhdfs|ldaps?|imaps?|smtps?|pop3s?|rtsps")
_URL_SPAN = re.compile(
    r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+%=]*|(?<![A-Za-z0-9+.\-])(?:" + _URL_SCHEMES + r")://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+%=]*", re.I)
_DB_LABEL_CUT = re.compile(r"[=;,·→※*#‘’“”「」『』…(\[{<|\"'`]")
_DB_ANCHORED = re.compile(r"^(?:[/\\~$%]|[A-Za-z]:[/\\]|file:)", re.I)


def _url_spans(tok: str) -> tuple[list[int], list[int]]:
    spans = [(u.start(), u.end()) for u in _URL_SPAN.finditer(tok)]
    return [a for a, _ in spans], [b for _, b in spans]


def _in_url(spans: tuple[list[int], list[int]], pos: int) -> bool:
    """`pos` 가 주소 안인가 (낱말마다 주소 범위를 한 번만 구해 이진 탐색: 같은 파일 이름이 아주 많이 이어져도 선형)."""
    starts, ends = spans
    i = bisect.bisect_right(starts, pos) - 1
    return i >= 0 and pos < ends[i]


def _db_file_is_ours(tok: str, m: re.Match) -> bool:
    """`data/app.db` 앞 머리말이 없거나, 닻 있는 경로(`C:\\`·`/srv/`·`~/`)이거나 `solbox` 마디가 있으면 우리 파일이다 (`R&D/data/app.db`·`kt.com/data/app.db` 는 아니다)."""
    head = _DB_LABEL_CUT.split(tok[max(0, m.start() - 300):m.start()])[-1]  # 앞 300자까지만 본다 (같은 낱말에 후보가 많아도 선형)
    head = _LABEL_LEAD.sub("", head) if _LABEL_LEAD.match(head) else head
    return head == "" or _DB_ANCHORED.match(head) is not None or "solbox" in head.lower()


def _head_has_solbox(tok: str, m: re.Match) -> bool:
    head = _DB_LABEL_CUT.split(tok[max(0, m.start() - 300):m.start()])[-1]
    return "solbox" in head.lower()


def _has_mirror_file(t: str) -> bool:
    for tm in _TOKEN.finditer(t):
        tok = tm.group()
        if "." not in tok and "_" not in tok and "/" not in tok and "\\" not in tok and "／" not in tok:
            continue
        spans = None
        for m in _MIRROR_FILE.finditer(tok):
            spans = spans or _url_spans(tok)
            if not _in_url(spans, m.start()):
                return True
        for m in _DB_FILE.finditer(tok):
            spans = spans or _url_spans(tok)
            if not _in_url(spans, m.start()) and _db_file_is_ours(tok, m):
                return True
        for m in _DATA_SHAPE.finditer(tok):
            spans = spans or _url_spans(tok)
            if not _in_url(spans, m.start()):
                return True
        for m in _DATA_MODELS.finditer(tok):
            spans = spans or _url_spans(tok)
            if not _in_url(spans, m.start()):
                return True
        for m in _DB_BEFORE_MIGRATE.finditer(tok):
            spans = spans or _url_spans(tok)
            if not _in_url(spans, m.start()):
                return True
        for m in _DATA_SUBDIR.finditer(tok):
            spans = spans or _url_spans(tok)
            if not _in_url(spans, m.start()) and _head_has_solbox(tok, m):
                return True
    return False


def has_internal_mark(text) -> bool:
    """고객에게 보이면 안 되는 내부 표시(사본 경로)가 든 글인가."""
    t = _normalize(_clean(text))
    if _has_mirror_file(t):
        return True
    if not _FOLDER_AT.search(t):
        return False
    for tm in _TOKEN.finditer(t):
        tok = tm.group()
        matches = list(_FOLDER_AT.finditer(tok))
        if not matches:
            continue
        info = _Token(tok)
        if any(info.is_path(m.start(), m.end()) for m in matches):
            return True
    return False


def _scrub_customer(slide: dict, stats: dict | None = None) -> dict:
    """고객용 장에서 내부 경로가 든 줄을 뺀다 (구성안 정리가 이미 막지만, 손으로 넣은 글까지 한 번 더 막는 마지막 안전망).
    뺀 줄 수는 `stats["removed"]` 에 더한다."""
    removed = 0
    out = copy.deepcopy(slide)
    for key in ("bullets", "left", "right"):
        items = _list(out, key)
        out[key] = [i for i in items if not has_internal_mark(i)]
        removed += len(items) - len(out[key])
    columns = _list(out, "columns") if out.get("kind") == "table" else []  # 그리지 않는 장의 columns 는 세지도 않는다
    if columns:  # 표 머리글도 고객 눈에 보인다: 사본 경로가 든 머리글 칸은 비운다 (칸 수는 그대로 둬 열이 어긋나지 않게)
        cleaned = ["" if has_internal_mark(c) else c for c in columns]
        removed += sum(1 for a, b in zip(columns, cleaned) if a != b)
        out["columns"] = cleaned
    rows = _list(out, "rows")
    out["rows"] = [r for r in rows if not any(has_internal_mark(c) for c in (r if isinstance(r, (list, tuple)) else [r]))]
    removed += len(rows) - len(out["rows"])
    steps = _list(out, "steps")
    out["steps"] = [st for st in steps if isinstance(st, dict) and not has_internal_mark(st.get("name")) and not has_internal_mark(st.get("detail"))]
    removed += len(steps) - len(out["steps"])
    for key in ("title", "key_message", "subtitle", "left_title", "right_title"):
        if has_internal_mark(out.get(key)):
            out[key] = ""
            removed += 1
    if stats is not None:
        stats["removed"] = stats.get("removed", 0) + removed
    return out


def _with_contact(slide: dict, contact: list[str] | None) -> dict:
    """마무리 장 글머리 끝에 연락처 줄을 붙인다 (구성안에는 저장하지 않는다)."""
    lines = [_clean(c).strip() for c in contact or [] if _clean(c).strip()]
    if slide.get("kind") != "closing" or not lines:
        return slide
    return {**slide, "bullets": [*_list(slide, "bullets"), *lines]}


def _notes(slide, data: dict, source_labels: dict[str, str] | None = None):
    labels = source_labels or {}
    sources = [_clean(labels.get(s, s) if isinstance(s, str) else s) for s in _list(data, "sources") if _clean(s).strip()]
    checks = [_clean(s) for s in _list(data, "needs_check") if _clean(s).strip()]
    done = {_clean(s) for s in _list(data, "checked")}
    blocks = []
    if sources:
        blocks.append("\n".join(["[출처]", *sources]))
    if [c for c in checks if c not in done]:
        blocks.append("\n".join([f"[{ontology.term('check_item')}]", *[c for c in checks if c not in done]]))
    if [c for c in checks if c in done]:
        blocks.append("\n".join(["[확인함]", *[c for c in checks if c in done]]))
    if blocks:
        slide.notes_slide.notes_text_frame.text = "\n\n".join(blocks)



def _draw_body(slide, data: dict, number: int = 1, deck_title: str = ""):
    """종류별로 그린다. 그릴 내용이 없는 장은 헤더 + 결론 띠만 남는다."""
    kind = data.get("kind")
    if kind == "two_column":
        _draw_two_column(slide, data, number, deck_title)
    elif kind == "table" and _list(data, "columns") and _list(data, "rows"):
        _draw_table(slide, data, number, deck_title)
    elif kind == "steps" and any(isinstance(s, dict) for s in _list(data, "steps")):
        _draw_steps(slide, data, number, deck_title)
    elif kind == "closing":
        _draw_closing(slide, data, number, deck_title)
    elif kind in ("table", "steps"):
        _header(slide, data, number, deck_title)
        _body_bottom(slide, data)
    else:
        _draw_bullets(slide, data, number, deck_title)


def replace_retry(src: Path, dst: Path, tries: int = 12, wait: float = 0.25) -> None:
    """`src` 를 `dst` 로 바꿔치기한다. Windows 에서는 백신·인덱서가 방금 쓴 파일을 잠깐 잡고 있어 PermissionError 가 날 수 있어
    잠시 기다렸다가 다시 한다 (약 3초까지). 그 밖의 오류나 끝까지 안 되면 그대로 올린다."""
    import time

    for attempt in range(tries):
        try:
            Path(src).replace(dst)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(wait)


def _chapter_title(outline: dict, audience: str) -> str:
    """챕터 라벨에 쓰는 제안서 제목 (한 줄, 길면 줄인다). 고객용에서 내부 경로가 든 제목은 쓰지 않는다."""
    title = " ".join(_clean((outline or {}).get("title")).split())
    if audience == "customer" and has_internal_mark(title):
        return ""
    return title if len(title) <= 44 else title[:43].rstrip() + "…"


def build_deck(
    outline: dict, out_path: Path, *, customer_name: str = "", today: date | None = None,
    source_labels: dict[str, str] | None = None, audience: str = "internal", contact: list[str] | None = None, stats: dict | None = None,
    background: Path | str | None = None,
) -> int:
    """구성안으로 .pptx 를 쓰고 만든 슬라이드 수를 돌려준다.

    `audience="customer"`(고객용)는 발표자 노트를 비워 두고 내부 경로가 든 줄도 뺀다. `"internal"`(내부 검토용)은 노트에 출처(`source_labels`:
    경로→이름이 있으면 이름)와 확인 항목을 적고, 이름을 아는 출처는 슬라이드 아래 출처 줄에도 적는다. `contact` 는 마무리 장 맨 아래에 붙는 연락처 줄(서버가 만든 사람 정보로 채운다).
    `stats` 를 주면 고객용에서 뺀 줄 수를 `stats["removed"]` 로, 품질 검사 결과를 `stats["quality"]`(목록. 검사를 못 했으면 None)로 돌려준다.
    `background` 는 모든 장 맨 아래에 까는 배경 그림 파일(설정 `deck_background`). 없거나 읽지 못하면 옅은 회색 바탕으로 만든다.
    파일은 임시 파일에 쓴 뒤 바꿔치기한다 (쓰다 멈춰도 반쪽 파일이 남지 않는다)."""
    if audience not in ("customer", "internal"):
        raise ValueError(audience)
    source = (outline or {}).get("slides")
    if not isinstance(source, list) or not source:
        raise ValueError("구성안에 슬라이드가 없습니다.")
    today = today or date.today()
    stats = stats if stats is not None else {}
    stats["removed"] = 0
    stats["quality"] = None
    slides = [_with_contact(s, contact) for s in source if isinstance(s, dict)]
    if audience == "customer":
        slides = [_scrub_customer(s, stats) for s in slides]  # 쪽을 나누기 전에 한 번: 줄 수가 쪽마다 겹쳐 세어지지 않는다
    pages = [page for s in slides for page in paginate(s)]
    if not pages:
        raise ValueError("구성안에 슬라이드가 없습니다.")
    image = load_background(background)
    deck_title = _chapter_title(outline, audience)
    prs = blank_presentation()  # 설치 폴더의 default.pptx 를 읽지 않는다 (베타 AC)
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    for number, data in enumerate(pages, start=1):
        slide = prs.slides.add_slide(blank)
        _background(slide, prs, image)
        if data.get("kind") == "cover":
            _draw_cover(slide, data, customer_name, today)
        else:
            _draw_body(slide, data, number, deck_title)
            if audience == "internal":
                _source_line(slide, data, source_labels)
            _page_number(slide, number)
        if audience == "internal":
            _notes(slide, data, source_labels)
    try:  # 품질 검사는 결과만 돌려준다: 검사가 실패해도 파일은 만든다
        from app import deck_gates

        stats["quality"] = deck_gates.check_presentation(prs, kinds=[p.get("kind") for p in pages], background_missing=bool(background) and image is None)
    except Exception:  # noqa: BLE001
        log.exception("제안서 품질 검사를 하지 못했습니다 (파일은 그대로 만듭니다)")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    try:
        prs.save(part)
        replace_retry(part, out_path)
    finally:
        part.unlink(missing_ok=True)
    return len(pages)

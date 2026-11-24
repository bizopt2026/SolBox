"""구성안(dict) → .pptx. 템플릿 없이 빈 레이아웃 위에 고정 디자인으로 직접 그린다.

입력은 `proposals.clean_outline` / `slide_from_form` 이 만드는 형태이지만, 키가 빠져 있어도 견딘다.

디자인(베타 AF)은 paperlogy-ppt 디자인 시스템의 강의·마케팅 모드를 python-pptx 로 옮긴 것이다 (제안 덱은 결재란에 사인받는 문서가 아니다):
- 색은 승인 팔레트만(클라인 블루 + 강조 오렌지 1색 + 그레이), 글꼴은 `나눔스퀘어_ac` 하나. 굵게는 굵게 속성이 아니라 `나눔스퀘어_ac Bold` 페이스로 준다.
- 세로 띠(밴드)는 장마다 같은 자리다: 챕터 라벨 0.306 → 대제목 0.851 → 본문 카드 2.20~6.05 → 결론 띠 6.20 → 출처·쪽 번호 6.85.
- 카드는 선 없이 옅은 그림자만, 모든 글은 세로 가운데 정렬, 모든 글자에 1pt·투명도 100% 윤곽선.
만든 뒤 `app.deck_gates` 가 같은 규칙으로 검사한다 (막지는 않고 결과만 돌려준다).

베타 AG: 고객에게 영업하는 자료라 글보다 그림이 먼저다. 핵심 수치(`kpi`)·그래프(`chart`, 파워포인트에서 고칠 수 있는 진짜 차트)·도식(`diagram`: 구성도·중심과 주변·도입 전후·일정·2×2·순환) 장을 더했다.
수치는 구성안에 적힌 값만 그린다 (이 파일은 숫자를 만들지 않는다). 장 제목은 마침표로 끝내지 않는다.
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

from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_MARKER_STYLE, XL_TICK_MARK
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
BRAND_T2 = RGBColor(0x4D, 0x6D, 0xC1)    # 여러 계열 그래프의 둘째 단
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
AXIS_GREY = RGBColor(0xB7, 0xBD, 0xC8)   # 그래프 축 선
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
KPI_SIZES = (56, 48, 40, 34, 28, 24)  # 핵심 수치의 큰 숫자
NOTE_PT, NOTE_H = 9, 0.30            # 그림 아래 덧붙이는 말 한 줄
BLOCK_SIZES = ((14, 11), (13, 11), (12, 10), (11, 10), (10, 9))  # 도식 안 (이름, 설명) 글자 크기

# --- 넘침 규칙 ---
MAX_BULLETS_PER_SLIDE = 10
MAX_TABLE_ROWS = 8
MAX_COLUMN_ITEMS = 8
MAX_STEPS = 6
MAX_GRID_ITEMS = 6                   # 이보다 많은 글머리는 번호 카드 대신 목록 카드로 그린다
MAX_KPIS = ontology.MAX_KPIS
MAX_CHART_SERIES = ontology.MAX_CHART_SERIES
CHART_POINTS = {"column": 10, "line": 10, "bar": 8, "doughnut": 4}  # 그래프 한 장의 항목 수 (넘으면 쪽을 나눈다)
DOUGHNUT_MAX = 4                     # 도넛은 4조각까지 (그보다 많으면 가로 막대로 그린다)
DIAGRAM_MAX = dict(ontology.DIAGRAM_MAX_NODES)

_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
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


# ---------------------------------------------------------------- 시각 자료 장의 자료 읽기 (베타 AG)

def _kpis(slide: dict) -> list[dict]:
    """핵심 수치 항목 (값이 있는 것만). 키가 빠져 있어도 견딘다."""
    items = [{k: _clean(i.get(k)).strip() for k in ("value", "unit", "label", "note")} for i in _list(slide, "kpis") if isinstance(i, dict)]
    return [i for i in items if i["value"]]


def _nodes(slide: dict) -> list[dict]:
    """도식 항목 (이름이 있는 것만)."""
    items = [{k: _clean(n.get(k)).strip() for k in ("label", "detail", "group")} for n in _list(slide, "nodes") if isinstance(n, dict)]
    return [n for n in items if n["label"]]


def _number(value):
    """그래프 값: 숫자(또는 숫자로 읽히는 글)면 그 숫자, 아니면 None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).strip().replace(",", "")) if not isinstance(value, (int, float)) else float(value)
    except ValueError:
        return None
    if not math.isfinite(number) or abs(number) >= 1e15:
        return None
    return int(number) if number == int(number) else number


def _chart_plan(slide: dict):
    """그릴 수 있는 그래프면 (그래프 종류, 항목 이름들, [(계열 이름, 값들)]) 을, 아니면 None 을 돌려준다.
    항목 수와 값 수가 다르거나 숫자가 아닌 값이 있으면 그리지 않는다 (값을 짐작해 채우지 않는다).
    도넛은 계열 하나·4조각까지이고 음수가 없어야 한다: 그 밖에는 가로 막대로 그린다."""
    categories = [_clean(c).strip() for c in _list(slide, "categories")]
    series = []
    for item in _list(slide, "series"):
        if not isinstance(item, dict) or not isinstance(item.get("values"), (list, tuple)) or not item["values"]:
            continue
        numbers = [_number(v) for v in item["values"]]
        if len(numbers) != len(categories) or None in numbers:
            return None
        series.append((_clean(item.get("name")).strip(), numbers))
    if not categories or not series:
        return None
    series = series[:MAX_CHART_SERIES]
    ctype = slide.get("chart_type") if slide.get("chart_type") in ontology.CHART_TYPES else ontology.CHART_TYPE_DEFAULT
    if ctype == "doughnut" and (len(series) > 1 or len(categories) > DOUGHNUT_MAX or min(series[0][1]) < 0 or sum(series[0][1]) <= 0):
        ctype = "bar"
    return ctype, categories, series


def _note_shift(slide: dict) -> float:
    """덧붙이는 말(`note`) 한 줄이 본문에서 차지하는 높이."""
    return NOTE_H + 0.05 if slide.get("kind") in ("kpi", "chart", "diagram") and _clean(slide.get("note")).strip() else 0.0


_BA_ARROW_W = 0.9                    # 도입 전후: 두 카드 사이 화살표 자리
_BA_W = (CONTENT_W - _BA_ARROW_W) / 2


def _ba_area(slide: dict) -> tuple[float, float]:
    """도입 전후 도식에서 한쪽 카드의 (글 폭, 글 높이)."""
    return _BA_W - 2 * CARD_PAD, BODY_H - _COLUMN_HEAD_H - CARD_PAD_V - _message_shift(slide) - _note_shift(slide)


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
    elif kind == "kpi":
        parts = [{"kpis": c} for c in _chunks([i for i in _list(slide, "kpis") if isinstance(i, dict)], MAX_KPIS)]
    elif kind == "chart":
        plan = _chart_plan(slide)
        if plan is None:
            return [copy.deepcopy(slide)]
        ctype, categories, series = plan
        size = CHART_POINTS[ctype] if len(series) <= 2 else max(4, CHART_POINTS[ctype] // 2)
        parts = [{"chart_type": ctype, "categories": categories[i:i + size], "series": [{"name": n, "values": v[i:i + size]} for n, v in series]}
                 for i in range(0, len(categories), size)]
    elif kind == "diagram" and slide.get("diagram_type") == "before_after":
        width, height = _ba_area({**slide, "key_message": "", "note": ""})
        first = shift + _note_shift(slide)
        left = _height_chunks(_list(slide, "left"), MAX_COLUMN_ITEMS, width, height, first)
        right = _height_chunks(_list(slide, "right"), MAX_COLUMN_ITEMS, width, height, first)
        parts = [{"left": left[i] if i < len(left) else [], "right": right[i] if i < len(right) else []} for i in range(max(len(left), len(right)))]
    elif kind == "diagram":
        nodes = [n for n in _list(slide, "nodes") if isinstance(n, dict)]
        parts = [{"nodes": c} for c in _chunks(nodes, DIAGRAM_MAX.get(slide.get("diagram_type"), DIAGRAM_MAX["layers"]))]
    else:
        return [copy.deepcopy(slide)]
    result = []
    for i, part in enumerate(parts):
        page = copy.deepcopy(slide)
        page.update(part)
        if i:
            page["title"] = f"{slide.get('title') or ''} ({i + 1})".strip()
            page["key_message"] = ""
            if "note" in page:
                page["note"] = ""
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
    """장 제목은 마침표로 끝내지 않는다 (사용자 결정 2026-11-24). 구성안에 마침표가 있어도 뗀다. 물음표·느낌표는 둔다."""
    return _clean(title).strip().rstrip(".。．").rstrip()


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
    title = _headline_text(data.get("title"))
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
        title = _headline_text(data.get("title"))
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


# ---------------------------------------------------------------- 그리기: 시각 자료 장 (베타 AG)

def _note_line(slide, data: dict, bottom: float) -> float:
    """그림 아래 덧붙이는 말 한 줄(기준·조건)을 쓰고, 그림이 쓸 수 있는 아래 끝 y 를 돌려준다."""
    note = _clean(data.get("note")).strip()
    if not note:
        return bottom
    y = bottom - NOTE_H
    box = _textbox(slide, MARGIN + 0.1, y, CONTENT_W - 0.2, NOTE_H, name="주석")
    _write(box.text_frame.paragraphs[0], note, NOTE_PT, color=MUTE)
    lines = _est_lines(note, NOTE_PT, CONTENT_W - 0.2)
    if lines > 1:
        _shrink(box.text_frame, 1 / lines)
    return y - 0.05


def _block_need(title: str, detail: str, title_pt: float, detail_pt: float, width: float) -> float:
    need = _est_lines(title, title_pt, width) * _line_h(title_pt, 1.0) if title else 0.0
    if detail:
        need += (0.08 if title else 0.0) + _est_lines(detail, detail_pt, width) * _line_h(detail_pt)
    return need


def _block(slide, x, y, w, h, title: str, detail: str = "", *, sizes=BLOCK_SIZES, name: str = "도식 글", color=INK, detail_color=TEXT, align=None):
    """굵은 이름 + 설명 한 덩어리를 글상자에 쓴다. 들어가는 가장 큰 글자 크기를 고르고, 가장 작은 글자로도 넘치면 비율로 줄인다."""
    title_pt, detail_pt = next((p for p in sizes if _block_need(title, detail, p[0], p[1], w) <= h), sizes[-1])
    box = _textbox(slide, x, y, w, h, name=name)
    tf = box.text_frame
    p = tf.paragraphs[0]
    if title:
        if align is not None:
            p.alignment = align
        _write(p, title, title_pt, bold=True, color=color)
    if detail:
        if title:
            p.space_after = Pt(5)
            p = tf.add_paragraph()
        if align is not None:
            p.alignment = align
        p.line_spacing = BODY_LS
        _write(p, detail, detail_pt, color=detail_color)
    need = _block_need(title, detail, title_pt, detail_pt, w)
    if need > h:
        _shrink(tf, h / need)
    return box


def _shape_text(shape, text: str, sizes, *, color=WHITE, bold: bool = True, pad: float = 0.08, inner: float = 1.0):
    """도형 안 가운데에 글 한 덩어리를 쓴다 (들어가는 가장 큰 크기, 넘치면 줄인다). 원은 글이 놓이는 자리가 안쪽 네모(`inner=0.7`)다."""
    w = shape.width / 914400 * inner - 2 * pad
    h = shape.height / 914400 * inner - 0.08
    tf = _frame(shape.text_frame, pad_x=pad, pad_y=0.04)
    size = next((pt for pt in sizes if _est_lines(text, pt, w) * _line_h(pt, 1.0) <= h), sizes[-1])
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    _write(p, text, size, bold=bold, color=color)
    need = _est_lines(text, size, w) * _line_h(size, 1.0)
    if need > h:
        _shrink(tf, h / need)


def _text_em(text: str) -> float:
    return sum(_char_em(ch) for ch in text)


def _draw_kpi(slide, data: dict, number: int, deck_title: str):
    """핵심 수치: 카드 2~4장에 큰 숫자(브랜드 딥) + 단위, 그 아래 이름과 한 줄 설명. 숫자는 구성안에 적힌 글 그대로다."""
    items = _kpis(data)[:MAX_KPIS]
    _header(slide, data, number, deck_title)
    bottom = _note_line(slide, data, _body_bottom(slide, data))
    if not items:
        return
    n = len(items)
    card_w = (CONTENT_W - GAP_X * (n - 1)) / n
    card_h = bottom - BODY_Y
    text_w = card_w - 2 * CARD_PAD
    top_pad = 0.55
    inner_h = card_h - top_pad - CARD_PAD_V
    for i, item in enumerate(items):
        x = MARGIN + i * (card_w + GAP_X)
        _card(slide, x, BODY_Y, card_w, card_h, radius=RADIUS if n <= 3 else RADIUS_SMALL)
        _rect(slide, x + CARD_PAD, BODY_Y + 0.32, 0.46, 0.055, ACCENT, name="강조 막대")
        value, unit, label, note = item["value"], item["unit"], item["label"], item["note"]

        def need(size, label_pt, note_pt):
            unit_pt = max(14, round(size * 0.42))
            lines = max(1, math.ceil((_text_em(value) * size + (_text_em(unit) * unit_pt + 4 if unit else 0)) / 72 / text_w))
            total = lines * _line_h(size, 1.0)
            if label:
                total += 0.12 + _est_lines(label, label_pt, text_w) * _line_h(label_pt, 1.0)
            if note:
                total += 0.07 + _est_lines(note, note_pt, text_w) * _line_h(note_pt)
            return total

        options = [(size, lp, np_) for size in KPI_SIZES for lp, np_ in ((14, 11),)] + [(KPI_SIZES[-1], 12, 10), (KPI_SIZES[-1], 11, 9)]
        size, label_pt, note_pt = next((o for o in options if need(*o) <= inner_h and (_text_em(value) * o[0] + _text_em(unit) * max(14, round(o[0] * 0.42))) / 72 <= text_w), None) \
            or next((o for o in options if need(*o) <= inner_h), options[-1])
        box = _textbox(slide, x + CARD_PAD, BODY_Y + top_pad, text_w, inner_h, name="핵심 수치")
        tf = box.text_frame
        p = tf.paragraphs[0]
        _write(p, value, size, bold=True, color=BRAND_DEEP, spacing=-1 if size >= 40 else 0)
        if unit:
            _write(p, " " + unit, max(14, round(size * 0.42)), bold=True, color=TEXT)
        if label:
            p = tf.add_paragraph()
            p.space_before = Pt(8)
            _write(p, label, label_pt, bold=True, color=INK)
        if note:
            p = tf.add_paragraph()
            p.space_before = Pt(5)
            p.line_spacing = BODY_LS
            _write(p, note, note_pt, color=MUTE)
        if need(size, label_pt, note_pt) > inner_h:
            _shrink(tf, inner_h / need(size, label_pt, note_pt))


def _number_text(value) -> str:
    """값을 글로: 정수는 천 단위 쉼표, 소수는 둘째 자리까지 (뒤의 0 은 뗀다)."""
    if isinstance(value, int) or float(value) == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _number_format(series: list, unit: str = "") -> str:
    """그래프의 숫자 서식: 값이 모두 정수면 `#,##0`, 소수가 있으면 둘째 자리까지. 단위는 서식 안에 글자로 붙인다."""
    whole = all(float(v) == int(v) for _, values in series for v in values)
    unit = unit.replace('"', "").replace("\\", "")
    return ("#,##0" if whole else "#,##0.0#") + (f'"{unit}"' if unit else "")


def _series_colors(count: int) -> list:
    """계열 색 위계: 하나면 브랜드, 둘이면 비교군 회색 + 브랜드, 셋 이상이면 같은 블루의 명도(진 → 연). 무지개색을 쓰지 않는다."""
    if count <= 1:
        return [BRAND]
    if count == 2:
        return [MUTE, BRAND]
    return [BRAND_DEEP, BRAND, BRAND_T2, BRAND_T3][:count]


def _doughnut_colors(count: int) -> list:
    """도넛 조각 색: 둘이면 회색 + 브랜드, 셋·넷이면 진한 블루부터. 조각 위 흰 글자가 읽히도록 옅은 틴트는 쓰지 않는다."""
    return [MUTE, BRAND] if count == 2 else [BRAND_DEEP, BRAND, BRAND_T2, MUTE][:max(count, 1)]


def _chart_font(font, size: float, face: str, color):
    font.size = Pt(size)
    font.name = face
    font.bold = False  # 굵기는 페이스로
    font.color.rgb = color


def _chart_clean(chart):
    """그래프 공통 정리: 바깥 상자·그림 영역 테두리 없음(흰 면), 둥근 모서리 끔, 글꼴은 한글 자리(ea·cs)에도 같은 페이스."""
    space = chart._chartSpace
    for rpr in space.iter(f"{{{_A}}}defRPr"):
        latin = rpr.find(f"{{{_A}}}latin")
        if latin is None:
            continue
        for tag in ("ea", "cs"):
            for old in rpr.findall(f"{{{_A}}}{tag}"):
                rpr.remove(old)
        ea = etree.Element(f"{{{_A}}}ea")
        ea.set("typeface", latin.get("typeface"))
        cs = etree.Element(f"{{{_A}}}cs")
        cs.set("typeface", latin.get("typeface"))
        latin.addnext(ea)
        ea.addnext(cs)
        for old in rpr.findall(f"{{{_A}}}ln"):
            rpr.remove(old)
        fill = rpr.find(f"{{{_A}}}solidFill/{{{_A}}}srgbClr")
        if fill is not None and fill.get("val", "").upper() != str(WHITE):  # 글자 윤곽선 1pt·투명도 100% (흰 글자는 `_style_run` 과 같은 이유로 뺀다)
            ln = etree.Element(f"{{{_A}}}ln")
            ln.set("w", "12700")
            clr = etree.SubElement(etree.SubElement(ln, f"{{{_A}}}solidFill"), f"{{{_A}}}srgbClr")
            clr.set("val", fill.get("val"))
            etree.SubElement(clr, f"{{{_A}}}alpha").set("val", "0")
            rpr.insert(0, ln)

    def plain():
        sp = etree.Element(f"{{{_C}}}spPr")
        etree.SubElement(etree.SubElement(sp, f"{{{_A}}}solidFill"), f"{{{_A}}}srgbClr").set("val", str(WHITE))
        etree.SubElement(etree.SubElement(sp, f"{{{_A}}}ln"), f"{{{_A}}}noFill")
        return sp

    chart_el = space.find(f"{{{_C}}}chart")
    for old in space.findall(f"{{{_C}}}spPr"):
        space.remove(old)
    chart_el.addnext(plain())
    plot_area = chart_el.find(f"{{{_C}}}plotArea")
    for old in plot_area.findall(f"{{{_C}}}spPr"):
        plot_area.remove(old)
    ext = plot_area.find(f"{{{_C}}}extLst")
    (ext.addprevious if ext is not None else plot_area.append)(plain())
    rounded = space.find(f"{{{_C}}}roundedCorners")
    if rounded is None:
        rounded = etree.Element(f"{{{_C}}}roundedCorners")
        anchor = space.find(f"{{{_C}}}lang")
        anchor = anchor if anchor is not None else space.find(f"{{{_C}}}date1904")
        if anchor is not None:
            anchor.addnext(rounded)
        else:
            space.insert(0, rounded)
    rounded.set("val", "0")


def _native_chart(slide, ctype: str, categories: list, series: list, unit: str, x: float, y: float, w: float, h: float):
    """파워포인트에서 고칠 수 있는 진짜 그래프를 넣는다 (그림이 아니다). 모든 값에 데이터 라벨이 붙고, 눈금선은 값 축만 아주 옅게, 눈금 표시는 없다."""
    fmt = _number_format(series, unit)
    data = CategoryChartData(number_format=fmt)
    flip = ctype == "bar"  # 가로 막대는 첫 항목이 맨 위에 오도록 거꾸로 넣는다
    data.categories = list(reversed(categories)) if flip else list(categories)
    for name, values in series:
        data.add_series(name or "값", list(reversed(values)) if flip else list(values))
    kind = {"column": XL_CHART_TYPE.COLUMN_CLUSTERED, "bar": XL_CHART_TYPE.BAR_CLUSTERED, "line": XL_CHART_TYPE.LINE_MARKERS, "doughnut": XL_CHART_TYPE.DOUGHNUT}[ctype]
    frame = slide.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), data)
    frame.name = "그래프"
    chart = frame.chart
    chart.has_title = False
    _chart_font(chart.font, 10, FONT, TEXT)
    chart.has_legend = len(series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        _chart_font(chart.legend.font, 10, FONT, TEXT)
    plot = chart.plots[0]
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.number_format_is_linked = False
    colors = _doughnut_colors(len(categories)) if ctype == "doughnut" else _series_colors(len(series))
    if ctype == "doughnut":
        labels.number_format = "0%"
        labels.show_percentage, labels.show_value, labels.show_category_name = True, False, False
        _chart_font(labels.font, 10, FONT_B, WHITE)
        for i in range(len(categories)):
            point = plot.series[0].points[i]
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = colors[i % len(colors)]
        hole = plot._element.find(f"{{{_C}}}holeSize")
        if hole is None:
            hole = etree.SubElement(plot._element, f"{{{_C}}}holeSize")
        hole.set("val", "56")
    else:
        labels.number_format = fmt
        labels.show_value = True
        labels.position = XL_LABEL_POSITION.ABOVE if ctype == "line" else XL_LABEL_POSITION.OUTSIDE_END
        _chart_font(labels.font, 9, FONT_B, INK)
        for i, item in enumerate(plot.series):
            color = colors[i % len(colors)]
            if ctype == "line":
                item.smooth = False
                item.format.line.color.rgb = color
                item.format.line.width = Pt(2.25)
                item.marker.style = XL_MARKER_STYLE.CIRCLE
                item.marker.size = 7
                item.marker.format.fill.solid()
                item.marker.format.fill.fore_color.rgb = color
                item.marker.format.line.color.rgb = color
            else:
                item.invert_if_negative = False
                item.format.fill.solid()
                item.format.fill.fore_color.rgb = color
        if ctype != "line":
            plot.gap_width = 70 if len(categories) * len(series) >= 6 else 110
            if len(series) > 1:
                plot.overlap = -10
        cat, val = chart.category_axis, chart.value_axis
        cat.has_major_gridlines = cat.has_minor_gridlines = False
        cat.major_tick_mark = cat.minor_tick_mark = XL_TICK_MARK.NONE
        cat.format.line.color.rgb = AXIS_GREY
        cat.format.line.width = Pt(0.75)
        _chart_font(cat.tick_labels.font, 10, FONT_B, INK)
        val.has_major_gridlines, val.has_minor_gridlines = True, False
        val.major_gridlines.format.line.color.rgb = GRID_GREY
        val.major_gridlines.format.line.width = Pt(0.5)
        val.major_tick_mark = val.minor_tick_mark = XL_TICK_MARK.NONE
        val.format.line.color.rgb = AXIS_GREY
        val.format.line.width = Pt(0.75)
        val.tick_labels.number_format = fmt
        val.tick_labels.number_format_is_linked = False
        _chart_font(val.tick_labels.font, 9, FONT, MUTE)
        if min(v for _, values in series for v in values) >= 0:
            val.minimum_scale = 0
    _chart_clean(chart)
    return frame


def _value_rows(slide, x, y, w, h, head: str, categories: list, values: list, unit: str, colors: list | None = None):
    """그래프 옆 값 요약 카드: 항목 이름과 그 값을 한 줄씩 (그래프에 든 값 그대로). `colors` 가 있으면 도넛 조각의 범례 표시(짧은 굵은 선)를 붙인다."""
    _card(slide, x, y, w, h)
    text_w = w - 2 * CARD_PAD
    top = y + CARD_PAD_V
    if head:
        head_pt = next((pt for pt in (CARD_HEAD_PT, 13, 12, 11) if _est_lines(head, pt, text_w) <= 1), 11)
        box = _textbox(slide, x + CARD_PAD, y + 0.18, text_w, 0.36, name="카드 제목")
        _write(box.text_frame.paragraphs[0], head, head_pt, bold=True, color=INK)
        if _est_lines(head, head_pt, text_w) > 1:
            _shrink(box.text_frame, 1 / _est_lines(head, head_pt, text_w))
        _line(slide, x + CARD_PAD, y + 0.62, x + w - CARD_PAD, y + 0.62)
        top = y + _COLUMN_HEAD_H
    n = len(categories)
    row_h = min(0.78, (y + h - CARD_PAD_V - top) / n)
    top += max(0.0, (y + h - CARD_PAD_V - top - row_h * n) / 2)
    value_w = text_w * 0.42
    key_w = 0.3 if colors else 0.0
    name_w = text_w - value_w - 0.1 - key_w
    for i, (name, value) in enumerate(zip(categories, values)):
        ry = top + i * row_h
        if i:
            _line(slide, x + CARD_PAD, ry, x + w - CARD_PAD, ry)
        if colors:
            _line(slide, x + CARD_PAD, ry + row_h / 2, x + CARD_PAD + 0.18, ry + row_h / 2, colors[i % len(colors)], 7, name="범례 표시")
        _block(slide, x + CARD_PAD + key_w, ry + 0.03, name_w, row_h - 0.06, "", name, sizes=((0, 13), (0, 12), (0, 11), (0, 10)), name="값 이름", detail_color=INK)
        text = _number_text(value)
        size = next((pt for pt in (20, 18, 16, 14, 12) if (_text_em(text) * pt + (_text_em(unit) * 11 + 4 if unit else 0)) / 72 <= value_w and _line_h(pt, 1.0) <= row_h - 0.06), 12)
        box = _textbox(slide, x + w - CARD_PAD - value_w, ry + 0.03, value_w, row_h - 0.06, name="값")
        p = box.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.RIGHT
        _write(p, text, size, bold=True, color=BRAND_DEEP)
        if unit:
            _write(p, " " + unit, 11, color=TEXT)
        if (_text_em(text) * size + (_text_em(unit) * 11 + 4 if unit else 0)) / 72 > value_w:
            _shrink(box.text_frame, value_w * 72 / (_text_em(text) * size + _text_em(unit) * 11 + 4))


def _draw_chart(slide, data: dict, number: int, deck_title: str):
    """그래프: 흰 카드 위의 진짜 차트. 계열이 하나이고 항목이 6개 이하면 왼쪽 그래프(7) + 오른쪽 값 요약(5), 그 밖에는 풀폭 그래프."""
    _header(slide, data, number, deck_title)
    bottom = _note_line(slide, data, _body_bottom(slide, data))
    plan = _chart_plan(data)
    if plan is None:
        return
    ctype, categories, series = plan
    limit = CHART_POINTS[ctype]
    categories, series = categories[:limit], [(n, v[:limit]) for n, v in series]  # 쪽 나누기를 거치지 않고 온 큰 자료도 넘치지 않게
    unit = _clean(data.get("unit")).strip()
    height = bottom - BODY_Y
    side = len(series) == 1 and len(categories) <= 6
    chart_w = _CLOSING_RIGHT_W if side else CONTENT_W
    _card(slide, MARGIN, BODY_Y, chart_w, height)
    if ctype == "doughnut":
        size = min(chart_w - 0.5, height - 0.3)
        cx, cy = MARGIN + chart_w / 2, BODY_Y + height / 2
        _native_chart(slide, ctype, categories, series, unit, cx - size / 2, cy - size / 2, size, size)
        name = series[0][0]
        hole_w = size * 0.56 * 0.72
        if name and _est_lines(name, 14, hole_w) <= 1:  # 도넛 가운데는 "무엇의 비율인지" (숫자를 되풀이하지 않는다)
            box = _textbox(slide, cx - hole_w / 2, cy - 0.2, hole_w, 0.4, name="도넛 가운데")
            p = box.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            _write(p, name, 14, bold=True, color=BRAND_DEEP)
    else:
        _native_chart(slide, ctype, categories, series, unit, MARGIN + 0.2, BODY_Y + 0.15, chart_w - 0.4, height - 0.3)
    if side:
        colors = _doughnut_colors(len(categories)) if ctype == "doughnut" else None
        _value_rows(slide, MARGIN + chart_w + GAP_X, BODY_Y, _CLOSING_LEFT_W, height, series[0][0], categories, series[0][1], unit, colors)


_ITEM_SPLIT = re.compile(r"\s*[,，、]\s*")


def _chips(slide, x, y, w, h, parts: list) -> bool:
    """짧은 낱말들을 칩(옅은 블루 알약)으로 한 줄에 놓는다. 한 줄에 안 들어가면 그리지 않고 False."""
    for pt in (12, 11, 10):
        widths = [_text_em(part) * pt / 72 + 0.4 for part in parts]
        if sum(widths) + 0.12 * (len(parts) - 1) <= w:
            break
    else:
        return False
    chip_h = min(h, max(0.34, _line_h(pt, 1.0) + 0.14))
    cx = x
    for part, width in zip(parts, widths):
        chip = _card(slide, cx, y + (h - chip_h) / 2, width, chip_h, fill=BRAND_PALE, radius=chip_h / 2, shadow=False, name="칩")
        _shape_text(chip, part, (pt,), color=INK, pad=0.1)
        cx += width + 0.12
    return True


def _draw_layers(slide, nodes: list, top: float, bottom: float):
    """구성도: 위에서 아래로 쌓은 층. 층마다 왼쪽에 이름(브랜드 블루), 오른쪽에 그 층에 든 것(쉼표로 나눈 낱말은 칩으로)."""
    n = len(nodes)
    gap = 0.22 if n > 1 else 0.0
    tier_h = min(1.1, (bottom - top - gap * (n - 1)) / n)  # 층이 적어도 한 층이 너무 두꺼워지지 않게
    top += max(0.0, (bottom - top - tier_h * n - gap * (n - 1)) / 2)
    pad = min(0.12, tier_h * 0.15)
    label_w = 2.7
    for i, node in enumerate(nodes):
        y = top + i * (tier_h + gap)
        _card(slide, MARGIN, y, CONTENT_W, tier_h, radius=RADIUS_SMALL)
        tag = _card(slide, MARGIN + pad, y + pad, label_w, tier_h - 2 * pad, fill=BRAND, radius=0.1, shadow=False, name="층 이름")
        _shape_text(tag, node["label"], (14, 13, 12, 11, 10), pad=0.12)
        x0 = MARGIN + pad + label_w + 0.25
        width, height = RIGHT - 0.25 - x0, tier_h - 2 * pad
        detail = node["detail"]
        parts = [part for part in _ITEM_SPLIT.split(detail) if part]
        if detail and not (len(parts) >= 2 and all(_text_em(part) <= 16 for part in parts) and _chips(slide, x0, y + pad, width, height, parts)):
            _block(slide, x0, y + pad, width, height, "", detail, sizes=((0, 13), (0, 12), (0, 11), (0, 10)), name="층 내용")
        if i < n - 1:
            cx = MARGIN + pad + label_w / 2
            _line(slide, cx, y + tier_h + 0.03, cx, y + tier_h + gap - 0.03, BRAND_T3, 1.25, arrow=True, name="흐름 화살표")


def _mini_card(slide, x, y, w, h, node: dict, *, fill=PAGE_BG):
    """도식 안 작은 상자: 앞에 작은 네모, 굵은 이름과 설명."""
    _card(slide, x, y, w, h, fill=fill, radius=RADIUS_SMALL, shadow=False, name="도식 상자")
    _rect(slide, x + 0.2, y + h / 2 - 0.07, 0.14, 0.14, BRAND, name="머리 표시")
    _block(slide, x + 0.48, y + 0.08, w - 0.48 - 0.2, h - 0.16, node["label"], node["detail"])


def _draw_hub(slide, data: dict, nodes: list, top: float, bottom: float):
    """중심과 주변: 흰 판 가운데에 브랜드 블루 원(가운데 이름), 양옆에 주변 항목 상자, 원과 상자를 잇는 선."""
    height = bottom - top
    _card(slide, MARGIN, top, CONTENT_W, height)
    half = (len(nodes) + 1) // 2
    sides = (nodes[:half], nodes[half:])
    center = _clean(data.get("center")).strip()
    side_w, edge = 4.15, 0.25
    diameter = min(height - 0.4, 2.5)
    cx, cy = MARGIN + CONTENT_W / 2, top + height / 2
    boxes = []
    for i, items in enumerate(sides):
        if not items:
            continue
        box_h = min(1.25, (height - 2 * 0.2 - GAP_Y * (len(items) - 1)) / len(items))
        start = cy - (box_h * len(items) + GAP_Y * (len(items) - 1)) / 2
        x = MARGIN + edge if i == 0 else RIGHT - edge - side_w
        for k, node in enumerate(items):
            boxes.append((x, start + k * (box_h + GAP_Y), box_h, i, node))
    for x, y, box_h, i, _node in boxes:  # 선을 먼저 긋는다 (원과 상자가 선의 끝을 덮는다)
        _line(slide, cx, cy, x + side_w if i == 0 else x, y + box_h / 2, BRAND_T3, 1.0, name="연결선")
    hub = _rect(slide, cx - diameter / 2, cy - diameter / 2, diameter, diameter, BRAND, shape=MSO_SHAPE.OVAL, name="중심 원")
    if center:
        _shape_text(hub, center, (20, 18, 16, 14, 12, 11), pad=0.04, inner=0.7)
    for x, y, box_h, _i, node in boxes:
        _mini_card(slide, x, y, side_w, box_h, node)


def _draw_before_after(slide, data: dict, top: float, bottom: float):
    """도입 전후: 같은 폭 카드 둘과 그 사이 화살표. 제목은 알약 이름표(왼쪽은 비교군 회색, 오른쪽은 브랜드 블루)."""
    height = bottom - top
    sides = {side: _list(data, side) for side in ("left", "right")}
    text_w = _BA_W - 2 * CARD_PAD
    inner_h = height - _COLUMN_HEAD_H - CARD_PAD_V
    fits = {side: _list_fit(items, text_w, inner_h) for side, items in sides.items() if items}
    size = min((f[0] for f in fits.values()), default=LIST_SIZES[0])
    gap = min((f[1] for f in fits.values()), default=size * 0.4)
    for i, side in enumerate(("left", "right")):
        x = MARGIN + i * (_BA_W + _BA_ARROW_W)
        _card(slide, x, top, _BA_W, height)
        head = _clean(data.get(f"{side}_title")).strip() or ("도입 후" if i else "도입 전")
        pill_w = min(text_w, _text_em(head) * 12 / 72 + 0.5)
        pill = _card(slide, x + CARD_PAD, top + 0.17, pill_w, 0.34, fill=BRAND if i else MUTE, radius=0.17, shadow=False, name="머리 표시")
        _shape_text(pill, head, (12, 11, 10), pad=0.12)
        _line(slide, x + CARD_PAD, top + 0.62, x + _BA_W - CARD_PAD, top + 0.62)
        items = sides[side]
        if items:
            box = _textbox(slide, x + CARD_PAD, top + _COLUMN_HEAD_H, text_w, inner_h, name="목록")
            _fill_list(box.text_frame, items, size, gap, color=INK if i else TEXT)
            if not fits[side][2]:
                _shrink(box.text_frame, inner_h / _list_need(items, size, text_w)[1])
    _rect(slide, MARGIN + _BA_W + 0.2, top + height / 2 - 0.25, _BA_ARROW_W - 0.4, 0.5, BRAND, shape=MSO_SHAPE.RIGHT_ARROW, name="전후 화살표")


_PERIOD = re.compile(r"(\d+(?:\.\d+)?)\s*(주|일|개월|달|년|분기|시간)")


def _period_weights(nodes: list) -> list[float]:
    """일정 막대의 폭 비율: 기간이 모두 같은 단위의 숫자(`2주`, `3주`)면 그 비율로(너무 좁아지지 않게 묶는다), 아니면 같은 폭."""
    n = len(nodes)
    found = [_PERIOD.fullmatch(node["group"]) for node in nodes]
    if all(found) and len({m.group(2) for m in found}) == 1:
        values = [float(m.group(1)) for m in found]
        if all(v > 0 for v in values):
            raw = [max(v / sum(values), 0.6 / n) for v in values]
            return [v / sum(raw) for v in raw]
    return [1 / n] * n


def _draw_timeline(slide, nodes: list, top: float, bottom: float):
    """일정: 흰 판 위 가로 일정표. 윗줄에 기간, 그 아래 단계마다 한 줄씩 내려가는 막대(간트 차트 모양)와 설명."""
    height = bottom - top
    n = len(nodes)
    _card(slide, MARGIN, top, CONTENT_W, height)
    x0, inner_w = MARGIN + CARD_PAD, CONTENT_W - 2 * CARD_PAD
    head_h = 0.4 if any(node["group"] for node in nodes) else 0.0
    axis_y = top + 0.2 + head_h
    weights = _period_weights(nodes) if n > 1 else [0.45]
    starts = [x0 + inner_w * sum(weights[:i]) for i in range(n)]
    widths = [inner_w * w for w in weights]
    rows_top = axis_y + 0.1
    row_h = (top + height - 0.2 - rows_top) / n
    bar_h = min(0.46, row_h - 0.08)
    _line(slide, x0, axis_y, x0 + inner_w, axis_y, AXIS_GREY, 0.75, name="일정 축")
    for edge in [*starts, starts[-1] + widths[-1]]:
        _line(slide, edge, axis_y, edge, top + height - 0.2, HAIRLINE, 0.75, name="눈금선")
    for i, node in enumerate(nodes):
        xs, width = starts[i], widths[i]
        if node["group"]:
            box = _textbox(slide, xs, top + 0.18, width, head_h - 0.04, name="기간")
            p = box.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            _write(p, node["group"], 11, bold=True, color=BRAND)
            lines = _est_lines(node["group"], 11, width)
            if lines > 1:
                _shrink(box.text_frame, 1 / lines)
        row_y = rows_top + i * row_h
        bar = _card(slide, xs + 0.04, row_y + (row_h - bar_h) / 2, width - 0.08, bar_h, fill=BRAND, radius=0.08, shadow=False, name="일정 막대")
        _shape_text(bar, node["label"], (13, 12, 11, 10, 9), pad=0.1)
        if node["detail"]:
            left_w = xs - x0 - 0.15
            right_w = x0 + inner_w - (xs + width) - 0.15
            if right_w >= left_w:
                _block(slide, xs + width + 0.12, row_y, max(right_w, 0.6), row_h, "", node["detail"], sizes=((0, 11), (0, 10), (0, 9)), name="일정 설명")
            else:
                _block(slide, x0, row_y, max(left_w, 0.6), row_h, "", node["detail"], sizes=((0, 11), (0, 10), (0, 9)), name="일정 설명", align=PP_ALIGN.RIGHT)


def _pair_need(items: list, pt: float, width: float) -> float:
    text_w = width - _indent_in(pt)
    return sum(_est_lines(n["label"] + ("  " + n["detail"] if n["detail"] else ""), pt, text_w) * _line_h(pt) for n in items) + max(len(items) - 1, 0) * pt * 0.4 / 72


def _draw_matrix(slide, data: dict, nodes: list, top: float, bottom: float):
    """2×2 위치: 흰 판 위 네 칸. 가로축·세로축 이름과 화살표, 칸마다 항목. 첫 항목이 든 칸을 옅은 블루로 강조한다."""
    height = bottom - top
    _card(slide, MARGIN, top, CONTENT_W, height)
    axis_x, axis_y = _clean(data.get("axis_x")).strip(), _clean(data.get("axis_y")).strip()
    left_w = 1.75 if axis_y else 0.0
    foot_h = 0.42 if axis_x else 0.0
    gx, gy = MARGIN + 0.25 + left_w, top + 0.2
    gw, gh = RIGHT - 0.25 - gx, height - 0.4 - foot_h
    gap = 0.12
    qw, qh = (gw - gap) / 2, (gh - gap) / 2
    groups = [[] for _ in range(4)]
    for i, node in enumerate(nodes):  # 자리 이름이 없으면 순서대로 왼쪽 위 → 오른쪽 위 → 왼쪽 아래 → 오른쪽 아래
        groups[ontology.MATRIX_QUADRANTS.get(node["group"], i % 4)].append(node)
    first = ontology.MATRIX_QUADRANTS.get(nodes[0]["group"], 0)
    for q, items in enumerate(groups):
        x, y = gx + (q % 2) * (qw + gap), gy + (q // 2) * (qh + gap)
        _card(slide, x, y, qw, qh, fill=BRAND_PALE if q == first else PAGE_BG, radius=RADIUS_SMALL, shadow=False, name="칸")
        if not items:
            continue
        text_w, text_h = qw - 0.4, qh - 0.2
        size = next((pt for pt in (13, 12, 11, 10, 9) if _pair_need(items, pt, text_w) <= text_h), 9)
        box = _textbox(slide, x + 0.2, y + 0.1, text_w, text_h, name="칸 글")
        tf = box.text_frame
        for k, node in enumerate(items):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            p.line_spacing = BODY_LS
            p.space_after = Pt(size * 0.4 if k < len(items) - 1 else 0)
            _write(p, node["label"], size, bold=True, color=INK)
            if node["detail"]:
                _write(p, "  " + node["detail"], size, color=TEXT)
            _bullet(p, size)
        if _pair_need(items, size, text_w) > text_h:
            _shrink(tf, text_h / _pair_need(items, size, text_w))
    if axis_y:
        _line(slide, gx - 0.12, gy + gh, gx - 0.12, gy, MUTE, 1.0, arrow=True, name="세로축")
        _block(slide, MARGIN + 0.25, gy, left_w - 0.27, gh, axis_y, "", sizes=((12, 0), (11, 0), (10, 0)), name="세로축 이름", color=TEXT, align=PP_ALIGN.CENTER)
    if axis_x:
        _line(slide, gx, gy + gh + 0.1, gx + gw, gy + gh + 0.1, MUTE, 1.0, arrow=True, name="가로축")
        _block(slide, gx, gy + gh + 0.13, gw, foot_h - 0.13, axis_x, "", sizes=((12, 0), (11, 0), (10, 0)), name="가로축 이름", color=TEXT, align=PP_ALIGN.CENTER)


def _clip(cx: float, cy: float, half_w: float, half_h: float, tx: float, ty: float) -> tuple[float, float]:
    """상자 가운데 (cx, cy) 에서 (tx, ty) 쪽으로 가는 선이 상자 테두리와 만나는 점."""
    dx, dy = tx - cx, ty - cy
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return cx, cy
    t = min(half_w / abs(dx) if abs(dx) > 1e-9 else math.inf, half_h / abs(dy) if abs(dy) > 1e-9 else math.inf)
    return cx + dx * min(t, 1.0), cy + dy * min(t, 1.0)


def _draw_cycle(slide, data: dict, nodes: list, top: float, bottom: float):
    """순환: 흰 판 위에 단계 상자를 고리 모양(타원)으로 놓고 화살표로 차례대로 잇는다. 마지막 단계는 첫 단계로 돌아온다."""
    height = bottom - top
    n = len(nodes)
    _card(slide, MARGIN, top, CONTENT_W, height)
    area_w, area_h = CONTENT_W - 0.6, height - 0.4
    mx, my = MARGIN + CONTENT_W / 2, top + height / 2
    box_w = 3.3 if n <= 4 else 2.9
    box_h = min(1.0 if n <= 4 else 0.85, area_h / 3 - 0.12) if n > 2 else min(1.0, area_h / 2 - 0.3)
    rx, ry = (area_w - box_w) / 2, (area_h - box_h) / 2
    if n == 1:
        points = [(mx, my)]
    else:
        points = [(mx + rx * math.cos(-math.pi / 2 + 2 * math.pi * k / n), my + ry * math.sin(-math.pi / 2 + 2 * math.pi * k / n)) for k in range(n)]
    half_w, half_h = box_w / 2 + 0.06, box_h / 2 + 0.06
    if n == 2:  # 위·아래 두 상자: 내려가는 선과 올라오는 선을 좌우로 벌려 긋는다
        (ax, ay), (bx, by) = points
        _line(slide, mx + 1.5, ay + half_h, mx + 1.5, by - half_h, BRAND, 1.25, arrow=True, name="순환 화살표")
        _line(slide, mx - 1.5, by - half_h, mx - 1.5, ay + half_h, BRAND, 1.25, arrow=True, name="순환 화살표")
    elif n > 2:
        for k in range(n):
            a, b = points[k], points[(k + 1) % n]
            x1, y1 = _clip(a[0], a[1], half_w, half_h, b[0], b[1])
            x2, y2 = _clip(b[0], b[1], half_w, half_h, a[0], a[1])
            _line(slide, x1, y1, x2, y2, BRAND, 1.25, arrow=True, name="순환 화살표")
    for k, (node, (px, py)) in enumerate(zip(nodes, points)):
        x, y = px - box_w / 2, py - box_h / 2
        _card(slide, x, y, box_w, box_h, fill=BRAND_PALE, radius=RADIUS_SMALL, shadow=False, name="도식 상자")
        dot = _rect(slide, x + 0.16, py - 0.16, 0.32, 0.32, BRAND, shape=MSO_SHAPE.OVAL, name="번호 원")
        tf = _frame(dot.text_frame)
        tf.word_wrap = False
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        _write(p, str(k + 1), 10, bold=True, color=WHITE)
        _block(slide, x + 0.6, y + 0.07, box_w - 0.6 - 0.16, box_h - 0.14, node["label"], node["detail"], sizes=((13, 10), (12, 10), (11, 9), (10, 9)))
    center = _clean(data.get("center")).strip()
    if center and n > 1:
        _block(slide, mx - 1.3, my - 0.32, 2.6, 0.64, center, "", sizes=((14, 0), (13, 0), (12, 0), (11, 0)), name="가운데 이름", color=BRAND_DEEP, align=PP_ALIGN.CENTER)


def _draw_diagram(slide, data: dict, number: int, deck_title: str):
    """도식: `diagram_type` 에 따라 구성도(층)·중심과 주변·도입 전후·일정·2×2·순환을 그린다. 모르는 종류는 구성도로 그린다."""
    _header(slide, data, number, deck_title)
    bottom = _note_line(slide, data, _body_bottom(slide, data))
    dtype = data.get("diagram_type")
    if dtype == "before_after":
        if _list(data, "left") or _list(data, "right"):
            _draw_before_after(slide, data, BODY_Y, bottom)
        return
    nodes = _nodes(data)[:DIAGRAM_MAX.get(dtype, DIAGRAM_MAX["layers"])]
    if not nodes:
        return
    if dtype == "hub":
        _draw_hub(slide, data, nodes, BODY_Y, bottom)
    elif dtype == "timeline":
        _draw_timeline(slide, nodes, BODY_Y, bottom)
    elif dtype == "matrix":
        _draw_matrix(slide, data, nodes, BODY_Y, bottom)
    elif dtype == "cycle":
        _draw_cycle(slide, data, nodes, BODY_Y, bottom)
    else:
        _draw_layers(slide, nodes, BODY_Y, bottom)


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
    # 시각 자료 장 (베타 AG): 내부 경로가 든 항목은 통째로 뺀다. 그래프의 항목 이름이면 그 자리의 값도 함께 뺀다
    for key, fields in (("kpis", ("value", "unit", "label", "note")), ("nodes", ("label", "detail", "group"))):
        items = _list(out, key)
        out[key] = [i for i in items if isinstance(i, dict) and not any(has_internal_mark(i.get(f)) for f in fields)]
        removed += len(items) - len(out[key])
    categories = _list(out, "categories")
    bad = {i for i, c in enumerate(categories) if has_internal_mark(c)}
    series = [x for x in _list(out, "series") if isinstance(x, dict)]
    if bad:
        out["categories"] = [c for i, c in enumerate(categories) if i not in bad]
        for item in series:
            if isinstance(item.get("values"), (list, tuple)):
                item["values"] = [v for i, v in enumerate(item["values"]) if i not in bad]
        removed += len(bad)
    for item in series:
        if has_internal_mark(item.get("name")):
            item["name"] = ""
            removed += 1
    if series or _list(out, "series"):
        out["series"] = series
    for key in ("title", "key_message", "subtitle", "left_title", "right_title", "note", "unit", "center", "axis_x", "axis_y"):
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
    elif kind == "kpi":
        _draw_kpi(slide, data, number, deck_title)
    elif kind == "chart":
        _draw_chart(slide, data, number, deck_title)
    elif kind == "diagram":
        _draw_diagram(slide, data, number, deck_title)
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

"""구성안(dict) → .pptx. 템플릿 없이 빈 레이아웃 위에 고정 디자인으로 직접 그린다.

입력은 `proposals.clean_outline` / `slide_from_form` 이 만드는 형태이지만, 키가 빠져 있어도 견딘다.
"""

import bisect
import copy
import math
import re
import unicodedata
from datetime import date
from pathlib import Path

from lxml import etree

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from app import ontology

FONT = "맑은 고딕"
TEXT = RGBColor(0x22, 0x22, 0x22)
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
LIGHT = RGBColor(0xEA, 0xF1, 0xF8)
STRIPE = RGBColor(0xF5, 0xF8, 0xFB)
MUTED = RGBColor(0x66, 0x66, 0x66)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LINE = RGBColor(0xBF, 0xCD, 0xDB)

# --- 좌표 (인치) ---
SLIDE_W, SLIDE_H = 13.333, 7.5
MARGIN = 0.6
CONTENT_W = SLIDE_W - 2 * MARGIN
TITLE_Y, TITLE_H = 0.4, 0.85
MSG_Y, MSG_H = 1.3, 0.75
BODY_Y = 2.2
BODY_BOTTOM = 6.85
BODY_H = BODY_BOTTOM - BODY_Y
NUM_X, NUM_Y, NUM_W, NUM_H = 11.9, 6.95, 0.85, 0.35
COVER_BAND_Y, COVER_BAND_H = 2.1, 3.1
COLUMN_GAP = 0.4
STEP_GAP = 0.55

# --- 넘침 규칙 ---
MAX_BULLETS_PER_SLIDE = 10
MAX_TABLE_ROWS = 8
MAX_COLUMN_ITEMS = 8
MAX_STEPS = 6
LONG_ITEM_CHARS = 120
MIN_FONT_PT = 12

_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _clean(value) -> str:
    """XML 에 쓸 수 없는 문자를 없앤다. 줄바꿈은 \\n 하나로 맞춘다."""
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _ILLEGAL_XML.sub("", text)


def _list(slide: dict, key: str) -> list:
    value = slide.get(key)
    return list(value) if isinstance(value, (list, tuple)) else []


# ---------------------------------------------------------------- 줄 수 어림

LINE_SPACING = 1.25  # 글자 크기에 곱해 한 줄 높이를 어림한다
_SAFETY = 1.08       # 글꼴마다 폭이 달라 넉넉히 잡는다


def _char_em(ch: str) -> float:
    if ch == " ":
        return 0.32
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0
    return 0.6


def _est_lines(text, pt: float, width_in: float) -> int:
    """`text` 가 폭 `width_in`(인치) 에 `pt` 크기로 들어갈 때 줄 수를 어림한다. 줄바꿈 문자는 새 줄이다."""
    avail = max(width_in, 0.5) * 72 / pt
    total = 0
    for line in _clean(text).split("\n"):
        em = sum(_char_em(ch) for ch in line) * _SAFETY
        total += max(1, math.ceil(em / avail))
    return total


def _line_h(pt: float) -> float:
    return pt * LINE_SPACING / 72


def _message_style(message: str) -> tuple[int, int]:
    """핵심 메시지의 (글자 크기, 줄 수)."""
    pt = 18 if len(message) <= 70 else 16
    return pt, _est_lines(message, pt, CONTENT_W - 0.1)


def _message_shift(slide: dict) -> float:
    """핵심 메시지가 여러 줄일 때 본문이 내려가는 만큼(인치)."""
    message = _clean(slide.get("key_message"))
    if not message:
        return 0.0
    pt, lines = _message_style(message)
    return (lines - 1) * _line_h(pt)


def _table_font(columns: list, rows: list) -> int:
    return 11 if len(columns) > 6 else 16 if len(columns) <= 4 and len(rows) <= 6 else 14


def _table_row_heights(columns: list, rows: list, pt: float) -> list[float]:
    """머리글 + 각 행의 높이(인치). 칸 안 글이 줄바꿈되는 만큼 키운다."""
    count = max(len(columns), 1)
    cell_w = CONTENT_W / count - 0.2
    base = _line_h(pt) + 0.3
    heights = []
    for r, cells in enumerate([columns, *rows]):
        cells = list(cells) if isinstance(cells, (list, tuple)) else [cells]
        lines = max([_est_lines(c, pt, cell_w) for c in cells[:count]] or [1])
        heights.append(max(base, lines * _line_h(pt) + 0.2))
    return heights


def _fit_table_font(columns: list, rows: list, avail: float = BODY_H) -> tuple[int, list[float]]:
    """처음 글자 크기에서 표가 본문 영역을 넘으면 16 → 14 → 12 → 11 로 내린다."""
    start = _table_font(columns, rows)
    sizes = [s for s in (16, 14, 12, 11) if s <= start] or [11]
    for pt in sizes:
        heights = _table_row_heights(columns, rows, pt)
        if sum(heights) <= avail:
            return pt, heights
    return sizes[-1], _table_row_heights(columns, rows, sizes[-1])


def _table_chunks(columns: list, rows: list, first_shift: float = 0.0) -> list[list]:
    """표 행을 장별로 나눈다. 장당 MAX_TABLE_ROWS 행 이하이고, 가장 작은 글자(11pt)로도 본문 영역에 들어가게 한다."""
    chunks, current, used = [], [], 0.0
    budget = BODY_H - first_shift  # 첫 장만 핵심 메시지가 여러 줄이면 그만큼 좁다
    head = _table_row_heights(columns, [], 11)[0]
    for row in rows:
        h = _table_row_heights(columns, [row], 11)[1]
        if current and (len(current) >= MAX_TABLE_ROWS or head + used + h > budget):
            chunks.append(current)
            current, used, budget = [], 0.0, BODY_H
        current.append(row)
        used += h
    return chunks + [current] if current or not chunks else chunks


# ---------------------------------------------------------------- 나누기

def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def paginate(slide: dict) -> list[dict]:
    """넘치는 장을 여러 장으로 나눈다. 빈 목록은 돌려주지 않는다.

    나뉜 장은 제목에 `(2)`, `(3)` … 이 붙고, key_message / sources / needs_check 는 첫 장에만 남는다.
    """
    kind = slide.get("kind")
    if kind == "table":
        parts = [{"rows": c} for c in _table_chunks(_list(slide, "columns"), _list(slide, "rows"), _message_shift(slide))]
    elif kind == "two_column":
        left, right = _chunks(_list(slide, "left"), MAX_COLUMN_ITEMS), _chunks(_list(slide, "right"), MAX_COLUMN_ITEMS)
        count = max(len(left), len(right))
        parts = [{"left": left[i] if i < len(left) else [], "right": right[i] if i < len(right) else []} for i in range(count)]
    elif kind == "steps":
        parts = [{"steps": c} for c in _chunks(_list(slide, "steps"), MAX_STEPS)]
    elif kind in ("bullets", "closing"):
        parts = [{"bullets": c} for c in _chunks(_list(slide, "bullets"), MAX_BULLETS_PER_SLIDE)]
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

def _style_run(run, size: float, *, bold=False, color=TEXT):
    font = run.font
    font.size = Pt(size)
    font.bold = bold
    font.color.rgb = color
    font.name = FONT
    rpr = run._r.get_or_add_rPr()
    for old in rpr.findall(f"{{{_A}}}ea"):
        rpr.remove(old)
    ea = etree.SubElement(rpr, f"{{{_A}}}ea")
    ea.set("typeface", FONT)
    latin = rpr.find(f"{{{_A}}}latin")
    if latin is not None:
        latin.addnext(ea)


def _write(paragraph, text, size, *, bold=False, color=TEXT):
    """문단에 글자를 쓴다. 줄바꿈은 같은 문단 안의 줄 바꿈이 된다."""
    for i, line in enumerate(_clean(text).split("\n")):
        if i:
            paragraph.add_line_break()
        run = paragraph.add_run()
        run.text = line
        _style_run(run, size, bold=bold, color=color)


def _bullet(paragraph, size):
    ppr = paragraph._p.get_or_add_pPr()
    indent = int(Pt(size) * 1.1)
    ppr.set("marL", str(indent))
    ppr.set("indent", str(-indent))
    color = etree.SubElement(ppr, f"{{{_A}}}buClr")
    etree.SubElement(color, f"{{{_A}}}srgbClr").set("val", str(ACCENT))
    char = etree.SubElement(ppr, f"{{{_A}}}buChar")
    char.set("char", "•")


def _textbox(slide, x, y, w, h, *, anchor=MSO_ANCHOR.TOP, fit=False, margin=0.05):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE if fit else MSO_AUTO_SIZE.NONE
    return box


def _fill_bullets(tf, items, size, *, gap=8):
    first = True
    for item in items:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_after = Pt(gap)
        _write(p, item, size)
        _bullet(p, size)


def _shrink_if_long(items, size) -> float:
    """한 항목이 120자를 넘으면 두 단계(4pt) 줄인다. 12pt 아래로는 내려가지 않는다."""
    if any(len(_clean(i)) > LONG_ITEM_CHARS for i in items):
        return min(size, max(MIN_FONT_PT, size - 4))
    return size


def _rect(slide, x, y, w, h, fill, *, line=None, shape=MSO_SHAPE.RECTANGLE):
    shp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(1)
    style = shp._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}style")
    if style is not None:  # 테마의 그림자·윤곽 참조를 떼어 납작한 도형으로 둔다
        shp._element.remove(style)
    return shp


# ---------------------------------------------------------------- 그리기

def _header(slide, data: dict, *, key_message=True) -> float:
    """제목과 핵심 메시지를 그리고 본문이 시작할 y(인치)를 돌려준다. 핵심 메시지가 여러 줄이면 그만큼 내려간다."""
    title = _clean(data.get("title"))
    size = 28 if len(title) <= 30 else 24 if len(title) <= 45 else 20
    box = _textbox(slide, MARGIN, TITLE_Y, CONTENT_W, TITLE_H, anchor=MSO_ANCHOR.MIDDLE)
    _write(box.text_frame.paragraphs[0], title, size, bold=True, color=ACCENT)
    _rect(slide, MARGIN + 0.05, TITLE_Y + TITLE_H + 0.03, 1.2, 0.05, ACCENT)
    message = _clean(data.get("key_message"))
    body_y = BODY_Y
    if key_message and message:
        pt, lines = _message_style(message)
        msg_h = MSG_H + (lines - 1) * _line_h(pt)
        box = _textbox(slide, MARGIN, MSG_Y + 0.08, CONTENT_W, msg_h, anchor=MSO_ANCHOR.MIDDLE)
        _write(box.text_frame.paragraphs[0], message, pt, bold=True)
        body_y = BODY_Y + (lines - 1) * _line_h(pt)
    return body_y


def _draw_cover(slide, data: dict, customer_name: str, today: date):
    _rect(slide, 0, 0, SLIDE_W, 0.25, ACCENT)
    _rect(slide, 0, COVER_BAND_Y, SLIDE_W, COVER_BAND_H, ACCENT)
    title = _clean(data.get("title"))
    size = 40 if len(title) <= 24 else 34 if len(title) <= 40 else 28
    box = _textbox(slide, 1.0, COVER_BAND_Y + 0.25, SLIDE_W - 2.0, 1.7, anchor=MSO_ANCHOR.BOTTOM)
    _write(box.text_frame.paragraphs[0], title, size, bold=True, color=WHITE)
    subtitle = _clean(data.get("subtitle"))
    if subtitle:
        box = _textbox(slide, 1.0, COVER_BAND_Y + 2.0, SLIDE_W - 2.0, 0.9)
        _write(box.text_frame.paragraphs[0], subtitle, 20 if len(subtitle) <= 50 else 16, color=WHITE)
    box = _textbox(slide, 1.0, COVER_BAND_Y + COVER_BAND_H + 0.5, SLIDE_W - 2.0, 1.4)
    tf = box.text_frame
    p = tf.paragraphs[0]
    if _clean(customer_name):
        _write(p, customer_name, 20, bold=True)
        p = tf.add_paragraph()
    _write(p, f"{today.year}. {today.month}. {today.day}.", 16, color=MUTED)


def _draw_bullets(slide, data: dict):
    items = _list(data, "bullets")
    top = _header(slide, data)
    if not items:
        return
    size = _shrink_if_long(items, 20 if len(items) <= 7 else 16)
    box = _textbox(slide, MARGIN, top, CONTENT_W, BODY_BOTTOM - top, fit=True)
    _fill_bullets(box.text_frame, items, size, gap=10 if size >= 20 else 6)


def _draw_closing(slide, data: dict):
    items = _list(data, "bullets")
    message = _clean(data.get("key_message"))
    if not items and not message:
        box = _textbox(slide, MARGIN, 2.5, CONTENT_W, 2.5, anchor=MSO_ANCHOR.MIDDLE)
        p = box.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        _write(p, data.get("title"), 40, bold=True, color=ACCENT)
        return
    _header(slide, data, key_message=False)
    if items:
        size = _shrink_if_long(items, 20 if len(items) <= 7 else 16)
        top = BODY_Y - 0.2
        box = _textbox(slide, MARGIN, top, CONTENT_W, 3.0 if message else BODY_H + 0.2, fit=True)
        _fill_bullets(box.text_frame, items, size, gap=8)
    if message:
        y = 5.35 if items else 3.2
        _rect(slide, MARGIN, y, CONTENT_W, 1.2, LIGHT)
        box = _textbox(slide, MARGIN + 0.3, y, CONTENT_W - 0.6, 1.2, anchor=MSO_ANCHOR.MIDDLE)
        p = box.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        _write(p, message, 22 if len(message) <= 60 else 18, bold=True, color=ACCENT)


def _column_size(items: list) -> float:
    return _shrink_if_long(items, 18 if len(items) <= 5 else 16)


def _column_need(items: list, width: float) -> float:
    """한쪽 상자에 항목을 다 담는 데 필요한 높이(머리띠 포함)."""
    size = _column_size(items)
    text_w = width - 0.3 - 0.1 - size * 1.1 / 72
    return 0.75 + sum(_est_lines(i, size, text_w) * _line_h(size) + 6 / 72 for i in items) + 0.15


def _draw_two_column(slide, data: dict):
    top = _header(slide, data)
    width = (CONTENT_W - COLUMN_GAP) / 2
    sides = {side: _list(data, side) for side in ("left", "right")}
    room = BODY_BOTTOM - top
    height = min(room, max(3.0, *(_column_need(items, width) for items in sides.values())))
    for i, side in enumerate(("left", "right")):  # 두 상자는 더 큰 쪽 높이로 맞춘다
        x = MARGIN + i * (width + COLUMN_GAP)
        items = sides[side]
        size = _column_size(items)
        _rect(slide, x, top, width, height, STRIPE, line=LINE)
        _rect(slide, x, top, width, 0.6, ACCENT)
        head = _textbox(slide, x + 0.15, top, width - 0.3, 0.6, anchor=MSO_ANCHOR.MIDDLE)
        _write(head.text_frame.paragraphs[0], data.get(f"{side}_title"), 18, bold=True, color=WHITE)
        if items:
            body = _textbox(slide, x + 0.15, top + 0.75, width - 0.3, height - 0.9, fit=True)
            _fill_bullets(body.text_frame, items, size, gap=6)


def _draw_table(slide, data: dict):
    columns, rows = _list(data, "columns"), _list(data, "rows")
    top = _header(slide, data)
    count = len(columns)
    size, heights = _fit_table_font(columns, rows, BODY_BOTTOM - top)
    frame = slide.shapes.add_table(len(rows) + 1, count, Inches(MARGIN), Inches(top), Inches(CONTENT_W), Inches(sum(heights)))
    table = frame.table
    for c in range(count):
        table.columns[c].width = Emu(int(Inches(CONTENT_W) / count))
    for r in range(len(rows) + 1):
        table.rows[r].height = Inches(heights[r])
        cells = columns if r == 0 else rows[r - 1]
        cells = list(cells) if isinstance(cells, (list, tuple)) else [cells]
        for c in range(count):
            cell = table.cell(r, c)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = cell.margin_right = Inches(0.1)
            cell.fill.solid()
            cell.fill.fore_color.rgb = ACCENT if r == 0 else (STRIPE if r % 2 == 0 else WHITE)
            tf = cell.text_frame
            tf.word_wrap = True
            _write(tf.paragraphs[0], cells[c] if c < len(cells) else "", size, bold=r == 0, color=WHITE if r == 0 else TEXT)


def _draw_steps(slide, data: dict):
    steps = [s for s in _list(data, "steps") if isinstance(s, dict)]
    shift = _header(slide, data) - BODY_Y
    n = len(steps)
    width = (CONTENT_W - STEP_GAP * (n - 1)) / n
    name_size = 18 if n <= 4 else 16
    detail_texts = [s.get("detail") or "" for s in steps]
    detail_size = _shrink_if_long(detail_texts, 16 if n <= 4 else 14)
    top, name_h, detail_h = BODY_Y + shift + 0.4, 1.0, 2.9 - shift
    for i, step in enumerate(steps):
        x = MARGIN + i * (width + STEP_GAP)
        _rect(slide, x, top, width, name_h, ACCENT)
        head = _textbox(slide, x + 0.1, top, width - 0.2, name_h, anchor=MSO_ANCHOR.MIDDLE)
        p = head.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        _write(p, step.get("name"), name_size, bold=True, color=WHITE)
        _rect(slide, x, top + name_h, width, detail_h, STRIPE, line=LINE)
        body = _textbox(slide, x + 0.1, top + name_h + 0.1, width - 0.2, detail_h - 0.2, fit=True)
        _write(body.text_frame.paragraphs[0], step.get("detail"), detail_size)
        if i < n - 1:
            _rect(slide, x + width + 0.08, top + name_h / 2 - 0.2, STEP_GAP - 0.16, 0.4, ACCENT, shape=MSO_SHAPE.RIGHT_ARROW)


def _page_number(slide, number: int):
    box = _textbox(slide, NUM_X, NUM_Y, NUM_W, NUM_H, anchor=MSO_ANCHOR.MIDDLE)
    p = box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    _write(p, str(number), 12, color=MUTED)


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
_FOLDER_AT = re.compile(r"(?:" + "|".join(map(re.escape, mirror_folders())) + rf"){_SEP}(?:\.?{_SEP})*(\d+)", re.I)
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
# 번호 뒤에 붙어도 낱말이 아닌 단위: `3.5배`·`3~5종`·`12%`·`5G`·`3rd`·`3단계`·`24시간`·`30여곳`·`100MB`·`10Gbps`.
# 센 단위(`_UNIT_STRONG`: 개사 개 건 종 명 곳 년 월 일 회 배 억 천 원 단계 시간 …, 영문 `KB MB GB Mbps Gbps`·`K`)는 낱말 끝이거나 뒤에 조사·접미(`가 이 을 를 의 에 에서 에는 로 와 도 만 까지 간 씩 급 …`)가 붙어도 수량이다:
# `partners/2개사가`·`customers/100명의`·`solutions/3개를`·`projects/6개월의`·`research/2026년에는`·`solutions/100MB`. 약한 단위(`_UNIT_WEAK`: 만 인 대 위 등 분 차 조 `%` `G D M x` 서수)는 낱말 끝일 때만 수량이다
# (`solutions/3대로`·`3조차`·`3%a`·`3%의`·`3만이` 는 경로). 약한 단위 뒤에 다른 단위를 더 잇지 않는다(`조`+`차` 가 `조차` 로 읽히지 않게). 예외: 센 단위 앞의 `만`(`1만명`, `3천만원`).
# 조사와 같은 글자인 한 글자 단위(`만 인 등`)가 낱말 끝에 홀로 오면(`solutions/3만 참고`) 수량인지 가를 수 없어 경로로 지운다. 단위 뒤에 확장자가 오면(`3개.md`) 경로다.
_UNIT_WORDS = r"개사|개월|개소|개씩|가지|인당|단계|분기|시간|세대|차년도|년차|배속|여곳|여개사|여건|여명"
_UNIT_ENG = r"(?:[KMGT]?[Bb]ps|[KMGT]B|[kKmMgGtT]b|[kKmMgG]?Hz)"
_UNIT_STRONG = r"(?:" + _UNIT_WORDS + "|" + _UNIT_ENG + r"|[개건종명곳억천백원년월일회배점사社주Kk])"
_UNIT_WEAK = r"(?:[만인대위등분차조%GDMxX]|(?i:st|nd|rd|th))"
_PARTICLE = (
    r"(?:에서는|에서도|에서|에는|에도|에게|까지|부터|이나|으로|이며|이고|이다|입니다|인데|이라고|라고|처럼|보다|마다|만에|밖에|이상|이하|이내|미만|정도|짜리"
    r"|쯤|씩|간|급|째|별|이|가|은|는|을|를|의|에|로|와|과|도|만|나|며|고|인|당)"
)
_UNIT_TAIL = re.compile(
    r"^(?:[.~\-–]\d+)*(?:(?P<solo>" + _UNIT_WEAK + r")(?!\w)"
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
    return re.sub(r"%2f", "/", re.sub(r"%5c", lambda _m: chr(92), t, flags=re.I), flags=re.I)


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
        if n is not None and not self.has_ext_from(end + n):
            return False
        if self.url_end <= start:
            return False
        if self.knowledge_end <= start:
            return True
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
        e = end + (n or 0)  # 단위 뒤의 확장자(`3개.md`)·`/card.md` 도 파일 경로다
        if _EXT.match(tok, e):
            return True
        return e < len(tok) and bool(_SEP_CH.match(tok[e])) and self.has_ext_from(e + 1)


def has_internal_mark(text) -> bool:
    """고객에게 보이면 안 되는 내부 표시(사본 경로)가 든 글인가."""
    t = _normalize(_clean(text))
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


def _draw_body(slide, data: dict):
    """종류별로 그린다. 그릴 내용이 없는 장은 제목 + 핵심 메시지만 남는다."""
    kind = data.get("kind")
    if kind == "two_column":
        _draw_two_column(slide, data)
    elif kind == "table" and _list(data, "columns") and _list(data, "rows"):
        _draw_table(slide, data)
    elif kind == "steps" and any(isinstance(s, dict) for s in _list(data, "steps")):
        _draw_steps(slide, data)
    elif kind == "closing":
        _draw_closing(slide, data)
    elif kind in ("table", "steps"):
        _header(slide, data)
    else:
        _draw_bullets(slide, data)


def build_deck(
    outline: dict, out_path: Path, *, customer_name: str = "", today: date | None = None,
    source_labels: dict[str, str] | None = None, audience: str = "internal", contact: list[str] | None = None, stats: dict | None = None,
) -> int:
    """구성안으로 .pptx 를 쓰고 만든 슬라이드 수를 돌려준다.

    `audience="customer"`(고객용)는 발표자 노트를 비워 두고 내부 경로가 든 줄도 뺀다. `"internal"`(내부 검토용)은 노트에 출처(`source_labels`:
    경로→이름이 있으면 이름)와 확인 항목을 적는다. `contact` 는 마무리 장 맨 아래에 붙는 연락처 줄(서버가 만든 사람 정보로 채운다).
    `stats` 를 주면 고객용에서 뺀 줄 수를 `stats["removed"]` 로 돌려준다. 파일은 임시 파일에 쓴 뒤 바꿔치기한다 (쓰다 멈춰도 반쪽 파일이 남지 않는다)."""
    if audience not in ("customer", "internal"):
        raise ValueError(audience)
    source = (outline or {}).get("slides")
    if not isinstance(source, list) or not source:
        raise ValueError("구성안에 슬라이드가 없습니다.")
    today = today or date.today()
    stats = stats if stats is not None else {}
    stats["removed"] = 0
    slides = [_with_contact(s, contact) for s in source if isinstance(s, dict)]
    if audience == "customer":
        slides = [_scrub_customer(s, stats) for s in slides]  # 쪽을 나누기 전에 한 번: 줄 수가 쪽마다 겹쳐 세어지지 않는다
    pages = [page for s in slides for page in paginate(s)]
    if not pages:
        raise ValueError("구성안에 슬라이드가 없습니다.")
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    for number, data in enumerate(pages, start=1):
        slide = prs.slides.add_slide(blank)
        if data.get("kind") == "cover":
            _draw_cover(slide, data, customer_name, today)
        else:
            _draw_body(slide, data)
            _page_number(slide, number)
        if audience == "internal":
            _notes(slide, data, source_labels)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    try:
        prs.save(part)
        part.replace(out_path)
    finally:
        part.unlink(missing_ok=True)
    return len(pages)

"""팀 업무 보고 파일 만들기: 내용 모델(`reports.report_content`) 하나에서 MD · 워드(docx) · PPT(pptx) 를 뽑는다 (설계 §5.6, §5.6a).
확정된 보고에서만 부른다 (라우터가 확인). 파일은 `.part` 에 쓴 뒤 바꿔치기한다.
워드·MD 는 구역마다 소제목 → 보고 문구 1~2줄 → 표 순서이고, PPT 는 장마다 공간이 좁아 표·카드 중심이다."""
from __future__ import annotations

from pathlib import Path

FORMATS = {
    "pptx": ("PPT", "윗선 보고용 6장 (표지·한눈에·주요 성과와 영업기회·지난 계획 점검·다음 계획·이슈)"),
    "docx": ("워드 (docx)", "A4 문서, 구역마다 보고 문구와 표"),
    "md": ("MD", "메신저·위키에 붙여 넣기용 글"),
}
MEDIA_TYPES = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown; charset=utf-8",
}
MAX_ROWS = 10  # PPT 표 한 장의 최대 줄 (넘으면 다음 장으로 "(계속)")
FIRST_OPPS_ROWS = 4  # 주요 성과 장에서 영업기회 표가 쓸 수 있는 줄 (글 아래라 좁다)
SUMMARY_CAP = 3  # 주요 성과 장에 보이는 요약 줄 수 (나머지는 "외 n건", 전체는 워드·MD 에)
SUMMARY_SIZE, SUMMARY_GAP = 15, 8  # 요약 줄 글자 크기·줄 간격 (pt)
SUMMARY_BOX_H = 2.6  # in. 최대 4문단(3줄 + "외 n건"), 각각 2줄로 넘어가도 들어간다: 4 × (2 × 15 × 1.2 + 8) pt ≈ 2.6in

# 단위별 말: (이번, 지난, 다음)
WORDS = {"day": ("오늘", "지난 보고", "다음 평일"), "week": ("이번 주", "지난 주", "다음 주"), "month": ("이번 달", "지난 달", "다음 달")}

SECTION_NAMES = {"kpi": "요약 숫자", "opps": "영업기회 단계 변화", "proposals": "제안 진행", "check": "지난 계획 점검", "plan": "다음 계획", "issues": "이슈·건의"}


WORKSPACE_TITLE = "사업 방향·과제"
WORKSPACE_LEAD = "이번 기간 새 이슈 {issues_new}건, 닫은 이슈 {issues_closed}건."


def _has_workspace(c: dict) -> bool:
    ws = c.get("workspace") or {}
    return any(ws.get(k) for k in ("strategies", "issues", "initiatives"))


def _words(content: dict) -> tuple[str, str, str]:
    return WORDS.get(content.get("unit"), WORDS["week"])


def _chunks(rows: list, size: int = MAX_ROWS) -> list[list]:
    return [rows[i:i + size] for i in range(0, len(rows), size)] or [[]]


# ---- MD ----

def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _md_table(head: list[str], rows: list) -> list[str]:
    if not rows:
        return []
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return out + [""]


def _md(c: dict) -> str:
    notes, words = c["notes"], _words(c)
    n_check = len(c["check"])
    head = [f"# {c['title']}", "", f"- 기간: {c['period']}", f"- 다음 기간: {c['next_period']}"]
    made = "솔박스 기록 기준" + (f", {c['confirmed_at']} 확정" if c["confirmed_at"] else "")
    head.append(f"- 작성: {c['author']} ({made})" if c["author"] else f"- 작성: {made}")
    out = head + [""]

    def section(no: int, title: str, key: str | None, body: list[str]) -> None:
        out.extend([f"## {no}. {title}", ""])
        if key:
            out.extend(line for ln in notes[key] for line in (ln, ""))
        out.extend(body)

    section(1, "요약 숫자", "kpi", _md_table(["항목", "값"], [(a, f"{b} ({t})" if t else b) for a, b, t in c["kpis"]]))
    section(2, "주요 성과", None, [f"- {s}" for s in c["summary"]] + [""] if c["summary"] else ["작성된 요약 글이 없습니다.", ""])
    section(3, "영업기회 단계 변화", "opps", _md_table(["영업기회", "고객", "변화"], c["opps"]))
    section(4, "제안 진행", "proposals", _md_table(["제안", "고객", "단계"], c["proposals"]))
    section(5, f"지난 계획 점검 (완료 {c['done_count']}/{n_check})", "check", _md_table(["계획", "담당", "결과", "메모"], c["check"]))
    plan_body = ([f"**방향**: {c['direction']}", ""] if c["direction"] else []) + _md_table(["할 일", "담당", "기한", "비고"], c["plan"])
    section(6, f"{words[2]} 계획 ({c['next_period']})", "plan", plan_body)
    section(7, "이슈·건의", "issues", [f"- {s}" for s in c["issues"]] + [""] if c["issues"] else [])
    ws = c.get("workspace") or {}
    if any(ws.get(k) for k in ("strategies", "issues", "initiatives")):
        body = [WORKSPACE_LEAD.format(**ws), ""]
        body += ["### 사업 방향", ""] + _md_table(["방향", "상태", "과제", "비고"], ws["strategies"])
        body += ["### 열린 이슈", ""] + _md_table(["이슈", "상태", "담당"], ws["issues"])
        body += ["### 과제 진행", ""] + _md_table(["과제", "종류", "상태 변화", "진행 메모"], ws["initiatives"])
        section(8, WORKSPACE_TITLE, None, body)
    if c.get("legacy_undated"):
        out.extend([f"> 기능 도입 전 기록 {c['legacy_undated']}건은 완료·단계 변경 날짜를 몰라 숫자에서 빠졌습니다.", ""])
    return "\n".join(out).rstrip("\n") + "\n"


# ---- DOCX ----

NAVY, SOFT_GRAY, HEAD_FILL, GREEN, AMBER, MUTED = "1F3A8A", "F1F5F9", "E8EDF8", "15803D", "B45309", "64748B"
DOC_FONT = "맑은 고딕"
DOC_WIDTHS = {  # cm, 합 17
    "opps": [7, 4, 6], "proposals": [7, 4, 6], "check": [7, 2.2, 2.2, 5.6], "plan": [9, 2.2, 2, 3.8],
}


def _rgb(hexcolor: str):
    from docx.shared import RGBColor

    return RGBColor.from_string(hexcolor)


def _set_font(run, size: float | None = None, bold: bool | None = None, color: str | None = None) -> None:
    from docx.oxml.ns import qn

    run.font.name = DOC_FONT
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), DOC_FONT)
    if size:
        from docx.shared import Pt

        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color:
        run.font.color.rgb = _rgb(color)


def _shade(cell, fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shd)


def _docx(c: dict, path: Path) -> None:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    notes, words = c["notes"], _words(c)
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2)
    sec.top_margin = sec.bottom_margin = Cm(1.8)
    for name in ("Normal", "Heading 2"):
        st = doc.styles[name]
        st.font.name = DOC_FONT
        st.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), DOC_FONT)
        for attr in ("w:ascii", "w:hAnsi"):
            st.element.rPr.rFonts.set(qn(attr), DOC_FONT)
    doc.styles["Normal"].font.size = Pt(10)

    def heading(text: str) -> None:
        p = doc.add_paragraph(style="Heading 2")
        _set_font(p.add_run(text), 12.5, True, NAVY)
        p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(8), Pt(4)
        p.paragraph_format.keep_with_next = True

    def note(lines: list[str]) -> None:
        for line in lines:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.left_indent = Cm(0.3)
            _set_font(p.add_run("○ " + line), 10)

    def table(head: list[str], rows: list, widths: list[float] | None = None) -> None:
        if not rows:
            return
        t = doc.add_table(rows=1, cols=len(head))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        flag = OxmlElement("w:tblHeader")  # 쪽이 넘어가면 머리글 줄을 되풀이한다
        flag.set(qn("w:val"), "true")
        t.rows[0]._tr.get_or_add_trPr().append(flag)
        for i, h in enumerate(head):
            cell = t.rows[0].cells[i]
            cell.text = ""
            _set_font(cell.paragraphs[0].add_run(h), 9.5, True)
            _shade(cell, HEAD_FILL)
        for row in rows:
            cells = t.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = ""
                color = GREEN if v == "완료" else AMBER if v in ("진행 중", "미착수") else None
                _set_font(cells[i].paragraphs[0].add_run(str(v)), 9.5, color=color)
        if widths:
            t.autofit = False
            for i, w in enumerate(widths):
                t.columns[i].width = Cm(w)
                for r in t.rows:
                    r.cells[i].width = Cm(w)
        doc.add_paragraph().paragraph_format.space_after = Pt(0)

    p = doc.add_paragraph()
    _set_font(p.add_run(c["title"]), 20, True)
    p = doc.add_paragraph()
    _set_font(p.add_run(f"{c['period']}" + (f" · 작성 {c['author']}" if c["author"] else "")), 10, color=MUTED)
    if c["summary"]:
        box = doc.add_table(rows=1, cols=1)
        box.style = "Table Grid"
        cell = box.rows[0].cells[0]
        _shade(cell, SOFT_GRAY)
        cell.text = ""
        _set_font(cell.paragraphs[0].add_run("핵심 요약"), 10, True)
        for s in c["summary"]:
            _set_font(cell.add_paragraph().add_run("• " + s), 10)
        doc.add_paragraph()

    heading("1. 요약 숫자")
    note(notes["kpi"])
    cards = c["kpis"]
    for part in (cards[:4], cards[4:]):
        table([a for a, _, _ in part], [[b + (f" ({t})" if t else "") for _, b, t in part]], [4.25] * 4)
    heading("2. 영업기회 단계 변화")
    note(notes["opps"])
    table(["영업기회", "고객", "변화"], c["opps"], DOC_WIDTHS["opps"])
    heading("3. 제안 진행")
    note(notes["proposals"])
    table(["제안", "고객", "단계"], c["proposals"], DOC_WIDTHS["proposals"])
    heading(f"4. 지난 계획 점검 (완료 {c['done_count']}/{len(c['check'])})")
    note(notes["check"])
    table(["계획", "담당", "결과", "메모"], c["check"], DOC_WIDTHS["check"])
    heading(f"5. {words[2]} 계획 ({c['next_period']})")
    note(notes["plan"])
    if c["direction"]:
        p = doc.add_paragraph()
        _set_font(p.add_run("방향  "), 10, True)
        _set_font(p.add_run(c["direction"]), 10)
    table(["할 일", "담당", "기한", "비고"], c["plan"], DOC_WIDTHS["plan"])
    heading("6. 이슈·건의")
    note(notes["issues"])
    for s in c["issues"]:
        _set_font(doc.add_paragraph().add_run("• " + s), 10)
    if _has_workspace(c):
        ws = c["workspace"]
        heading(f"7. {WORKSPACE_TITLE}")
        note([WORKSPACE_LEAD.format(**ws)])
        table(["방향", "상태", "과제", "비고"], ws["strategies"], [6, 2.5, 4, 4.5])
        table(["열린 이슈", "상태", "담당"], ws["issues"], [9, 3, 5])
        table(["과제", "종류", "상태 변화", "진행 메모"], ws["initiatives"], [5, 2, 3.5, 6.5])
    if c.get("legacy_undated"):
        p = doc.add_paragraph()
        _set_font(p.add_run(f"기능 도입 전 기록 {c['legacy_undated']}건은 완료·단계 변경 날짜를 몰라 숫자에서 빠졌습니다."), 9, color=MUTED)
    doc.save(str(path))


# ---- PPTX ----

def _pptx(c: dict, path: Path) -> None:
    from pptx.dml.color import RGBColor as P
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    from app.pptx_template import blank_presentation

    this, last, nxt = _words(c)
    prs = blank_presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    W, H = prs.slide_width, prs.slide_height
    blank = prs.slide_layouts[6]
    navy, ink, muted, soft, line = P(0x1F, 0x3A, 0x8A), P(0x1E, 0x29, 0x3B), P(0x64, 0x74, 0x8B), P(0xEE, 0xF2, 0xFA), P(0xD5, 0xDC, 0xE8)
    white, amber, green = P(0xFF, 0xFF, 0xFF), P(0xB4, 0x53, 0x09), P(0x15, 0x80, 0x3D)
    counter = {"n": 0}

    def text(sl, x, y, w, h, s, size=16, bold=False, color=ink, align=None, gap=8):
        tb = sl.shapes.add_textbox(x, y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        for i, ln in enumerate(s if isinstance(s, list) else [s]):
            par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            run = par.add_run()
            run.text = ln
            run.font.size, run.font.bold, run.font.name = Pt(size), bold, DOC_FONT
            run.font.color.rgb = color
            par.space_after = Pt(gap)
            if align:
                par.alignment = align

    def rect(sl, shape, x, y, w, h, fill, outline=None):
        r = sl.shapes.add_shape(shape, x, y, w, h)
        r.fill.solid()
        r.fill.fore_color.rgb = fill
        if outline is None:
            r.line.fill.background()
        else:
            r.line.color.rgb = outline
        return r

    def frame(title: str):
        counter["n"] += 1
        sl = prs.slides.add_slide(blank)
        rect(sl, MSO_SHAPE.RECTANGLE, 0, 0, W, Inches(0.12), navy)
        text(sl, Inches(0.6), Inches(0.4), Inches(12), Inches(0.8), title, 28, True)
        text(sl, Inches(0.6), Inches(6.95), Inches(10), Inches(0.4), f"{c['title']} · {c['period']}", 10, color=muted)
        text(sl, Inches(12.2), Inches(6.95), Inches(0.7), Inches(0.4), str(counter["n"]), 10, color=muted, align=PP_ALIGN.RIGHT)
        return sl

    def ptable(sl, head, rows, y, widths):
        shp = sl.shapes.add_table(len(rows) + 1, len(head), Inches(0.6), y, Inches(sum(widths)), Inches(0.45) * (len(rows) + 1))
        t = shp.table
        for i, w in enumerate(widths):
            t.columns[i].width = Inches(w)
        for ri, row in enumerate([head] + list(rows)):
            for ci, v in enumerate(row):
                cell = t.cell(ri, ci)
                cell.text = str(v) or " "
                run = cell.text_frame.paragraphs[0].runs[0]
                run.font.size, run.font.name, run.font.bold = Pt(13), DOC_FONT, ri == 0
                run.font.color.rgb = white if ri == 0 else green if v == "완료" else amber if v in ("미착수", "진행 중", "지난 계획") else ink
                cell.fill.solid()
                cell.fill.fore_color.rgb = navy if ri == 0 else (soft if ri % 2 == 0 else white)

    def paged(title: str, head, rows, widths, y, lead=None, none="해당 내용이 없습니다."):
        """표 한 장 (줄이 많으면 다음 장으로 나눠 "(계속)"). `lead(sl)` 은 첫 장 맨 위에 넣을 것(방향 줄)과 표 시작 높이를 정한다."""
        for page, chunk in enumerate(_chunks(rows)):
            sl = frame(title if page == 0 else f"{title} (계속)")
            top = y
            if page == 0 and lead:
                top = lead(sl) or y
            if chunk:
                ptable(sl, head, chunk, top, widths)
            else:
                text(sl, Inches(0.6), top, Inches(12), Inches(0.6), none, 18, color=muted)

    # 1 표지
    sl = prs.slides.add_slide(blank)
    counter["n"] += 1
    rect(sl, MSO_SHAPE.RECTANGLE, 0, 0, W, H, navy)
    text(sl, Inches(0.9), Inches(2.4), Inches(11), Inches(1.2), c["title"], 48, True, white)
    text(sl, Inches(0.9), Inches(3.6), Inches(11), Inches(0.6), c["period"], 22, False, P(0xDB, 0xE4, 0xFF))
    text(sl, Inches(0.9), Inches(5.6), Inches(11), Inches(0.5), (f"작성 {c['author']} · " if c["author"] else "") + "솔박스 기록 기준", 14, False, P(0xC7, 0xD2, 0xFE))

    # 2 한눈에
    sl = frame(f"{this} 한눈에")
    for i, (name, value, tail) in enumerate(c["kpis"]):
        x, y = Inches(0.6 + (i % 4) * 3.05), Inches(1.6 + (i // 4) * 2.4)
        card = rect(sl, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, Inches(2.85), Inches(2.1), soft, line)
        card.adjustments[0] = 0.08
        text(sl, x + Inches(0.25), y + Inches(0.25), Inches(2.4), Inches(1), value, 44, True, amber if tail == "지연" else navy)
        text(sl, x + Inches(0.25), y + Inches(1.3), Inches(2.5), Inches(0.5), name + (f"  {tail}" if tail else ""), 15, color=muted)

    # 3 주요 성과 + 영업기회 (표가 길면 나머지는 다음 장)
    first, rest = c["opps"][:FIRST_OPPS_ROWS], c["opps"][FIRST_OPPS_ROWS:]
    sl = frame("주요 성과")
    shown = c["summary"][:SUMMARY_CAP]
    bullets = ["■ " + s for s in shown] + ([f"외 {len(c['summary']) - SUMMARY_CAP}건"] if len(c["summary"]) > SUMMARY_CAP else [])
    text(sl, Inches(0.6), Inches(1.3), Inches(12), Inches(SUMMARY_BOX_H), bullets or ["작성된 요약 글이 없습니다."], SUMMARY_SIZE, color=ink if bullets else muted, gap=SUMMARY_GAP)
    text(sl, Inches(0.6), Inches(4.0), Inches(12), Inches(0.5), "영업기회 단계 변화", 18, True, navy)
    if first:
        ptable(sl, ["영업기회", "고객", "변화"], first, Inches(4.5), [5.0, 3.0, 4.1])
    else:
        text(sl, Inches(0.6), Inches(4.5), Inches(12), Inches(0.6), "단계가 바뀌거나 새로 생긴 영업기회가 없습니다.", 16, color=muted)
    for chunk in _chunks(rest) if rest else []:
        sl = frame("영업기회 단계 변화 (계속)")
        ptable(sl, ["영업기회", "고객", "변화"], chunk, Inches(1.6), [5.0, 3.0, 4.1])

    # 4 지난 계획 점검
    paged(f"{last} 계획 점검 — 완료 {c['done_count']}/{len(c['check'])}", ["계획", "담당", "결과", "메모"], c["check"], [5.2, 1.6, 1.6, 3.7], Inches(1.6), none="점검할 지난 계획이 없습니다.")

    # 5 다음 계획
    def direction(sl):
        if c["direction"]:
            text(sl, Inches(0.6), Inches(1.35), Inches(12), Inches(0.5), "방향  " + c["direction"], 16, True, navy)
            return Inches(2.0)
        return Inches(1.6)

    paged(f"{nxt} 계획 ({c['next_period']})", ["할 일", "담당", "기한", "비고"], c["plan"], [6.4, 1.7, 1.5, 2.5], Inches(2.0), lead=direction, none="다음 계획 항목이 없습니다.")

    # 6 이슈·건의
    pages = _chunks(c["issues"], 7)
    for page, chunk in enumerate(pages):
        sl = frame("이슈·건의" if page == 0 else "이슈·건의 (계속)")
        text(sl, Inches(0.6), Inches(1.6), Inches(12), Inches(4), ["■ " + s for s in chunk] or ["적어 둔 이슈·건의가 없습니다."], 20, color=ink if chunk else muted)

    # 7 사업 방향·과제 (AI 코워크 ①)
    if _has_workspace(c):
        ws = c["workspace"]
        paged(f"{WORKSPACE_TITLE} — 사업 방향", ["방향", "상태", "과제", "비고"], ws["strategies"], [5.0, 1.8, 2.8, 2.5], Inches(1.6), none="합의된 사업 방향이 없습니다.")
        paged(f"{WORKSPACE_TITLE} — 열린 이슈", ["이슈", "상태", "담당"], ws["issues"], [7.5, 2.1, 2.5], Inches(1.6), none="열린 이슈가 없습니다.")
        paged(f"{WORKSPACE_TITLE} — 과제 진행", ["과제", "종류", "상태 변화", "진행 메모"], ws["initiatives"], [4.0, 1.3, 2.6, 4.2], Inches(1.6), none="이번 기간에 움직인 과제가 없습니다.")

    prs.save(str(path))


# ---- 공통 ----

def write(content: dict, fmt: str, out_dir: Path, stem: str) -> Path:
    """`fmt`(pptx / docx / md) 파일을 `out_dir/<stem>.<fmt>` 로 쓴다 (.part 에 쓴 뒤 바꿔치기). 형식이 목록 밖이면 ValueError."""
    if fmt not in FORMATS:
        raise ValueError(f"알 수 없는 파일 형식: {fmt}")
    from app.deck import replace_retry

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{stem}.{fmt}"
    part = out_dir / f"{stem}.{fmt}.part"
    try:
        if fmt == "md":
            part.write_text(_md(content), encoding="utf-8")
        elif fmt == "docx":
            _docx(content, part)
        else:
            _pptx(content, part)
        replace_retry(part, final)
    finally:
        part.unlink(missing_ok=True)
    return final


def listing(folder: Path, file_names: dict) -> list[dict]:
    """화면에 보일, 만들어 둔 파일: [{fmt, name, built_at(파일 수정 시각)}] (목록 순서, 파일이 없으면 뺀다)."""
    from datetime import datetime

    out = []
    for fmt in FORMATS:
        name = (file_names or {}).get(fmt)
        path = folder / name if name else None
        if path is not None and path.is_file():
            out.append({"fmt": fmt, "name": name, "built_at": datetime.fromtimestamp(path.stat().st_mtime)})
    return out

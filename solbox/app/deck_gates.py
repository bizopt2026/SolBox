"""제안서 PPT 품질 검사 (베타 AF). `deck.build_deck` 이 파일을 쓰기 직전에 부른다.

paperlogy-ppt 의 검사 규칙(GATES.md 의 ppt_lint · brandlogy_lint) 가운데, 구성안으로 python-pptx 가 만든 덱에 적용되는 것만 옮겼다.
**만들기를 막지 않는다**: 결과(목록)만 돌려주고, 화면이 제안서 상자에 "품질 검사: 경고 N건" 으로 보인다.
원래 규칙에서 ERROR 인 것은 `level="error"`, WARN 인 것은 `"warn"` 이다. 각 항목의 `gate` 는 GATES.md 의 번호·이름이다.

옮기지 않은 것: 빌드 스크립트(build.js)를 읽는 검사(#4·#5·#6·#16·#22 …), 차트·삽입 그림 검사(#8·#17·#18·#23 …: 구성안에 차트·그림이 없다),
카드 안 여백 실측 4형제(#19·#24·#26·#27: 카드와 글을 한 덩어리로 가운데 놓으므로 좌표로는 재지 않는다).
"""

from __future__ import annotations

import re

from pptx.util import Inches

from app import deck

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS = {"a": _A, "p": _P}
_EMU = 914400

MAX_FINDINGS = 60        # 저장·표시하는 항목 수 한도
HEADLINE_MAX_CHARS = 30  # 대제목은 30자 안쪽 (DESIGN-SYSTEM §3)

# 승인 팔레트 (GATES §7): 블루 6 + 강조 오렌지 1 + 중립 24
PALETTE_BLUE = {"002FA7", "001C64", "4D6DC1", "99ACDB", "D9E0F2", "EAEEF8", "FF7A00"}
PALETTE_NEUTRAL = {
    "FFFFFF", "000000", "222222", "3A4654", "5A6678", "8A94A2", "5F6B78", "EFF2F6", "F4F6F9", "B7BDC8", "D5DAE3", "F0F0F0",
    "404040", "45515E", "8E8E93", "5F5F5F", "F2F3F5", "DDE1E8", "EEF0F4", "F6F7FB", "EEEEEE", "DCE3EC", "ECEEF2", "D0D5DD",
}
PALETTE = PALETTE_BLUE | PALETTE_NEUTRAL
# 폐기 색 (GATES §7-4: #6b-2 코랄·차콜·웜그레이, #8b 옛 블루)
PALETTE_RETIRED = {
    "D97757", "292524", "6B6862", "F4F2EE", "8A8378", "DAD6CE", "E7E2DE", "F4EEE8", "D9A38D", "A8A29E", "8B857D", "C8BEB4",
    "4F6EF1", "0014D3", "7E94F5", "A8B7F8", "DDE3FB", "F8F9FF", "1C6DD0", "0B2E4F", "5B9BD5", "A9C9E8", "DBE9F6", "F1F6FB",
}
FACES = {deck.FONT, deck.FONT_B}

_DASH = re.compile("[—–]")
_CIRCLED = re.compile("[①-⑳⓪❶-❿㉑-㉟]")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
_EMOJI_OK = {"\U0001F4C1", "\U0001F4AC"}  # 폴더·말풍선만 기능 아이콘으로 예외 (#3)
_QUOTES = re.compile("[「」『』]")
# brandlogy_lint ERROR (GATES §5-1)
_COPY_ERRORS = (
    ("솔직히 류", re.compile("솔직(?:히|하게|한|함)"), "'솔직히' 같은 자격 선언은 빼고 바로 본론을 쓰세요"),
    ("강조 도입부", re.compile("(?:정직|정확)(?:하게|히)\\s*(?:말|짚|얘기|보면)"), "'정확히 말하면' 같은 도입부 없이 바로 본론을 쓰세요"),
    ("군더더기 부사", re.compile("근본적으로|기본적으로"), "'근본적으로'·'기본적으로' 는 지우세요"),
    ("이중피동", re.compile("되어지|되어진|되어질|되어집|되어졌|되여지"), "'되어지다' 는 '되다' 로 쓰세요"),
    ("일본어투", re.compile("에 다름 아니|지 않으면 안 (?:되|된|됩|됐)"), "번역투입니다. '~해야 합니다' 처럼 쓰세요"),
    ("관용 번역체", re.compile("아무리 강조해도 지나치"), "번역투입니다. 우리말로 바로 강조하세요"),
    ("갈림길", re.compile("갈림길"), "'갈림길' 대신 선택 상황을 풀어 쓰세요"),
)
# brandlogy_lint WARN (GATES §5-2). 덱 전체 글을 한 덩어리로 본다
_CONTRAST = re.compile("지 않습니다|이 아닙니다|가 아닙니다|지 않아요|는 게 아니라|것이 아니라|이 아니라|가 아니라")
_FILLER = re.compile("사실은|사실 |어떻게 보면|말하자면|다시 말해|결국 말이죠")
_ELLIPSIS = re.compile("…|\\.\\.\\.")


def _finding(level: str, gate: str, slide: int | None, text: str) -> dict:
    return {"level": level, "gate": gate, "slide": slide, "text": text}


def _quote(text: str, limit: int = 24) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _is_blue(hex6: str) -> bool:
    """파란 계열 판정 (GATES §7-3): B > R+16 이고 B > G+8."""
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    return b > r + 16 and b > g + 8


class _Shape:
    """도형 하나의 좌표(인치)와 글. 그룹은 쓰지 않으므로 맨 위 도형만 본다."""

    def __init__(self, el):
        self.el = el
        self.tag = el.tag.split("}")[1]
        c = el.find(".//p:cNvPr", _NS)
        self.name = c.get("name", "") if c is not None else ""
        self.id = c.get("id", "") if c is not None else ""
        off = el.find(".//a:off", _NS) if self.tag != "graphicFrame" else el.find("p:xfrm/a:off", _NS)
        ext = el.find(".//a:ext", _NS) if self.tag != "graphicFrame" else el.find("p:xfrm/a:ext", _NS)
        self.x = int(off.get("x")) / _EMU if off is not None else 0.0
        self.y = int(off.get("y")) / _EMU if off is not None else 0.0
        self.w = int(ext.get("cx")) / _EMU if ext is not None else 0.0
        self.h = int(ext.get("cy")) / _EMU if ext is not None else 0.0
        if self.tag == "graphicFrame":  # 표는 행 높이의 합이 실제 높이다
            rows = el.findall(".//a:tr", _NS)
            if rows:
                self.h = sum(int(r.get("h", "0")) for r in rows) / _EMU
        self.body = el.find("p:txBody", _NS)
        self.text = "".join(t.text or "" for t in self.body.iter(f"{{{_A}}}t")) if self.body is not None else ""
        self.is_field = self.body is not None and self.body.find(".//a:fld", _NS) is not None
        self.geom = (el.find(".//a:prstGeom", _NS).get("prst") if el.find(".//a:prstGeom", _NS) is not None else "")
        self.is_textbox = el.find("p:nvSpPr/p:cNvSpPr[@txBox='1']", _NS) is not None

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


def _text_need(shape: _Shape) -> float:
    """글상자 안 글의 높이 어림(인치): 문단마다 가장 큰 글자 크기와 줄 간격으로 줄 수를 센다 (#28-W 의 잉크 높이 어림)."""
    body_pr = shape.body.find("a:bodyPr", _NS)
    inset_x = sum(int(body_pr.get(k, "91440")) for k in ("lIns", "rIns")) / _EMU if body_pr is not None else 0.2
    inset_y = sum(int(body_pr.get(k, "45720")) for k in ("tIns", "bIns")) / _EMU if body_pr is not None else 0.1
    fit = body_pr.find("a:normAutofit", _NS) if body_pr is not None else None
    scale = int(fit.get("fontScale", "100000")) / 100000 if fit is not None else 1.0
    wrap = body_pr is None or body_pr.get("wrap") != "none"
    total = 0.0
    for p in shape.body.findall("a:p", _NS):
        sizes = [int(r.get("sz")) / 100 for r in p.iter(f"{{{_A}}}rPr") if r.get("sz")]
        if not sizes:
            continue
        pt = max(sizes) * scale
        ppr = p.find("a:pPr", _NS)
        indent = int(ppr.get("marL", "0")) / _EMU if ppr is not None else 0.0
        pct = p.find("a:pPr/a:lnSpc/a:spcPct", _NS)
        ls = int(pct.get("val")) / 100000 if pct is not None else 1.0
        after = p.find("a:pPr/a:spcAft/a:spcPts", _NS)
        before = p.find("a:pPr/a:spcBef/a:spcPts", _NS)
        text = "".join((n.text or "") if n.tag.endswith("}t") else "\n" for n in p.iter() if n.tag.endswith("}t") or n.tag.endswith("}br"))
        lines = deck._est_lines(text, pt, shape.w - inset_x - indent) if wrap else 1 + text.count("\n")
        # 실제 줄 높이는 글꼴 기본(약 1.1em)에 가깝다: 넉넉한 어림(1.25)이 아니라 1.1 로 재서, 넘친 것이 분명할 때만 알린다
        total += lines * pt * 1.1 * ls / 72
        total += sum(int(x.get("val")) / 100 / 72 * scale for x in (after, before) if x is not None)
    return total + inset_y


def _check_copy(slide_no: int | None, text: str, out: list[dict]):
    """글자 규칙: 줄표·원형숫자·이모지 (#1 #2 #3), 한글 낫표(DESIGN-SYSTEM §3), brandlogy_lint ERROR."""
    if not text.strip():
        return
    if _DASH.search(text):
        out.append(_finding("error", "#1 줄표", slide_no, f"줄표(— –)가 있습니다. 쌍점·쉼표·물결(~)로 바꾸세요: “{_quote(text)}”"))
    if _CIRCLED.search(text):
        out.append(_finding("error", "#2 원형숫자", slide_no, f"원형숫자(①②…)가 있습니다. 1, 2, 3 으로 쓰세요: “{_quote(text)}”"))
    if any(ch not in _EMOJI_OK for ch in _EMOJI.findall(text)):
        out.append(_finding("error", "#3 이모지", slide_no, f"이모지가 있습니다. 지우세요: “{_quote(text)}”"))
    if _QUOTES.search(text):
        out.append(_finding("warn", "문장부호", slide_no, f"낫표(「」『』)가 있습니다. 작은따옴표나 큰따옴표로 바꾸세요: “{_quote(text)}”"))
    for name, pattern, advice in _COPY_ERRORS:
        if pattern.search(text):
            out.append(_finding("error", f"카피 · {name}", slide_no, f"{advice}: “{_quote(text)}”"))
    if "!!" in text:
        out.append(_finding("warn", "카피 · 느낌표 남발", slide_no, f"느낌표가 이어집니다: “{_quote(text)}”"))
    if _ELLIPSIS.search(text) and not text.rstrip().endswith("…") or text.count("…") > 1:
        out.append(_finding("warn", "카피 · 줄임표", slide_no, f"줄임표가 있습니다. 문장을 끝까지 쓰세요: “{_quote(text)}”"))


def _check_runs(slide_no: int, root, out: list[dict]):
    """글꼴·굵게·윤곽선·글자 크기·글자색 (#5b-1, #5b-2, #5c-2, #5c-3, #8f, 최소 글자 8pt)."""
    faces, bold, italic, no_outline, bad_outline, small, orange = set(), 0, 0, 0, 0, set(), 0
    for rpr in root.iter(f"{{{_A}}}rPr"):
        for tag in ("latin", "ea", "cs"):
            face = rpr.find(f"a:{tag}", _NS)
            if face is not None and face.get("typeface") not in FACES:
                faces.add(face.get("typeface"))
        if rpr.find("a:latin", _NS) is None:
            faces.add("(글꼴 지정 없음)")
        bold += rpr.get("b") == "1"
        italic += rpr.get("i") == "1"
        if rpr.get("sz") and int(rpr.get("sz")) < deck.MIN_FONT_PT * 100:
            small.add(int(rpr.get("sz")) / 100)
        color = rpr.find("a:solidFill/a:srgbClr", _NS)
        white = color is not None and color.get("val", "").upper() == "FFFFFF"
        ln = rpr.find("a:ln", _NS)
        if ln is None or rpr[0] is not ln:
            no_outline += not white  # 흰 글자는 윤곽선을 넣지 않는다 (deck._style_run 의 이유)
        else:
            alpha = ln.find("a:solidFill/a:srgbClr/a:alpha", _NS)
            bad_outline += ln.get("w") != "12700" or alpha is None or alpha.get("val") != "0"
        orange += color is not None and color.get("val", "").upper() == "FF7A00"
    if faces:
        out.append(_finding("error", "#5b-1 승인 외 폰트", slide_no, "승인 글꼴(나눔스퀘어_ac, 나눔스퀘어_ac Bold) 밖의 글꼴이 있습니다: " + ", ".join(sorted(faces))))
    if bold:
        out.append(_finding("error", "#5b-2 굵게 속성", slide_no, f"굵게 속성을 쓴 글자가 {bold}곳 있습니다. 굵기는 Bold 페이스로 줍니다"))
    if italic:
        out.append(_finding("error", "기울임 한글", slide_no, f"기울임 글자가 {italic}곳 있습니다. 한글은 기울이지 않습니다"))
    if no_outline:
        out.append(_finding("error", "#5c-2 윤곽선 없는 텍스트 런", slide_no, f"글꼴 윤곽선이 없는 글자가 {no_outline}곳 있습니다"))
    if bad_outline:
        out.append(_finding("error", "#5c-3 윤곽선 규격", slide_no, f"윤곽선이 1pt·투명도 100% 가 아닌 글자가 {bad_outline}곳 있습니다"))
    if small:
        out.append(_finding("error", "최소 글자 8pt", slide_no, f"{min(small):g}pt 글자가 있습니다. 8pt 보다 작게 쓰지 않습니다"))
    if orange:
        out.append(_finding("error", "#8f 오렌지 글자색", slide_no, f"강조 오렌지를 글자색으로 쓴 곳이 {orange}곳 있습니다. 오렌지는 도형에만 씁니다"))


def _check_colors(slide_no: int, root, out: list[dict]):
    """승인 팔레트만 (#14, #14-W, #6b-2, #8b)."""
    used = {c.get("val", "").upper() for c in root.iter(f"{{{_A}}}srgbClr")}
    retired = sorted(used & PALETTE_RETIRED)
    blue = sorted(c for c in used - PALETTE - PALETTE_RETIRED if len(c) == 6 and _is_blue(c))
    other = sorted(c for c in used - PALETTE - PALETTE_RETIRED if len(c) == 6 and not _is_blue(c))
    if retired:
        out.append(_finding("error", "#6b-2·#8b 폐기 팔레트", slide_no, "폐기한 색이 있습니다: " + ", ".join(retired)))
    if blue:
        out.append(_finding("error", "#14 팔레트 밖 파란 계열", slide_no, "승인 팔레트 밖의 파란 색이 있습니다: " + ", ".join(blue)))
    if other:
        out.append(_finding("warn", "#14-W 팔레트 밖 중립색", slide_no, "승인 팔레트 밖의 색이 있습니다: " + ", ".join(other)))


def _check_layout(slide_no: int, shapes: list[_Shape], is_cover: bool, out: list[dict]):
    slide_w, slide_h, tol = deck.SLIDE_W, deck.SLIDE_H, 0.01
    ids = [s.id for s in shapes]
    if len(ids) != len(set(ids)):
        out.append(_finding("error", "#0b 도형 id 중복", slide_no, "도형 번호가 겹칩니다 (파워포인트가 복구 창을 띄울 수 있습니다)"))
    outside = [s for s in shapes if s.x < -tol or s.y < -tol or s.right > slide_w + tol or s.bottom > slide_h + tol]
    if outside:
        out.append(_finding("error", "슬라이드 밖 도형", slide_no, f"슬라이드 밖으로 나간 도형이 {len(outside)}개 있습니다 ({outside[0].name})"))
    band = next((s for s in shapes if s.name == "결론 띠"), None)
    if not is_cover:
        # 본문 콘텐츠 최대 y 6.05 (DESIGN-SYSTEM §4 RESERVED Y BANDS, §6 콘텐츠 max y). 결론 띠가 위로 자랐으면 띠 위 0.15 까지
        limit = min(deck.BODY_BOTTOM, band.y - deck.BAND_GAP) if band is not None else deck.BODY_BOTTOM
        low = [s for s in shapes if s is not band and s.name != "배경" and s.y < deck.BAND_Y and s.bottom > limit + tol and s.y >= deck.BODY_Y - tol]
        if low:
            out.append(_finding("error", "본문 최대 y 6.05", slide_no, f"본문이 아래 한계({limit:.2f}in)를 넘습니다: {low[0].name} 아래 끝 {max(s.bottom for s in low):.2f}in"))
        high = [s for s in shapes if s.name not in ("배경", "챕터", "대제목", "부제") and s.y < deck.BODY_Y - tol and s.bottom > deck.CHAPTER_Y]
        if high and any(s.name == "대제목" for s in shapes if s.y < 1.0):
            out.append(_finding("error", "헤더 띠 침범", slide_no, f"본문 도형이 헤더 자리로 올라왔습니다: {high[0].name}"))
        if band is not None and (abs(band.x - deck.MARGIN) > 0.01 or abs(band.w - deck.CONTENT_W) > 0.01 or abs(band.bottom - (deck.BAND_Y + deck.BAND_H)) > 0.01):
            out.append(_finding("warn", "#13 SO WHAT 띠", slide_no, "결론 띠의 자리·크기가 규격과 다릅니다"))
    # 텍스트 박스끼리 겹침 (#25-b): 가로 겹침이 좁은 쪽 폭의 25% 초과이고 세로 겹침이 0.05in 초과
    boxes = [s for s in shapes if s.is_textbox and s.text.strip()]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            over_x = min(a.right, b.right) - max(a.x, b.x)
            over_y = min(a.bottom, b.bottom) - max(a.y, b.y)
            if over_x > 0.25 * min(a.w, b.w) and over_y > 0.05:
                out.append(_finding("error", "#25-b 텍스트 박스 겹침", slide_no, f"글상자가 겹칩니다: {a.name} / {b.name}"))
    for s in shapes:
        if s.body is None or not s.text.strip():
            continue
        body_pr = s.body.find("a:bodyPr", _NS)
        if not s.is_field and (body_pr is None or body_pr.get("anchor") != "ctr"):
            out.append(_finding("error", "#28-b 세로 가운데 아님", slide_no, f"세로 가운데 정렬이 아닌 글상자가 있습니다: {s.name}"))
        if body_pr is not None and body_pr.get("tIns", "45720") != body_pr.get("bIns", "45720"):
            out.append(_finding("error", "#28-c 위아래 여백 비대칭", slide_no, f"글상자의 위아래 안쪽 여백이 다릅니다: {s.name}"))
        fit = body_pr.find("a:normAutofit", _NS) if body_pr is not None else None
        if fit is not None and int(fit.get("fontScale", "100000")) < 100000:  # 가장 작은 글자로도 안 들어가 비율로 줄인 글
            pct = int(fit.get("fontScale")) // 1000
            out.append(_finding("warn", "#28-W 글자 넘침", slide_no, f"글이 너무 길어 글자를 {pct}%로 줄여 넣었습니다. 글을 줄이거나 항목을 나누세요: “{_quote(s.text)}”"))
            continue
        need = _text_need(s)
        if need > s.h + 0.06:  # #28-W 글자가 박스 세로 넘침 (잉크 높이가 박스 높이 + 0.06in 초과)
            out.append(_finding("warn", "#28-W 글자 넘침", slide_no, f"글이 상자보다 깁니다 (약 {need:.2f}in / 상자 {s.h:.2f}in). 글을 줄이거나 장을 나누세요: “{_quote(s.text)}”"))
    for s in shapes:
        if s.tag == "graphicFrame":
            for tc_pr in s.el.iter(f"{{{_A}}}tcPr"):
                if tc_pr.get("anchor") != "ctr":
                    out.append(_finding("error", "#28-b 세로 가운데 아님", slide_no, "세로 가운데 정렬이 아닌 표 칸이 있습니다"))
                    break
    # 카드 라인 (#9): 카드는 선 없이 그림자만
    lined = [s for s in shapes if s.tag == "sp" and s.el.find("p:spPr/a:ln/a:solidFill", _NS) is not None]
    if lined:
        out.append(_finding("warn", "#9 카드 라인", slide_no, f"테두리 선이 있는 도형이 {len(lined)}개 있습니다. 카드는 선 없이 그림자만 씁니다"))
    # 세로 컬러바 (#15): 폭 0.15 미만, 높이 1 이상인 면 도형
    bars = [s for s in shapes if s.tag == "sp" and s.w < 0.15 and s.h >= 1]
    if bars:
        out.append(_finding("warn", "#15 세로 컬러바", slide_no, "카드 옆 세로 색 띠로 보이는 도형이 있습니다"))
    # 좌우 라인 정렬 (#21): 큰 카드의 좌우 끝은 공통 마진(0.422 / 12.912)에 맞춘다
    cards = [s for s in shapes if s.tag == "sp" and s.geom == "roundRect" and s.w >= 2.5]
    if cards and (abs(min(s.x for s in cards) - deck.MARGIN) > 0.05 or abs(max(s.right for s in cards) - deck.RIGHT) > 0.05):
        out.append(_finding("warn", "#21 좌우 라인 정렬", slide_no, "카드의 좌우 끝이 공통 마진과 어긋납니다"))
    fills = {c.get("val") for s in shapes if s.tag == "sp" for c in s.el.findall("p:spPr/a:solidFill/a:srgbClr", _NS)}
    if len(fills) > 5:
        out.append(_finding("warn", "면 색 5종 초과", slide_no, f"한 장에 면 색이 {len(fills)}종입니다 (5종까지)"))


def _check_header(slide_no: int, shapes: list[_Shape], root, out: list[dict], band_optional: bool = False):
    """헤더 3요소와 쪽 번호 (#4 자동 슬라이드번호, #10 헤드라인 폰트, #11 대제목 마침표, #12 챕터 형식, #13 SO WHAT 띠)."""
    if root.find(f".//{{{_A}}}fld[@type='slidenum']") is None:
        out.append(_finding("error", "#4 자동 슬라이드번호", slide_no, "쪽 번호가 자동 번호 필드가 아닙니다"))
    head = next((s for s in shapes if s.name == "대제목"), None)
    chapter = next((s for s in shapes if s.name == "챕터"), None)
    if chapter is not None and not re.match(r"\d{2}(\s*·|$)", chapter.text):
        out.append(_finding("warn", "#12 헤더 챕터 형식", slide_no, "챕터 라벨이 'NN · 이름' 꼴이 아닙니다"))
    if head is None or not head.text.strip():
        out.append(_finding("warn", "#10 헤드라인", slide_no, "대제목이 없습니다"))
        return
    title = head.text.strip()
    if abs(head.y - deck.HEAD_Y) < 0.01:  # 표지 없는 마무리 장의 큰 제목은 헤더 자리가 아니다
        sizes = {int(r.get("sz")) / 100 for r in head.el.iter(f"{{{_A}}}rPr") if r.get("sz")}
        fit = head.el.find(f".//{{{_A}}}normAutofit")
        if sizes != {deck.HEAD_PT} or fit is not None:
            out.append(_finding("warn", "#10 헤드라인 폰트", slide_no, f"대제목이 길어 26pt 로 쓰지 못했습니다 ({max(sizes):g}pt): “{_quote(title)}”"))
        if len(title.rstrip(".?!")) > HEADLINE_MAX_CHARS:
            out.append(_finding("warn", "헤드라인 30자", slide_no, f"대제목이 {len(title.rstrip('.?!'))}자입니다. 30자 안쪽으로 줄이세요: “{_quote(title)}”"))
        if title[-1] not in ".?!":
            out.append(_finding("warn", "#11 대제목 마침표", slide_no, f"대제목이 마침표로 끝나지 않습니다: “{_quote(title)}”"))
    if not band_optional and not any(s.name in ("결론 띠", "강조 카드") for s in shapes) and any(s.name not in ("배경", "챕터", "대제목", "부제", "쪽 번호", "출처", "강조 막대") for s in shapes):
        out.append(_finding("warn", "#13 SO WHAT 띠", slide_no, "결론 띠가 없습니다. 이 장의 핵심 메시지(결론 한 문장)를 채우세요"))


def check_presentation(prs, *, kinds: list | None = None, background_missing: bool = False) -> list[dict]:
    """만든 프레젠테이션(python-pptx 객체)을 검사해 결과 목록을 돌려준다. `kinds` 는 쪽마다의 장 종류(마무리 장은 결론 띠가 없어도 된다). 항목: `{"level": "error"|"warn", "gate", "slide"(쪽 번호 또는 None), "text"}`."""
    out: list[dict] = []
    if background_missing:
        out.append(_finding("warn", "#6 배경 이미지", None, "설정한 배경 그림(deck_background)을 읽지 못해 그림 없이 만들었습니다"))
    if (prs.slide_width, prs.slide_height) != (Inches(13.333), Inches(7.5)):
        out.append(_finding("error", "슬라이드 크기", None, "슬라이드가 13.333 × 7.5 인치(16:9)가 아닙니다"))
    all_text = []
    for slide_no, slide in enumerate(prs.slides, start=1):
        root = slide._element
        tree = root.find("p:cSld/p:spTree", _NS)
        shapes = [_Shape(el) for el in tree if el.tag.split("}")[1] in ("sp", "pic", "graphicFrame", "cxnSp")]
        is_cover = any(s.name == "표지 카드" for s in shapes)
        for p in root.iter(f"{{{_A}}}p"):
            if p.find("a:fld", _NS) is not None:
                continue
            text = "".join(t.text or "" for t in p.iter(f"{{{_A}}}t"))
            _check_copy(slide_no, text, out)
            all_text.append(text)
        _check_runs(slide_no, root, out)
        _check_colors(slide_no, root, out)
        _check_layout(slide_no, shapes, is_cover, out)
        if not is_cover:
            closing = bool(kinds) and slide_no <= len(kinds) and kinds[slide_no - 1] == "closing"
            _check_header(slide_no, shapes, root, out, band_optional=closing)
    whole = "\n".join(all_text)
    contrast = len(_CONTRAST.findall(whole))
    if contrast >= 3:
        out.append(_finding("warn", "카피 · 대조말투 남발", None, f"'~이 아니라', '~지 않습니다' 같은 대조 말투가 {contrast}번 나옵니다. 한두 번만 쓰세요"))
    if _FILLER.search(whole):
        out.append(_finding("warn", "카피 · 군더더기 표현", None, f"'{_FILLER.search(whole).group().strip()}' 같은 군더더기 표현이 있습니다"))
    if whole.count("!") >= 4:
        out.append(_finding("warn", "카피 · 느낌표 남발", None, f"느낌표가 {whole.count('!')}개입니다"))
    return dedupe(out)[:MAX_FINDINGS]


def dedupe(findings: list[dict]) -> list[dict]:
    """같은 (쪽, 검사, 글)은 한 번만. 오류를 앞에, 그다음 쪽 번호 순."""
    seen, result = set(), []
    for f in findings:
        key = (f["slide"], f["gate"], f["text"])
        if key not in seen:
            seen.add(key)
            result.append(f)
    return sorted(result, key=lambda f: (f["level"] != "error", f["slide"] or 0))


def counts(findings) -> dict:
    """화면용 집계: 전체·오류·경고 수. 목록이 아니면(옛 제안 건, 검사 실패) 모두 0."""
    items = [f for f in findings if isinstance(f, dict)] if isinstance(findings, list) else []
    errors = sum(1 for f in items if f.get("level") == "error")
    return {"total": len(items), "errors": errors, "warns": len(items) - errors}

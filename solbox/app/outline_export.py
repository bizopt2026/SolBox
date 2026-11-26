"""구성안을 Claude 용 글로 내보낸다 (베타 AM).

사용자 결정 2026-11-26: 디자인은 서버의 `deck.py` 대신 사용자가 Claude(데스크톱·claude.ai)에서 PPT 스킬로 만든다.
솔박스는 내용의 정확성(자료에 있는 수치만, 확인 항목, 고객용 글)을 지키고, 이 글이 그 내용을 Claude 에 그대로 넘긴다.
서버는 AI 에 실행 권한을 주지 않는다 (사용자가 자기 Claude 에 붙여 넣는다).

글의 차례: 1) Claude 에게 하는 부탁과 규칙(내용을 바꾸지 말 것, 디자인 규칙은 `deck.py` 의 토큰에서 만든다)
2) 덱 정보(제목·대상·날짜·연락처) 3) 장마다 내용(고객용: 내부 경로가 든 줄은 뺀다, 출처 경로는 싣지 않는다)
4) 작성자 확인 사항(아직 확인하지 않은 항목, "슬라이드에 넣지 말 것")."""
from __future__ import annotations

from datetime import date

from app import deck, ontology

EXPORT_VERSION = 1


def _md(text) -> str:
    """한 줄 글: 줄바꿈을 빈칸으로, 표 칸 구분자(|)는 전각으로 (마크다운 표가 깨지지 않게)."""
    return " ".join(str(text or "").split()).replace("|", "｜")


def design_rules() -> list[str]:
    """덱 디자인 규칙 (색·글꼴·글자 크기 체계). `deck.py` 의 토큰 한 곳에서 만든다."""
    return [
        f"- 화면 16:9 ({deck.SLIDE_W:g} × {deck.SLIDE_H:g} 인치), 글꼴은 `{deck.FONT}` 하나 (굵게는 `{deck.FONT_B}`). 없으면 비슷한 고딕 하나만 쓰세요.",
        f"- 색: 메인 블루 #{deck.BRAND}, 진한 블루 #{deck.BRAND_DEEP}, 강조 오렌지 #{deck.ACCENT}(도형에만, 글자에는 쓰지 않음), "
        f"글자 #{deck.INK}·#{deck.TEXT}, 바탕 #{deck.PAGE_BG}, 카드 흰색. 이 밖의 색은 쓰지 마세요.",
        f"- **글자 크기는 아래 단계만 쓰고, 같은 역할은 모든 장에서 같은 크기로 맞추세요**: 핵심 수치 {deck.T_KPI} · 표지 제목 {deck.T_DISPLAY} · 장 제목 {deck.T_H1} · "
        f"항목 제목 {deck.T_H2} · 본문 {deck.T_BODY} · 보조(표·설명) {deck.T_SMALL} · 캡션(단위·주석) {deck.T_CAPTION} · 쪽 번호 {deck.T_STRIP} (pt). "
        "내용이 넘치면 글자를 줄이지 말고 장을 나누세요.",
        "- 장마다 맨 위에 장 제목(결론 한 문장), 맨 아래에 결론 띠(\"결론\" 줄의 문장)를 두고, 그 사이를 내용으로 채우세요.",
        "- 글보다 그림이 먼저입니다: \"종류\" 에 적힌 표현(표·그래프·도식·핵심 수치·단계)을 살려 시각 자료로 그리세요. 그래프는 값을 고칠 수 있는 진짜 차트로, 막대·점마다 값을 표시하세요.",
        "- 장 제목 끝에 마침표를 쓰지 마세요. 줄표(—)·원형숫자·이모지·느낌표는 쓰지 마세요. 로고·사진·남의 회사 이름이 든 그림은 넣지 마세요.",
    ]


def _slide_lines(s: dict) -> list[str]:
    """한 장의 내용 줄들 (마크다운)."""
    kind = s.get("kind", "")
    out: list[str] = []
    if s.get("key_message"):
        out.append(f"- 결론: {_md(s['key_message'])}")
    if kind == "cover" and s.get("subtitle"):
        out.append(f"- 부제: {_md(s['subtitle'])}")
    if s.get("bullets"):
        out.append("- 내용:")
        out += [f"  - {_md(b)}" for b in s["bullets"]]
    if kind == "diagram" and s.get("diagram_type") == "before_after":
        out.append(f"- 도식: {ontology.DIAGRAM_TYPES['before_after']} (왼쪽 카드 → 화살표 → 오른쪽 카드)")
    if kind == "two_column" or (kind == "diagram" and s.get("diagram_type") == "before_after"):
        for side in ("left", "right"):
            out.append(f"- {_md(s.get(f'{side}_title')) or ('왼쪽' if side == 'left' else '오른쪽')}:")
            out += [f"  - {_md(b)}" for b in s.get(side) or []]
    if kind == "table" and s.get("columns"):
        cols = [_md(c) for c in s["columns"]]
        out += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        out += ["| " + " | ".join(_md(c) for c in row) + " |" for row in s.get("rows") or []]
        out.append("")
    if kind == "steps" and s.get("steps"):
        out.append("- 단계:")
        out += [f"  {i}. {_md(st.get('name'))}" + (f": {_md(st.get('detail'))}" if st.get("detail") else "") for i, st in enumerate(s["steps"], 1)]
    if kind == "kpi" and s.get("kpis"):
        out.append("- 핵심 수치:")
        out += [f"  - **{_md(k.get('value'))}{_md(k.get('unit'))}** {_md(k.get('label'))}" + (f" ({_md(k.get('note'))})" if k.get("note") else "") for k in s["kpis"]]
    if kind == "chart" and s.get("categories") and s.get("series"):
        out.append(f"- 그래프: {ontology.CHART_TYPES.get(s.get('chart_type'), ontology.CHART_TYPES['column'])}" + (f", 단위 {_md(s['unit'])}" if s.get("unit") else ""))
        names = [_md(x.get("name")) or "값" for x in s["series"]]
        out += ["", "| 항목 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        for r, cat in enumerate(s["categories"]):
            vals = [deck._number_text(x["values"][r]) if r < len(x.get("values") or []) and isinstance(x["values"][r], (int, float)) else "" for x in s["series"]]
            out.append(f"| {_md(cat)} | " + " | ".join(vals) + " |")
        out.append("")
    if kind == "diagram" and s.get("diagram_type") != "before_after":
        dtype = s.get("diagram_type", "")
        out.append(f"- 도식: {ontology.DIAGRAM_TYPES.get(dtype, dtype)}" + (f" ({ontology.DIAGRAM_TYPE_DESCRIPTIONS.get(dtype, '')})" if ontology.DIAGRAM_TYPE_DESCRIPTIONS.get(dtype) else ""))
        if s.get("center"):
            out.append(f"  - 가운데: {_md(s['center'])}")
        if s.get("axis_x") or s.get("axis_y"):
            out.append(f"  - 가로축: {_md(s.get('axis_x'))} / 세로축: {_md(s.get('axis_y'))}")
        if dtype == "matrix":
            out.append("  - 첫 항목이 우리 제안입니다. 그 칸을 메인 블루로 강조하세요.")
        for n in s.get("nodes") or []:
            line = f"  - {_md(n.get('label'))}"
            if n.get("group"):
                line += f" [{_md(n['group'])}]"
            if n.get("detail"):
                line += f": {_md(n['detail'])}"
            out.append(line)
    if kind in ("kpi", "chart", "diagram") and s.get("note"):
        out.append(f"- 덧붙이는 말: {_md(s['note'])}")
    return out


def open_checks(outline: dict) -> list[tuple[int, str]]:
    """(장 번호, 아직 확인하지 않은 확인 항목) 목록."""
    out = []
    for i, s in enumerate((outline or {}).get("slides") or [], 1):
        checked = set(s.get("checked") or [])
        out += [(i, c) for c in s.get("needs_check") or [] if c not in checked]
    return out


def export_markdown(outline: dict, *, deck_title: str, target: str, contact: list[str], today: date) -> str:
    """구성안 → Claude 에 붙여 넣을 마크다운 글. 고객용과 같은 안전망(`deck._scrub_customer`)으로 내부 경로가 든 줄을 뺀다."""
    slides = [deck._with_contact(s, contact) for s in (outline or {}).get("slides") or [] if isinstance(s, dict)]
    slides = [deck._scrub_customer(s) for s in slides]
    title = _md(deck_title) or _md((outline or {}).get("title"))
    lines = [
        f"# 제안서 PPT 만들기: {title}",
        "",
        "아래 구성안으로 고객에게 보낼 제안 PPT(.pptx)를 만들어 주세요. PPT 스킬이 있으면 그 스킬을 쓰세요.",
        "",
        "## 꼭 지킬 것",
        "",
        "- **내용은 바꾸거나 더하지 마세요.** 구성안에 없는 수치·실적·고객사 이름·기능을 새로 쓰지 마세요. 수치는 적힌 값과 단위 그대로 씁니다. 문장을 짧게 다듬는 것은 괜찮습니다.",
        "- 장의 순서와 장 수는 구성안 그대로입니다. 한 장에 넘치면 그 장만 둘로 나누세요.",
        "- 맨 아래 \"작성자 확인 사항\" 은 만드는 사람에게 하는 말입니다. 슬라이드에 넣지 마세요.",
        "- 완성한 뒤 장마다 글자 크기가 아래 단계에 맞는지, 글이 칸 밖으로 넘치지 않는지 확인하고 알려 주세요.",
        "",
        "## 디자인 규칙",
        "",
        *design_rules(),
        "",
        "## 덱 정보",
        "",
        f"- 제목: {title}",
        f"- 제안 대상: {_md(target)}" if target else "- 제안 대상: (적지 않음)",
        f"- 제안일: {today.year}. {today.month}. {today.day}.",
        f"- 장 수: {len(slides)}장",
        "",
        "## 장별 내용",
        "",
    ]
    for i, s in enumerate(slides, 1):
        kind_label = ontology.SLIDE_KINDS.get(s.get("kind"), s.get("kind", ""))
        lines.append(f"### {i}. {_md(s.get('title')) or '(제목 없음)'}")
        lines.append(f"- 종류: {kind_label}")
        lines += _slide_lines(s)
        lines.append("")
    checks = open_checks(outline)
    lines += ["## 작성자 확인 사항 (슬라이드에 넣지 마세요)", ""]
    if checks:
        lines += [f"- {n}장: {_md(c)}" for n, c in checks]
    else:
        lines.append("- 없음 (모두 확인함)")
    lines += ["", f"<!-- 솔박스 구성안 내보내기 v{EXPORT_VERSION} -->", ""]
    return "\n".join(lines)

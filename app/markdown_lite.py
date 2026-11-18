"""AI 답변용 작은 Markdown 렌더러. 전체를 먼저 HTML 이스케이프한 뒤 안전한 몇 가지 서식만 태그로 바꾼다.

지원: #/##/### 제목(h4/h5/h6), `- `·`* ` 글머리, `1. ` 번호 목록(한 단계), **굵게**, `코드`, 문단(줄바꿈은 br).
원시 HTML·링크·이미지는 만들지 않는다. 짝이 맞지 않는 `**`·`` ` `` 는 글자 그대로 둔다."""
from __future__ import annotations

import re

from markupsafe import Markup, escape

_HEADING = re.compile(r"(#{1,3}) +(\S.*)")
_BULLET = re.compile(r"[-*] +(.*)")
_NUMBER = re.compile(r"\d{1,9}\. +(.*)")
_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")


def _inline(text: str) -> str:
    out, pos = [], 0
    for m in _CODE.finditer(text):
        out.append(_BOLD.sub(r"<strong>\1</strong>", text[pos:m.start()]))
        out.append(f"<code>{m.group(1)}</code>")
        pos = m.end()
    out.append(_BOLD.sub(r"<strong>\1</strong>", text[pos:]))
    return "".join(out)


def render_markdown(text: str) -> Markup:
    lines = str(escape(text or "")).splitlines()
    html: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""

    def flush_para() -> None:
        if para:
            html.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if items:
            html.append(f"<{list_tag}>" + "".join(f"<li>{i}</li>" for i in items) + f"</{list_tag}>")
            items.clear()
        list_tag = ""

    def continues_list(start: int) -> bool:
        """빈 줄 다음 첫 줄이 지금 목록과 같은 종류의 항목이면 목록을 이어 간다 (항목 사이에 빈 줄이 있는 목록)."""
        for nxt in lines[start:]:
            if nxt.strip():
                return nxt[0] not in " \t" and bool((_BULLET if list_tag == "ul" else _NUMBER).fullmatch(nxt.rstrip()))
        return False

    for n, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            if not (items and continues_list(n + 1)):
                flush_list()
            continue
        if items and line[0] in " \t":  # 목록 항목 아래 들여쓴 줄은 그 항목에 이어 붙는다
            items[-1] += "<br>" + _inline(line.strip())
            continue
        if m := _HEADING.fullmatch(line):
            flush_para()
            flush_list()
            level = len(m.group(1)) + 3
            html.append(f"<h{level}>{_inline(m.group(2).strip())}</h{level}>")
            continue
        m = _BULLET.fullmatch(line)
        tag = "ul"
        if not m:
            m, tag = _NUMBER.fullmatch(line), "ol"
        if m:
            flush_para()
            if list_tag != tag:
                flush_list()
                list_tag = tag
            items.append(_inline(m.group(1).strip()))
            continue
        flush_list()
        para.append(line.strip())
    flush_para()
    flush_list()
    return Markup("".join(html))

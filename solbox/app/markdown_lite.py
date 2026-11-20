"""AI 답변용 작은 Markdown 렌더러. 전체를 먼저 HTML 이스케이프한 뒤 안전한 몇 가지 서식만 태그로 바꾼다.

지원: #/##/### 제목(h3/h4/h5: 카드 제목 h2 바로 밑에서 단계를 건너뛰지 않게), `- `·`* ` 글머리, `1. ` 번호 목록(한 단계), **굵게**, `코드`,
`[글](http(s) 주소)` 링크(rel=noopener, 새 창), 파이프 표(`| 가 | 나 |` + `|---|---|`), 문단(줄바꿈은 br).
원시 HTML·이미지는 만들지 않는다. http/https 가 아닌 링크(javascript: 등)는 글자 그대로 둔다. 짝이 맞지 않는 `**`·`` ` `` 도 그렇다.
`resolve(경로)` 를 주면 지식 사본 경로(`solutions/2/files/….md` 등)를 출처 칩으로 바꾼다 (알아볼 수 없는 경로는 글자 그대로)."""
from __future__ import annotations

import re
from collections.abc import Callable

from markupsafe import Markup, escape

_HEADING = re.compile(r"(#{1,3}) +(\S.*)")
_BULLET = re.compile(r"[-*] +(.*)")
_NUMBER = re.compile(r"\d{1,9}\. +(.*)")
_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
# 입력은 이미 이스케이프돼 있다: 주소 안의 따옴표·꺾쇠는 &#34; 같은 글자라 속성을 벗어날 수 없다. 길이를 묶어 두어 긴 입력에서도 선형으로 돈다.
_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^\s)]{1,500})\)")
_SEPARATOR = re.compile(r"\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*")
# 지식 사본 경로 후보: 유형 폴더로 시작하고 .md 로 끝난다 (실제로 있는 항목인지는 resolve 가 가린다)
_PATH = re.compile(r"(?<![\w/.\-])((?:solutions|customers|partners|projects|businesses|opportunities|bms|activities|research)/[^\s`'\"<>()\[\]|&]{1,200}?\.md)(?![\w/\-])")
# 폴더식 언급(`solutions/3`, `opportunities/2/`): 번호가 붙은 유형 폴더만, 실제로 있는 항목일 때만 칩으로 바꾼다
_FOLDER = re.compile(r"(?<![\w/.\-])((?:solutions|customers|partners|projects|businesses|opportunities|bms|activities)/\d{1,9})/?(?![\w/\-]|\.\w)")
_TOKEN = re.compile("\x00(\\d+)\x00")


def _chip(path: str, resolve: Callable | None) -> str | None:
    """알아본 사본 경로의 출처 칩 HTML, 아니면 None."""
    if resolve is None:
        return None
    try:
        d = resolve(path)
    except Exception:  # noqa: BLE001  표시용이라 조회가 실패해도 글자 그대로 둔다
        return None
    if not d or not d.get("url"):
        return None
    kind = f'<span class="chip-kind">{escape(d["kind"])}</span>' if d.get("kind") else ""
    return f'<a class="chip" href="{escape(d["url"])}">{kind}{escape(d["label"])}</a>'


def _folder_path(folder: str) -> str:
    """`solutions/3` → `solutions/3/card.md`, `opportunities/2` → `opportunities/2.md` (사본 경로 형식)."""
    return folder + "/card.md" if folder.startswith("solutions/") else folder + ".md"


def _plain(text: str, resolve: Callable | None, stash: list[str]) -> str:
    """코드가 아닌 글: 링크·출처 칩은 자리 표시로 빼 두고(굵게 표시가 속을 건드리지 않게) 굵게를 바꾼 뒤 되돌린다."""
    def hold(html: str) -> str:
        stash.append(html)
        return f"\x00{len(stash) - 1}\x00"

    if "](http" in text:  # 링크가 없을 입력은 정규식을 돌리지 않는다
        text = _LINK.sub(lambda m: hold(f'<a href="{m.group(2)}" rel="noopener noreferrer" target="_blank">{m.group(1)}</a>'), text)
    if resolve is not None:
        text = _PATH.sub(lambda m: hold(chip) if (chip := _chip(m.group(1), resolve)) else m.group(0), text)
        if "/" in text:
            text = _FOLDER.sub(lambda m: hold(chip) if (chip := _chip(_folder_path(m.group(1)), resolve)) else m.group(0), text)
    return _BOLD.sub(r"<strong>\1</strong>", text)


def _inline(text: str, resolve: Callable | None = None) -> str:
    stash: list[str] = []
    out, pos = [], 0
    for m in _CODE.finditer(text):
        out.append(_plain(text[pos:m.start()], resolve, stash))
        out.append(_chip(m.group(1), resolve) if _PATH.fullmatch(m.group(1)) and _chip(m.group(1), resolve) else f"<code>{m.group(1)}</code>")
        pos = m.end()
    out.append(_plain(text[pos:], resolve, stash))
    return _TOKEN.sub(lambda t: stash[int(t.group(1))], "".join(out))


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def render_markdown(text: str, resolve: Callable | None = None) -> Markup:
    lines = str(escape((text or "").replace("\x00", ""))).splitlines()
    html: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""

    def flush_para() -> None:
        if para:
            html.append("<p>" + "<br>".join(_inline(p, resolve) for p in para) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if items:
            html.append(f"<{list_tag}>" + "".join(f"<li>{i}</li>" for i in items) + f"</{list_tag}>")
            items.clear()
        list_tag = ""

    next_text = [-1] * (len(lines) + 1)  # 각 줄 이후 첫 비어 있지 않은 줄의 번호 (뒤에서 한 번만 훑는다)
    for k in range(len(lines) - 1, -1, -1):
        next_text[k] = k if lines[k].strip() else next_text[k + 1]

    def continues_list(start: int) -> bool:
        """빈 줄 다음 첫 줄이 지금 목록과 같은 종류의 항목이면 목록을 이어 간다 (항목 사이에 빈 줄이 있는 목록)."""
        k = next_text[start]
        if k < 0:
            return False
        nxt = lines[k]
        return nxt[0] not in " \t" and bool((_BULLET if list_tag == "ul" else _NUMBER).fullmatch(nxt.rstrip()))

    table_end = 0
    for n, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            if not (items and continues_list(n + 1)):
                flush_list()
            continue
        if items and line[0] in " \t":  # 목록 항목 아래 들여쓴 줄은 그 항목에 이어 붙는다
            items[-1] += "<br>" + _inline(line.strip(), resolve)
            continue
        if m := _HEADING.fullmatch(line):
            flush_para()
            flush_list()
            level = len(m.group(1)) + 2
            html.append(f"<h{level}>{_inline(m.group(2).strip(), resolve)}</h{level}>")
            continue
        if "|" in line and n + 1 < len(lines) and "|" in lines[n + 1] and _SEPARATOR.fullmatch(lines[n + 1]) and n >= table_end:
            flush_para()
            flush_list()
            end = n + 2
            while end < len(lines) and lines[end].strip() and "|" in lines[end]:
                end += 1
            head = _cells(line)
            body = [(_cells(r) + [""] * len(head))[:len(head)] for r in lines[n + 2:end]]
            html.append('<div class="md-table"><table><thead><tr>' + "".join(f"<th>{_inline(c, resolve)}</th>" for c in head)
                        + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c, resolve)}</td>" for c in r) + "</tr>" for r in body)
                        + "</tbody></table></div>")
            table_end = end
            continue
        if n < table_end:  # 표로 이미 그린 줄
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
            items.append(_inline(m.group(1).strip(), resolve))
            continue
        flush_list()
        para.append(line.strip())
    flush_para()
    flush_list()
    return Markup("".join(html))

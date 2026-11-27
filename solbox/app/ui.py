"""화면 도우미: 날짜 글(상대 기한·"11월 24일"), 목록의 구분 탭. 템플릿에서도 쓴다 (main 이 전역으로 등록)."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlencode

WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


class RelDue(NamedTuple):
    text: str
    tone: str  # "bad" 기한 지남 / "today" 오늘 / "" 보통


def asset_versions(static_dir: Path, names=("app.css", "app.js", "htmx.min.js")) -> dict[str, str]:
    """정적 파일 이름 -> 내용의 짧은 해시 (시작할 때 한 번). 주소 끝에 `?v=` 로 붙여, 배포 뒤 브라우저가 옛 파일을 쓰지 않게 한다."""
    out = {}
    for name in names:
        try:
            out[name] = hashlib.sha1((static_dir / name).read_bytes()).hexdigest()[:10]
        except OSError:
            out[name] = "0"
    return out


def short_date(d: date | None, today: date) -> str:
    """"11월 24일". 올해가 아니면 연도를 붙인다."""
    if d is None:
        return ""
    if hasattr(d, "hour"):  # datetime 이 와도 날짜만
        d = d.date()
    return f"{d.month}월 {d.day}일" if d.year == today.year else f"{d.year}년 {d.month}월 {d.day}일"


def days_ago(d: date | None, today: date) -> str:
    """"오늘" / "어제" / "24일 전" (미래 날짜는 "N일 뒤"). 없으면 빈 글."""
    if d is None:
        return ""
    n = (today - d).days
    if n == 0:
        return "오늘"
    if n == 1:
        return "어제"
    return f"{n}일 전" if n > 0 else f"{-n}일 뒤"


def iso_date_text(text, today: date) -> str:
    """"2026-11-17" 처럼 ISO 날짜인 글이면 화면의 날짜 모양("11월 17일")으로, 아니면 글 그대로 (사용자가 쓴 글·빈 글)."""
    import re

    raw = str(text or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw.strip()):
        try:
            return short_date(date.fromisoformat(raw.strip()), today)
        except ValueError:
            pass
    return raw


def short_datetime(d, today: date) -> str:
    """"11월 24일 14:30" (올해가 아니면 연도도). 시각이 없는 날짜(date)면 날짜만."""
    if d is None:
        return ""
    if hasattr(d, "hour"):
        return f"{short_date(d.date(), today)} {d:%H:%M}"
    return short_date(d, today)


def relative_due(due: date | None, today: date, done: bool = False) -> RelDue:
    """기한을 오늘 기준 한국어로: 3일 지남(나쁜 색) / 오늘 / 내일 / 11월 24일. 끝낸 일은 지났어도 날짜만 보인다."""
    if due is None:
        return RelDue("기한 없음", "")
    days = (due - today).days
    if done:
        return RelDue(short_date(due, today), "")
    if days < 0:
        return RelDue(f"{-days}일 지남", "bad")
    if days == 0:
        return RelDue("오늘", "today")
    if days == 1:
        return RelDue("내일", "")
    return RelDue(short_date(due, today), "")


def today_line(today: date) -> str:
    """"11월 20일 금요일"."""
    return f"{today.month}월 {today.day}일 {WEEKDAYS[today.weekday()]}요일"


def first_line(text: str | None, limit: int = 100) -> str:
    """글의 첫 의미 있는 줄 (마크다운 머리 기호 `#`·`-`·`*`·`>` 와 굵은 표시를 걷어낸다). 길면 limit 글자에서 줄인다."""
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#>*-+ ").replace("**", "").replace("`", "").strip()
        if line:
            return line if len(line) <= limit else line[:limit].rstrip() + "…"
    return ""


_PLAIN_MD = [(re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"), (re.compile(r"(\*\*|__|`|~~)"), ""),
             (re.compile(r"^\s*(#{1,6}|[-*+>]|\d+[.)])\s+"), "")]


def plain_lines(md: str, count: int = 3) -> list[str]:
    """마크다운 정리본의 앞 몇 줄을 기호 없는 글자로 (요약용). 제목 줄과 참석자 목록은 건너뛴다."""
    lines: list[str] = []
    skipping = False
    for raw in str(md or "").splitlines():
        if re.match(r"\s*#{1,6}\s", raw):
            skipping = raw.lstrip("# \t").startswith("참석자")
            continue
        if skipping:
            continue
        line = raw
        for pattern, repl in _PLAIN_MD:
            line = pattern.sub(repl, line)
        line = line.strip()
        if line:
            lines.append(line)
        if len(lines) >= count:
            break
    return lines


def summary_line(md: str | None, limit: int = 100) -> str:
    """정리본(마크다운)의 한 줄 요약: 제목 줄(`## 참석자` 같은 구역 이름)과 참석자 목록을 건너뛴 첫 본문 줄.
    AI 정리본은 `## 참석자` 로 시작해 `first_line` 으로는 모든 기록이 "참석자" 로 보였다 (UX 1순위 2026-11-27). 본문이 없으면 `first_line`."""
    lines = plain_lines(md or "", 1)
    if not lines:
        return first_line(md, limit)
    line = lines[0]
    return line if len(line) <= limit else line[:limit].rstrip() + "…"


class SegTab(NamedTuple):
    code: str
    label: str
    count: int
    href: str
    active: bool


def seg_tabs(request, param: str, choices, counts: dict, current: str, total: int, all_label: str = "전체", with_all: bool = True) -> list[SegTab]:
    """목록 위의 구분 탭: `전체` + choices([(코드, 이름)]). 링크는 다른 쪽 조건(검색어·걸러 보기·다른 구분)을 그대로 두고 `param` 만 바꾸며 쪽 번호는 버린다.
    with_all=False 면 `전체` 탭이 없고 모든 탭 링크가 `param` 을 명시한다 (기본값이 있는 구분). 건수는 지금의 검색어·걸러 보기를 적용한 목록 기준이라 누르면 나오는 목록의 건수와 같다."""
    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in (param, "page", "fu_toggled", "notice", "nt")]

    def href(code: str) -> str:
        pairs = keep + ([(param, code)] if code else [])
        return request.url.path + (f"?{urlencode(pairs)}" if pairs else "")

    tabs = [SegTab("", all_label, total, href(""), not current)] if with_all else []
    tabs += [SegTab(code, label, counts.get(code, 0), href(code), code == current) for code, label in choices]
    return tabs


class SortLink(NamedTuple):
    code: str
    label: str
    href: str
    active: bool


def sort_options(request) -> list[SortLink]:
    """목록 도구줄의 정렬 링크 (정의: ontology.LIST_SORTS). 이 주소가 정렬을 가진 목록이 아니면 빈 목록. 다른 조건(검색어·구분·걸러 보기)은 그대로 두고
    쪽 번호만 버린다. 기본 순서의 링크에는 `sort` 를 붙이지 않는다."""
    from app import ontology

    code = next((c for c in ontology.LIST_SORTS if ontology.TYPES[c].list_url == request.url.path), None)
    if code is None:
        return []
    choices = ontology.LIST_SORTS[code]
    current = request.query_params.get("sort")
    current = current if current in dict(choices) else choices[0][0]
    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in ("sort", "page", "fu_toggled", "notice", "nt")]

    def href(c: str) -> str:
        pairs = keep + ([("sort", c)] if c != choices[0][0] else [])
        return request.url.path + (f"?{urlencode(pairs)}" if pairs else "")

    return [SortLink(c, label, href(c), c == current) for c, label in choices]


def current_sort(request) -> str:
    """지금 목록의 정렬 코드 (정의: ontology.LIST_SORTS, 틀린 값·없으면 기본 순서). 정렬이 없는 주소는 빈 글."""
    from app import ontology

    code = next((c for c in ontology.LIST_SORTS if ontology.TYPES[c].list_url == request.url.path), None)
    if code is None:
        return ""
    choices = ontology.LIST_SORTS[code]
    chosen = request.query_params.get("sort")
    return chosen if chosen in dict(choices) else choices[0][0]


def stamp_info(db, obj) -> dict:
    """상세 화면 옆 칸의 "작성자·수정일": 작성자 이름(없으면 None), 만든 때, 고친 때."""
    from app.models import User

    author = db.get(User, obj.created_by_id) if getattr(obj, "created_by_id", None) else None
    editor = db.get(User, obj.updated_by_id) if getattr(obj, "updated_by_id", None) else None  # 옛 기록·시스템이 고친 기록은 없다
    return {"author": (author.name or author.username) if author else None, "created": obj.created_at, "updated": obj.updated_at,
            "editor": (editor.name or editor.username) if editor else None}


class FieldError(str):
    """어느 칸의 오류인지 이름을 함께 들고 다니는 오류 글 (보통 문자열처럼 쓴다). 라우터가 아는 칸은 이렇게 알려 주고, `error_field` 는 이것을 먼저 읽는다."""
    field: str | None

    def __new__(cls, text: str, field: str | None = None):
        obj = super().__new__(cls, text)
        obj.field = field
        return obj


def error_field(error: str | None) -> str | None:
    """폼 맨 위 오류 문구가 가리키는 칸의 이름(칸 옆에도 같은 글을 보이기 위해). 오류가 `FieldError` 면 그 칸, 아니면 글에서 짐작한다
    (대체 수단). 모르면 None: 위쪽 알림만 보인다."""
    from app import ontology

    if not error:
        return None
    if isinstance(error, FieldError) and error.field:
        return error.field
    rules = (("이름을", "name"), ("고객을 선택", "customer_id"), ("파트너", "partner_id"), ("단계", "stage"),
             (ontology.label("bm"), "bm_id"), ("메모를", "raw_text"))
    return next((field for key, field in rules if key in error), None)


def crumbs(code: str, title: str | None = None, obj_url: str | None = None, last: str | None = None) -> list[tuple[str, str | None]]:
    """화면 제목 위 경로: 메뉴 묶음 › 메뉴 이름(목록 주소) [› 이름 › 수정·등록 …]. 묶음·이름은 온톨로지에서 읽는다."""
    from app import ontology

    t = ontology.TYPES[code]
    groups = dict(ontology.NAV_GROUPS)
    out: list[tuple[str, str | None]] = [(groups[t.nav_group], None), (t.nav or t.label, t.list_url)]
    if title and last:
        out += [(title, obj_url), (last, None)]
    elif title:
        out.append((title, None))
    elif last:
        out.append((last, None))
    return out

"""화면 도우미: 날짜 글(상대 기한·"11월 24일"), 목록의 구분 탭. 템플릿에서도 쓴다 (main 이 전역으로 등록)."""
from __future__ import annotations

from datetime import date
from typing import NamedTuple
from urllib.parse import urlencode

WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


class RelDue(NamedTuple):
    text: str
    tone: str  # "bad" 기한 지남 / "today" 오늘 / "" 보통


def short_date(d: date | None, today: date) -> str:
    """"11월 24일". 올해가 아니면 연도를 붙인다."""
    if d is None:
        return ""
    return f"{d.month}월 {d.day}일" if d.year == today.year else f"{d.year}년 {d.month}월 {d.day}일"


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


class SegTab(NamedTuple):
    code: str
    label: str
    count: int
    href: str
    active: bool


def seg_tabs(request, param: str, choices, counts: dict, current: str, total: int, all_label: str = "전체", with_all: bool = True) -> list[SegTab]:
    """목록 위의 구분 탭: `전체` + choices([(코드, 이름)]). 링크는 다른 쪽 조건(검색어·걸러 보기·다른 구분)을 그대로 두고 `param` 만 바꾸며 쪽 번호는 버린다.
    with_all=False 면 `전체` 탭이 없고 모든 탭 링크가 `param` 을 명시한다 (기본값이 있는 구분). 건수는 지금의 검색어·걸러 보기를 적용한 목록 기준이라 누르면 나오는 목록의 건수와 같다."""
    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in (param, "page")]

    def href(code: str) -> str:
        pairs = keep + ([(param, code)] if code else [])
        return request.url.path + (f"?{urlencode(pairs)}" if pairs else "")

    tabs = [SegTab("", all_label, total, href(""), not current)] if with_all else []
    tabs += [SegTab(code, label, counts.get(code, 0), href(code), code == current) for code, label in choices]
    return tabs


def stamp_info(db, obj) -> dict:
    """상세 화면 옆 칸의 "작성자·수정일": 작성자 이름(없으면 None), 만든 때, 고친 때."""
    from app.models import User

    author = db.get(User, obj.created_by_id) if getattr(obj, "created_by_id", None) else None
    return {"author": (author.name or author.username) if author else None, "created": obj.created_at, "updated": obj.updated_at}


def error_field(error: str | None) -> str | None:
    """폼 맨 위 오류 문구가 가리키는 칸의 이름(칸 옆에도 같은 글을 보이기 위해). 모르면 None: 위쪽 알림만 보인다."""
    from app import ontology

    if not error:
        return None
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

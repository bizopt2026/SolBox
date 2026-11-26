"""전체 검색: 이름·제목은 물론 소개·설명·메모·연락처·정리본·원문·후속 조치·고객 니즈 글까지 대소문자 구분 없이 부분 일치하는 기록을 유형별로 모은다.

찾는 곳은 솔루션·고객·파트너·프로젝트·영업기회·BM·제안 건·활동 기록과 활동 기록에서 나온 후속 조치·고객 니즈 (유형 이름·주소는 온톨로지에서 읽는다).
유형마다 찾는 칸은 `FIELDS` 한 곳이고, 결과 줄에는 이름·제목이 아닌 칸에서 맞았을 때 맞은 칸의 이름과 짧은 발췌(맞은 글자는 `<mark>`)가 붙는다.
올리기 묶음은 만든 사람만 보는 기록이라 찾지 않는다. 질의 수는 모든 유형의 건수 질의(유형 수 10) + 맞은 유형의 줄 질의 1 + 줄이 보여 주는 연결을 미리 읽는 질의(프로젝트·영업기회 1: 고객, 제안 건 2: 고객·프로젝트, 후속 조치·고객 니즈 1: 활동 기록)로 일정하다 (기록 수와 무관).
"""
from __future__ import annotations

import re
from datetime import date

from markupsafe import Markup, escape
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import aliases, ontology

MIN_CHARS = 2  # 영문·숫자 검색어는 이보다 짧으면 찾지 않는다 (한 글자는 너무 많이 걸린다). 한글 같은 한 글자가 한 낱말인 글자는 한 글자도 찾는다
PER_TYPE = 8  # 유형마다 보여 주는 최대 수. 나머지는 건수로만 알리고 목록으로 보낸다
EXCERPT_RADIUS = 36  # 맞은 글자 앞뒤로 보여 주는 글자 수
SEARCH_TYPES = ("solution", "customer", "partner", "project", "opportunity", "bm", "proposal", "activity", "follow_up", "customer_need")

# 유형 -> 찾는 칸 (모델 속성 이름, 앞의 것이 이름·제목: 맞으면 발췌를 붙이지 않는다). 칸 이름표는 온톨로지의 속성 이름 (없으면 FIELD_LABELS)
FIELDS: dict[str, tuple[str, ...]] = {
    "solution": ("name", "summary", "description", "tags"),
    "customer": ("name", "aliases", "industry", "contact", "memo"),  # aliases: 다른 이름 (베타 AE)
    "partner": ("name", "aliases", "contact", "description", "memo"),
    "project": ("name", "description", "memo"),
    "opportunity": ("name", "next_step", "memo"),  # 다음 할 일 (영업 A)
    "bm": ("name", "summary"),
    "proposal": ("title", "group_name"),  # 다수 고객 제안은 대상 그룹 이름도 본다
    "activity": ("title", "summary_md", "raw_text"),
    "follow_up": ("task",),
    "customer_need": ("content", "customer_name"),
}
# "목록에서 모두 보기" 가 가는 곳: 그 유형의 글을 실제로 찾아 주는 목록만. 후속 조치는 `/follow-ups?q=`(할 일 글), 고객 니즈는 찾는 목록이 없어 링크를 두지 않는다.
MORE_LIST = {code: ontology.TYPES[code].list_url for code in SEARCH_TYPES if code != "customer_need" and ontology.TYPES[code].list_url}
MORE_LIST["follow_up"] = "/follow-ups"
FIELD_LABELS = {"summary_md": "정리본", "raw_text": "원문", "task": "할 일", "content": "내용", "customer_name": "고객", "tags": "태그"}


def like_pattern(q: str) -> str:
    """`%`·`_`·`\\` 를 글자 그대로 찾는 LIKE 패턴 (escape="\\" 와 함께 쓴다)."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _field_label(code: str, field: str) -> str:
    try:
        return ontology.prop(code, field)
    except KeyError:
        return FIELD_LABELS.get(field, field)


def _matches(code: str, like: str):
    """유형별 찾는 칸: (조건, 정렬). 제안 건은 제목이 비면 고객 이름으로 보이므로 고객 이름도 본다."""
    from app import models

    model = ontology.TYPES[code].model
    conds = [getattr(model, f).like(like, escape="\\") for f in FIELDS[code]]
    if code == "proposal":
        conds.append(model.customer_id.in_(select(models.Customer.id).where(models.Customer.name.like(like, escape="\\"))))
    if code == "activity":
        order = (model.date.desc().nulls_last(), model.id.desc())
    elif code in ("proposal", "follow_up", "customer_need"):
        order = (model.id.desc(),)
    else:
        order = (model.name, model.id)
    return or_(*conds), order


def _find(text: str, q: str) -> int:
    """대소문자를 무시한 위치 (글자 수가 바뀌는 드문 글은 대소문자를 구분해서 찾는다). 없으면 -1."""
    low, ql = text.lower(), q.lower()
    return low.find(ql) if len(low) == len(text) else text.find(q)


_MARKER = re.compile(r"(?m)^[ \t]*(?:#{1,6}|[-*+>]|\d+[.)])[ \t]+|^[ \t]*[*>#]+(?=\S)")


def _plain(text: str) -> str:
    """발췌용 글: 줄 앞 마크다운 표지(`#`·`-`·`*`·`>`)를 떼고 공백을 한 칸으로 모은다 (이스케이프는 발췌가 한다)."""
    return " ".join(_MARKER.sub("", str(text or "")).split())


def excerpt(text: str, q: str) -> Markup | None:
    """`text` 에서 `q`(대소문자 무시)가 처음 나온 곳 둘레의 짧은 발췌. 글은 이스케이프하고 맞은 글자만 `<mark>`. 없으면 None."""
    text = _plain(text)
    at = _find(text, q)
    if at < 0:
        return None
    start, end = max(0, at - EXCERPT_RADIUS), min(len(text), at + len(q) + EXCERPT_RADIUS)
    head = ("…" if start else "") + str(escape(text[start:at]))
    hit = Markup("<mark>") + escape(text[at:at + len(q)]) + Markup("</mark>")
    tail = str(escape(text[at + len(q):end])) + ("…" if end < len(text) else "")
    return Markup(head) + hit + Markup(tail)


def _matched_field(code: str, obj, q: str) -> dict | None:
    """이름·제목이 아닌 칸에서 처음 맞은 칸: {"field", "excerpt"}. 이름·제목에서 맞았으면 None (발췌가 필요 없다)."""
    fields = FIELDS[code]
    primary = fields[0] if code not in ("follow_up", "customer_need") else None
    if primary and _find(str(getattr(obj, primary) or ""), q) >= 0:
        return None
    for f in fields[1:] if primary else fields:
        text = getattr(obj, f)
        if f == "aliases":  # 맞은 다른 이름만 "(다른 이름: …)" 로 보인다
            found = excerpt(", ".join(aliases.alias_matches(obj, q)), q) if isinstance(text, str) else None
            if found is not None:
                return {"field": _field_label(code, f), "excerpt": found, "paren": True}
            continue
        if isinstance(text, str) and (found := excerpt(text, q)) is not None:
            return {"field": _field_label(code, f), "excerpt": found}
    return None


def _item(code: str, obj, q: str = "") -> dict:
    """결과 한 줄: 제목(label)·주소(url)·오른쪽 글(note)·단계 배지(badge: (코드 목록 키, 코드))·한 줄 보조 글(sub)·맞은 칸(hit)."""
    from app import ui

    t = ontology.TYPES[code]
    note, badge, sub = "", None, ""
    url = t.url(obj.id) if t.detail_url else None
    if code == "solution":
        note, badge, sub = ontology.STAGES.get(obj.stage, ""), ("stage", obj.stage), ui.first_line(obj.summary, 80)
    elif code == "activity":
        note, badge, sub = (ui.short_date(obj.date, date.today()) if obj.date else ""), ("activity_kind", obj.kind), ui.first_line(obj.summary_md, 80)
    elif code == "opportunity":
        note, badge, sub = ontology.OPPORTUNITY_STAGES.get(obj.stage, ""), ("opportunity_stage", obj.stage), obj.customer.name
    elif code == "proposal":
        from app.proposals import is_group, target_name

        badge, sub = ("proposal_status", obj.status), target_name(obj)
        if is_group(obj):
            sub = f"{ontology.PROPOSAL_GROUP_BADGE} · {sub}"
    elif code == "project":
        sub = " · ".join(x for x in (obj.customer.name, obj.period) if x)
    elif code == "customer":
        sub = obj.industry
    elif code == "partner":
        sub = ui.first_line(obj.description, 80)
    elif code == "bm":
        sub = ui.first_line(obj.summary, 80)
    elif code in ("follow_up", "customer_need"):  # 활동 기록에서 나온 줄: 활동 기록으로 이어진다
        url = ontology.TYPES["activity"].url(obj.activity_id)
        sub = f"활동 기록 · {obj.activity.title or '(제목 없음)'}"
        note = "완료" if code == "follow_up" and obj.done else ""
    if code == "proposal":
        from app.proposals import display_title

        label = display_title(obj)
    elif code == "activity":
        label = obj.title or "(제목 없음)"
    elif code == "follow_up":
        label = obj.task
    elif code == "customer_need":
        label = " ".join(obj.content.split())[:80]
    else:
        label = obj.name
    hit = _matched_field(code, obj, q) if q else None
    if code in ("follow_up", "customer_need") and hit and hit["field"] in (_field_label(code, "task"), _field_label(code, "content")):
        hit = None  # 제목으로 보이는 글이 곧 맞은 글이다
    return {"label": label, "url": url, "note": note, "badge": badge, "sub": sub, "hit": hit}


def search(db: Session, q: str) -> dict:
    """{"q", "state": "empty"|"short"|"none"|"found", "groups": [{"code", "label", "total", "shown", "rows", "more_url"}]}"""
    q = (q or "").strip()
    if not q:
        return {"q": q, "state": "empty", "groups": []}
    if len(q) < MIN_CHARS and q.isascii():
        return {"q": q, "state": "short", "groups": []}
    like = like_pattern(q)
    groups = []
    for code in SEARCH_TYPES:
        t = ontology.TYPES[code]
        cond, order = _matches(code, like)
        model = t.model
        total = db.scalar(select(func.count()).select_from(model).where(cond)) or 0
        if not total:
            continue
        loaders = [selectinload(model.customer)] if code in ("opportunity", "proposal", "project") else []
        if code == "proposal":
            loaders.append(selectinload(model.project))  # display_title 이 제목이 빈 제안 건에서 프로젝트 이름을 읽는다
        if code in ("follow_up", "customer_need"):
            loaders = [selectinload(model.activity)]
        rows = list(db.scalars(select(model).options(*loaders).where(cond).order_by(*order).limit(PER_TYPE)))
        from urllib.parse import urlencode

        more = None
        if total > len(rows) and code in MORE_LIST:
            more = f"{MORE_LIST[code]}?{urlencode({'q': q})}"
        groups.append({"code": code, "label": t.label, "total": total, "rows": [_item(code, o, q) for o in rows], "more_url": more})
    return {"q": q, "state": "found" if groups else "none", "groups": groups}


# 바로 만들기 (UX 개선 2026-11-26): 검색어가 이 낱말들에 들어 있거나 이름과 맞으면 검색 결과 위에 만들기 동작을 보인다 (Ctrl+K 로 검색칸)
def quick_actions(q: str) -> list[tuple[str, str]]:
    from app import ontology

    q = " ".join((q or "").split())
    if not q:
        return []
    acts = [
        ("activity", "/activities/new", "활동 기록 쓰기", "메모 회의 통화 기록 활동"),
        ("intake", "/intake/new", "파일 올리기", "올리기 업로드 파일 사진 명함"),
        ("initiative", "/initiatives/new", "새 " + ontology.label("initiative"), "과제 할일 task"),
        ("initiative", "/initiatives/new?kind=issue", "새 이슈", "이슈 문제 issue"),
        ("strategy", "/strategies/new", "새 " + ontology.label("strategy"), "방향 전략 합의"),
        ("solution", "/solutions/new", "새 " + ontology.label("solution"), "솔루션 제품"),
        ("customer", "/customers/new", "새 " + ontology.label("customer"), "고객 회사"),
        ("partner", "/partners/new", "새 " + ontology.label("partner"), "파트너 공급사"),
        ("opportunity", "/opportunities/new", "새 " + ontology.label("opportunity"), "영업기회 기회 딜"),
        ("proposal", "/proposals/new", "새 " + ontology.label("proposal"), "제안 제안서 ppt"),
        ("report", "/reports/new", "새 " + ontology.label("report"), "보고 업무보고 주간"),
    ]
    low = q.casefold()
    return [(label, url) for _code, url, label, words in acts if low in (label + " " + words).casefold()][:4]

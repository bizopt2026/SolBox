"""전체 검색: 이름·제목에 대소문자 구분 없이 부분 일치하는 기록을 유형별로 모은다.

찾는 곳은 솔루션·고객·파트너·프로젝트·영업기회·BM·제안 건·활동 기록 (유형 이름·주소는 온톨로지에서 읽는다).
올리기 묶음은 만든 사람만 보는 기록이라 찾지 않는다.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import ontology

MIN_CHARS = 2  # 이보다 짧은 검색어는 찾지 않는다
PER_TYPE = 8  # 유형마다 보여 주는 최대 수. 나머지는 건수로만 알리고 목록으로 보낸다
SEARCH_TYPES = ("solution", "customer", "partner", "project", "opportunity", "bm", "proposal", "activity")


def like_pattern(q: str) -> str:
    """`%`·`_`·`\\` 를 글자 그대로 찾는 LIKE 패턴 (escape="\\" 와 함께 쓴다)."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _matches(code: str, like: str):
    """유형별 찾는 칸: (조건, 정렬). 제안 건은 제목이 비면 고객 이름으로 보이므로 고객 이름도 본다."""
    from app import models

    model = ontology.TYPES[code].model
    if code == "activity":
        return model.title.like(like, escape="\\"), (model.date.desc().nulls_last(), model.id.desc())
    if code == "proposal":
        cond = or_(model.title.like(like, escape="\\"),
                   model.customer_id.in_(select(models.Customer.id).where(models.Customer.name.like(like, escape="\\"))))
        return cond, (model.id.desc(),)
    return model.name.like(like, escape="\\"), (model.name, model.id)


def _item(code: str, obj) -> dict:
    """결과 한 줄: 제목(label)·주소(url)·오른쪽 글(note)·단계 배지(badge: (코드 목록 키, 코드))·한 줄 보조 글(sub)."""
    from app import ui

    t = ontology.TYPES[code]
    note, badge, sub = "", None, ""
    if code == "solution":
        note, badge, sub = ontology.STAGES.get(obj.stage, ""), ("stage", obj.stage), ui.first_line(obj.summary, 80)
    elif code == "activity":
        note, badge, sub = (obj.date.isoformat() if obj.date else ""), ("activity_kind", obj.kind), ui.first_line(obj.summary_md, 80)
    elif code == "opportunity":
        note, badge, sub = ontology.OPPORTUNITY_STAGES.get(obj.stage, ""), ("opportunity_stage", obj.stage), obj.customer.name
    elif code == "proposal":
        badge, sub = ("proposal_status", obj.status), obj.customer.name
    elif code == "project":
        sub = " · ".join(x for x in (obj.customer.name, obj.period) if x)
    elif code == "customer":
        sub = obj.industry
    elif code == "partner":
        sub = ui.first_line(obj.description, 80)
    elif code == "bm":
        sub = ui.first_line(obj.summary, 80)
    if code == "proposal":
        from app.proposals import display_title

        label = display_title(obj)
    elif code == "activity":
        label = obj.title or "(제목 없음)"
    else:
        label = obj.name
    return {"label": label, "url": t.url(obj.id), "note": note, "badge": badge, "sub": sub}


def search(db: Session, q: str) -> dict:
    """{"q", "state": "empty"|"short"|"none"|"found", "groups": [{"code", "label", "total", "shown", "rows", "more_url"}]}"""
    q = (q or "").strip()
    if not q:
        return {"q": q, "state": "empty", "groups": []}
    if len(q) < MIN_CHARS:
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
        rows = list(db.scalars(select(model).options(*loaders).where(cond).order_by(*order).limit(PER_TYPE)))
        from urllib.parse import urlencode

        groups.append({"code": code, "label": t.label, "total": total, "rows": [_item(code, o) for o in rows],
                       "more_url": f"{t.list_url}?{urlencode({'q': q})}" if total > len(rows) else None})
    return {"q": q, "state": "found" if groups else "none", "groups": groups}

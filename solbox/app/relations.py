"""관계 따라가기 엔진. 정의는 `app/ontology.py`(RELATIONS, DERIVED, VIEW_LABELS …)에만 있고, 여기는 그 정의를 따라가는 기계다.

- 저장된 관계(fk·연결 표)는 RELATIONS 의 물리 위치와 ORM 메타데이터로 자동으로 따라간다 (화면마다 손으로 쓴 조인 없음).
  JSON·글에 든 관계는 ontology.RESOLVERS 의 작은 해석기가 맡는다.
- 한 걸음은 IN 질의 한 번이라 연결 수와 무관하게 질의 수가 일정하다 (N+1 없음). 한 그룹의 항목은 `limit` 개까지 읽고 전체 건수를 함께 돌려준다.
- 따라가서 얻는 관계(D1~)는 경로로 정의되고, 거쳐 온 기록 수를 "via" 설명으로 돌려준다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm import Session

from app import ontology, origin
from app.db import Base

CHUNK = 500  # IN 목록 하나의 최대 길이


def _chunks(ids):
    ids = list(ids)
    for i in range(0, len(ids), CHUNK):
        yield ids[i:i + CHUNK]


def _model_type(code: str) -> str | None:
    """따라갈 때 쓰는 유형: 모델이 있으면 그 유형, 다른 기록 안에 든 유형이면 담은 기록의 유형, 아니면 None."""
    if code in ontology.HOLDERS:
        return ontology.HOLDERS[code]
    return code if ontology.TYPES[code].model_name else None


def _table(code: str):
    return Base.metadata.tables[ontology.TYPES[code].table]


def _link_col(code: str) -> str:
    return ontology.TYPES[code].physical.get("fk_column", f"{code}_id")


# ======================================================================================
# 걸음(Edge): (관계, 출발 유형, 도착 유형) 하나를 한 방향으로 따라간다
# ======================================================================================

@dataclass(frozen=True)
class Edge:
    rel: str
    src: str
    dst: str
    kind: str  # fk / link / origin / resolver
    storage: str  # 화면에 표시할 저장 방식 (fk link json text)
    pair_fn: object = None  # (db, ids) -> [(출발 번호, 도착 번호, 덧붙임|None)]

    def pairs(self, db: Session, ids) -> list[tuple[int, int, dict | None]]:
        ids = list(ids)
        if not ids:
            return []
        out: list[tuple[int, int, dict | None]] = []
        if self.kind == "resolver":
            return list(self.pair_fn(db, ids))
        for chunk in _chunks(ids):
            out.extend((a, b, None) for a, b in self.pair_fn(db, chunk))
        return out


def _fk_pairs(table, col: str, src_is_parent: bool, origin_value: str | None):
    child_id, child_col = table.c.id, table.c[col]
    extra = [table.c.origin_type == origin_value] if origin_value else []

    if src_is_parent:
        def fn(db, ids):
            return [(a, b) for a, b in db.execute(select(child_col, child_id).where(child_col.in_(ids), *extra))]
    else:
        def fn(db, ids):
            return [(a, b) for a, b in db.execute(select(child_id, child_col).where(child_id.in_(ids), child_col.is_not(None), *extra))]
    return fn


def _link_pairs(table, src_col: str, dst_col: str, forward: bool):
    a, b = table.c[src_col], table.c[dst_col]
    if forward:
        def fn(db, ids):
            return [(x, y) for x, y in db.execute(select(a, b).where(a.in_(ids)))]
    else:
        def fn(db, ids):
            return [(y, x) for x, y in db.execute(select(a, b).where(b.in_(ids)))]
    return fn


@lru_cache(maxsize=1)
def _edges() -> dict[tuple[str, str, str], Edge]:
    edges: dict[tuple[str, str, str], Edge] = {}

    def add(e: Edge):
        edges[(e.rel, e.src, e.dst)] = e

    for rel in ontology.RELATIONS:
        if rel.key in ontology.UNTRAVERSED:
            continue
        srcs = [t for t in dict.fromkeys(_model_type(c) for c in rel.source) if t]
        dsts = [t for t in dict.fromkeys(_model_type(c) for c in rel.target) if t]
        tokens = [tuple(t.split(".")) for t in rel.physical.split() if "." in t]
        tables = rel.physical.split() if rel.storage == "link" else []
        for s in srcs:
            for d in dsts:
                if rel.storage == "link":
                    table = Base.metadata.tables[tables[dsts.index(d)] if len(tables) == len(dsts) else tables[0]]
                    add(Edge(rel.key, s, d, "link", "link", _link_pairs(table, _link_col(s), _link_col(d), True)))
                    add(Edge(rel.key, d, s, "link", "link", _link_pairs(table, _link_col(s), _link_col(d), False)))
                elif rel.storage in ("json", "text"):
                    for a, b in ((s, d), (d, s)):
                        fn = ontology.RESOLVERS.get((rel.key, a, b))
                        if fn is None:
                            raise LookupError(f"{rel.key} {a}->{b}: {rel.storage} 관계의 해석기가 없다 (ontology.RESOLVERS)")
                        add(Edge(rel.key, a, b, "resolver", rel.storage, fn))
                elif "origin_type" in {c for _, c in tokens}:
                    value = ontology.ORIGIN_PARENTS[s]
                    t = _table(d)
                    add(Edge(rel.key, s, d, "origin", "fk", _fk_pairs(t, "origin_id", True, value)))
                    add(Edge(rel.key, d, s, "origin", "fk", _fk_pairs(t, "origin_id", False, value)))
                else:
                    child, col = _fk_child(tokens, s, d)
                    t = _table(child)
                    add(Edge(rel.key, s, d, "fk", "fk", _fk_pairs(t, col, child == d, None)))
                    add(Edge(rel.key, d, s, "fk", "fk", _fk_pairs(t, col, child == s, None)))
    return edges


def _fk_child(tokens, s: str, d: str) -> tuple[str, str]:
    """외래 키 열을 가진 쪽(child)과 열 이름. 물리 위치의 표 이름으로 정하고, 모든 대상에 붙는 열(created_by_id)은 도착 쪽 표에서 찾는다."""
    s_table, d_table = ontology.TYPES[s].table, ontology.TYPES[d].table
    for tbl, col in tokens:
        if tbl == d_table:
            return d, col
    for tbl, col in tokens:
        if tbl == s_table:
            return s, col
    col = tokens[0][1]
    for code in (d, s):
        if col in _table(code).c:
            return code, col
    raise LookupError(f"외래 키 열을 찾지 못했다: {tokens} ({s}, {d})")


def all_edges() -> dict[tuple[str, str, str], Edge]:
    return _edges()


def edges_from(code: str) -> list[Edge]:
    return [e for (_, s, _d), e in _edges().items() if s == code]


def edges_for(rel: str) -> list[Edge]:
    return [e for (r, _, _d), e in _edges().items() if r == rel]


def _edge(rel: str, src: str, to: str | None = None) -> Edge:
    found = [e for (r, s, d), e in _edges().items() if r == rel and s == src and (to is None or d == to)]
    if len(found) != 1:
        raise LookupError(f"{rel} 을 {src} 에서 {'->' + to if to else ''} 따라갈 수 없거나 도착 유형이 여럿이다 ({len(found)}개)")
    return found[0]


# ======================================================================================
# 경로 따라가기
# ======================================================================================

@lru_cache(maxsize=None)
def _flatten(key: str) -> tuple[tuple[ontology.Step, ...], ...]:
    """따라가서 얻는 관계의 경로들. 문자열 원소(다른 Derived 키)는 그 경로들로 펼쳐 넣는다."""
    d = ontology.DERIVED_BY_KEY[key]
    result: list[tuple[ontology.Step, ...]] = []
    for path in d.paths:
        variants: list[tuple[ontology.Step, ...]] = [()]
        for el in path:
            subs = [(el,)] if isinstance(el, ontology.Step) else list(_flatten(el))
            variants = [v + sub for v in variants for sub in subs]
        result.extend(variants)
    return tuple(result)


def _apply_where(db: Session, code: str, ids, where) -> set[int]:
    model = ontology.TYPES[code].model
    out: set[int] = set()
    for chunk in _chunks(ids):
        stmt = select(model.id).where(model.id.in_(chunk), *(getattr(model, attr) == value for attr, value in where))
        out.update(db.scalars(stmt))
    return out


def _run_path(db: Session, start: str, ids, steps) -> tuple[str, dict[int, dict[str, set[int]]], dict[int, list]]:
    """경로를 따라간다. (도착 유형, {도착 번호: {거쳐 온 유형: {번호}}}, {도착 번호: 덧붙임들}) 을 돌려준다."""
    frontier: dict[int, dict[str, set[int]]] = {i: {} for i in ids}
    metas: dict[int, list] = {}
    cur = start
    for n, step in enumerate(steps):
        edge = _edge(step.rel, cur, step.to)
        rel = next(r for r in ontology.RELATIONS if r.key == step.rel)
        in_source = cur in {_model_type(c) for c in rel.source}
        if step.reverse == in_source:
            raise ValueError(f"{step.rel} 의 방향이 정의와 다르다 ({cur} 에서 {'거꾸로' if step.reverse else '앞으로'})")
        last = n == len(steps) - 1
        nxt: dict[int, dict[str, set[int]]] = {}
        metas = {}
        for a, b, meta in edge.pairs(db, list(frontier)):
            prov = frontier.get(a)
            if prov is None:
                continue
            entry = nxt.setdefault(b, {})
            for t, s in prov.items():
                entry.setdefault(t, set()).update(s)
            if not last:
                entry.setdefault(edge.dst, set()).add(b)
            if meta is not None:
                metas.setdefault(b, []).append((a, meta))
        if step.where and nxt:
            keep = _apply_where(db, edge.dst, nxt.keys(), step.where)
            nxt = {k: v for k, v in nxt.items() if k in keep}
        frontier, cur = nxt, edge.dst
    return cur, frontier, metas


def _derived_ids(db: Session, d: ontology.Derived, ids) -> dict[int, dict[str, set[int]]]:
    merged: dict[int, dict[str, set[int]]] = {}
    for steps in _flatten(d.key):
        _, found, _m = _run_path(db, d.source, ids, steps)
        for b, prov in found.items():
            entry = merged.setdefault(b, {})
            for t, s in prov.items():
                entry.setdefault(t, set()).update(s)
    return merged


def _parse_key(key: str) -> tuple[str, str | None]:
    name, _, to = key.partition(":")
    return name.strip(), (to.strip() or None)


def _load(db: Session, code: str, ids, *, order: bool = True, limit: int | None = None, start: str | None = None) -> list:
    """번호들의 ORM 객체. 기본 순서(유형마다 하나)로 `limit` 개까지."""
    ids = list(ids)
    if not ids:
        return []
    model = ontology.TYPES[code].model
    opts = _LOAD_OPTIONS.get(code, ())
    if len(ids) > CHUNK:  # 아주 큰 묶음: 덩어리마다 SQL 로 순서를 매겨 앞쪽만 뽑고, 뽑힌 번호끼리 한 번 더 SQL 로 순서를 매긴다 (드문 경우)
        if limit is None or limit >= CHUNK:  # 순서 있는 부분 선택이 의미 없는 크기: 모두 읽어 번호순
            objs = []
            for chunk in _chunks(ids):
                objs.extend(db.scalars(select(model).where(model.id.in_(chunk)).options(*opts)).unique())
            return sorted(objs, key=lambda o: o.id)[:limit]
        keep: list[int] = []
        for chunk in _chunks(ids):
            stmt = select(model.id).where(model.id.in_(chunk))
            if order:
                stmt = stmt.order_by(*_order(code))
            keep.extend(db.scalars(stmt.limit(limit)))
        return _load(db, code, keep, order=order, limit=limit, start=start)
    stmt = select(model).where(model.id.in_(ids))
    if opts:
        stmt = stmt.options(*opts)
    if order:
        stmt = stmt.order_by(*_order(code))
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).unique())


def _count_existing(db: Session, code: str, ids) -> int:
    """`ids` 중 실제로 있는 기록의 수."""
    model = ontology.TYPES[code].model
    return sum(db.scalar(select(func.count()).select_from(model).where(model.id.in_(chunk))) or 0 for chunk in _chunks(list(ids)))


def _order(code: str):
    from app.models import Activity, CustomerNeed, FollowUp, Intake, IntakeFile, Proposal, ResearchReport

    return {
        "activity": (Activity.date.is_(None), Activity.date.desc(), Activity.id.desc()),
        "proposal": (Proposal.updated_at.desc(), Proposal.id.desc()),
        "follow_up": (FollowUp.due.is_(None), FollowUp.due, FollowUp.id),
        "customer_need": (CustomerNeed.activity_id.desc(), CustomerNeed.position, CustomerNeed.id),
        "intake": (Intake.id.desc(),),
        "intake_file": (IntakeFile.id,),
        "research_report": (ResearchReport.id.desc(),),
    }.get(code) or _name_order(code)


def _name_order(code: str):
    model = ontology.TYPES[code].model
    if hasattr(model, "name") and code not in ("user",):
        return (model.name, model.id)
    return (model.id,)


class _LoadOptions(dict):
    def get(self, code, default=()):
        from app.models import CustomerNeed, FollowUp, IntakeFile, Opportunity, Project, Proposal

        table = {
            "project": (joinedload(Project.customer),),
            "opportunity": (joinedload(Opportunity.customer),),
            "proposal": (joinedload(Proposal.customer), joinedload(Proposal.project)),
            "customer_need": (joinedload(CustomerNeed.activity),),
            "follow_up": (joinedload(FollowUp.activity),),
            "intake_file": (joinedload(IntakeFile.intake),),
        }
        return table.get(code, default)


_LOAD_OPTIONS = _LoadOptions()


# ======================================================================================
# 항목·그룹
# ======================================================================================

@dataclass
class RelatedItem:
    type: str
    id: int | None
    label: str
    url: str | None = None
    note: str = ""
    badge: tuple[str, str] | None = None  # (CSS 클래스, 글)
    via: str = ""
    done: bool = False
    obj: object = None


@dataclass
class RelatedGroup:
    key: str  # "R4:activity" 또는 "D1"
    label: str
    target_type: str
    items: list[RelatedItem]
    total: int
    derived: bool = False
    storage: str = "fk"  # fk / link / json / text / derived
    more_url: str | None = None
    relation: str = ""

    @property
    def hidden(self) -> int:
        return max(0, self.total - len(self.items))


def _date_text(d) -> str:
    return d.isoformat() if d else "일자 없음"


def _describe(db: Session, code: str, objs: list, start: str) -> dict[int, RelatedItem]:
    from app.models import Opportunity
    from app.proposals import display_title

    T = ontology.TYPES
    opps = {}
    if code == "customer_need":
        ids = {o.opportunity_id for o in objs if o.opportunity_id is not None}
        if ids:
            opps = {o.id: o for o in db.scalars(select(Opportunity).where(Opportunity.id.in_(ids)))}
    out: dict[int, RelatedItem] = {}
    for o in objs:
        url = T[code].url(o.id) if T[code].detail_url else None
        it = RelatedItem(code, o.id, getattr(o, "name", "") or "", url, obj=o)
        if code == "solution":
            it.badge = (ontology.tone_class("stage", o.stage), ontology.STAGES.get(o.stage, o.stage))
            it.note = o.summary or ""
        elif code == "opportunity":
            it.badge = (ontology.OPPORTUNITY_STAGE_CLASS.get(o.stage, ""), ontology.OPPORTUNITY_STAGES.get(o.stage, o.stage))
            it.note = o.customer.name if start != "customer" else ""
        elif code == "project":
            it.note = o.customer.name if start != "customer" else (o.status or "")
        elif code == "proposal":
            it.label = display_title(o)
            it.note = ontology.PROPOSAL_STATUSES.get(o.status, o.status)
        elif code == "activity":
            it.label = o.title or "(제목 없음)"
            it.note = _date_text(o.date)
        elif code == "customer_need":
            it.label = " ".join(o.content.split())
            it.url = f"/activities/{o.activity_id}"
            it.note = f"{o.activity.title or '(제목 없음)'} ({_date_text(o.activity.date)})"
            opp = opps.get(o.opportunity_id)
            if opp is not None:
                it.badge = (ontology.OPPORTUNITY_STAGE_CLASS.get(opp.stage, ""), f"{ontology.label('opportunity')} · {opp.name} · {ontology.OPPORTUNITY_STAGES.get(opp.stage, opp.stage)}")
                it.url = f"/activities/{o.activity_id}"
        elif code == "follow_up":
            it.label = o.task
            it.url = f"/activities/{o.activity_id}"
            it.done = bool(o.done)
            bits = [f"담당 {o.owner}" if o.owner else "", f"기한 {o.due.isoformat()}" if o.due else "", o.activity.title or "(제목 없음)"]
            it.note = " · ".join(b for b in bits if b)
        elif code == "intake":
            it.label = f"올리기 묶음 {o.id}번"
            it.note = ontology.INTAKE_STATUSES.get(o.status, o.status)
        elif code == "intake_file":
            it.label = o.original_name
            it.url = f"/intake/{o.intake_id}"
            it.note = f"올리기 묶음 {o.intake_id}번"
        elif code == "solution_file":
            it.label = o.original_name
            it.url = f"/files/{o.id}/download"
            it.note = ontology.FILE_KINDS.get(o.kind, o.kind)
        elif code == "research_report":
            it.label = o.title or "리서치 보고서"
            it.url = f"/research/runs/{o.run_id}"
        elif code == "research_run":
            it.label = f"리서치 {o.started_at.date().isoformat()}"
        elif code == "user":
            it.label = o.name or o.username
        out[o.id] = it
    return out


def _visible(viewer, code: str, objs: list) -> list:
    """올리기 묶음에 닿는 항목은 열 수 있는 사람에게만 (origin.can_open_intake)."""
    if code not in ontology.INTAKE_BACKED_TYPES:
        return objs
    if viewer is None:
        return []
    return [o for o in objs if origin.can_open_intake(viewer, o if code == "intake" else o.intake)]


def _more_url(start: str, rel_or_d: str, target: str, obj_id: int) -> str | None:
    for (list_type, param), (owner, key) in ontology.LIST_FILTERS.items():
        if owner == start and key == rel_or_d and list_type == target:
            return f"{ontology.TYPES[list_type].list_url}?{param}={obj_id}"
    link = ontology.MORE_LINKS.get((start, rel_or_d))
    return link.format(id=obj_id) if link else None


def _excluded(exclude, key: str, dst: str | None = None) -> bool:
    return key in exclude or (dst is not None and f"{key}:{dst}" in exclude)


def _sorted_edges(start: str, edges: list[Edge]) -> list[Edge]:
    pref = ontology.GROUP_ORDER.get(start, ())

    def k(e: Edge):
        return (pref.index(e.rel) if e.rel in pref else len(pref), int(e.rel[1:]), e.dst)

    return sorted(edges, key=k)


def related(db: Session, obj, *, viewer=None, exclude=None, limit: int = 10) -> list[RelatedGroup]:
    """`obj` 에서 연결된 것: 저장된 관계(양방향) 그룹 다음에 따라가서 얻는 관계 그룹. 빈 그룹은 만들지 않는다.
    `exclude` 는 관계 키("R4") 또는 "관계 키:도착 유형"("R6:project") 목록. 올리기 묶음에 닿는 그룹은 `viewer` 가 열 수 있는 것만 보인다."""
    start = ontology.type_of(obj)
    exclude = set(exclude or ())
    if start == "intake" and (viewer is None or not origin.can_open_intake(viewer, obj)):
        return []
    groups: list[RelatedGroup] = []
    for e in _sorted_edges(start, edges_from(start)):
        if e.rel in ontology.QUIET_RELATIONS or _excluded(exclude, e.rel, e.dst):
            continue
        hits = e.pairs(db, [obj.id])
        if not hits:
            continue
        ids = list(dict.fromkeys(b for _, b, _m in hits))
        label = ontology.VIEW_LABELS.get((e.rel, e.src, e.dst), ontology.label(e.dst))
        more = _more_url(start, e.rel, e.dst, obj.id)
        if e.dst == "research_report" and e.rel == "R18":  # 리서치 제안: 제안 한 줄이 한 항목이다
            reports = {r.id: r for r in _load(db, "research_report", ids, order=False)}
            items = []
            for _, rid, meta in hits:
                r = reports.get(rid)
                if r is not None:
                    kind = ontology.SUGGESTION_KINDS.get(meta["kind"], meta["kind"])
                    items.append(RelatedItem("suggestion", rid, meta["title"], f"/research/runs/{r.run_id}#suggestions",
                                             f"{kind} · 리서치 {r.created_at.date().isoformat()}" if r.created_at else kind, obj=r))
            items = list({(i.id, i.label): i for i in items}.values())
            groups.append(RelatedGroup(f"{e.rel}:{e.dst}", label, "suggestion", items[:limit], len(items), False, e.storage, more, e.rel))
            continue
        if e.dst in ontology.INTAKE_BACKED_TYPES:
            objs = _visible(viewer, e.dst, _load(db, e.dst, ids))
            total, objs = len(objs), objs[:limit]
        else:
            total, objs = len(ids), _load(db, e.dst, ids, limit=limit)
            if e.rel in ontology.JSON_BACKED:  # JSON·글에 든 번호는 지워진 기록을 가리킬 수 있다: 있는 것만 센다
                total = _count_existing(db, e.dst, ids)
        if not objs:
            continue
        desc = _describe(db, e.dst, objs, start)
        groups.append(RelatedGroup(f"{e.rel}:{e.dst}", label, e.dst, [desc[o.id] for o in objs], total, False, e.storage, more, e.rel))
    for d in ontology.DERIVED:
        if d.source != start or _excluded(exclude, d.key):
            continue
        found = _derived_ids(db, d, [obj.id])
        if not found:
            continue
        objs = _load(db, d.target, list(found), limit=limit)
        desc = _describe(db, d.target, objs, start)
        for o in objs:
            desc[o.id].via = via_text(found[o.id], d)
        groups.append(RelatedGroup(d.key, d.label, d.target, [desc[o.id] for o in objs], len(found), True, "derived",
                                   _more_url(start, d.key, d.target, obj.id), d.key))
    return groups


def via_text(prov: dict[str, set[int]], d: ontology.Derived) -> str:
    """"활동 기록 2건, 제안 건 1건을 거쳐" 처럼 거쳐 온 기록의 수."""
    parts = [f"{ontology.label(t)} {len(ids)}건" for t, ids in prov.items()]
    if parts:
        return ", ".join(parts) + "을 거쳐"
    return d.direct_note


def related_for_page(db: Session, obj, viewer, **kw) -> list[RelatedGroup]:
    """상세 화면용: 그 화면이 속성 줄·특수 구역으로 이미 보이는 그룹을 뺀다 (ontology.RELATED_EXCLUDE)."""
    return related(db, obj, viewer=viewer, exclude=ontology.RELATED_EXCLUDE.get(ontology.type_of(obj), ()), **kw)


# ======================================================================================
# path_query: 관계 키들을 차례로 따라가 닿은 기록들
# ======================================================================================

def path_query(db: Session, start, keys) -> list:
    """`start` 에서 `keys` 를 차례로 따라간다. 키: "R4"(저장된 관계, 방향은 지금 유형에서 자동), "R6:project"(도착 유형을 정함),
    "D7"(따라가서 얻는 관계). 닿은 기록을 중복 없이 유형별 기본 순서로 돌려준다 (끝이 여러 유형이면 유형 순으로 이어 붙인다)."""
    current: dict[str, set[int]] = {ontology.type_of(start): {start.id}}
    for key in keys:
        name, to = _parse_key(key)
        if name not in ontology.DERIVED_BY_KEY and not edges_for(name):
            raise LookupError(f"알 수 없거나 따라갈 수 없는 관계: {key}")
        nxt: dict[str, set[int]] = {}
        for code, ids in current.items():
            if name in ontology.DERIVED_BY_KEY:
                d = ontology.DERIVED_BY_KEY[name]
                if d.source != code:
                    continue
                nxt.setdefault(d.target, set()).update(_derived_ids(db, d, ids))
                continue
            for e in edges_for(name):
                if e.src == code and (to is None or e.dst == to):
                    nxt.setdefault(e.dst, set()).update(b for _, b, _m in e.pairs(db, ids))
        current = {c: s for c, s in nxt.items() if s}
        if not current:
            return []
    out: list = []
    for code, ids in current.items():
        out.extend(_load(db, code, ids))
    return out


# ======================================================================================
# 목록의 "연결" 건수와 "최근 활동": 쪽에 보이는 번호들만 묶어서 센다 (기록마다 묻지 않는다)
# ======================================================================================

def _reach(db: Session, start: str, ids: list[int], key: str, to: str | None) -> dict[int, set[int]]:
    """각 출발 번호에서 `key`(저장된 관계 또는 따라가서 얻는 관계)로 닿는 기록 번호들. 걸음마다 IN 질의 한 번."""
    paths = _flatten(key) if key in ontology.DERIVED_BY_KEY else ((ontology.Step(key, False, to),),)
    out: dict[int, set[int]] = {i: set() for i in ids}
    for steps in paths:
        frontier: dict[int, set[int]] = {i: {i} for i in ids}
        cur = start
        for step in steps:
            edge = _edge(step.rel, cur, step.to if step.to else (to if len(steps) == 1 else None))
            nxt: dict[int, set[int]] = {}
            for a, b, _meta in edge.pairs(db, list(frontier)):
                origins = frontier.get(a)
                if origins:
                    nxt.setdefault(b, set()).update(origins)
            if step.where and nxt:
                keep = _apply_where(db, edge.dst, nxt.keys(), step.where)
                nxt = {k: v for k, v in nxt.items() if k in keep}
            frontier, cur = nxt, edge.dst
        for b, origins in frontier.items():
            for o in origins:
                out[o].add(b)
    return out


def link_counts(db: Session, start: str, ids, key: str, to: str | None = None) -> dict[int, int]:
    """`ids` 각각에서 `key` 로 닿는 서로 다른 기록의 수. 닿는 것이 없는 번호는 빠진다."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    return {i: len(v) for i, v in _reach(db, start, ids, key, to).items() if v}


def latest_activity_dates(db: Session, start: str, ids) -> dict:
    """`ids` 각각에 연결된 활동 기록 중 가장 늦은 일자 (ontology.LIST_LATEST_ACTIVITY 의 관계). 일자가 있는 활동이 없으면 빠진다."""
    from app.models import Activity

    ids = list(dict.fromkeys(ids))
    key = ontology.LIST_LATEST_ACTIVITY.get(start)
    if not ids or key is None:
        return {}
    reach = _reach(db, start, ids, key, "activity")
    activity_ids = set().union(*reach.values()) if reach else set()
    dates: dict[int, object] = {}
    for chunk in _chunks(activity_ids):
        dates.update((a, d) for a, d in db.execute(select(Activity.id, Activity.date).where(Activity.id.in_(chunk), Activity.date.is_not(None))))
    out = {}
    for i, acts in reach.items():
        found = [dates[a] for a in acts if a in dates]
        if found:
            out[i] = max(found)
    return out


# ======================================================================================
# 목록 걸러 보기와 지식 사본 이웃
# ======================================================================================

def filter_ids(db: Session, list_type: str, param: str, raw) -> list[int] | None:
    """목록 화면의 `?<param>=<번호>` 가 뜻하는 번호들. 이 목록에 없는 파라미터면 None(걸러 보기 아님), 숫자가 아니거나 없는 번호면 빈 목록."""
    spec = ontology.LIST_FILTERS.get((list_type, param))
    if spec is None or raw is None or str(raw) == "":
        return None
    owner, key = spec
    text = str(raw).strip()
    if not (text.isascii() and text.isdecimal() and len(text) <= 18):
        return []
    owner_obj = db.get(ontology.TYPES[owner].model, int(text))
    if owner_obj is None:
        return []
    if key in ontology.DERIVED_BY_KEY:
        return list(_derived_ids(db, ontology.DERIVED_BY_KEY[key], [owner_obj.id]))
    ids: list[int] = []
    for e in edges_from(owner):
        if e.rel == key and e.dst == list_type:
            ids.extend(b for _, b, _m in e.pairs(db, [owner_obj.id]))
    return list(dict.fromkeys(ids))


MIRRORED_TYPES = ("solution", "customer", "partner", "project", "opportunity", "bm", "activity")


def mirror_neighbors(db: Session, obj) -> list:
    """`obj` 에서 저장된 관계로 한 걸음 닿는, 지식 사본이 있는 기록들 (관계가 바뀌면 양쪽 카드를 다시 쓴다). 따라가서 얻는 구역은 조금 늦을 수 있다."""
    start = ontology.type_of(obj)
    found = []
    for e in edges_from(start):
        if e.dst in MIRRORED_TYPES and e.rel not in ontology.QUIET_RELATIONS:
            ids = list(dict.fromkeys(b for _, b, _m in e.pairs(db, [obj.id])))
            found.extend(_load(db, e.dst, ids, order=False))
    return found

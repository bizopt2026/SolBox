"""항목 화면(파트너·고객·프로젝트·솔루션 …)이 함께 쓰는 도우미."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, TypeVar

from fastapi import HTTPException, Request
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app import ontology, relations, ui
from app.search import like_pattern
from app.knowledge import KnowledgeMirror
from app.opportunities import dependents
from app.models import BM, Activity, Customer, CustomerNeed, IntakeFile, Opportunity, Partner, Project, Solution, User
from app.security import ensure_can_delete

T = TypeVar("T")

NOT_FOUND = "항목을 찾을 수 없습니다."
NAME_REQUIRED = ui.FieldError("이름을 입력하세요.", "name")


async def submitted_fields(request: Request) -> frozenset[str]:
    """폼으로 실제 온 칸 이름 (빈 값으로 온 칸도 포함). FastAPI 는 빈 폼 값을 "안 옴"으로 보므로 "그대로 둔다" 와 "비운다" 를 가를 때 쓴다."""
    try:
        return frozenset((await request.form()).keys())
    except Exception:  # noqa: BLE001  (폼이 아니거나 읽지 못하면 아무 칸도 안 온 것으로 본다)
        return frozenset()


def get_or_404(db: Session, model: type[T], obj_id: int) -> T:
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return obj


def get_deletable_or_404(db: Session, model: type[T], obj_id: int, user: User) -> T:
    """없으면 404, 본인이 만든 것도 관리자도 아니면 403."""
    obj = get_or_404(db, model, obj_id)
    ensure_can_delete(user, obj)
    return obj


def search_by_name(db: Session, model, q: str = "", *extra_columns, joins=(), loaders=()) -> list:
    """이름(과 추가 칼럼)에 `q` 가 부분 일치하는 항목을 이름순으로 돌려준다. `q` 가 비면 전부. `loaders` 는 줄에 쓰는 연결을 한꺼번에 읽는 옵션."""
    stmt = select(model).options(*loaders)
    for target in joins:
        stmt = stmt.join(target)
    q = q.strip()
    if q:
        like = like_pattern(q)
        stmt = stmt.where(or_(*(c.like(like, escape="\\") for c in (model.name, *extra_columns))))
    return list(db.scalars(stmt.order_by(model.name, model.id)).unique())


def _same_text(text: str | None) -> str:
    """글자까지 같은지 보는 모양: 앞뒤·가운데 공백 정리 + 대소문자 무시 (그 밖의 기호·㈜ 따위가 다르면 "비슷한" 이름)."""
    return " ".join((text or "").split()).casefold()


def duplicate_warning(db: Session, model, name: str, confirm: str = "", exclude: object | None = None) -> dict | None:
    """같은 종류에 정규형(`normalize_name`: 공백·기호·대소문자 무시, 고객·파트너는 ㈜·(주)·주식회사 도 무시)이 같은 이름이 이미 있으면 폼에 보일 경고
    `{"label", "name", "similar", "records": [{"name", "url", "similar"}], "open_url"}`. 공백 정리·대소문자만 무시하고 글자가 같은 이름이 하나라도 있으면 "같은 이름",
    없고 정규형만 같으면 `similar` (화면은 "비슷한 이름"). `confirm == "1"`("그래도 새로 등록")이면 None.
    이름을 고치는 폼은 `exclude` 로 자기 자신을 빼고, 이름이 그대로면 묻지 않는다."""
    from app.ai.tasks import normalize_name

    company = model.__name__ in ("Customer", "Partner")
    key = normalize_name(name or "", company)
    if confirm == "1" or not key:
        return None
    if exclude is not None and normalize_name(exclude.name or "", company) == key:
        return None
    code = next(t.code for t in ontology.TYPES.values() if t.model_name == model.__name__)
    typed = (name or "").strip()
    records = [{"name": n, "url": ontology.detail_url(code, i), "similar": _same_text(n) != _same_text(typed)}
               for i, n in db.execute(select(model.id, model.name).order_by(model.id))
               if normalize_name(n or "", company) == key and (exclude is None or i != exclude.id)]
    if not records:
        return None
    records.sort(key=lambda r: r["similar"])  # 글자까지 같은 것이 위
    return {"label": ontology.label(code), "name": typed, "similar": records[0]["similar"], "records": records, "open_url": records[0]["url"],
            "company": company}  # company: 고객·파트너 (㈜·(주)·주식회사 를 무시하고 견준다)


def name_error(name: str) -> str | None:
    """이름이 비었으면 화면에 보일 오류 문구를, 아니면 None."""
    return None if name.strip() else NAME_REQUIRED


def activities_linked_to(db: Session, relationship, obj) -> list[Activity]:
    """`relationship` (예: Activity.projects) 으로 `obj` 에 연결된 활동 기록."""
    return list(db.scalars(select(Activity).where(relationship.any(id=obj.id))))


def activities_with_needs_of(db: Session, customer: Customer) -> list[Activity]:
    """고객 니즈 줄이 이 고객 기록에 연결된 활동 기록 (이름이 사본에 적힌다)."""
    return list(db.scalars(select(Activity).where(Activity.needs.any(CustomerNeed.customer_id == customer.id))))


def clear_file_targets(db: Session, target_type: str, target_id: int) -> None:
    """대상 기록을 지우기 전에 올린 파일의 대상 표시(R21)를 비운다: 번호를 다시 쓰는 새 기록이 원본을 물려받지 않게 (같은 트랜잭션)."""
    db.execute(update(IntakeFile).where(IntakeFile.target_type == target_type, IntakeFile.target_id == target_id)
               .values(target_type="", target_id=None))


def resync_activities(db: Session, mirror: KnowledgeMirror, activities: list[Activity]) -> None:
    """연결 대상이 지워진 뒤 활동 기록 사본을 다시 쓴다."""
    for a in activities:
        db.refresh(a)
        mirror.sync(a)


def resync_objects(db: Session, mirror: KnowledgeMirror, objs) -> None:
    """사본이 다른 항목에 걸려 바뀌어야 하는 항목들을 DB 에서 다시 읽어 사본을 다시 쓴다 (이미 지운 항목은 건너뛴다)."""
    seen = set()
    for item in objs:
        if item is None or (type(item), item.id) in seen or db.get(type(item), item.id) is None:
            continue
        seen.add((type(item), item.id))
        db.refresh(item)
        mirror.sync(item)


def resync_related(db: Session, mirror: KnowledgeMirror, obj) -> None:
    """`obj` 의 이름이 적히는 다른 사본(상위·하위 항목, 연결된 활동 기록)을 다시 쓴다."""
    if isinstance(obj, Customer):
        related = [*obj.projects, *obj.opportunities, *activities_linked_to(db, Activity.customers, obj), *activities_with_needs_of(db, obj),
                   *([obj.partner] if obj.partner else [])]
    elif isinstance(obj, Project):
        related = [obj.customer, obj.bm, *activities_linked_to(db, Activity.projects, obj),
                   *db.scalars(select(Opportunity).where(Opportunity.project_id == obj.id))]
    elif isinstance(obj, Partner):
        related = [*obj.solutions, *activities_linked_to(db, Activity.partners, obj), *([obj.customer] if obj.customer else [])]
    elif isinstance(obj, Solution):
        related = [*([obj.partner] if obj.partner else []), *activities_linked_to(db, Activity.solutions, obj), *obj.bms, *obj.opportunities]
    elif isinstance(obj, (BM, Opportunity)):
        related = dependents(db, obj)
    else:
        return
    # 정의(레지스트리)로 한 걸음 닿는 사본 기록도 함께: 양쪽 카드의 "## 연결" 구역이 새 관계를 따라간다 (따라가서 얻는 구역은 조금 늦을 수 있다:
    # 그 카드가 다시 쓰일 때나 `rebuild-knowledge` 때 맞춰진다)
    seen: set = set()
    for item in [*related, *relations.mirror_neighbors(db, obj)]:
        if item is None or (type(item), item.id) in seen:
            continue
        seen.add((type(item), item.id))
        db.refresh(item)
        mirror.sync(item)


def list_filter(db: Session, request: Request, list_type: str, rows) -> tuple[list, list[dict]]:
    """목록 화면의 `?customer_id=` 같은 걸러 보기 (정의는 ontology.LIST_FILTERS, 뜻은 연결 목록의 "모두 보기" 와 같다).
    숫자가 아니거나 없는 번호면 빈 목록이고 오류가 아니다. (걸러진 목록, 화면에 보일 걸러 보기 안내들)."""
    rows, notes = list(rows), []
    for (lt, param), (owner, _key) in ontology.LIST_FILTERS.items():
        raw = request.query_params.get(param)
        if lt != list_type or raw in (None, ""):
            continue
        keep = set(relations.filter_ids(db, lt, param, raw) or [])
        rows = [r for r in rows if r.id in keep]
        text = raw.strip()
        found = db.get(ontology.TYPES[owner].model, int(text)) if text.isascii() and text.isdecimal() and len(text) <= 18 else None
        name = getattr(found, "name", None) or getattr(found, "title", None) or ""
        rest = [(k, v) for k, v in request.query_params.multi_items() if k not in (param, "page")]
        from urllib.parse import urlencode

        clear = ontology.TYPES[list_type].list_url + (f"?{urlencode(rest)}" if rest else "")
        notes.append({"label": f"{ontology.label(owner)} '{name}'" if found is not None else f"{ontology.label(owner)} {raw[:20]} (없는 항목)",
                      "param": param, "value": raw, "clear": clear})
    return rows, notes


def origin_bucket(obj) -> str:
    """기록의 유래 구분: intake / research / manual (비어 있으면 manual)."""
    if obj.origin_type == ontology.ORIGIN_IMPORT:
        return ontology.ORIGIN_INTAKE  # 파일 가져오기도 올리기 묶음에서 만든 기록이라 "올리기" 구분에 센다
    return obj.origin_type if obj.origin_type in (ontology.ORIGIN_INTAKE, ontology.ORIGIN_RESEARCH) else ontology.ORIGIN_MANUAL


def origin_filter(request: Request, rows) -> tuple[list, list, str]:
    """목록의 `?origin=` 걸러 보기 (전체 / 올리기 / 리서치 / 직접 입력). (걸러진 목록, 구분 탭, 지금 구분).
    건수는 지금의 검색어·다른 걸러 보기를 적용한 목록 기준이고, 알 수 없는 값은 전체로 본다. 다른 조건과 쪽 번호는 `ui.seg_tabs` 가 지킨다."""
    rows = list(rows)
    current = request.query_params.get(ontology.ORIGIN_FILTER_PARAM, "")
    if current not in ontology.ORIGINS or current == ontology.ORIGIN_IMPORT:
        current = ""
    counts = {code: 0 for code in ontology.ORIGINS}
    for r in rows:
        counts[origin_bucket(r)] += 1
    tabs = ui.seg_tabs(request, ontology.ORIGIN_FILTER_PARAM, [(c, ontology.ORIGINS[c]) for c in (ontology.ORIGIN_INTAKE, ontology.ORIGIN_RESEARCH, ontology.ORIGIN_MANUAL)],
                       counts, current, len(rows))
    if current:
        rows = [r for r in rows if origin_bucket(r) == current]
    return rows, tabs, current


def sort_rows(request: Request, list_type: str, rows, name_of=None, db: Session | None = None) -> list:
    """목록의 `?sort=` (정의: ontology.LIST_SORTS). 화이트리스트가 아니면 기본 순서. 기본 순서는 각 목록이 이미 정해 둔 대로 두고 다른 것만 다시 정렬한다."""
    rows = list(rows)
    choices = ontology.LIST_SORTS[list_type]
    chosen = request.query_params.get("sort")
    sort = chosen if chosen in dict(choices) else choices[0][0]
    if sort == choices[0][0]:
        return rows
    name = name_of or (lambda o: getattr(o, "name", "") or "")
    if sort == "name":
        return sorted(rows, key=lambda o: (name(o), o.id))
    if sort == "date":
        return newest_first(rows)
    if sort == "active":  # 표의 "최근 활동" 칸(연결된 활동 기록의 가장 늦은 일자) 순. 활동이 없는 줄은 뒤로
        if db is None:
            return rows
        latest = relations.latest_activity_dates(db, list_type, [o.id for o in rows])
        return sorted(rows, key=lambda o: (latest.get(o.id) is not None, latest[o.id].toordinal() if latest.get(o.id) else 0, o.id), reverse=True)
    return sorted(rows, key=lambda o: (o.updated_at, o.id), reverse=True)  # recent


def row_info(db: Session, type_code: str, items) -> dict[int, dict]:
    """목록 한 쪽의 "연결" 건수와 "최근 활동" 일자: {번호: {"links": [(이름, 건수)], "latest": 일자|None}}.
    건수는 쪽의 번호들만 묶어서 센다 (relations.link_counts: 줄 수와 상관없이 질의 수가 일정)."""
    ids = [o.id for o in items]
    links: dict[int, list[tuple[str, int]]] = {i: [] for i in ids}
    for text, key, to in ontology.LIST_LINKS.get(type_code, ()):
        for i, n in relations.link_counts(db, type_code, ids, key, to).items():
            links[i].append((text, n))
    latest = relations.latest_activity_dates(db, type_code, ids)
    return {i: {"links": links[i], "latest": latest.get(i)} for i in ids}


def newest_first(activities) -> list[Activity]:
    """날짜 최신순. 날짜 없는 활동은 맨 뒤."""
    return sorted(
        activities,
        key=lambda a: (a.date is not None, a.date.toordinal() if a.date else 0, a.id),
        reverse=True,
    )


PER_PAGE = 50


@dataclass(frozen=True)
class Page:
    """목록 한 쪽. `items` 는 이 쪽의 항목, `total` 은 전체 건수."""
    items: list
    page: int
    pages: int
    total: int
    per_page: int = 50

    @property
    def has_pages(self) -> bool:
        return self.pages > 1

    @property
    def first(self) -> int:
        """이 쪽 첫 항목의 번호 (1부터, 비었으면 0)."""
        return (self.page - 1) * self.per_page + 1 if self.total else 0

    @property
    def last(self) -> int:
        return min(self.total, self.page * self.per_page)


def paginate(items: Sequence, page, per_page: int = PER_PAGE) -> Page:
    """`items` 를 `per_page` 건씩 나눈 `page` 쪽 (1부터). 범위 밖이거나 숫자가 아니면 1쪽."""
    items = list(items)
    pages = max(1, -(-len(items) // per_page))
    try:
        number = int(page)
    except (TypeError, ValueError):
        number = 1
    if not 1 <= number <= pages:
        number = 1
    start = (number - 1) * per_page
    return Page(items=items[start:start + per_page], page=number, pages=pages, total=len(items), per_page=per_page)

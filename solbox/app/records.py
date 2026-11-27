"""활동 기록과 솔루션 자료 파일을 저장하는 공용 로직. 화면(라우터)과 올리기 저장(`app.intake.apply`)이 함께 쓴다.

여기 함수는 커밋하지 않는다 (호출자가 커밋·롤백하고, 커밋 뒤에 지식 사본을 갱신한다)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.idparse import parse_id
from app import labels, ontology, origin as origins
from app.activity_rows import load_index, reconcile_follow_ups, reconcile_needs
from app.ai.tasks import aliases_of, normalize_name
from app.config import Settings
from app.extract import ExtractResult, extract_text
from app.intake import extension
from app.knowledge import KnowledgeMirror
from app.models import Activity, Initiative, Opportunity, Solution, SolutionFile, Strategy, User, WorkspaceFile

LINK_TYPES = {  # 연결 종류: (모델, Activity 관계 이름). 정의는 ontology.ACTIVITY_LINKS
    t: (ontology.model_for(t), rel) for t, rel in ontology.ACTIVITY_LINKS.items()
}
NEW_TYPES = {rel: ontology.model_for(t) for rel, t in ontology.ACTIVITY_NEW_TYPES.items()}  # 이름만으로 새로 만들 수 있는 대상


# ---- 솔루션 자료 파일 ----

import hashlib


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class DuplicateFile(Exception):
    """같은 솔루션에 내용이 같은 자료가 이미 있다. `existing` 은 그 자료."""

    def __init__(self, existing: SolutionFile):
        super().__init__(existing.original_name)
        self.existing = existing


def stored_sha(path: Path, f) -> str | None:
    """파일의 내용 해시: 저장된 값이 있으면 그것, 없으면(옛 파일) 지금 읽어 채운다 (비교할 때만, 기존 값은 고치지 않는다). 파일이 없으면 None."""
    if f.sha256:
        return f.sha256
    try:
        f.sha256 = sha256_of(path)
    except OSError:
        return None
    return f.sha256


def solution_file_path(settings: Settings, f: SolutionFile) -> Path:
    return settings.uploads_dir / "solutions" / str(f.solution_id) / f.stored_name



# ---- 사업 방향·과제 공간 자료 (AI 코워크 ①). 솔루션 자료와 같은 규칙: 같은 공간에 같은 내용이면 새로 만들지 않는다 ----

def workspace_dir(settings: Settings, owner) -> Path:
    kind = "strategy" if isinstance(owner, Strategy) else "initiative"
    return settings.uploads_dir / "workspaces" / kind / str(owner.id)


def workspace_file_path(settings: Settings, f: WorkspaceFile) -> Path:
    kind, oid = ("strategy", f.strategy_id) if f.strategy_id is not None else ("initiative", f.initiative_id)
    return settings.uploads_dir / "workspaces" / kind / str(oid) / f.stored_name


def new_workspace_file(
    db: Session, settings: Settings, owner: Strategy | Initiative, name: str, user: User, write: Callable[[Path], int | None],
) -> tuple[WorkspaceFile, ExtractResult] | None:
    """공간 자료 한 건 (커밋하지 않는다). 돌려주는 값과 예외는 `new_solution_file` 과 같다 (크기 초과 None, 같은 내용 `DuplicateFile`)."""
    is_strategy = isinstance(owner, Strategy)
    f = WorkspaceFile(strategy_id=owner.id if is_strategy else None, initiative_id=None if is_strategy else owner.id,
                      original_name=name, created_by_id=user.id)
    db.add(f)
    db.flush()
    f.stored_name = f"{f.id}{extension(name)}"
    path = workspace_file_path(settings, f)
    try:
        size = write(path)
        if size is None:
            return None
        f.size = size
        f.sha256 = sha256_of(path)
        col = WorkspaceFile.strategy_id if is_strategy else WorkspaceFile.initiative_id
        for other in db.scalars(select(WorkspaceFile).where(col == owner.id, WorkspaceFile.id != f.id).order_by(WorkspaceFile.id)):
            if stored_sha(workspace_file_path(settings, other), other) == f.sha256:
                path.unlink(missing_ok=True)
                db.delete(f)
                db.flush()
                raise DuplicateFile(other)
        result = extract_text(path)
        f.extract_status = result.status
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return f, result


def remove_workspace_files(settings: Settings, owner: Strategy | Initiative) -> list[Path]:
    """공간을 지울 때 지울 원본 파일 경로들 (호출자가 커밋한 뒤에 지운다). 폴더는 비면 지운다."""
    return [workspace_file_path(settings, f) for f in owner.files]


def new_solution_file(
    db: Session, settings: Settings, solution: Solution, name: str, kind: str, user: User,
    write: Callable[[Path], int | None], extracted: ExtractResult | None = None,
) -> tuple[SolutionFile, ExtractResult] | None:
    """자료 파일 한 건을 만든다: 행을 넣고(flush) `write(경로)` 로 파일을 쓰고 글을 읽는다. 커밋하지 않는다.

    `write` 는 쓴 바이트 수를, 크기 제한을 넘겨 쓰지 않았으면 None 을 돌려준다 (그러면 None; 행은 호출자가 롤백한다).
    `extracted` 가 있으면 파일에서 다시 읽지 않고 그 읽기 결과를 쓴다. 쓰다 실패하면 부분 파일을 지우고 예외를 그대로 올린다."""
    f = SolutionFile(solution_id=solution.id, original_name=name, kind=kind, created_by_id=user.id)
    db.add(f)
    db.flush()  # file_id 를 얻는다
    f.stored_name = f"{f.id}{extension(name)}"
    path = solution_file_path(settings, f)
    try:
        size = write(path)
        if size is None:
            return None
        f.size = size
        f.sha256 = sha256_of(path)
        for other in db.scalars(select(SolutionFile).where(SolutionFile.solution_id == solution.id, SolutionFile.id != f.id).order_by(SolutionFile.id)):
            if stored_sha(solution_file_path(settings, other), other) == f.sha256:  # 같은 솔루션에 내용이 같은 자료가 이미 있으면 새로 만들지 않는다
                path.unlink(missing_ok=True)
                db.delete(f)
                db.flush()
                raise DuplicateFile(other)
        result = extracted if extracted is not None else extract_text(path)
        f.extract_status = result.status
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return f, result


# ---- 활동 기록 ----

def first_line(text: str, limit: int = 40) -> str:
    """원문의 첫 줄 (제목 자동 채우기). 길면 낱말 중간에서 끊지 않고 마지막 띄어쓰기에서 끊어 "…" 를 붙인다 (베타 사전 점검 2026-11-27)."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            if len(line) <= limit:
                return line
            cut = line[:limit - 1]
            space = cut.rfind(" ")
            return (cut[:space] if space >= limit // 2 else cut).rstrip() + "…"
    return ""


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip()) if len(value.strip()) == 10 else date.today()
    except ValueError:
        return date.today()


def valid_due(value: str) -> str:
    value = value.strip()
    try:
        date.fromisoformat(value)
    except ValueError:
        return ""
    return value if len(value) == 10 else ""


def rows(*columns: list[str]) -> list[tuple[str, ...]]:
    """같은 순서의 반복 필드를 행으로 묶는다. 짧은 열은 빈 문자열로 채운다."""
    count = max((len(c) for c in columns), default=0)
    return [tuple((c[i] if i < len(c) else "").strip() for c in columns) for i in range(count)]


def follow_up_rows(task, owner, due, done) -> list[tuple[str, str, str, bool]]:
    """폼의 반복 필드를 (할 일, 담당, 기한 글자, 완료) 줄로 묶는다. 기한이 날짜가 아니면 빈 글자. 완료는 줄 위치(문자열)로 온다."""
    flags = set(done)
    return [(t, o, valid_due(d), str(i) in flags) for i, (t, o, d) in enumerate(rows(task, owner, due))]


def need_rows(customer, content, field=None) -> list[tuple]:
    """고객 니즈 줄: (고객 글, 내용, 분야 번호 글 또는 None). `field` 가 None 이면(분야 칸이 없는 화면) 분야는 그대로 둔다."""
    if field is None:
        return [(c, t, None) for c, t in rows(customer, content)]
    return list(rows(customer, content, field))


def linked(a: Activity) -> list:
    """활동 기록에 연결된 항목과, 이 기록의 니즈가 묶인 영업기회 (이들의 사본이 이 기록과 같이 바뀐다)."""
    out = [obj for _, rel in LINK_TYPES.values() for obj in getattr(a, rel)] + list(a.opportunities)
    session = object_session(a)
    ids = {n.opportunity_id for n in a.needs if n.opportunity_id is not None}
    if session is not None and ids:
        out += [o for o in session.scalars(select(Opportunity).where(Opportunity.id.in_(ids))) if o not in out]
    return out


def pick(db: Session, model, ids: list[str]) -> list:
    """id 문자열 목록에서 실제로 있는 항목만 (중복 없이) 고른다."""
    picked, seen = [], set()
    for raw in ids:
        n = parse_id(raw)
        if n is None or n in seen:
            continue
        seen.add(n)
        if (obj := db.get(model, n)) is not None:
            picked.append(obj)
    return picked


def get_or_create(db: Session, model, name: str, user: User, *, origin: tuple[str, int | None] = origins.MANUAL,
                  **fields) -> tuple[object | None, bool]:
    """같은 이름(공백·문장부호·대소문자 무시, 완전 일치만; 고객·파트너는 다른 이름도)의 항목이 있으면 그것을(유래는 그대로), 없으면 `fields` 로 새로 만든다 (flush).
    새로 만들 때만 유래 `origin` = (유형, 번호) 를 적는다 (기본: 직접 입력). (항목, 새로 만들었는지). 이름이 비면 (None, False)."""
    key = normalize_name(name)
    if not key:
        return None, False
    rows = list(db.scalars(select(model).order_by(model.id)))
    for obj in rows:
        if normalize_name(obj.name) == key:
            return obj, False
    for obj in rows:  # 고객·파트너의 다른 이름 (베타 AE)
        if any(normalize_name(a) == key for a in aliases_of(obj)):
            return obj, False
    extra = {"stage": ontology.STAGE_DEFAULT} if model is Solution else {}
    obj = model(name=name.strip(), created_by_id=user.id, origin_type=origin[0], origin_id=origin[1], **extra, **fields)
    db.add(obj)
    db.flush()
    return obj, True


def find_or_create(db: Session, model, names: list[str], user: User, origin: tuple[str, int | None] = origins.MANUAL) -> list:
    """이름만 가진 항목을 만든다. 같은 이름이 있으면 그것을 쓴다."""
    result = []
    for name in names:
        obj, _ = get_or_create(db, model, name, user, origin=origin)
        if obj is not None and obj not in result:
            result.append(obj)
    return result


def ambiguous_view(db: Session, model, items: list[dict]) -> list[dict]:
    """모호한 이름의 후보 id 를 화면에 보일 이름으로 바꾼다."""
    return [{"name": i["name"], "candidates": [o.name for c in i["candidates"] if (o := db.get(model, c)) is not None]}
            for i in items]


def apply_activity_fields(
    db: Session, user: User, a: Activity, *, kind, date_, title, raw_text, summary_md,
    fu_task, fu_owner, fu_due, fu_done, ds_customer, ds_content,
    solution_ids, customer_ids, project_ids, partner_ids, new_solutions, new_customers, new_partners,
    fu_id=(), ds_id=(), origin: tuple[str, int | None] = origins.MANUAL, opportunity_ids: list[str] | None = None,
    ds_field: list[str] | None = None,
) -> None:
    """폼 값을 활동 기록에 반영한다: 필드, 후속 조치, 고객 니즈, 연결, 새로 등록할 이름. 커밋하지 않는다.
    후속 조치·고객 니즈는 `fu_id`·`ds_id`(줄 번호, 새 줄은 빈 글자) 기준으로 표의 행을 맞춘다 (번호 유지·삭제·추가)."""
    a.kind = kind if kind in labels.ACTIVITY_KINDS else ontology.ACTIVITY_KIND_OTHER
    a.date = parse_date(date_)
    a.raw_text = raw_text
    a.title = title.strip() or first_line(raw_text) or first_line(summary_md)
    a.summary_md = summary_md.strip()
    ids = dict(solution=solution_ids, customer=customer_ids, project=project_ids, partner=partner_ids)
    for t, (model, rel) in LINK_TYPES.items():
        setattr(a, rel, pick(db, model, ids[t]))
    if opportunity_ids is not None:  # 활동 기록 화면에서만 직접 고른다 (None 이면 올리기 저장처럼 손대지 않는다)
        a.opportunities = pick(db, Opportunity, opportunity_ids)
    for rel, names in (("solutions", new_solutions), ("customers", new_customers), ("partners", new_partners)):
        current = getattr(a, rel)
        for obj in find_or_create(db, NEW_TYPES[rel], names, user, origin):
            if obj not in current:
                current.append(obj)
    index = load_index(db)  # 새로 등록한 고객도 포함하도록 연결 뒤에 맞춘다
    reconcile_follow_ups(a, fu_id, follow_up_rows(fu_task, fu_owner, fu_due, fu_done), index)
    reconcile_needs(a, ds_id, need_rows(ds_customer, ds_content, ds_field), index, db)
    for n in a.needs:  # 새 니즈 줄의 작성자는 저장한 사람 (고객니즈 관리 2026-11-27)
        if n.id is None and n.created_by_id is None and user is not None:
            n.created_by_id = user.id


def sync_all(mirror: KnowledgeMirror, a: Activity, extra: list = ()) -> None:
    """활동 기록과 거기 연결된(또는 `extra` 로 준) 항목의 지식 사본을 다시 쓴다."""
    mirror.sync(a)
    seen = set()
    for obj in [*linked(a), *extra]:
        if (type(obj), obj.id) not in seen:
            seen.add((type(obj), obj.id))
            mirror.sync(obj)


# ---- 참석한 담당자 (담당자 관리 부담 줄이기 ①, 2026-11-27). 규칙: app/contacts.py ----

def contact_scope(a: Activity) -> list:
    """이 활동 기록에 이을 수 있는 담당자: 연결된 고객·파트너의 담당자 (번호순)."""
    out = [p for c in a.customers for p in c.contacts] + [p for c in a.partners for p in c.contacts]
    return sorted(dict.fromkeys(out), key=lambda p: p.id)


def link_contacts(db: Session, user: User, a: Activity, contact_ids, new_people, origin: tuple[str, int | None] = origins.MANUAL) -> int:
    """폼에서 고른 담당자를 잇는다 (범위 밖 번호는 버린다). `new_people`("이름|직함")은 연결된 회사가 정확히 하나일 때 그 회사에 만든다
    (같은 사람이 이미 있으면 그 사람을 잇는다). 만든 수를 돌려준다. 커밋하지 않는다."""
    from app import contacts as ct
    from app.models import Contact

    db.flush()
    scope = {p.id: p for p in contact_scope(a)}
    ids = {n for i in contact_ids if (n := parse_id(i)) is not None}
    picked = [scope[i] for i in sorted(ids) if i in scope]
    made = 0
    companies = [("customer", c) for c in a.customers] + [("partner", p) for p in a.partners]
    if len(companies) == 1:
        kind, company = companies[0]
        for raw in new_people:
            name, _, title = str(raw).partition("|")
            if not name.strip():
                continue
            same = ct.same_person(company.contacts, name.strip())
            if same is None:
                same = Contact(name=name.strip(), title=title.strip(), created_by_id=user.id, origin_type=origin[0], origin_id=origin[1])
                setattr(same, kind, company)
                db.add(same)
                db.flush()
                made += 1
            if same not in picked:
                picked.append(same)
    a.contacts = picked
    return made


def auto_link_contacts(db: Session, a: Activity, attendees=()) -> int:
    """피커가 없는 저장(메모 "저장"·직접 입력 새 폼·올리기)의 자동 연결: AI 참석자 중 정확히 맞은 사람 + 글에 이름이 나온 사람 (같은 이름이 둘이면 잇지 않는다).
    이미 이어진 사람은 그대로. 더한 수를 돌려준다."""
    from app import contacts as ct

    db.flush()
    scope = contact_scope(a)
    by_id = {p.id: p for p in scope}
    found = [by_id[m["contact_id"]] for m in ct.match_attendees(scope, ct.attendee_list(attendees), ct.staff_names(db)) if m["status"] == "matched"]
    found += ct.found_in_text(scope, f"{a.raw_text or ''}\n{a.summary_md or ''}", ct.staff_names(db))
    added = [p for p in dict.fromkeys(found) if p not in a.contacts]
    a.contacts = list(a.contacts) + added
    return len(added)


def spread_contacts(a: Activity) -> int:
    """참석한 담당자를 이 활동 기록에 이어진 영업기회(직접·니즈로)에 더한다: 그 영업기회 고객의 담당자만, 빼지는 않는다."""
    n = 0
    for o in (x for x in linked(a) if isinstance(x, Opportunity)):
        add = [p for p in a.contacts if p.customer_id is not None and p.customer_id == o.customer_id and p not in o.contacts]
        if add:
            o.contacts = list(o.contacts) + add
            n += len(add)
    return n

"""솔루션 분야 규칙 한 곳 (베타 AD, 사용자 결정 2026-11-24).

- 분야는 코드 목록이 아니라 데이터다: 표 `solution_fields` (이름 유일, 순서, 사용 여부). 관리자만 더하고 고치고 순서를 바꾸고 끄고 지운다 (`routers/solution_fields.py`).
- 솔루션은 분야를 하나 가질 수 있다 (열 `solutions.field_id`, 선택). 비면 "분야 미지정".
- 처음 표가 만들어질 때 `ontology.SOLUTION_FIELD_SEED` 를 이 순서로 한 번만 채운다 (`seed_once`: 설정 표시 `solution_fields_seeded`). 관리자가 지운 값은 다시 넣지 않는다.
- AI 는 제안만 하고 저장하지 않는다: 프롬프트에 사용 중인 분야 이름 목록을 주고, 돌아온 이름을 사용 중인 분야에서 찾아 번호로 바꾼다 (모르는 이름은 버린다).
- 묶어 보기(솔루션 목록)의 묶음 만들기·순서는 `build_groups`.
"""
from __future__ import annotations

from typing import Any, NamedTuple

from fastapi import Request
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app import ontology, ui
from app.models import AppSetting, Solution, SolutionField

SEEDED_KEY = "solution_fields_seeded"  # AppSetting 표시: 처음 채우기를 이미 했다

NAME_REQUIRED = ui.FieldError("분야 이름을 입력해 주세요.", "name")
NAME_TOO_LONG = ui.FieldError(f"분야 이름은 {ontology.SOLUTION_FIELD_NAME_MAX}자까지 쓸 수 있습니다.", "name")
NAME_TAKEN = ui.FieldError("이미 있는 분야 이름입니다.", "name")
BAD_FIELD = ui.FieldError("알 수 없는 분야입니다. 목록에서 다시 골라 주세요.", "field_id")
INACTIVE_FIELD = ui.FieldError("사용 중이 아닌 분야는 새로 고를 수 없습니다. 목록에서 다시 골라 주세요.", "field_id")


def seed_once(engine: Engine) -> bool:
    """시작할 때: 처음 한 번만 기본 분야를 채운다 (표시가 있으면 아무것도 하지 않는다. 관리자가 모두 지워도 다시 채우지 않는다).
    표에 이미 줄이 있으면 (손으로 넣은 DB) 줄은 건드리지 않고 표시만 남긴다. 채웠으면 True."""
    with Session(engine) as db:
        if db.get(AppSetting, SEEDED_KEY) is not None:
            return False
        seeded = False
        if db.scalar(select(func.count()).select_from(SolutionField)) == 0:
            db.add_all(SolutionField(name=name, sort_order=i + 1, active=True) for i, name in enumerate(ontology.SOLUTION_FIELD_SEED))
            seeded = True
        db.add(AppSetting(key=SEEDED_KEY, value={"seeded": seeded}))
        db.commit()
        return seeded


# ---- 읽기 ----

def all_fields(db: Session) -> list[SolutionField]:
    return list(db.scalars(select(SolutionField).order_by(SolutionField.sort_order, SolutionField.id)))


def active_fields(db: Session) -> list[SolutionField]:
    return [f for f in all_fields(db) if f.active]


def usage_counts(db: Session) -> dict[int, int]:
    """분야 번호 -> 그 분야를 쓰는 솔루션 수 (질의 하나)."""
    return dict(db.execute(select(Solution.field_id, func.count()).where(Solution.field_id.is_not(None)).group_by(Solution.field_id)).all())


def none_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Solution).where(Solution.field_id.is_(None))) or 0


def select_options(db: Session, solution: Solution | None = None, current_id: Any = None) -> list[tuple[str, str]]:
    """폼의 선택 목록: `("", "(분야 미지정)")` + 사용 중인 분야 + (이 솔루션이 이미 가진 사용 안 하는 분야는 "(사용 안 함)" 표시로 남긴다).
    `current_id` 는 오류로 폼을 다시 그릴 때 사용자가 고른 값 (그 값이 사용 안 하는 분야여도 보이게)."""
    keep = {x for x in (solution.field_id if solution is not None else None, _as_int(current_id)) if x is not None}
    out = [("", f"({ontology.SOLUTION_FIELD_NONE})")]
    for f in all_fields(db):
        if f.active:
            out.append((str(f.id), f.name))
        elif f.id in keep:
            out.append((str(f.id), f"{f.name} {ontology.SOLUTION_FIELD_INACTIVE}"))
    return out


def _as_int(raw: Any) -> int | None:
    text = str(raw).strip() if raw is not None else ""
    return int(text) if text.isascii() and text.isdecimal() and len(text) <= 18 else None


def resolve_form(db: Session, raw: str, solution: Solution | None = None) -> tuple[int | None, ui.FieldError | None]:
    """폼의 분야 값 -> (분야 번호 또는 None, 오류). 빈 값은 분야 없음. 없는 번호·숫자 아닌 값은 400 오류. 사용을 끈 분야는 이 솔루션이 이미 가진 것일 때만 받는다."""
    text = (raw or "").strip()
    if not text:
        return None, None
    number = _as_int(text)
    found = db.get(SolutionField, number) if number is not None else None
    if found is None:
        return None, BAD_FIELD
    if not found.active and not (solution is not None and solution.field_id == found.id):
        return None, INACTIVE_FIELD
    return found.id, None


def name_of(solution: Solution) -> str:
    """지식 사본·충돌 안내용 한 줄 값: 분야 이름 (없으면 빈 글)."""
    return solution.field.name if solution.field is not None else ""


# ---- 관리자 화면의 쓰기 ----

def clean_name(raw: str) -> str:
    """한 줄로: 줄바꿈·연속 공백은 한 칸."""
    return " ".join((raw or "").split())


def name_problem(db: Session, raw: str, exclude: SolutionField | None = None) -> ui.FieldError | None:
    """빈 이름·30자 초과·이미 있는 이름(앞뒤 공백·영문 대소문자를 무시) 이면 오류."""
    name = clean_name(raw)
    if not name:
        return NAME_REQUIRED
    if len(name) > ontology.SOLUTION_FIELD_NAME_MAX:
        return NAME_TOO_LONG
    key = name.casefold()
    if any(f.name.casefold() == key for f in all_fields(db) if exclude is None or f.id != exclude.id):
        return NAME_TAKEN
    return None


def add(db: Session, raw: str) -> SolutionField:
    """맨 뒤에 새 분야를 더한다 (이름은 미리 `name_problem` 으로 확인한다)."""
    last = db.scalar(select(func.max(SolutionField.sort_order))) or 0
    field = SolutionField(name=clean_name(raw), sort_order=last + 1, active=True)
    db.add(field)
    return field


def move(db: Session, field: SolutionField, direction: int) -> bool:
    """한 칸 위(-1)·아래(+1)로. 순서 번호를 1..n 으로 다시 매기므로 겹치거나 비어도 바로잡힌다. 맨 위·맨 아래에서 더 못 가면 False."""
    rows = all_fields(db)
    i = next(n for n, f in enumerate(rows) if f.id == field.id)
    j = i + direction
    if not 0 <= j < len(rows):
        return False
    rows[i], rows[j] = rows[j], rows[i]
    for n, f in enumerate(rows, start=1):
        if f.sort_order != n:
            f.sort_order = n
    return True


def need_usage_counts(db: Session) -> dict[int, int]:
    """분야 번호 -> 그 분야의 고객 니즈 수 (고객니즈 관리 2026-11-27: 니즈도 솔루션 분야를 같이 쓴다)."""
    from app.models import CustomerNeed

    return dict(db.execute(select(CustomerNeed.field_id, func.count()).where(CustomerNeed.field_id.is_not(None)).group_by(CustomerNeed.field_id)).all())


def delete_problem(db: Session, field: SolutionField) -> str | None:
    """지울 수 없는 이유 (쓰는 솔루션·고객 니즈가 있을 때): 몇 건인지와 대신 할 일. 없으면 None."""
    n = usage_counts(db).get(field.id, 0)
    m = need_usage_counts(db).get(field.id, 0)
    if not (n or m):
        return None
    used = " · ".join(x for x in (f"솔루션 {n}건" if n else "", f"고객 니즈 {m}건" if m else "") if x)
    return f"이 분야를 쓰는 {used}이 있어 지울 수 없습니다. 사용을 끄면 새로 고를 수 없고 이미 붙은 기록은 그대로 둘 수 있습니다."


def solutions_of(db: Session, field: SolutionField) -> list[Solution]:
    return list(db.scalars(select(Solution).where(Solution.field_id == field.id)))


# ---- AI 제안 (저장하지 않는다) ----

def prompt_names(db: Session) -> str:
    """프롬프트에 주는 사용 중인 분야 이름 목록 (한 줄에 하나, 없으면 안내). 이름은 사용자가 정한 글이라 지시가 아니다."""
    names = [clean_name(f.name) for f in active_fields(db)]
    return "\n".join(f"- {n}" for n in names) or "(등록된 분야 없음)"


def suggest_id(db: Session, raw: Any) -> int | None:
    """AI 가 돌려준 분야 이름 -> 사용 중인 분야의 번호 (이름이 정확히 같을 때만, 공백·대소문자 무시). 모르는 이름·글이 아닌 값·빈 값은 None."""
    if not isinstance(raw, str):
        return None
    key = clean_name(raw).casefold()
    if not key:
        return None
    return next((f.id for f in active_fields(db) if clean_name(f.name).casefold() == key), None)


# ---- 솔루션 목록 묶어 보기 ----

class Group(NamedTuple):
    key: str  # 묶음 값 (분야·파트너 번호, 단계·사업방식 코드, 없는 묶음은 ""). 화면 앵커로 쓴다
    label: str
    items: list
    muted: bool  # "분야 미지정" 같은 빈 묶음 표시 (이름 대신 값이 없다는 뜻)


def group_param(request: Request) -> tuple[str, bool]:
    """(묶어 볼 기준, 주소에 `group` 이 있었는가). 기준: field/stage/business_type/partner, 없음은 "". 주소에 없으면 쿠키에 기억한 값, 모르는 값은 없음.
    `group=none`(또는 알 수 없는 값)이면 묶지 않는다."""
    raw = request.query_params.get(ontology.SOLUTION_GROUP_PARAM)
    explicit = raw is not None
    if raw is None:
        raw = request.cookies.get(ontology.SOLUTION_GROUP_COOKIE, "")
    return (raw if raw in ontology.SOLUTION_GROUPS else ""), explicit


def build_groups(db: Session, kind: str, solutions: list[Solution]) -> list[Group]:
    """걸러진 솔루션 전부를 `kind` 기준의 묶음으로 (빈 묶음은 만들지 않는다). 묶음 안의 순서는 들어온 순서(정렬 선택)를 그대로 지킨다.
    - field: 분야 순서(`sort_order`)대로, 사용을 끈 분야도 쓰는 솔루션이 있으면 "(사용 안 함)" 표시로, 분야 미지정은 맨 뒤.
    - stage: 발굴 단계 순서(`labels.STAGES`).
    - business_type: 사업방식 코드 순서, 여러 방식을 가진 솔루션은 각 묶음에 모두 나오고, 사업방식 미지정은 맨 뒤.
    - partner: 파트너 이름순(번호가 보조 키), 파트너 없음은 맨 뒤."""
    from app import labels

    if kind == "field":
        order = all_fields(db)
        by: dict[int | None, list] = {}
        for s in solutions:
            by.setdefault(s.field_id, []).append(s)
        out = [Group(str(f.id), f.name if f.active else f"{f.name} {ontology.SOLUTION_FIELD_INACTIVE}", by[f.id], False) for f in order if f.id in by]
        return out + _none_group(kind, by.get(None, []))
    if kind == "stage":
        by = {}
        for s in solutions:
            by.setdefault(s.stage, []).append(s)
        known = [Group(code, name, by[code], False) for code, name in labels.STAGES.items() if code in by]
        extra = [Group(code, code or "(알 수 없음)", rows, True) for code, rows in by.items() if code not in labels.STAGES]  # 정의에 없는 값도 잃지 않는다
        return known + extra
    if kind == "business_type":
        out, none_rows = [], []
        for code, name in ontology.BUSINESS_TYPES.items():
            rows = [s for s in solutions if code in s.business_type_codes]
            if rows:
                out.append(Group(code, name, rows, False))
        none_rows = [s for s in solutions if not s.business_type_codes]
        return out + _none_group(kind, none_rows)
    if kind == "partner":
        by = {}
        for s in solutions:
            by.setdefault(s.partner_id, []).append(s)
        partners = sorted((rows[0].partner for pid, rows in by.items() if pid is not None and rows[0].partner is not None), key=lambda p: (p.name, p.id))
        return [Group(str(p.id), p.name, by[p.id], False) for p in partners] + _none_group(kind, by.get(None, []))
    return []


def _none_group(kind: str, rows: list) -> list[Group]:
    return [Group("", ontology.SOLUTION_GROUP_NONE_LABELS[kind], rows, True)] if rows else []

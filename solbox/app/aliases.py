"""고객·파트너의 "다른 이름" (베타 AE, 2026-11-24).

같은 회사가 "HS홀딩스" 와 "에이치에스홀딩스" 처럼 글자 체계가 달라 둘로 등록되는 일을 줄이려고, 고객·파트너는 이름 말고도 다른 이름 목록을 가진다.
저장은 `partners.aliases`·`customers.aliases` 글 칸 하나(한 줄에 하나)다. 작은 표를 따로 두지 않은 까닭: 한 기록에 몇 개 안 되고, 폼의 글 칸으로 고치고,
지식 사본·고침 표시·충돌 비교·검색(LIKE)이 글 칸 그대로 쓰이며, 옛 DB 에는 칸 하나만 더하면 된다 (`migrate.py` 자동).

이름 견주기는 이름과 다른 이름을 같은 정규형으로 본다: `ai.tasks.normalize_name` (호출하는 곳이 쓰던 `company` 값을 그대로 넘긴다).
커밋하지 않는다 (`add_alias` 는 값만 바꾼다)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tasks import normalize_name
from app.models import Customer, Partner

MAX_ALIASES = 20
MAX_ALIAS_CHARS = 100
LABEL = "다른 이름"
HELP = "한 줄에 하나씩 적어요. 같은 회사의 영문·한글 표기, 옛 이름 등 (예: SOLUM)."


def parse(text: str | None) -> list[str]:
    """글 칸 → 이름 목록: 줄마다 공백을 정리하고, 빈 줄과 (정규형이 같은) 되풀이를 뺀다. 앞 것이 남는다."""
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        name = " ".join(line.split())
        key = normalize_name(name, True)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def dump(names: list[str]) -> str:
    return "\n".join(names)


def parse_text(text: str | None) -> str:
    """폼 글 칸을 저장 모양(줄마다 하나, 정리·되풀이 제거)으로."""
    return dump(parse(text))


def of(obj) -> list[str]:
    return parse(getattr(obj, "aliases", "") or "")


def keys_of(obj, company: bool = False) -> set[str]:
    """이름과 다른 이름들의 정규형 (빈 것 제외)."""
    keys = {normalize_name(n, company) for n in (obj.name or "", *of(obj))}
    keys.discard("")
    return keys


def index(db: Session, model, company: bool = False) -> dict[str, list]:
    """정규형 → 그 이름이나 다른 이름을 가진 기록들 (번호순). 한 기록은 한 정규형에 한 번만 든다."""
    out: dict[str, list] = {}
    for obj in db.scalars(select(model).order_by(model.id)):
        for key in keys_of(obj, company):
            out.setdefault(key, []).append(obj)
    return out


def lookup(db: Session, model, company: bool = False) -> dict[str, object]:
    """정규형 → 기록 하나. 이름이 정규형인 기록이 먼저이고(같은 이름이 둘이면 번호가 큰 쪽, 옛 `{정규형: 기록}` 모음과 같다),
    이름으로는 없고 다른 이름으로 가진 정규형만 번호가 가장 작은 기록으로 채운다. 한 정규형을 가진 기록이 여럿인지는 `index` 가 알려 준다."""
    named: dict[str, object] = {}
    aliased: dict[str, object] = {}
    for obj in db.scalars(select(model).order_by(model.id)):
        own = normalize_name(obj.name or "", company)
        if own:
            named[own] = obj
        for a in of(obj):
            key = normalize_name(a, company)
            if key:
                aliased.setdefault(key, obj)
    return {**aliased, **named}


def alias_matches(obj, q: str) -> list[str]:
    """검색어 `q`(대소문자 무시, 부분 일치)가 든 다른 이름들."""
    low = (q or "").casefold()
    return [a for a in of(obj) if low and low in a.casefold()]


def validate(db: Session, model, name: str, text: str, exclude=None) -> tuple[str, str | None]:
    """폼의 다른 이름 칸을 검사해 (정리한 글, 오류 문구 또는 None). 오류가 나도 정리한 글은 돌려준다.

    오류: 자기 이름과 같음 / 같은 칸 안의 되풀이 / 같은 종류의 다른 기록의 이름이나 다른 이름과 같음 (그 기록 이름을 말해 준다).
    고객·파트너 이름 규칙대로 ㈜·(주)·주식회사·공백·기호·대소문자는 무시하고 견준다."""
    raw_lines = [" ".join(x.split()) for x in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    raw_lines = [x for x in raw_lines if normalize_name(x, True)]
    names = parse(text)
    cleaned = dump(names)
    if len(raw_lines) > MAX_ALIASES:
        return cleaned, f"다른 이름은 {MAX_ALIASES}개까지 적을 수 있어요."
    own = normalize_name(name or "", True)
    seen: dict[str, str] = {}
    for line in raw_lines:
        key = normalize_name(line, True)
        if len(line) > MAX_ALIAS_CHARS:
            return cleaned, f"다른 이름 '{line[:20]}…' 이 너무 길어요 ({MAX_ALIAS_CHARS}자까지)."
        if key == own:
            return cleaned, f"다른 이름 '{line}' 은 이 기록의 이름과 같아요. 이름과 다른 표기만 적어 주세요."
        if key in seen:
            return cleaned, f"다른 이름 '{line}' 이 '{seen[key]}' 과 같아요. 한 번만 적어 주세요."
        seen[key] = line
    if seen:
        for other in db.scalars(select(model).order_by(model.id)):
            if exclude is not None and other.id == exclude.id:
                continue
            theirs = keys_of(other, True)
            hit = next((line for key, line in seen.items() if key in theirs), None)
            if hit is not None:
                return cleaned, f"다른 이름 '{hit}' 은 이미 {kind_label(model)} '{other.name}' 의 이름(또는 다른 이름)이에요. 같은 회사라면 합치기로 하나로 만들어 주세요."
    return cleaned, None


def kind_label(model) -> str:
    return "고객" if model is Customer else "파트너" if model is Partner else "기록"


def add_alias(obj, name: str) -> bool:
    """`name` 을 obj 의 다른 이름에 더한다 (자기 이름이거나 이미 있으면 안 더한다). 더했으면 True."""
    key = normalize_name(name or "", True)
    if not key or key == normalize_name(obj.name or "", True) or key in {normalize_name(a, True) for a in of(obj)}:
        return False
    obj.aliases = dump([*of(obj), " ".join(name.split())])
    return True

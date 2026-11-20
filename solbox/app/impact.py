"""지우기 전에 알려 주는 영향: "영업기회 2건, BM 1건, 활동 기록 3건과의 연결이 끊깁니다. 제안 건 1건의 방향에 이 솔루션이 들어 있습니다."

건수는 관계 엔진(`relations.link_counts`)으로 세고, 무엇을 셀지는 `ontology.DELETE_IMPACT` 한 곳이다. 지우는 규칙은 바꾸지 않는다 (알려 주기만 한다)."""
from __future__ import annotations

from sqlalchemy.orm import Session, object_session

from app import ontology, relations


def type_code(obj) -> str | None:
    return next((t.code for t in ontology.TYPES.values() if t.model_name == type(obj).__name__), None)


def counts(db: Session, code: str, obj) -> dict[str, list[tuple[str, int]]]:
    """{"cut": [(이름, 건수)], "gone": [...], "inside": [(문장, 건수)]} (0건은 뺀다)."""
    spec = ontology.DELETE_IMPACT.get(code, {})
    out: dict[str, list] = {"cut": [], "gone": [], "inside": []}
    for kind in ("cut", "gone"):
        for label, key, to in spec.get(kind, ()):
            n = relations.link_counts(db, code, [obj.id], key, to).get(obj.id, 0)
            if n:
                out[kind].append((label, n))
    for key, template in spec.get("inside", ()):
        n = relations.link_counts(db, code, [obj.id], key, "proposal").get(obj.id, 0)
        if n:
            out["inside"].append((template, n))
    return out


def sentences(db: Session, code: str, obj) -> list[str]:
    """영향 문장들 (없으면 연결된 기록이 없다는 한 줄)."""
    c = counts(db, code, obj)
    lines = []
    if c["cut"]:
        lines.append(", ".join(f"{label} {n}건" for label, n in c["cut"]) + "과의 연결이 끊깁니다.")
    if c["gone"]:
        lines.append(", ".join(f"{label} {n}건" for label, n in c["gone"]) + "이 함께 지워집니다.")
    lines += [template.format(n=n) for template, n in c["inside"]]
    return lines or ["연결된 다른 기록이 없어 다른 기록에는 영향이 없습니다."]


def text_for(obj) -> str:
    """상세 화면의 삭제 버튼(스크립트가 있을 때 그 자리에서 묻는 글)에 붙이는 한 덩어리 글. 세션이 없는 객체면 빈 글."""
    db = object_session(obj)
    code = type_code(obj)
    if db is None or code is None or code not in ontology.DELETE_IMPACT:
        return ""
    return " ".join(sentences(db, code, obj))

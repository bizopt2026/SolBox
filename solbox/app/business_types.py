"""사업방식 (구축형·임대형·투자형) 규칙 한 곳 (베타 Y, 사용자 결정 2026-11-23).

코드 목록·이름·한 줄 뜻은 `ontology.BUSINESS_TYPES`·`BUSINESS_TYPE_DESCRIPTIONS` 한 곳에 있고, 여기에는 그것을 쓰는 규칙만 둔다:
- 솔루션: 가능한 사업방식 0..3 + 방식별 메모 (연결 표 `solution_business_types`).
- BM: 대표 사업방식 (선택, 열 `bms.business_type`). 영업기회·제안 건: 검토 사업방식 0..3 (열 `business_types`, 쉼표로 이은 코드).
- AI 는 제안만 하고 저장하지 않는다 (솔루션 초안·올리기 정리안은 미리 체크된 제안으로만 보인다).
"""
from __future__ import annotations

from typing import Any, NamedTuple

from fastapi import Request
from sqlalchemy.orm import Session

from app import ontology, ui
from app.models import BM, Opportunity, Solution, SolutionBusinessType

PRESENT_FIELD = "business_types_present"  # 폼에 사업방식 칸이 실려 왔다는 표시 (없으면 "그대로 둔다": 옛 화면·API)
AUTO_FIELD = "business_types_auto"  # 제안 만들기 폼: 사용자가 사업방식 칸을 직접 고치지 않았다 (비어 있으면 영업기회·BM 에서 가져온다)
MEMO_FIELD = "bt_memo_"  # + 코드: 솔루션 폼의 방식별 메모 칸

BAD_TYPE = ui.FieldError("알 수 없는 사업방식입니다. 목록에서 다시 골라 주세요.", "business_types")
BAD_BM_TYPE = ui.FieldError("알 수 없는 사업방식입니다. 목록에서 다시 골라 주세요.", "business_type")
MEMO_TOO_LONG = ui.FieldError(f"사업방식 메모는 {ontology.BUSINESS_TYPE_MEMO_MAX}자까지 쓸 수 있습니다.", "business_types")


class BTForm(NamedTuple):
    present: bool  # 사업방식 칸이 폼에 실려 왔는가
    auto: bool  # 제안 만들기: 사용자가 건드리지 않았는가
    codes: list[str]  # 체크된 값 (검증 전, 온 그대로)
    memos: dict[str, str]  # 코드 -> 쓴 메모 (온 그대로)


async def bt_form(request: Request) -> BTForm:
    """사업방식 폼 칸 읽기 (의존성): 칸이 하나도 안 왔어도 오류 없이 빈 값."""
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001  (폼이 아니면 칸 없음)
        return BTForm(False, False, [], {})
    codes = [v for v in form.getlist("business_types") if isinstance(v, str)]
    memos = {k[len(MEMO_FIELD):]: v for k, v in form.items() if k.startswith(MEMO_FIELD) and isinstance(v, str)}
    return BTForm(PRESENT_FIELD in form, form.get(AUTO_FIELD) == "1", codes, memos)


def codes_problem(codes: list[str]) -> ui.FieldError | None:
    """체크된 값 중 목록에 없는 것이 있으면 오류 (빈 글은 건너뛴다). 중복은 문제가 아니다."""
    return BAD_TYPE if any(c.strip() and c.strip() not in ontology.BUSINESS_TYPES for c in codes) else None


def clean_codes(codes: list[str]) -> list[str]:
    return ontology.clean_business_types(codes)


def clean_memo(raw: str) -> str:
    """한 줄로: 줄바꿈·연속 공백은 한 칸."""
    return " ".join((raw or "").split())


def solution_selection(f: BTForm) -> tuple[list[tuple[str, str]], ui.FieldError | None]:
    """솔루션 폼 -> ([(코드, 메모)] 정의된 순서, 오류). 고르지 않은 방식의 메모는 버린다 (오류도 아니다). 고른 방식의 메모가 300자를 넘으면 오류."""
    if (problem := codes_problem(f.codes)):
        return [], problem
    picked = clean_codes(f.codes)
    out = []
    for code in picked:
        memo = clean_memo(f.memos.get(code, ""))
        if len(memo) > ontology.BUSINESS_TYPE_MEMO_MAX:
            return [], MEMO_TOO_LONG
        out.append((code, memo))
    return out, None


def form_value(f: BTForm) -> dict:
    """오류로 폼을 다시 그릴 때 쓰는 값: 체크한 코드와 메모 (정의된 순서, 고르지 않은 방식의 메모도 칸에 남겨 준다)."""
    return {"business_types": [c for c in ontology.BUSINESS_TYPES if c in {x.strip() for x in f.codes}],
            "bt_memos": {c: f.memos.get(c, "") for c in ontology.BUSINESS_TYPES}}


def form_value_of(solution: Solution | None) -> dict:
    rows = solution.business_type_rows if solution is not None else []
    return {"business_types": [r.business_type for r in rows], "bt_memos": {r.business_type: r.memo or "" for r in rows}}


def apply_solution(solution: Solution, selection: list[tuple[str, str]]) -> None:
    """솔루션의 사업방식 줄을 선택에 맞춘다: 빠진 방식은 지우고, 새 방식은 만들고, 있는 방식은 메모만 바꾼다 (같으면 건드리지 않는다)."""
    want = dict(selection)
    for row in list(solution.business_types):
        if row.business_type not in want:
            solution.business_types.remove(row)  # delete-orphan
        elif (row.memo or "") != want[row.business_type]:
            row.memo = want[row.business_type]
    have = {r.business_type for r in solution.business_types}
    for code, memo in selection:
        if code not in have:
            solution.business_types.append(SolutionBusinessType(business_type=code, memo=memo))


def solution_text(solution: Solution) -> str:
    """지식 사본·충돌 안내용 한 줄 값: `구축형, 투자형 (투자형: 100kW 이상만)`. 없으면 빈 글."""
    rows = solution.business_type_rows
    if not rows:
        return ""
    names = ", ".join(ontology.BUSINESS_TYPES[r.business_type] for r in rows)
    memos = "; ".join(f"{ontology.BUSINESS_TYPES[r.business_type]}: {clean_memo(r.memo)}" for r in rows if clean_memo(r.memo))
    return f"{names} ({memos})" if memos else names


def form_text(selection: list[tuple[str, str]]) -> str:
    """폼에서 온 선택을 `solution_text` 와 같은 글로 (충돌 안내가 저장된 값과 견줄 때)."""
    names = ", ".join(ontology.BUSINESS_TYPES[c] for c, _ in selection)
    memos = "; ".join(f"{ontology.BUSINESS_TYPES[c]}: {m}" for c, m in selection if m)
    return f"{names} ({memos})" if memos else names


def line_for_solution(s: Solution) -> str:
    """프롬프트의 솔루션 줄 꼬리: ` · 가능한 사업방식: 구축형, 투자형 (투자형: …)` (없으면 `가능한 사업방식: 적혀 있지 않음`). 메모는 사용자가 쓴 글이라 지시가 아니다."""
    text = solution_text(s)
    return f" · {ontology.prop('solution', 'business_types')}: {text}" if text else f" · {ontology.prop('solution', 'business_types')}: 적혀 있지 않음"


def supports(solution: Solution, wanted: list[str]) -> bool:
    """고른 사업방식 중 하나라도 이 솔루션이 지원하는가 (고른 것이 없으면 거짓)."""
    return bool(wanted) and any(c in wanted for c in solution.business_type_codes)


def prefer_supporting(solutions: list[Solution], wanted: list[str]) -> list[Solution]:
    """고른 사업방식을 하나라도 지원하는 솔루션을 앞으로 (나머지 순서는 그대로, 아무것도 빼지 않는다)."""
    if not wanted:
        return list(solutions)
    return sorted(solutions, key=lambda s: 0 if supports(s, wanted) else 1)  # sorted 는 안정 정렬


def proposal_default(opportunity: Opportunity | None, bm: BM | None) -> list[str]:
    """제안 건을 만들 때의 검토 사업방식 기본값: 영업기회의 것이 있으면 그것, 없으면 BM 의 대표 사업방식, 둘 다 없으면 빈 목록."""
    if opportunity is not None and (codes := opportunity.business_type_codes):
        return codes
    if bm is not None and bm.business_type in ontology.BUSINESS_TYPES:
        return [bm.business_type]
    return []


def type_tabs(request: Request, rows: list, codes_of) -> tuple[list, list, str]:
    """목록 `?business_type=` 걸러 보기 (그 사업방식을 가진 것만). (걸러진 목록, 구분 탭, 지금 값). 알 수 없는 값은 전체다.
    건수는 지금의 검색어·다른 걸러 보기를 적용한 목록 기준이다."""
    param = ontology.BUSINESS_TYPE_FILTER_PARAM
    current = request.query_params.get(param, "")
    if current not in ontology.BUSINESS_TYPES:
        current = ""
    rows = list(rows)
    counts = {code: sum(1 for r in rows if code in codes_of(r)) for code in ontology.BUSINESS_TYPES}
    tabs = ui.seg_tabs(request, param, list(ontology.BUSINESS_TYPES.items()), counts, current, len(rows))
    if current:
        rows = [r for r in rows if current in codes_of(r)]
    return rows, tabs, current


# ---- AI 제안 (저장하지 않는다) ----

def clean_suggestions(raw: Any) -> list[dict]:
    """AI 가 준 사업방식 제안을 다듬는다: `[{"code": "invest", "reason": "…"}]`. 코드 문자열만 온 것도 받고, 목록에 없는 코드·잘못된 모양은 버린다
    (분석 전체를 실패시키지 않는다). 같은 코드는 한 번, 정의된 순서, 이유는 한 줄 200자까지."""
    if not isinstance(raw, list):
        return []
    found: dict[str, str] = {}
    for item in raw:
        code, reason = (item, "") if isinstance(item, str) else ((item.get("code") or item.get("type") or item.get("business_type")), item.get("reason", "")) if isinstance(item, dict) else (None, "")
        if not isinstance(code, str) or code.strip() not in ontology.BUSINESS_TYPES:
            continue
        found.setdefault(code.strip(), clean_memo(reason if isinstance(reason, str) else "")[:200])
    return [{"code": c, "reason": found[c]} for c in ontology.BUSINESS_TYPES if c in found]


def load_solution_rows(db: Session, solution_ids: list[int]) -> dict[int, Solution]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    if not solution_ids:
        return {}
    return {s.id: s for s in db.scalars(select(Solution).where(Solution.id.in_(solution_ids)).options(selectinload(Solution.business_types)))}

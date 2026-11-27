"""사업기획의 행위 규칙 (2026-11-27, 설계 docs/superpowers/specs/2026-11-27-planning-bm-design.md).

- 운영 사업: 사업부서가 운영 중인 BM. 운영 현황은 이미 있는 프로젝트·영업기회로만 센다 (`op_summary`).
- 신규 사업: AI 안 만들기(`start_ideas` → 작업 `bm_ideas` → `clean_ideas`)와 "이 안 쓰기"(`apply_idea`), 사업화되면 운영 사업으로 옮긴다(`launch`).
AI 결과는 초안이다: 안은 `BM.ideas` 에만 두고, BM 칸은 사람이 "이 안 쓰기"를 눌러야 바뀐다."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import ontology
from app.models import BM, CustomerNeed, Opportunity, Project, Solution, SolutionField, User

MAX_NEEDS = 40
MAX_SOLUTIONS = 60
MAX_IDEAS = 3
MEMO_MAX = 2000
NO_CANDIDATES = "안에 쓸 솔루션이 없습니다. 보류가 아닌 솔루션이 있어야 합니다."
TOO_FEW_IDEAS = "BM 안을 만들지 못했습니다. 다시 만들어 보세요."
BAD_IDEA = "없는 안입니다. 화면을 새로 고친 뒤 다시 골라 주세요."
NOT_NEW = "신규 사업에서만 할 수 있습니다."
NOT_LAUNCH = "개발 단계가 사업화일 때만 운영 사업으로 옮길 수 있습니다."


def is_new(bm: BM) -> bool:
    return bm.kind == ontology.BM_KIND_NEW


def list_url(bm: BM) -> str:
    return "/bm-lab" if is_new(bm) else "/bms"


def crumbs(bm: BM | None, kind: str, *tail) -> list[tuple[str, str | None]]:
    """경로 줄: 사업기획 › 운영 사업|신규 사업 › …"""
    new = (bm is not None and is_new(bm)) or (bm is None and kind == ontology.BM_KIND_NEW)
    out = [(ontology.NAV_GROUP_LABELS["planning"], None), ((ontology.BM_KINDS["new"], "/bm-lab") if new else (ontology.BM_KINDS["existing"], "/bms"))]
    return out + [x for x in tail if x[0]]


# ---- 운영 사업: 운영 현황 ----

def op_summary(db: Session, bm: BM) -> dict:
    """따르는 프로젝트 수, 진행 중 영업기회 수·예상 금액 합, 수주한 영업기회 수."""
    projects = db.scalar(select(func.count()).select_from(Project).where(Project.bm_id == bm.id)) or 0
    open_q = select(func.count(), func.coalesce(func.sum(Opportunity.amount), 0)).where(
        Opportunity.bm_id == bm.id, Opportunity.stage.in_(ontology.OPPORTUNITY_OPEN_STAGES))
    open_n, open_amount = db.execute(open_q).one()
    won = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.bm_id == bm.id, Opportunity.stage == ontology.OPPORTUNITY_STAGE_WON)) or 0
    return {"projects": projects, "open": open_n, "open_amount": int(open_amount or 0), "won": won}


# ---- 신규 사업: AI 기획 재료 ----

def _ids(raw) -> list[int]:
    out = []
    for x in raw or []:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n not in out:
            out.append(n)
    return out


def clean_plan(db: Session, field_id, need_ids, solution_ids, business_types, memo: str) -> dict:
    """폼 값 → 저장할 기획 입력 (없는 번호는 버린다)."""
    fid = _ids([field_id])[0] if _ids([field_id]) else None
    if fid is not None and db.get(SolutionField, fid) is None:
        fid = None
    nids = [i for i in _ids(need_ids) if db.get(CustomerNeed, i) is not None]
    sids = [i for i in _ids(solution_ids) if db.get(Solution, i) is not None]
    bts = [c for c in ontology.BUSINESS_TYPES if c in set(business_types or [])]
    return {"field_id": fid, "need_ids": nids, "solution_ids": sids, "business_types": bts, "memo": (memo or "").strip()[:MEMO_MAX]}


def material_needs(db: Session, plan: dict) -> list[CustomerNeed]:
    """고른 니즈. 없으면 그 분야(분야가 없으면 전체)의 해결되지 않은 니즈 최근 것부터 최대 MAX_NEEDS 개."""
    if plan.get("need_ids"):
        rows = [db.get(CustomerNeed, i) for i in plan["need_ids"]]
        return [n for n in rows if n is not None][:MAX_NEEDS]
    stmt = select(CustomerNeed).where(CustomerNeed.status.not_in(("resolved",)))
    if plan.get("field_id"):
        stmt = stmt.where(CustomerNeed.field_id == plan["field_id"])
    return list(db.scalars(stmt.order_by(CustomerNeed.created_at.desc(), CustomerNeed.id.desc()).limit(MAX_NEEDS)))


def candidate_solutions(db: Session, plan: dict) -> list[Solution]:
    """고른 솔루션. 없으면 보류가 아닌 솔루션 (그 분야 것을 앞에), 최대 MAX_SOLUTIONS 개."""
    if plan.get("solution_ids"):
        rows = [db.get(Solution, i) for i in plan["solution_ids"]]
        return [s for s in rows if s is not None][:MAX_SOLUTIONS]
    rows = list(db.scalars(select(Solution).where(Solution.stage != ontology.STAGE_HOLD).order_by(Solution.name, Solution.id)))
    fid = plan.get("field_id")
    rows.sort(key=lambda s: (fid is None or s.field_id != fid,))
    return rows[:MAX_SOLUTIONS]


def safe(text: str) -> str:
    """프롬프트의 자료 울타리(<<<…>>>)를 글 안에서 닫거나 열지 못하게 한다 (다른 AI 작업의 `_safe` 와 같다)."""
    return (text or "").replace("<<<", "< < <").replace(">>>", "> > >")


def _one_line(text: str, limit: int = 200) -> str:
    return safe(" ".join((text or "").split())[:limit])


def prompt_values(db: Session, bm: BM) -> dict:
    plan = dict(bm.plan_input or {})
    field = db.get(SolutionField, plan["field_id"]) if plan.get("field_id") else None
    needs = material_needs(db, plan)
    sols = candidate_solutions(db, plan)
    need_lines = "\n".join(f"- [{n.id}] ({_one_line(n.customer_name, 40) or '고객 미상'}) {_one_line(n.content, 300)}" for n in needs) or "(없음)"
    sol_lines = "\n".join(
        f"- 번호 {s.id}: {_one_line(s.name, 80)} — {_one_line(s.summary, 160) or '(소개 없음)'}"
        f" (파트너 {_one_line(s.partner.name, 40) if s.partner else '없음'}; 가능한 사업방식 {', '.join(ontology.BUSINESS_TYPES[c] for c in s.business_type_codes) or '미정'})"
        for s in sols)
    hint = ""
    if plan.get("business_types"):
        hint = "사용자가 검토하려는 사업방식: " + ", ".join(f"`{c}`({ontology.BUSINESS_TYPES[c]})" for c in plan["business_types"]) + ". 이 방식들을 먼저 따져 보세요."
    others = [b.name for b in db.scalars(select(BM).where(BM.id != bm.id).order_by(BM.name))]
    return {
        "title": _one_line(bm.name, 120), "request_memo": safe(plan.get("memo") or "") or "(없음)",
        "field_line": f"이 기획의 분야: {field.name}" if field else "분야를 정하지 않았습니다.",
        "needs": need_lines, "solutions": sol_lines, "business_type_hint": hint,
        "existing_bms": "\n".join(f"- {_one_line(n, 80)}" for n in others) or "(없음)",
    }


def clean_ideas(db: Session, bm: BM, raw_ideas: list) -> list[dict]:
    """AI 안을 다듬는다: 후보 밖 솔루션·없는 니즈 번호·모르는 사업방식은 버리고, 솔루션이 하나도 남지 않은 안은 뺀다."""
    plan = dict(bm.plan_input or {})
    allowed = {s.id for s in candidate_solutions(db, plan)}
    need_ok = {n.id for n in material_needs(db, plan)}
    out: list[dict] = []
    for item in raw_ideas or []:
        if not isinstance(item, dict):
            continue
        sids = [i for i in _ids(item.get("solution_ids")) if i in allowed]
        name = _one_line(str(item.get("name") or ""), 80)
        if not sids or not name:
            continue
        bt = str(item.get("business_type") or "")
        out.append({
            "name": name, "summary": _one_line(str(item.get("summary") or ""), 200),
            "solution_ids": sids, "business_type": bt if bt in ontology.BUSINESS_TYPES else "",
            "target_customers": str(item.get("target_customers") or "").strip()[:1000],
            "revenue_model": str(item.get("revenue_model") or "").strip()[:1000],
            "need_ids": [i for i in _ids(item.get("need_ids")) if i in need_ok],
            "rationale": str(item.get("rationale") or "").strip()[:2000],
        })
        if len(out) >= MAX_IDEAS:
            break
    return out


def start_ideas(db: Session, bm: BM, user: User):
    """AI 안 만들기 작업을 넣는다 (이전 작업의 결과는 쓰지 않게 `active_job_id` 를 바꾼다)."""
    from app.models import AIJob

    job = AIJob(kind="bm_ideas", input={"bm_id": bm.id}, requester_id=user.id, priority=0, status="pending")
    db.add(job)
    db.flush()
    bm.active_job_id = job.id  # 작업과 같은 커밋에서 정한다 (아주 빨리 끝난 작업이 "지난 작업"으로 버려지지 않게)
    db.commit()
    return job


IDEA_CHANGED = "그 사이 AI 안이 바뀌었습니다. 화면을 새로 고친 뒤 다시 골라 주세요."
IDEAS_RUNNING = "새 AI 안을 만드는 중입니다. 끝난 뒤 다시 골라 주세요."


def idea_sig(idea: dict) -> str:
    """화면에 보인 안의 지문 (쓸 때 같은 안인지 확인한다). "쓴 안" 표시는 빼고 센다."""
    import hashlib
    import json

    body = {k: v for k, v in (idea or {}).items() if k != "used"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def apply_idea(db: Session, bm: BM, index: int, sig: str | None = None) -> str | None:
    """"이 안 쓰기": 한 줄 설명·대상 고객·수익 구조·대표 사업방식·솔루션을 그 안으로, 이름이 비었으면 이름도, 이유는 메모 끝에. 오류면 글.
    `sig` 는 화면에 보인 안의 지문: 그 사이 안이 바뀌었으면 쓰지 않는다. 새 안을 만드는 중이어도 쓰지 않는다."""
    from app.models import AIJob

    ideas = list(bm.ideas or [])
    if not is_new(bm):
        return NOT_NEW
    if not 0 <= index < len(ideas):
        return BAD_IDEA
    job = db.get(AIJob, bm.active_job_id) if bm.active_job_id else None
    if job is not None and job.status in ontology.JOB_ACTIVE:
        return IDEAS_RUNNING
    if sig is not None and sig != idea_sig(ideas[index]):
        return IDEA_CHANGED
    idea = ideas[index]
    bm.name = bm.name.strip() or idea["name"]
    bm.summary = idea.get("summary") or bm.summary
    bm.target_customers = idea.get("target_customers") or bm.target_customers
    bm.revenue_model = idea.get("revenue_model") or bm.revenue_model
    if idea.get("business_type") in ontology.BUSINESS_TYPES:
        bm.business_type = idea["business_type"]
    bm.solutions = [s for s in (db.get(Solution, i) for i in idea.get("solution_ids", [])) if s is not None]
    if idea.get("rationale"):
        note = f"[AI 안 \"{idea['name']}\"의 이유] {idea['rationale']}"
        bm.memo = f"{bm.memo.rstrip()}\n\n{note}".strip() if bm.memo else note
    bm.ideas = [{**x, "used": i == index} for i, x in enumerate(ideas)]  # 새 목록을 대입한다 (제자리 수정 금지)
    return None


def launch(bm: BM) -> str | None:
    """사업화된 신규 사업을 운영 사업으로 옮긴다 (운영 상태: 운영 중)."""
    if not is_new(bm):
        return NOT_NEW
    if bm.dev_stage != "launch":
        return NOT_LAUNCH
    bm.kind = ontology.BM_KIND_EXISTING
    bm.op_status = bm.op_status or "running"
    return None

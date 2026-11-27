"""영업 B 단계 규칙 한 곳: 단계별 확인 항목, AI 자동 분석 요청, 제안 다듬기·저장·반영.
라우터·템플릿·AI 처리기는 여기 함수만 부른다. 여기 함수는 (따로 적지 않으면) 커밋하지 않는다."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import ontology
from app.models import Opportunity, OpportunityCheck


# ---- 확인 항목 ----

def items_of(stage: str) -> tuple[tuple[str, str], ...]:
    return ontology.OPPORTUNITY_CHECKLIST.get(stage, ())


def checked_items(opp: Opportunity) -> set[str]:
    return {c.item for c in opp.checks}


def set_check(db: Session, opp: Opportunity, item: str, on: bool, user_id: int | None, source: str = "", evidence: str = "") -> bool:
    """확인 항목을 켜거나 끈다 (이력·고친 표시는 guards 가 남긴다). 바뀌었으면 True. 모르는 코드는 ValueError."""
    if item not in ontology.OPPORTUNITY_CHECK_ITEMS:
        raise ValueError(item)
    found = next((c for c in opp.checks if c.item == item), None)
    if on and found is None:
        opp.checks.append(OpportunityCheck(item=item, checked_by_id=user_id, source=source, evidence=evidence))
        return True
    if not on and found is not None:
        opp.checks.remove(found)
        db.delete(found)
        return True
    return False


def open_items(opp: Opportunity, stage: str) -> list[tuple[str, str]]:
    """그 단계에서 아직 확인하지 않은 항목 [(코드, 이름)]."""
    done = checked_items(opp)
    return [(c, n) for c, n in items_of(stage) if c not in done]


def stage_counts(opp: Opportunity) -> dict[str, tuple[int, int]]:
    """단계마다 (확인한 수, 전체 수)."""
    done = checked_items(opp)
    return {stage: (sum(1 for c, _ in items if c in done), len(items)) for stage, items in ontology.OPPORTUNITY_CHECKLIST.items()}


def forward_move(old: str, new: str) -> bool:
    """진행 중 흐름(발굴 → 제안 중 → 협상)에서 앞으로 넘기는가 (남은 확인 항목을 알릴 때)."""
    flow = ontology.OPPORTUNITY_OPEN_STAGES
    return old in flow and new in flow and flow.index(new) > flow.index(old)


def leftovers_notice(opp: Opportunity, old: str, new: str) -> str | None:
    """앞으로 넘겼고 떠난 단계에 남은 항목이 있으면 알림 코드 `opportunity_stage_open.<옛>.<새>.<개수>`, 아니면 None."""
    if not forward_move(old, new):
        return None
    n = len(open_items(opp, old))
    return f"opportunity_stage_open.{old}.{new}.{n}" if n else None


def check_view(opp: Opportunity) -> dict | None:
    """상세 화면 "확인 항목" 카드 값 (진행 중 단계만). 앞 단계의 남은 항목도 함께."""
    if opp.stage not in ontology.OPPORTUNITY_CHECKLIST:
        return None
    by_item = {c.item: c for c in opp.checks}
    rows = [{"code": c, "label": n, "row": by_item.get(c)} for c, n in items_of(opp.stage)]
    flow = ontology.OPPORTUNITY_OPEN_STAGES
    earlier = [{"stage": s, "name": ontology.OPPORTUNITY_STAGES[s], "items": open_items(opp, s)} for s in flow[:flow.index(opp.stage)]]
    return {"stage": opp.stage, "rows": rows, "done": sum(1 for r in rows if r["row"]), "total": len(rows),
            "earlier": [e for e in earlier if e["items"]]}


# ---- AI 자동 분석 요청 ----

REVIEW_KIND = "opportunity_review"
REVIEW_PRIORITY = 5  # 사람이 누른 작업(0)보다 뒤, 리서치(10)보다 앞


def has_activities(db: Session, opp: Opportunity) -> bool:
    return bool(opp.activities)


def pending_job(db: Session, opp_id: int, statuses=("pending", "running")):
    """이 영업기회의 대기·진행 중 분석 작업 (없으면 None). 자동 요청은 "대기" 만 본다 (진행 중인 분석은 새로 저장한 메모를 못 읽는다)."""
    from sqlalchemy import select

    from app.models import AIJob

    for job in db.scalars(select(AIJob).where(AIJob.kind == REVIEW_KIND, AIJob.status.in_(tuple(statuses))).order_by(AIJob.id)):
        if (job.input or {}).get("opportunity_id") == opp_id:
            return job
    return None


def failed_review(db: Session, opp_id: int, insight):
    """이 영업기회의 가장 최근 분석 작업이 실패했고 최신 분석보다 늦으면 그 작업 (브리핑 카드가 실패와 이유를 보인다, 베타 점검 2026-11-27). 아니면 None."""
    from sqlalchemy import select

    from app.models import AIJob

    last = None
    for job in db.scalars(select(AIJob).where(AIJob.kind == REVIEW_KIND).order_by(AIJob.id.desc()).limit(200)):
        if (job.input or {}).get("opportunity_id") == opp_id:
            last = job
            break
    if last is None or last.status != "failed":
        return None
    if insight is not None and last.finished_at is not None and insight.created_at is not None and insight.created_at >= last.finished_at:
        return None
    return last


def request_review(db: Session, opp_ids, user_id: int | None, request, priority: int = REVIEW_PRIORITY) -> list:
    """활동 기록 저장 뒤 부른다: 진행 중이고 연결된 활동 기록이 있는 영업기회마다 분석 작업을 하나씩 (이미 대기·진행 중이면 넣지 않는다).
    AI 처리기가 꺼져 있으면 아무것도 하지 않는다 (저장은 그대로 성공). 자동으로 AI 작업을 만드는 곳은 이 함수와 정렬 점검 `alignment.request` 둘이다 (docs/rules/AI-작업과-오류.md)."""
    from app.ai import gate
    from app.ai.queue import enqueue

    if gate.ai_is_off(request):
        return []
    made = []
    for oid in dict.fromkeys(opp_ids):
        opp = db.get(Opportunity, oid)
        if opp is None or opp.stage not in ontology.OPPORTUNITY_OPEN_STAGES or not has_activities(db, opp) or pending_job(db, oid, ("pending",)) is not None:
            continue
        made.append(enqueue(db, REVIEW_KIND, {"opportunity_id": oid}, user_id, priority))
    return made


# ---- AI 분석: 프롬프트 · 다듬기 · 저장 ----

ACT_OPEN, ACT_CLOSE = "<<<ACTIVITY", "ACTIVITY>>>"
MAX_ACTIVITIES, MAX_ACTIVITY_CHARS, MAX_TOTAL_CHARS = 10, 4000, 30000
MAX_EVIDENCE, MAX_BRIEF_TEXT, MAX_BRIEF_ITEMS = 300, 300, 5
MAX_NEXT_STEP = 200
MAX_AMOUNT = 10 ** 15  # 천조 원: SQLite 정수 범위 안에서 말이 되는 상한 (AI 가 터무니없는 수를 내면 버린다)
INSIGHTS_KEPT = 20  # 영업기회마다 남기는 분석 수


def today():
    from app.routers.home import today as _today

    return _today()


def _safe(text) -> str:
    """사용자 글이 구역 표시를 닫거나 열지 못하게 한다."""
    return str(text or "").replace(ACT_CLOSE, "ACTIVITY> >>").replace(ACT_OPEN, "<<< ACTIVITY")


def _norm(text: str) -> str:
    return "".join(str(text or "").split()).strip("\"'“”‘’")


def _activities(opp: Opportunity) -> list:
    return sorted(opp.activities, key=lambda a: (a.date is not None, a.date.toordinal() if a.date else 0, a.id), reverse=True)[:MAX_ACTIVITIES]


def build_prompt(db: Session, opp: Opportunity, today_) -> tuple[str, dict[int, str]]:
    """프롬프트와 {활동 번호: AI 에게 보인 글} (근거 확인에 쓴다). 활동 글은 최근 10건, 한 건 4,000자, 합계 30,000자."""
    from app import money
    from app.ai.tasks import load_prompt
    from app.opportunities import needs_of, open_follow_ups

    P = lambda k: ontology.prop("opportunity", k)  # noqa: E731
    lines = [f"- {P('name')}: {_safe(opp.name)}", f"- {P('customer')}: {_safe(opp.customer.name)}",
             f"- {P('stage')}: {ontology.OPPORTUNITY_STAGES[opp.stage]} ({opp.stage})",
             f"- {P('amount')}: {money.format_amount(opp.amount) or '없음'}" + (f" ({opp.amount}원)" if opp.amount else ""),
             f"- {P('expected_close')}: {opp.expected_close.isoformat() if opp.expected_close else '없음'}",
             f"- {P('next_step')}: {_safe(opp.next_step) or '없음'}" + (f" ({opp.next_step_due.isoformat()})" if opp.next_step_due else "")]
    if opp.contacts:  # 영업 D: 이름·직함·역할만 (전화·이메일은 보내지 않는다). 담당자 부담 줄이기 ⑤: 마지막 접촉 날수
        from app.opportunities import contact_last_touches

        touches = contact_last_touches(db, [c.id for c in opp.contacts])

        def _touch(c) -> str:
            last = touches.get(c.id)
            return f"마지막 접촉 {(today_ - last).days}일 전" if last else "참석 기록 없음"
        lines.append(f"- {P('contacts')}: " + " · ".join(_safe(" ".join(x for x in (c.name, c.title, ontology.CONTACT_ROLES.get(c.role, ""), f"({_touch(c)})") if x)) for c in opp.contacts))
    if opp.bm:
        lines.append(f"- {P('bm')}: {_safe(opp.bm.name)}")
    if opp.business_types:
        lines.append(f"- {P('business_types')}: {ontology.business_types_text(opp.business_types)}")
    if opp.memo:
        lines.append(f"- {P('memo')} (사용자 글, 자료): {_safe(opp.memo)[:1000]}")
    done = checked_items(opp)
    checklist = "\n".join(f"{code} | {name} | {'확인함' if code in done else '아직'}"
                          for stage in ontology.OPPORTUNITY_OPEN_STAGES for code, name in items_of(stage))
    needs = "\n".join(f"- {_safe(' '.join(n.content.split()))[:300]}" for n in needs_of(db, opp)) or "(없음)"
    fus = "\n".join(f"- {_safe(f.task)[:200]}" + (f" (기한 {f.due.isoformat()})" if f.due else "") for f in open_follow_ups(opp)[:20]) or "(없음)"
    texts: dict[int, str] = {}
    blocks, total = [], 0
    for a in _activities(opp):
        if total >= MAX_TOTAL_CHARS:
            break
        body = ((a.summary_md or "").strip() or (a.raw_text or "").strip())[:MAX_ACTIVITY_CHARS]
        body = body[:MAX_TOTAL_CHARS - total]
        if not body:  # 글이 빈 기록은 건너뛰고 더 오래된 기록을 읽는다
            continue
        total += len(body)
        texts[a.id] = body
        head = f"{ACT_OPEN} id={a.id} 일자={a.date.isoformat() if a.date else '모름'} 제목={_safe(a.title)[:100]}"
        blocks.append(f"{head}\n{_safe(body)}\n{ACT_CLOSE}")
    prompt = load_prompt("opportunity_review", today=today_.isoformat(), opportunity="\n".join(lines), checklist=checklist, needs=needs,
                         follow_ups=fus, activities="\n\n".join(blocks),
                         stage_codes=", ".join(f"`{c}`({ontology.OPPORTUNITY_STAGES[c]})" for c in ontology.OPPORTUNITY_OPEN_STAGES))
    return prompt, texts


def basis_of(opp: Opportunity, texts: dict[int, str]) -> dict:
    """분석할 때의 값 사본 (반영할 때 그 칸이 그 사이 바뀌었는지 본다)."""
    return {"activity_ids": list(texts), "amount": opp.amount, "expected_close": opp.expected_close.isoformat() if opp.expected_close else None,
            "next_step": opp.next_step or "", "next_step_due": opp.next_step_due.isoformat() if opp.next_step_due else None, "stage": opp.stage}


def _iso(value) -> str | None:
    from datetime import date

    try:
        return date.fromisoformat(str(value).strip()).isoformat() if value else None
    except ValueError:
        return None


def _amount(value) -> int | None:
    from app import money

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        found = int(value) if value > 0 and float(value).is_integer() else None
    else:
        try:
            found = money.parse_amount(str(value))
        except ValueError:
            found = None
    return found if found is not None and found <= MAX_AMOUNT else None


def clean_suggestions(out, opp: Opportunity, texts: dict[int, str], today_) -> list[dict]:
    """AI 결과를 제안 목록으로: 지금 값과 같은 것·모르는 항목·이미 확인한 항목·진행 중이 아닌 단계·읽지 않은 활동 번호는 버린다.
    근거는 300자, 그 활동 글(공백 무시)에 들어 있으면 verified."""
    found: list[dict] = []

    def add(kind: str, sug, **values) -> None:
        aid = sug.activity_id
        if aid is not None and aid not in texts:
            return
        evidence = " ".join(str(sug.evidence or "").split())[:MAX_EVIDENCE]
        verified = bool(evidence) and aid is not None and _norm(evidence) in _norm(texts[aid])
        found.append({"id": len(found) + 1, "kind": kind, "field": None, "item": None, "value": None, "due": None, **values,
                      "evidence": evidence, "activity_id": aid, "verified": verified, "state": "pending", "decided_by": None, "decided_at": None})

    f = out.fields or {}
    if (s := f.get("amount")) is not None and (v := _amount(s.value)) is not None and v != opp.amount:
        add("field", s, field="amount", value=v)
    if (s := f.get("expected_close")) is not None and (v := _iso(s.value)) and v != (opp.expected_close.isoformat() if opp.expected_close else None):
        add("field", s, field="expected_close", value=v)
    if (s := f.get("next_step")) is not None:
        text = " ".join(str(s.value or "").split())[:MAX_NEXT_STEP]
        due = _iso(s.due)
        current_due = opp.next_step_due.isoformat() if opp.next_step_due else None
        if text and not (text == (opp.next_step or "") and (due is None or due == current_due)):
            add("field", s, field="next_step", value=text, due=due)
    if out.stage is not None and out.stage.value in ontology.OPPORTUNITY_OPEN_STAGES and out.stage.value != opp.stage:
        add("stage", out.stage, value=out.stage.value)
    done, seen = checked_items(opp), set()
    for c in out.checks or []:
        if c.item in ontology.OPPORTUNITY_CHECK_ITEMS and c.item not in done and c.item not in seen:
            seen.add(c.item)
            add("check", c, item=c.item)
    return found


def clean_briefing(out) -> dict:
    b = out.briefing

    def one(t) -> str:
        return " ".join(str(t or "").split())[:MAX_BRIEF_TEXT]

    return {"situation": one(b.situation), "risks": [one(x) for x in b.risks if one(x)][:MAX_BRIEF_ITEMS],
            "next_actions": [one(x) for x in b.next_actions if one(x)][:MAX_BRIEF_ITEMS], "outlook": one(b.outlook)}


def latest_insight(db: Session, opp_id: int):
    from sqlalchemy import select

    from app.models import OpportunityInsight

    return db.scalars(select(OpportunityInsight).where(OpportunityInsight.opportunity_id == opp_id).order_by(OpportunityInsight.id.desc())).first()


def save_insight(db: Session, opp: Opportunity, job_id, briefing: dict, suggestions: list, basis: dict):
    """새 분석을 저장한다: 옛 분석의 반영 대기 제안은 "새 분석으로 바뀜" 으로 닫고, 영업기회마다 20개만 남긴다."""
    from sqlalchemy import select

    from app.models import OpportunityInsight

    old = list(db.scalars(select(OpportunityInsight).where(OpportunityInsight.opportunity_id == opp.id).order_by(OpportunityInsight.id)))
    for ins in old:
        if any(s.get("state") == "pending" for s in ins.suggestions or []):
            ins.suggestions = [{**s, "state": "superseded"} if s.get("state") == "pending" else s for s in ins.suggestions]
    new = OpportunityInsight(opportunity_id=opp.id, job_id=job_id, briefing=briefing, suggestions=suggestions, basis=basis)
    db.add(new)
    for ins in old[:max(0, len(old) + 1 - INSIGHTS_KEPT)]:
        db.delete(ins)
    db.flush()
    return new


# ---- 제안 반영·무시 ----

def _changed_since(opp: Opportunity, sug: dict, basis: dict) -> bool:
    """제안한 칸이 분석 뒤 바뀌었는가 (그 칸만 본다)."""
    if sug["kind"] == "stage":
        return basis.get("stage") not in (None, opp.stage)
    field = sug.get("field")
    if field == "amount":
        return "amount" in basis and basis["amount"] != opp.amount
    if field in ("expected_close", "next_step"):
        now = {"expected_close": opp.expected_close.isoformat() if opp.expected_close else None, "next_step": opp.next_step or ""}[field]
        return field in basis and basis[field] != now
    return False


def decide(db: Session, opp: Opportunity, insight_id: int, sid: int, user_id: int | None, apply: bool) -> str:
    """제안 하나를 반영하거나 무시한다. 알림 코드를 돌려준다 (커밋은 호출자).
    쓰기 잠금을 먼저 잡고 다시 읽어, 두 번 눌러도 한 번만 반영된다. 최신 분석의 반영 대기 제안이 아니면 `ai_stale`."""
    from datetime import date, datetime

    from sqlalchemy import update

    from app.models import OpportunityInsight
    from app.opportunities import apply_stage

    if opp.stage not in ontology.OPPORTUNITY_OPEN_STAGES:  # 닫힌(수주·실패·보류) 건의 남은 제안은 반영하지 않는다 (사유 화면을 우회하지 않게)
        return "ai_stale"
    db.execute(update(OpportunityInsight).where(OpportunityInsight.id == insight_id).values(job_id=OpportunityInsight.job_id))  # 쓰기 잠금
    ins = db.get(OpportunityInsight, insight_id)
    if ins is not None:
        db.refresh(ins)
    latest = latest_insight(db, opp.id)
    if ins is None or ins.opportunity_id != opp.id or latest is None or latest.id != ins.id:
        return "ai_stale"
    sug = next((s for s in ins.suggestions or [] if s.get("id") == sid), None)
    if sug is None or sug.get("state") != "pending":
        return "ai_stale"
    code = "ai_dismissed"
    if apply:
        code = "ai_applied_changed" if _changed_since(opp, sug, ins.basis or {}) else "ai_applied"
        db.info["change_source"], db.info["change_evidence"] = "ai", sug.get("evidence", "")
        kind, field = sug["kind"], sug.get("field")
        if kind == "field" and field == "amount":
            opp.amount = int(sug["value"])
        elif kind == "field" and field == "expected_close":
            opp.expected_close = date.fromisoformat(sug["value"])
        elif kind == "field" and field == "next_step":
            opp.next_step = sug["value"]
            opp.next_step_due = date.fromisoformat(sug["due"]) if sug.get("due") else None  # 새 할 일에 옛 기한이 남지 않게
        elif kind == "stage":
            old = opp.stage
            if sug["value"] in ontology.OPPORTUNITY_OPEN_STAGES and old != sug["value"]:
                left = leftovers_notice(opp, old, sug["value"])
                apply_stage(opp, sug["value"])
                if left and code == "ai_applied":
                    code = "ai_stage_open." + left.split(".", 1)[1]
        elif kind == "check":
            set_check(db, opp, sug["item"], True, user_id, source="ai", evidence=sug.get("evidence", ""))
    state = "applied" if apply else "dismissed"
    ins.suggestions = [{**s, "state": state, "decided_by": user_id, "decided_at": datetime.now().isoformat(timespec="seconds")} if s.get("id") == sid else s
                       for s in ins.suggestions]
    return code


# ---- 화면 ----

def suggestion_rows(db: Session, opp: Opportunity, insight, today_) -> list[dict]:
    """제안 띠의 줄들 (반영 대기만): {id, label, old, new, evidence, verified, activity_id, activity_title}."""
    from app import money
    from app.models import Activity
    from app.ui import short_date

    if insight is None or opp.stage not in ontology.OPPORTUNITY_OPEN_STAGES:
        return []
    pending = [s for s in insight.suggestions or [] if s.get("state") == "pending"]
    ids = {s["activity_id"] for s in pending if s.get("activity_id")}
    titles = {a.id: (a.title or "활동 기록", a.date) for a in db.query(Activity).filter(Activity.id.in_(ids))} if ids else {}

    def day(iso):
        from datetime import date

        return short_date(date.fromisoformat(iso), today_) if iso else ""

    rows = []
    for s in pending:
        kind, field = s["kind"], s.get("field")
        if kind == "stage":
            label, old, new = ontology.prop("opportunity", "stage"), ontology.OPPORTUNITY_STAGES[opp.stage], ontology.OPPORTUNITY_STAGES.get(s["value"], s["value"])
        elif kind == "check":
            label, old, new = ontology.prop("opportunity", "checks"), "", ontology.check_label(s["item"])
        elif field == "amount":
            label, old, new = ontology.prop("opportunity", "amount"), money.format_amount(opp.amount), money.format_amount(s["value"])
        elif field == "expected_close":
            label, old, new = ontology.prop("opportunity", "expected_close"), short_date(opp.expected_close, today_), day(s["value"])
        else:
            label, old = ontology.prop("opportunity", "next_step"), opp.next_step or ""
            new = s["value"] + (f" ({day(s['due'])})" if s.get("due") else "")
        title, when = titles.get(s.get("activity_id"), ("", None))
        rows.append({"id": s["id"], "label": label, "old": old, "new": new, "evidence": s.get("evidence", ""), "verified": s.get("verified"),
                     "activity_id": s.get("activity_id") if title else None, "activity_title": title,
                     "activity_date": short_date(when, today_) if when else ""})
    return rows


def pending_counts(db: Session, opp_ids) -> dict[int, int]:
    """영업기회마다 최신 분석의 반영 대기 제안 수 (0 이면 빠진다). 질의 하나 (영업기회마다 가장 큰 번호의 분석)."""
    from sqlalchemy import func, select

    from app.models import OpportunityInsight

    ids = list(dict.fromkeys(opp_ids))
    if not ids:
        return {}
    newest = select(func.max(OpportunityInsight.id)).where(OpportunityInsight.opportunity_id.in_(ids)).group_by(OpportunityInsight.opportunity_id)
    out = {}
    for oid, sugs in db.execute(select(OpportunityInsight.opportunity_id, OpportunityInsight.suggestions).where(OpportunityInsight.id.in_(newest))):
        n = sum(1 for s in sugs or [] if s.get("state") == "pending")
        if n:
            out[oid] = n
    return out

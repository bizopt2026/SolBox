"""같은 회사 기록 합치기 (베타 AE, 2026-11-24): 고객 둘 또는 파트너 둘을 하나로. 관리자만 한다.

흐름: `preview` (아무것도 바꾸지 않는다: 무엇이 옮겨지고 어떤 글이 붙는지, 막히는 까닭) → `execute` (백업을 먼저 만들고, 한 트랜잭션에서 모두 옮기고
없어지는 기록을 지운다). `execute` 는 커밋하지 않는다: 라우터가 사라지는 기록의 사본을 지우고 커밋한 뒤 남는 쪽과 이어진 사본을 다시 쓴다.

옮기는 연결 (온톨로지 관계 번호):
- 파트너: 솔루션의 파트너 칸 R1, 활동 기록 R7(겹치면 하나로), 같은 회사 고객 R25, 올린 파일의 대상 표시 R21.
- 고객: 프로젝트 R2, 영업기회 R27, 제안 건의 고객 R12, 다수 고객 제안에 붙인 고객 R34(겹치면 하나로), 활동 기록 R5(겹치면 하나로),
  고객 니즈 R10, 같은 회사 파트너 R25, 올린 파일의 대상 표시 R21.
- 옮기지 않고 없어지는 것: 사라지는 기록의 유래(R22, 한 기록에 유래 하나)와 만든 사람(R24). 이름과 다른 이름은 남는 기록의 다른 이름이 된다.
- 같은 회사 연결이 양쪽에 모두 다른 기록으로 있으면 어느 쪽을 버릴지 짐작하지 않고 막는다.
- 제안 건 방향·구성안 안의 사본 경로 글(R17)과 아직 확인하지 않은 올리기 정리안의 번호는 고치지 않는다 (기록을 지울 때와 같다: 저장할 때 다시 확인한다)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app import aliases, backup, same_company
from app.models import (
    Activity, Customer, CustomerNeed, DuplicateDismissal, IntakeFile, Opportunity, Partner, Project, Proposal, ReportPlanItem, Solution, User,
    activity_customers, activity_partners, proposal_customers,
)

log = logging.getLogger("app.merge")

KINDS = {"partner": Partner, "customer": Customer}
PLURAL = {"partner": "partners", "customer": "customers"}
KIND_LABEL = {"partner": "파트너", "customer": "고객"}
NOT_FOUND = "합칠 기록 중 하나가 이미 없어요. 합쳐졌거나 지워졌는지 목록에서 확인해 주세요."
BACKUP_FAILED = "백업을 만들지 못해 합치지 않았어요 (아무것도 바꾸지 않았어요): "
TEXT_FIELDS = {"partner": ("contact", "description", "memo"), "customer": ("contact", "memo")}
FIELD_LABEL = {"contact": "연락처", "description": "소개", "memo": "메모", "industry": "업종"}


class MergeStopped(Exception):
    """합칠 수 없다 (문구는 화면에 그대로 보인다)."""


@dataclass
class Preview:
    kind: str
    keep: object
    absorb: object
    counts: list[tuple[str, str, int, str]] = field(default_factory=list)  # (보이는 이름, 관계 번호, 건수, 보태는 글)
    texts: list[tuple[str, str]] = field(default_factory=list)  # (칸 이름, 붙는 글 요약)
    new_aliases: list[str] = field(default_factory=list)
    conflict: str = ""
    total_keep: int = 0
    total_absorb: int = 0


def model_of(kind: str):
    return KINDS[kind]


def link_counts(db: Session, kind: str, obj) -> list[tuple[str, str, int]]:
    """기록 하나에 이어진 것의 (이름, 관계 번호, 건수) 목록 (옮겨지는 연결 전부)."""
    i = obj.id

    def n(stmt) -> int:
        return db.scalar(stmt) or 0

    def cnt(model, *where) -> int:
        return n(select(func.count()).select_from(model).where(*where))

    files = n(select(func.count()).select_from(IntakeFile).where(IntakeFile.target_type == kind, IntakeFile.target_id == i))
    if kind == "partner":
        return [
            ("솔루션 (파트너 칸)", "R1", cnt(Solution, Solution.partner_id == i)),
            ("활동 기록", "R7", n(select(func.count()).select_from(activity_partners).where(activity_partners.c.partner_id == i))),
            ("같은 회사 고객", "R25", 1 if obj.customer_id is not None else 0),
            ("올린 파일의 대상 표시", "R21", files),
        ]
    return [
        ("프로젝트", "R2", cnt(Project, Project.customer_id == i)),
        ("영업기회", "R27", cnt(Opportunity, Opportunity.customer_id == i)),
        ("제안 건", "R12", cnt(Proposal, Proposal.customer_id == i)),
        ("다수 고객 제안에 붙인 고객", "R34", n(select(func.count()).select_from(proposal_customers).where(proposal_customers.c.customer_id == i))),
        ("활동 기록", "R5", n(select(func.count()).select_from(activity_customers).where(activity_customers.c.customer_id == i))),
        ("고객 니즈", "R10", cnt(CustomerNeed, CustomerNeed.customer_id == i)),
        ("같은 회사 파트너", "R25", cnt(Partner, Partner.customer_id == i)),
        ("올린 파일의 대상 표시", "R21", files),
    ]


def total_links(db: Session, kind: str, obj) -> int:
    return sum(c for _l, _r, c in link_counts(db, kind, obj))


def default_keep(db: Session, kind: str, a, b):
    """연결이 더 많은 쪽을 남긴다. 같으면 번호가 작은(먼저 만든) 쪽."""
    ta, tb = total_links(db, kind, a), total_links(db, kind, b)
    if ta != tb:
        return a if ta > tb else b
    return a if a.id < b.id else b


def _overlap(db: Session, kind: str, keep, absorb) -> dict[str, int]:
    """겹쳐서 하나로 합쳐질 연결 건수."""
    out = {}
    if kind == "partner":
        t, col = activity_partners, activity_partners.c.partner_id
        out["R7"] = db.scalar(select(func.count()).select_from(t).where(
            col == absorb.id, t.c.activity_id.in_(select(t.c.activity_id).where(col == keep.id)))) or 0
    else:
        for rel, t, col, key in (("R5", activity_customers, activity_customers.c.customer_id, "activity_id"),
                                 ("R34", proposal_customers, proposal_customers.c.customer_id, "proposal_id")):
            out[rel] = db.scalar(select(func.count()).select_from(t).where(
                col == absorb.id, t.c[key].in_(select(t.c[key]).where(col == keep.id)))) or 0
    return out


def conflict_of(db: Session, kind: str, keep, absorb) -> str:
    """같은 회사 연결이 막는 까닭 (없으면 빈 글)."""
    if kind == "partner":
        a, b = keep.customer_id, absorb.customer_id
        if a is not None and b is not None and a != b:
            ca, cb = db.get(Customer, a), db.get(Customer, b)
            return (f"두 파트너가 서로 다른 고객과 '같은 회사' 로 이어져 있어요: '{keep.name}' ↔ 고객 '{ca.name if ca else a}', "
                    f"'{absorb.name}' ↔ 고객 '{cb.name if cb else b}'. 어느 쪽을 남길지 짐작하지 않아요. "
                    "먼저 두 고객이 같은 회사라면 고객끼리 합치거나, 파트너 수정 화면에서 한쪽 연결을 풀어 주세요.")
    else:
        pk, pa = same_company.partner_of(db, keep), same_company.partner_of(db, absorb)
        if pk is not None and pa is not None:
            return (f"두 고객이 서로 다른 파트너와 '같은 회사' 로 이어져 있어요: '{keep.name}' ↔ 파트너 '{pk.name}', "
                    f"'{absorb.name}' ↔ 파트너 '{pa.name}'. 어느 쪽을 남길지 짐작하지 않아요. "
                    "먼저 두 파트너가 같은 회사라면 파트너끼리 합치거나, 고객 수정 화면에서 한쪽 연결을 풀어 주세요.")
    return ""


def _stamp(absorb, today: date) -> str:
    return f"--- {absorb.name}에서 옮김 ({today.isoformat()}) ---"


def merged_text(keep_text: str, absorb_text: str, absorb, today: date) -> str:
    """남는 쪽 글 뒤에 구분선과 사라지는 쪽 글을 붙인다. 남는 쪽이 비었으면 그대로 옮기고, 글이 같으면 붙이지 않는다."""
    k, a = (keep_text or "").strip(), (absorb_text or "").strip()
    if not a or a == k:
        return keep_text or ""
    return a if not k else f"{k}\n\n{_stamp(absorb, today)}\n{a}"


def planned_texts(kind: str, keep, absorb, today: date) -> list[tuple[str, str]]:
    """(칸, 붙는 글 요약). 업종은 남는 쪽이 비었을 때만 옮기고, 둘 다 있고 다르면 메모 끝 옮김 글에 한 줄로 적는다."""
    out = []
    for f in TEXT_FIELDS[kind]:
        a = (getattr(absorb, f) or "").strip()
        if a and a != (getattr(keep, f) or "").strip():
            out.append((FIELD_LABEL[f], a))
    if kind == "customer":
        ai, ki = (absorb.industry or "").strip(), (keep.industry or "").strip()
        if ai and not ki:
            out.append(("업종", ai + " (남는 고객의 업종이 비어 있어 옮겨요)"))
        elif ai and ai != ki:
            out.append(("업종", f"{ai} (남는 고객의 업종 '{ki}' 이 이미 있어 메모에 한 줄로 남겨요)"))
    return out


def planned_aliases(keep, absorb) -> list[str]:
    """남는 기록의 다른 이름에 새로 들어갈 이름들: 사라지는 이름 + 그 다른 이름 (이미 있거나 남는 이름과 같으면 뺀다)."""
    probe = type("P", (), {"name": keep.name, "aliases": keep.aliases or ""})()
    added = []
    for n in (absorb.name, *aliases.of(absorb)):
        if aliases.add_alias(probe, n):
            added.append(" ".join(n.split()))
    return added


def preview(db: Session, kind: str, keep, absorb, today: date | None = None) -> Preview:
    today = today or date.today()
    pv = Preview(kind=kind, keep=keep, absorb=absorb)
    over = _overlap(db, kind, keep, absorb)
    for (label, rel, n), (_l, _r, k) in zip(link_counts(db, kind, absorb), link_counts(db, kind, keep)):
        note = f"그중 {over[rel]}건은 이미 남는 쪽에도 있어 하나로 합쳐져요" if over.get(rel) else ""
        pv.counts.append((label, rel, n, note))
        pv.total_absorb += n
        pv.total_keep += k
    pv.texts = planned_texts(kind, keep, absorb, today)
    pv.new_aliases = planned_aliases(keep, absorb)
    pv.conflict = conflict_of(db, kind, keep, absorb)
    return pv


def _moved_summary(counts: list[tuple[str, str, int, str]]) -> str:
    return ", ".join(f"{label} {n}" for label, _rel, n, _note in counts if n) or "옮긴 연결 없음"


def audit_line(user: User, keep, absorb, summary: str, now: datetime) -> str:
    who = (user.name or user.username).strip()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    return (f"[합치기 {stamp}] {who}: '{absorb.name}'(번호 {absorb.id}) 를 '{keep.name}'(번호 {keep.id}) 에 합침 · 옮긴 것: {summary}")


def take_backup(settings) -> str:
    """합치기 전 백업. 같은 초에 이미 백업이 있으면(`FileExistsError`) 그것으로 갈음한다. 실패하면 `MergeStopped`."""
    try:
        return str(backup.run_backup(settings))
    except FileExistsError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001  (어떤 실패든 합치지 않는다)
        log.exception("합치기 전 백업 실패")
        raise MergeStopped(BACKUP_FAILED + str(e)) from e


def execute(db: Session, kind: str, keep_id: int, absorb_id: int, user: User, settings, now: datetime | None = None) -> dict:
    """백업 → 한 트랜잭션으로 합치기 → 커밋. `{"keep", "absorbed_name", "counts", "backup", "summary"}`.
    그 사이 한쪽이 없어졌으면 `LookupError`, 합칠 수 없으면 `MergeStopped` (둘 다 아무것도 바꾸지 않았다)."""
    model = KINDS[kind]
    if keep_id == absorb_id:
        raise MergeStopped("같은 기록끼리는 합칠 수 없어요.")
    now = now or datetime.now()
    today = now.date()
    first_keep, first_absorb = db.get(model, keep_id), db.get(model, absorb_id)
    if first_keep is None or first_absorb is None:
        raise LookupError(NOT_FOUND)
    if problem := conflict_of(db, kind, first_keep, first_absorb):  # 막힐 일이면 백업도 만들지 않는다 (잠금 안에서 다시 본다)
        raise MergeStopped(problem)
    backup_path = take_backup(settings)
    db.rollback()
    # 쓰기 잠금을 먼저 잡은 뒤 읽는다: 그 사이 다른 쪽이 지우거나 고쳤어도 잠금 뒤의 값으로 합친다
    db.execute(update(model).where(model.id.in_([keep_id, absorb_id])).values(id=model.id))
    keep, absorb = db.get(model, keep_id), db.get(model, absorb_id)
    if keep is None or absorb is None:
        db.rollback()
        raise LookupError(NOT_FOUND)
    db.refresh(keep)
    db.refresh(absorb)
    if problem := conflict_of(db, kind, keep, absorb):
        db.rollback()
        raise MergeStopped(problem)
    pv = preview(db, kind, keep, absorb, today)
    summary = _moved_summary(pv.counts)
    absorbed_name, absorbed_id = absorb.name, absorb.id
    try:
        _move(db, kind, keep, absorb)
        _merge_texts(kind, keep, absorb, today)
        for n in (absorb.name, *aliases.of(absorb)):
            aliases.add_alias(keep, n)
        line = audit_line(user, keep, absorb, summary, now)
        keep.memo = f"{keep.memo.rstrip()}\n\n{line}" if (keep.memo or "").strip() else line
        db.execute(delete(DuplicateDismissal).where(
            DuplicateDismissal.kind == kind, (DuplicateDismissal.low_id == absorb_id) | (DuplicateDismissal.high_id == absorb_id)))
        db.flush()
        db.delete(absorb)
        db.flush()
    except Exception:
        db.rollback()
        raise
    return {"keep": keep, "absorbed": absorb, "absorbed_id": absorbed_id, "absorbed_name": absorbed_name, "counts": pv.counts,
            "backup": backup_path, "summary": summary, "audit": line}


def log_done(user: User, kind: str, result: dict) -> None:
    keep = result["keep"]
    log.info("합치기 완료: %s '%s'(번호 %s) ← '%s'(번호 %s) · 사용자 %s · 옮긴 것: %s · 백업 %s", KIND_LABEL[kind], keep.name, keep.id,
             result["absorbed_name"], result["absorbed_id"], user.username, result["summary"], result["backup"])


def _merge_texts(kind: str, keep, absorb, today: date) -> None:
    for f in TEXT_FIELDS[kind]:
        setattr(keep, f, merged_text(getattr(keep, f), getattr(absorb, f), absorb, today))
    if kind == "customer":
        ai, ki = (absorb.industry or "").strip(), (keep.industry or "").strip()
        if ai and not ki:
            keep.industry = ai
        elif ai and ai != ki:  # 남는 쪽 업종은 그대로 두고, 사라지는 쪽 업종은 잃지 않게 메모에 적는다
            keep.memo = merged_text(keep.memo, f"업종: {ai}", absorb, today)


def _move(db: Session, kind: str, keep, absorb) -> None:
    """모든 연결을 남는 기록으로 옮긴다 (관계를 거쳐 바꿔 양쪽 컬렉션과 고침 표시가 맞게). 겹치는 연결 줄은 하나로."""
    if kind == "partner":
        for s in list(absorb.solutions):
            s.partner = keep
        for a in list(db.scalars(select(Activity).where(Activity.partners.any(id=absorb.id)))):
            rest = [p for p in a.partners if p.id != absorb.id]
            a.partners = rest if keep in rest else [*rest, keep]
        if absorb.customer_id is not None and keep.customer_id is None:
            moved = absorb.customer_id
            absorb.customer_id = None
            db.flush()  # 유일 색인: 먼저 풀고 나서 잇는다
            keep.customer_id = moved
        elif absorb.customer_id is not None:
            absorb.customer_id = None  # 같은 고객과의 연결 (둘이 같은 고객일 수는 없으나 방어)
    else:
        for p in list(absorb.projects):
            p.customer = keep
        for o in list(absorb.opportunities):
            o.customer = keep
        for p in list(absorb.proposals):
            p.customer = keep
        for p in list(absorb.group_proposals):
            rest = [c for c in p.group_customers if c.id != absorb.id]
            p.group_customers = rest if keep in rest else [*rest, keep]
        for a in list(db.scalars(select(Activity).where(Activity.customers.any(id=absorb.id)))):
            rest = [c for c in a.customers if c.id != absorb.id]
            a.customers = rest if keep in rest else [*rest, keep]
        for need in list(db.scalars(select(CustomerNeed).where(CustomerNeed.customer_id == absorb.id))):
            need.customer = keep  # 쓴 이름(`customer_name`)은 그대로, 묶인 영업기회도 그대로 (영업기회도 같이 옮겨 같은 고객이다)
        for item in list(db.scalars(select(ReportPlanItem).where(ReportPlanItem.customer_id == absorb.id))):
            item.customer = keep  # 업무 보고 계획 항목의 고객 연결 (항목 글은 그대로)
        partner = same_company.partner_of(db, absorb)
        if partner is not None:
            partner.customer = keep
    db.execute(update(IntakeFile).where(IntakeFile.target_type == kind, IntakeFile.target_id == absorb.id).values(target_id=keep.id))
    db.flush()

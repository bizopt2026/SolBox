"""영업기회·BM 의 행위 조건을 한 곳에 둔다. 라우터와 템플릿은 여기 함수만 부른다 (화면 안내 글은 `ontology.OPPORTUNITY_RULES`).

- 니즈 묶기: 영업기회의 고객에 연결된 니즈만, 이미 다른 영업기회에 묶이지 않은 것만 (`bundlable_needs`, `apply_needs`). 해제는 선택을 풀면 된다.
- 단계 변경: 목록 안의 값이면 어디로든 (순서 강제 없음).
- 프로젝트로 전환: 수주(won)이고 `project_id` 가 비었을 때만 한 번 (`convert_problem`). 같은 고객의 기존 프로젝트를 이을 수도 있다 (`link_problem`).
- 지우기: 영업기회는 니즈 묶음·제안 건 연결을 비우고(프로젝트는 그대로), BM 은 따르는 프로젝트·영업기회가 있으면 막는다.

여기 함수는 커밋하지 않는다 (호출자가 커밋하고, 커밋 뒤에 `dependents` 로 지식 사본을 다시 쓴다)."""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import ontology
from app.ai.tasks import normalize_name
from app.models import BM, Activity, Customer, CustomerNeed, Opportunity, Project, Proposal, User

L_OPP, L_BM, L_PROJECT, L_NEED = (ontology.label("opportunity"), ontology.label("bm"), ontology.label("project"), ontology.term("customer_need"))

NAME_REQUIRED = "이름을 입력하세요."
CUSTOMER_REQUIRED = "고객을 선택하세요."
BAD_STAGE = "알 수 없는 단계입니다."
BAD_BM = f"존재하지 않는 {L_BM}입니다."
NEED_NOT_BUNDLABLE = f"묶을 수 없는 {L_NEED}가 있습니다. 이 고객에 연결된 {L_NEED} 중 다른 {L_OPP}에 묶이지 않은 것만 고를 수 있습니다."
CHANGE_HAS_NEEDS = f"묶은 {L_NEED}가 있으면 고객을 바꿀 수 없습니다. {L_NEED} 선택을 먼저 풀어 주세요."
CHANGE_HAS_LINKS = f"{L_PROJECT}나 제안 건이 이어진 {L_OPP}는 고객을 바꿀 수 없습니다."
NOT_WON = f"{L_PROJECT}로 전환은 수주 단계에서만 할 수 있습니다."
NO_PROJECT = f"존재하지 않는 {L_PROJECT}입니다."
PROJECT_OTHER_CUSTOMER = f"선택한 고객의 {L_PROJECT}가 아닙니다."
PROJECT_TAKEN = f"이미 다른 {L_OPP}와 이어진 {L_PROJECT}입니다."
PROJECT_HAS_OPPORTUNITY = f"이 {L_PROJECT}는 {L_OPP}와 이어져 있어 고객을 바꿀 수 없습니다."
HAS_OPPORTUNITIES = f"{L_OPP}가 있는 고객은 삭제할 수 없습니다. {L_OPP}를 먼저 삭제해 주세요."
BM_IN_USE = f"이 {L_BM}을 따르는 {L_PROJECT}나 {L_OPP}가 있어 삭제할 수 없습니다. {L_PROJECT}·{L_OPP}의 {L_BM}을 먼저 바꾸거나 비워 주세요."
OPPORTUNITY_MISMATCH = f"선택한 고객의 {L_OPP}가 아닙니다."
NO_OPPORTUNITY = f"존재하지 않는 {L_OPP}입니다."


def parse_id(raw) -> int | None:
    raw = str(raw if raw is not None else "").strip()
    return int(raw) if raw.isascii() and raw.isdecimal() and len(raw) <= 18 else None


def parse_ids(raw: Iterable) -> list[int] | None:
    """체크 상자로 온 번호들(중복 없이, 순서 유지). 숫자가 아닌 값이 있으면 None."""
    ids: list[int] = []
    for r in raw:
        n = parse_id(r)
        if n is None:
            return None
        if n not in ids:
            ids.append(n)
    return ids


# ---- 니즈 묶기 ----

def bundlable_needs(db: Session, customer_id: int | None, opp: Opportunity | None = None) -> list[CustomerNeed]:
    """`customer_id` 고객에 연결된 니즈 중 어느 영업기회에도 묶이지 않은 것 (`opp` 가 있으면 그 영업기회에 이미 묶인 것도). 오래된 활동 기록부터."""
    if customer_id is None:
        return []
    free = CustomerNeed.opportunity_id.is_(None)
    if opp is not None and opp.id is not None:
        free = free | (CustomerNeed.opportunity_id == opp.id)
    stmt = (select(CustomerNeed).join(Activity, CustomerNeed.activity_id == Activity.id)
            .where(CustomerNeed.customer_id == customer_id, free).order_by(Activity.date, Activity.id, CustomerNeed.position, CustomerNeed.id))
    return list(db.scalars(stmt))


def needs_of(db: Session, opp: Opportunity) -> list[CustomerNeed]:
    stmt = (select(CustomerNeed).join(Activity, CustomerNeed.activity_id == Activity.id).where(CustomerNeed.opportunity_id == opp.id)
            .order_by(Activity.date, Activity.id, CustomerNeed.position, CustomerNeed.id))
    return list(db.scalars(stmt))


def need_label(need: CustomerNeed, limit: int = 60) -> str:
    """체크 상자에 보일 니즈 한 줄: 내용과 출처 활동 기록."""
    content = " ".join(need.content.split())
    a = need.activity
    when = f"{a.date.isoformat()} " if a.date else ""
    return f"{content[:limit]}{'…' if len(content) > limit else ''} ({when}{a.title or '제목 없음'})"


def apply_needs(db: Session, opp: Opportunity, raw_ids: Iterable) -> str | None:
    """영업기회에 묶을 니즈를 `raw_ids` 로 맞춘다: 체크한 것은 묶고, 이 영업기회에 묶여 있던 것 중 빠진 것은 푼다.
    묶을 수 없는 니즈(다른 고객의 것, 다른 영업기회에 묶인 것, 없는 번호)가 하나라도 있으면 아무것도 바꾸지 않고 오류 문구를 돌려준다.
    `opp` 는 번호가 있어야 한다 (flush 된 뒤)."""
    wanted = parse_ids(raw_ids)
    allowed = {n.id: n for n in bundlable_needs(db, opp.customer_id, opp)}
    if wanted is None or any(i not in allowed for i in wanted):
        return NEED_NOT_BUNDLABLE
    for need in allowed.values():
        need.opportunity_id = opp.id if need.id in wanted else None
    return None


def unlinked_same_name_needs(db: Session, customer: Customer | None) -> list[CustomerNeed]:
    """고객 기록에 연결되지 않았지만 쓴 이름이 이 고객과 같은 니즈 (안내만 한다. 묶을 수는 없다)."""
    if customer is None:
        return []
    key = normalize_name(customer.name)
    if not key:
        return []
    return [n for n in db.scalars(select(CustomerNeed).where(CustomerNeed.customer_id.is_(None), CustomerNeed.customer_name != ""))
            if normalize_name(n.customer_name) == key]


def customer_change_problem(db: Session, opp: Opportunity, new_customer_id: int, submitted_need_ids: Sequence) -> str | None:
    """고객을 바꿀 수 있는지: 묶은 니즈를 그대로 두거나, 프로젝트·제안 건이 이어져 있으면 바꿀 수 없다."""
    if new_customer_id == opp.customer_id:
        return None
    submitted = set(parse_ids(submitted_need_ids) or [])
    if any(n.id in submitted for n in needs_of(db, opp)):
        return CHANGE_HAS_NEEDS
    if opp.project_id is not None or db.scalar(select(func.count()).select_from(Proposal).where(Proposal.opportunity_id == opp.id)):
        return CHANGE_HAS_LINKS
    return None


# ---- 단계, 전환 ----

def stage_problem(stage: str) -> str | None:
    return None if stage in ontology.OPPORTUNITY_STAGES else BAD_STAGE


def convert_problem(opp: Opportunity) -> str | None:
    """프로젝트로 전환(만들기·잇기)할 수 없는 이유. 수주 단계이고 아직 프로젝트가 없을 때만 할 수 있다."""
    if opp.project_id is not None:
        return f"이미 {L_PROJECT}가 있습니다."
    if opp.stage != ontology.OPPORTUNITY_STAGE_WON:
        return NOT_WON
    return None


def can_convert(opp: Opportunity) -> bool:
    return convert_problem(opp) is None


def linkable_projects(db: Session, opp: Opportunity) -> list[Project]:
    """이을 수 있는 기존 프로젝트: 같은 고객의 것 중 다른 영업기회와 이어지지 않은 것."""
    taken = select(Opportunity.project_id).where(Opportunity.project_id.is_not(None), Opportunity.id != opp.id)
    return list(db.scalars(select(Project).where(Project.customer_id == opp.customer_id, Project.id.not_in(taken))
                           .order_by(Project.name, Project.id)))


def link_problem(db: Session, opp: Opportunity, project: Project | None) -> str | None:
    if project is None:
        return NO_PROJECT
    if project.customer_id != opp.customer_id:
        return PROJECT_OTHER_CUSTOMER
    if project.id not in {p.id for p in linkable_projects(db, opp)}:
        return PROJECT_TAKEN
    return None


def convert_create(db: Session, opp: Opportunity, name: str, user: User) -> Project:
    """같은 고객의 새 프로젝트를 만들어(BM 을 물려받는다) 잇는다. 조건은 호출자가 `convert_problem` 으로 확인한다."""
    project = Project(name=name.strip(), customer_id=opp.customer_id, bm_id=opp.bm_id, created_by_id=user.id)
    db.add(project)
    db.flush()
    opp.project_id = project.id
    return project


# ---- 지우기 ----

def release_opportunity(db: Session, opp: Opportunity) -> None:
    """영업기회를 지우기 전 정리: 묶인 니즈와 제안 건의 연결을 비운다 (프로젝트는 지우지 않는다). 연결 표 행은 지울 때 함께 사라진다."""
    db.execute(update(CustomerNeed).where(CustomerNeed.opportunity_id == opp.id).values(opportunity_id=None))
    db.execute(update(Proposal).where(Proposal.opportunity_id == opp.id).values(opportunity_id=None))


def release_project(db: Session, project: Project) -> list[Opportunity]:
    """프로젝트를 지우기 전: 그 프로젝트와 이어진 영업기회의 연결을 비운다. 사본을 다시 쓸 영업기회를 돌려준다."""
    opps = list(db.scalars(select(Opportunity).where(Opportunity.project_id == project.id)))
    for o in opps:
        o.project_id = None
    return opps


def project_customer_problem(db: Session, project: Project, new_customer_id: int) -> str | None:
    """프로젝트의 고객을 바꿀 수 있는지: 영업기회와 이어져 있으면 바꿀 수 없다 (같은 고객 규칙)."""
    if new_customer_id != project.customer_id and db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.project_id == project.id)):
        return PROJECT_HAS_OPPORTUNITY
    return None


def bm_delete_problem(db: Session, bm: BM) -> str | None:
    """BM 을 따르는 프로젝트나 영업기회가 있으면 삭제할 수 없다. 지워도 다른 것은 지우지 않는다."""
    follows = db.scalar(select(func.count()).select_from(Project).where(Project.bm_id == bm.id)) \
        or db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.bm_id == bm.id))
    return BM_IN_USE if follows else None


# ---- 목록·집계 ----

def open_count(db: Session) -> int:
    """진행 중인 영업기회 수 (발굴·제안 중·협상)."""
    return db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.stage.in_(ontology.OPPORTUNITY_OPEN_STAGES))) or 0


def opportunities_of_customer(db: Session, customer_id: int | None) -> list[Opportunity]:
    stmt = select(Opportunity).order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
    if customer_id is not None:
        stmt = stmt.where(Opportunity.customer_id == customer_id)
    return list(db.scalars(stmt))


def opportunity_of_project(db: Session, project: Project) -> Opportunity | None:
    return db.scalar(select(Opportunity).where(Opportunity.project_id == project.id))


# ---- 지식 사본: 이 대상이 바뀌면 사본을 다시 써야 하는 다른 대상 ----

def _unique(objs: Iterable) -> list:
    seen, out = set(), []
    for o in objs:
        if o is not None and (type(o), o.id) not in seen:
            seen.add((type(o), o.id))
            out.append(o)
    return out


def dependents(db: Session, obj) -> list:
    """`obj`(영업기회·BM)의 사본이 적히거나 `obj` 를 적는 다른 대상: 고객·BM·프로젝트·솔루션·활동 기록(니즈 출처 포함)·따르는 프로젝트·영업기회."""
    if isinstance(obj, Opportunity):
        need_activities = [n.activity for n in needs_of(db, obj)]
        return _unique([obj.customer, obj.bm, obj.project, *obj.solutions, *obj.activities, *need_activities])
    if isinstance(obj, BM):
        return _unique([*obj.solutions, *obj.projects, *obj.opportunities])
    return []

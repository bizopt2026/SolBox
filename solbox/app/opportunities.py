"""영업기회·BM 의 행위 조건을 한 곳에 둔다. 라우터와 템플릿은 여기 함수만 부른다 (화면 안내 글은 `ontology.OPPORTUNITY_RULES`).

- 니즈 묶기: 영업기회의 고객에 연결된 니즈만, 이미 다른 영업기회에 묶이지 않은 것만 (`bundlable_needs`, `apply_needs`). 해제는 선택을 풀면 된다.
- 단계 변경: 목록 안의 값이면 어디로든 (순서 강제 없음).
- 프로젝트로 전환: 수주(won)이고 `project_id` 가 비었을 때만 한 번 (`convert_problem`). 같은 고객의 기존 프로젝트를 이을 수도 있다 (`link_problem`).
- 지우기: 영업기회는 니즈 묶음·제안 건 연결을 비우고(프로젝트는 그대로), BM 은 따르는 프로젝트·영업기회가 있으면 막는다.

여기 함수는 커밋하지 않는다 (호출자가 커밋하고, 커밋 뒤에 `dependents` 로 지식 사본을 다시 쓴다)."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ontology
from app.ai.tasks import aliases_of, normalize_name
from app.korean import josa
from app.models import BM, Activity, Customer, CustomerNeed, Opportunity, Project, Proposal, User
from app.ui import FieldError

L_OPP, L_BM, L_PROJECT, L_NEED = (ontology.label("opportunity"), ontology.label("bm"), ontology.label("project"), ontology.term("customer_need"))

NAME_REQUIRED = FieldError("이름을 입력하세요.", "name")
CUSTOMER_REQUIRED = FieldError("고객을 선택하세요.", "customer_id")
BAD_STAGE = FieldError("알 수 없는 단계입니다.", "stage")
BAD_BM = FieldError(f"존재하지 않는 {L_BM}입니다.", "bm_id")
NEED_NOT_BUNDLABLE = f"묶을 수 없는 {josa(L_NEED, '이/가')} 있습니다. 이 고객에 연결된 {L_NEED} 중 다른 {L_OPP}에 묶이지 않은 것만 고를 수 있습니다."
CHANGE_HAS_NEEDS = f"묶은 {josa(L_NEED, '이/가')} 있으면 고객을 바꿀 수 없습니다. {L_NEED} 선택을 먼저 풀어 주세요."
CHANGE_HAS_LINKS = f"{josa(L_PROJECT, '이나/나')} 제안 건이 이어진 {josa(L_OPP, '은/는')} 고객을 바꿀 수 없습니다."
NOT_WON = f"{josa(L_PROJECT, '으로/로')} 전환은 수주 단계에서만 할 수 있습니다."
NO_PROJECT = f"존재하지 않는 {L_PROJECT}입니다."
PROJECT_OTHER_CUSTOMER = f"선택한 고객의 {josa(L_PROJECT, '이/가')} 아닙니다."
PROJECT_TAKEN = f"이미 다른 {josa(L_OPP, '와/과')} 이어진 {L_PROJECT}입니다."
PROJECT_HAS_OPPORTUNITY = f"이 {josa(L_PROJECT, '은/는')} {josa(L_OPP, '와/과')} 이어져 있어 고객을 바꿀 수 없습니다."
HAS_OPPORTUNITIES = f"{josa(L_OPP, '이/가')} 있는 고객은 삭제할 수 없습니다. {josa(L_OPP, '을/를')} 먼저 삭제해 주세요."
BM_IN_USE = f"이 {josa(L_BM, '을/를')} 따르는 {josa(L_PROJECT, '이나/나')} {josa(L_OPP, '이/가')} 있어 삭제할 수 없습니다. {L_PROJECT}·{L_OPP}의 {josa(L_BM, '을/를')} 먼저 바꾸거나 비워 주세요."
BM_IN_USE_PROPOSAL = f"이 {josa(L_BM, '을/를')} 신규 사업으로 삼은 다수 고객 제안 건이 있어 삭제할 수 없습니다. 제안 건에서 {josa(L_BM, '을/를')} 먼저 바꾸거나 비워 주세요."
OPPORTUNITY_MISMATCH = f"선택한 고객의 {josa(L_OPP, '이/가')} 아닙니다."
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
    # 불변식: 영업기회에 묶인 니즈는 모두 영업기회와 같은 고객이다. 고객이 어긋나 남은 니즈(이 영업기회를 가리키지만 고른 집합에 없는 것)도 푼다.
    db.execute(update(CustomerNeed).where(CustomerNeed.opportunity_id == opp.id, CustomerNeed.id.not_in(wanted) if wanted else true())
               .values(opportunity_id=None))
    return None


def retarget_need_customer(need: CustomerNeed, new_customer_id: int | None) -> None:
    """니즈의 고객 연결을 `new_customer_id` 로 바꾼다. 고객이 달라지면(다른 고객이거나 연결이 없어지면) 묶여 있던 영업기회에서 푼다:
    묶인 니즈는 영업기회와 같은 고객이어야 한다는 불변식. 호출자가 커밋하고 그 영업기회의 사본을 다시 쓴다."""
    if need.customer_id != new_customer_id:
        need.opportunity_id = None
    need.customer_id = new_customer_id


def unlinked_same_name_needs(db: Session, customer: Customer | None) -> list[CustomerNeed]:
    """고객 기록에 연결되지 않았지만 쓴 이름이 이 고객과 같은 니즈 (안내만 한다. 묶을 수는 없다)."""
    if customer is None:
        return []
    keys = {normalize_name(x) for x in (customer.name, *aliases_of(customer))} - {""}  # 이름과 다른 이름 (베타 AE)
    if not keys:
        return []
    return [n for n in db.scalars(select(CustomerNeed).where(CustomerNeed.customer_id.is_(None), CustomerNeed.customer_name != ""))
            if normalize_name(n.customer_name) in keys]


def customer_change_problem(db: Session, opp: Opportunity, new_customer_id: int) -> str | None:
    """고객을 바꿀 수 있는지: 묶은 니즈가 있으면 (폼이 어떤 니즈를 보냈든) 바꿀 수 없고, 프로젝트·제안 건이 이어져 있어도 바꿀 수 없다.
    니즈 선택을 먼저 풀어 저장한 뒤에 바꾼다. 이 잠금이 "묶인 니즈는 영업기회와 같은 고객" 불변식을 지킨다."""
    if new_customer_id == opp.customer_id:
        return None
    if needs_of(db, opp):
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
        return f"이미 {josa(L_PROJECT, '이/가')} 있습니다."
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


def claim_project(db: Session, opp: Opportunity, project_id: int) -> bool:
    """`UPDATE … WHERE project_id IS NULL` 로 이 영업기회의 프로젝트 자리를 차지한다 (한 번만 전환하는 규칙이 동시 요청에도 지켜지게).
    차지했으면 True, 그 사이 다른 요청이 먼저 이었으면 False. 같은 프로젝트를 다른 영업기회가 먼저 이었으면 유일 색인이 `IntegrityError` 를 낸다
    (호출자가 `PROJECT_TAKEN` 으로 보여 준다)."""
    claimed = db.execute(update(Opportunity).where(Opportunity.id == opp.id, Opportunity.project_id.is_(None)).values(project_id=project_id))
    if claimed.rowcount == 1:
        # 이 영업기회의 제안 건 중 프로젝트가 아직 없고 같은 고객인 것은 이 프로젝트를 발판으로 잇는다 (이미 프로젝트가 있는 제안 건은 건드리지 않는다)
        project = db.get(Project, project_id)
        if project is not None:
            db.execute(update(Proposal).where(Proposal.opportunity_id == opp.id, Proposal.project_id.is_(None), Proposal.customer_id == project.customer_id)
                       .values(project_id=project_id))
        db.refresh(opp)
        return True
    return False


def convert_create(db: Session, opp: Opportunity, name: str, user: User) -> Project | None:
    """같은 고객의 새 프로젝트를 만들어(BM 을 물려받는다) 잇는다. 조건은 호출자가 `convert_problem` 으로 확인한다.
    그 사이 다른 요청이 먼저 이었으면 (자리를 차지하지 못하면) 만든 프로젝트를 되돌리고 None 을 돌려준다 (호출자는 `db.rollback()` 된 상태에서 있던 프로젝트로 보낸다)."""
    project = Project(name=name.strip(), customer_id=opp.customer_id, bm_id=opp.bm_id, created_by_id=user.id)
    db.add(project)
    db.flush()
    if not claim_project(db, opp, project.id):
        db.rollback()
        return None
    return project


def link_project(db: Session, opp: Opportunity, project: Project) -> str | None:
    """기존 프로젝트를 잇는다. 이미 이어졌거나(다른 요청이 먼저) 그 프로젝트가 다른 영업기회와 이어졌으면 오류 문구를 돌려주고 되돌린다."""
    try:
        if not claim_project(db, opp, project.id):
            db.rollback()
            return f"이미 {josa(L_PROJECT, '이/가')} 있습니다."
    except IntegrityError:
        db.rollback()
        return PROJECT_TAKEN
    return None


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
    """BM 을 따르는 프로젝트나 영업기회가 있으면 삭제할 수 없다. 지워도 다른 것은 지우지 않는다.
    다수 고객 제안 건이 신규 사업으로 삼은 BM 도 같다 (베타 W: 그 연결은 외래 키 제약이 없는 옛 DB 도 있어, 몰래 비우지 않고 막는다)."""
    follows = db.scalar(select(func.count()).select_from(Project).where(Project.bm_id == bm.id)) \
        or db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.bm_id == bm.id))
    if follows:
        return BM_IN_USE
    from app.models import Proposal

    return BM_IN_USE_PROPOSAL if db.scalar(select(func.count()).select_from(Proposal).where(Proposal.bm_id == bm.id)) else None


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


# ---- 영업 A 단계: 담당 한 번 채우기 ----

OWNER_BACKFILL_KEY = "opportunity_owner_backfill"


def backfill_owners_once(engine) -> int:
    """시작할 때 한 번만: 담당이 빈 옛 영업기회의 담당을 만든 사람으로 채운다. 표시(app_settings)가 있으면 아무것도 하지 않는다
    (사용자가 나중에 비운 담당을 다시 채우지 않게). 채운 건수를 돌려준다. 필드 이력은 남기지 않는다 (시작 때 옮기기)."""
    from app.models import AppSetting

    with Session(engine) as db:
        if db.get(AppSetting, OWNER_BACKFILL_KEY) is not None:
            return 0
        n = db.execute(update(Opportunity).where(Opportunity.owner_user_id.is_(None), Opportunity.created_by_id.is_not(None))
                       .values(owner_user_id=Opportunity.created_by_id).execution_options(synchronize_session=False)).rowcount
        db.add(AppSetting(key=OWNER_BACKFILL_KEY, value={"filled": n}))
        db.commit()
        return n


# ---- 영업 A 단계: 진행 칸 (금액·예상 수주일·담당·다음 할 일) ----

BAD_DATE = "날짜는 2026-12-20 처럼 적어 주세요."
BAD_OWNER = FieldError("담당은 사용 중인 계정 중에서 고르세요.", "owner_user_id")
PROGRESS_FIELDS = ("amount", "expected_close", "owner_user_id", "next_step", "next_step_due")


def _parse_date(raw: str, field_name: str):
    from datetime import date

    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        return date.fromisoformat(raw), None
    except ValueError:
        return None, FieldError(BAD_DATE, field_name)


def parse_progress(db: Session, amount: str, expected_close: str, owner_user_id: str, next_step: str, next_step_due: str,
                   current_owner_id: int | None = None) -> tuple[dict, str | None]:
    """폼의 진행 칸을 모델 값으로. 오류면 (지금까지 읽은 값, 오류 글). 담당은 사용 중인 계정만 새로 고를 수 있고,
    지금 담당(`current_owner_id`)은 사용 중지여도 그대로 둘 수 있다 (저장 때 조용히 바뀌어 이력이 생기지 않게)."""
    from app import money

    values: dict = {"next_step": " ".join((next_step or "").split())}
    try:
        values["amount"] = money.parse_amount(amount)
    except ValueError as e:
        return values, FieldError(str(e), "amount")
    for name, raw in (("expected_close", expected_close), ("next_step_due", next_step_due)):
        values[name], problem = _parse_date(raw, name)
        if problem:
            return values, problem
    raw = (owner_user_id or "").strip()
    if not raw:
        values["owner_user_id"] = None
    else:
        uid = parse_id(raw)
        owner = db.get(User, uid) if uid is not None else None
        if owner is None or (not owner.is_active and owner.id != current_owner_id):
            return values, BAD_OWNER
        values["owner_user_id"] = owner.id
    return values, None


def progress_form(opp: Opportunity | None, default_owner_id: int | None = None) -> dict:
    """폼에 채울 진행 칸 글 (새로 만들 때는 담당만 기본값)."""
    from app import money

    if opp is None:
        return {"amount": "", "expected_close": "", "owner_user_id": default_owner_id or "", "next_step": "", "next_step_due": ""}
    return {"amount": money.edit_text(opp.amount), "expected_close": opp.expected_close.isoformat() if opp.expected_close else "",
            "owner_user_id": opp.owner_user_id or "", "next_step": opp.next_step or "",
            "next_step_due": opp.next_step_due.isoformat() if opp.next_step_due else ""}


# ---- 영업 A 단계: 수주·실패 사유 ----

REASON_REQUIRED = FieldError("사유를 고르세요.", "reason")
NOT_CLOSING = "수주나 실패로 바꿀 때만 사유를 받습니다."
NOTE_MAX = 500


def needs_reason(stage: str) -> bool:
    """이 단계로 바꾸려면 사유 화면을 거쳐야 하는지 (수주·실패)."""
    return stage in ontology.OPPORTUNITY_CLOSED_STAGES


def close_problem(stage: str, reason: str) -> str | None:
    if not needs_reason(stage):
        return NOT_CLOSING
    if reason not in ontology.close_reasons(stage):
        return REASON_REQUIRED
    return None


def apply_stage(opp: Opportunity, stage: str, reason: str = "", note: str = "") -> None:
    """단계를 바꾼다. 수주·실패면 사유·메모를 함께 두고, 그 밖의 단계로 가면 사유·메모를 비운다 (이력에는 남는다)."""
    opp.stage = stage
    if needs_reason(stage):
        opp.close_reason = reason
        opp.close_note = (note or "").strip()[:NOTE_MAX]
    else:
        opp.close_reason = ""
        opp.close_note = ""


def close_reason_label(opp: Opportunity) -> str:
    return ontology.close_reasons(opp.stage).get(opp.close_reason, "")

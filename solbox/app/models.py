"""DB 모델."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Table, Text, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app import ontology
from app.db import Base


class Stamped:
    """작성자와 생성·수정 시각."""

    @declared_attr
    def created_by_id(cls) -> Mapped[int | None]:
        return mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.now, onupdate=datetime.now
    )


class Origin:
    """기록의 유래: 직접 입력 / 올리기 묶음 / 리서치 보고서. 비어 있으면 모른다. `origin_id` 는 짝 표 번호이고 응용 수준에서만 지킨다."""

    origin_type: Mapped[str | None] = mapped_column(String, nullable=True)  # manual / intake / research (ontology.ORIGINS)
    origin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # intake 번호 또는 research_reports 번호


def _link_table(name: str, other: str, other_table: str) -> Table:
    return Table(
        name,
        Base.metadata,
        Column("activity_id", ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True),
        Column(f"{other}_id", ForeignKey(f"{other_table}.id", ondelete="CASCADE"), primary_key=True),
    )


activity_solutions = _link_table("activity_solutions", "solution", "solutions")
activity_customers = _link_table("activity_customers", "customer", "customers")
activity_projects = _link_table("activity_businesses", "business", "businesses")  # 물리 표 이름은 그대로 (프로젝트 = 옛 사업)
activity_partners = _link_table("activity_partners", "partner", "partners")
activity_opportunities = _link_table("activity_opportunities", "opportunity", "opportunities")

bm_solutions = Table(
    "bm_solutions",
    Base.metadata,
    Column("bm_id", ForeignKey("bms.id", ondelete="CASCADE"), primary_key=True),
    Column("solution_id", ForeignKey("solutions.id", ondelete="CASCADE"), primary_key=True),
)
opportunity_solutions = Table(
    "opportunity_solutions",
    Base.metadata,
    Column("opportunity_id", ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True),
    Column("solution_id", ForeignKey("solutions.id", ondelete="CASCADE"), primary_key=True),
)

proposal_solutions = Table(
    "proposal_solutions",
    Base.metadata,
    Column("proposal_id", ForeignKey("proposals.id", ondelete="CASCADE"), primary_key=True),
    Column("solution_id", ForeignKey("solutions.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String, default="")
    password_hash: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default=ontology.USER_ROLE_DEFAULT)  # admin / user
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)


class Partner(Stamped, Origin, Base):
    __tablename__ = "partners"
    __table_args__ = (Index("ux_partners_customer_id", "customer_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    contact: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(String, default="")
    memo: Mapped[str] = mapped_column(String, default="")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)  # 같은 회사인 고객 (1:1)

    solutions: Mapped[list[Solution]] = relationship(back_populates="partner")
    customer: Mapped[Customer | None] = relationship(back_populates="partner")


class Customer(Stamped, Origin, Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    industry: Mapped[str] = mapped_column(String, default="")
    contact: Mapped[str] = mapped_column(String, default="")
    memo: Mapped[str] = mapped_column(String, default="")

    projects: Mapped[list[Project]] = relationship(back_populates="customer")
    proposals: Mapped[list[Proposal]] = relationship(back_populates="customer")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="customer")
    partner: Mapped[Partner | None] = relationship(back_populates="customer", uselist=False)


class Project(Stamped, Base):
    """프로젝트 (옛 "기존 사업"). 물리 표·열 이름은 businesses / business_id 그대로 둔다."""

    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    description: Mapped[str] = mapped_column(String, default="")
    period: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="")
    memo: Mapped[str] = mapped_column(String, default="")
    bm_id: Mapped[int | None] = mapped_column(ForeignKey("bms.id"), nullable=True)  # 프로젝트가 따르는 BM (옛 DB 에는 migrate 가 제약 없이 더한다)

    customer: Mapped[Customer] = relationship(back_populates="projects")
    bm: Mapped[BM | None] = relationship(back_populates="projects")
    proposals: Mapped[list[Proposal]] = relationship(back_populates="project")


class BM(Stamped, Base):
    """BM (사업 모델). 고객에 묶이지 않는다. 특정 고객별로 나뉘는 것은 프로젝트다."""

    __tablename__ = "bms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(String, default="")  # 한 줄 설명
    target_customers: Mapped[str] = mapped_column(Text, default="")  # 대상 고객 유형
    revenue_model: Mapped[str] = mapped_column(Text, default="")  # 수익 구조
    memo: Mapped[str] = mapped_column(Text, default="")

    solutions: Mapped[list[Solution]] = relationship(secondary=bm_solutions, back_populates="bms")
    projects: Mapped[list[Project]] = relationship(back_populates="bm")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="bm")


class Opportunity(Stamped, Base):
    """영업기회: 한 고객의 니즈들을 묶어 사업으로 만들어 가는 건. 수주하면 프로젝트가 된다."""

    __tablename__ = "opportunities"
    __table_args__ = (Index("ux_opportunities_project_id", "project_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    stage: Mapped[str] = mapped_column(String, default=ontology.OPPORTUNITY_STAGE_DEFAULT)  # 순서 강제 없음 (ontology.OPPORTUNITY_STAGES)
    bm_id: Mapped[int | None] = mapped_column(ForeignKey("bms.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True)  # 이어진 프로젝트
    memo: Mapped[str] = mapped_column(Text, default="")

    customer: Mapped[Customer] = relationship(back_populates="opportunities")
    bm: Mapped[BM | None] = relationship(back_populates="opportunities")
    project: Mapped[Project | None] = relationship()
    solutions: Mapped[list[Solution]] = relationship(secondary=opportunity_solutions, back_populates="opportunities")
    activities: Mapped[list[Activity]] = relationship(secondary=activity_opportunities, back_populates="opportunities")
    proposals: Mapped[list[Proposal]] = relationship(back_populates="opportunity")


class Solution(Stamped, Origin, Base):
    __tablename__ = "solutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(String, default="")
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"), nullable=True)
    stage: Mapped[str] = mapped_column(String, default=ontology.STAGE_DEFAULT)
    usage_direction: Mapped[str] = mapped_column(String, default="")
    applicable_types: Mapped[str] = mapped_column(String, default="")
    tags: Mapped[str] = mapped_column(String, default="")  # 쉼표 구분
    origin_item: Mapped[str | None] = mapped_column(String, nullable=True)  # 리서치에서 등록했다면 보고서 안의 위치 ("suggestion:3", "trend:1")

    partner: Mapped[Partner | None] = relationship(back_populates="solutions")
    files: Mapped[list[SolutionFile]] = relationship(
        back_populates="solution", cascade="all, delete-orphan"
    )
    activities: Mapped[list[Activity]] = relationship(
        secondary=activity_solutions, back_populates="solutions"
    )
    proposals: Mapped[list[Proposal]] = relationship(
        secondary=proposal_solutions, back_populates="solutions"
    )
    bms: Mapped[list[BM]] = relationship(secondary=bm_solutions, back_populates="solutions")
    opportunities: Mapped[list[Opportunity]] = relationship(secondary=opportunity_solutions, back_populates="solutions")


class SolutionFile(Stamped, Base):
    __tablename__ = "solution_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    solution_id: Mapped[int] = mapped_column(ForeignKey("solutions.id"))
    original_name: Mapped[str] = mapped_column(String, default="")
    stored_name: Mapped[str] = mapped_column(String, default="")
    kind: Mapped[str] = mapped_column(String, default=ontology.FILE_KIND_DEFAULT)
    extract_status: Mapped[str] = mapped_column(String, default="")  # ok / failed / unsupported
    size: Mapped[int] = mapped_column(Integer, default=0)

    solution: Mapped[Solution] = relationship(back_populates="files")


class Activity(Stamped, Origin, Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, default=ontology.ACTIVITY_KIND_DEFAULT)
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    raw_text: Mapped[str] = mapped_column(Text)
    summary_md: Mapped[str] = mapped_column(Text, default="")
    # 옛 JSON 열 (물리 열 이름 follow_ups / demand_signals 그대로). 단위 2 이후 읽지도 쓰지도 않는다: 복사 이관(migrate.copy_activity_json)만 읽는다.
    # 후속 조치·고객 니즈는 follow_ups / customer_needs 표(아래 `follow_ups`·`needs` 관계)에 있다.
    legacy_follow_ups: Mapped[list] = mapped_column("follow_ups", JSON, default=list)
    legacy_demand_signals: Mapped[list] = mapped_column("demand_signals", JSON, default=list)

    follow_ups: Mapped[list[FollowUp]] = relationship(
        back_populates="activity", cascade="all, delete-orphan", passive_deletes=True,
        order_by="(FollowUp.position, FollowUp.id)",
    )
    needs: Mapped[list[CustomerNeed]] = relationship(
        back_populates="activity", cascade="all, delete-orphan", passive_deletes=True,
        order_by="(CustomerNeed.position, CustomerNeed.id)",
    )
    solutions: Mapped[list[Solution]] = relationship(
        secondary=activity_solutions, back_populates="activities"
    )
    customers: Mapped[list[Customer]] = relationship(secondary=activity_customers)
    projects: Mapped[list[Project]] = relationship(secondary=activity_projects)
    partners: Mapped[list[Partner]] = relationship(secondary=activity_partners)
    opportunities: Mapped[list[Opportunity]] = relationship(secondary=activity_opportunities, back_populates="activities")


class FollowUp(Base):
    """활동 기록에서 나온 할 일 한 줄. 활동 기록을 지우면 함께 지워진다."""

    __tablename__ = "follow_ups"

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)  # 활동 기록 안의 순서
    task: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String, default="")  # 담당 (쓴 그대로)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    due: Mapped[date | None] = mapped_column(Date, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.now, onupdate=datetime.now
    )

    activity: Mapped[Activity] = relationship(back_populates="follow_ups")
    owner_user: Mapped[User | None] = relationship()


class CustomerNeed(Base):
    """활동 기록에서 드러난 고객의 필요 한 줄 (옛 "수요 단서")."""

    __tablename__ = "customer_needs"

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    customer_name: Mapped[str] = mapped_column(String, default="")  # 고객 (쓴 그대로)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text)
    # 이 니즈가 묶인 영업기회 (R26). 단위 2 에서 만든 외래 키 제약 없는 번호 열을 그대로 쓴다 (SQLite 는 열에 제약을 나중에 붙일 수 없다):
    # 한 니즈는 영업기회 하나에만, 같은 고객의 니즈만 묶는 규칙과 영업기회를 지울 때 비우는 일은 app/opportunities.py 가 지킨다
    opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.now, onupdate=datetime.now
    )

    activity: Mapped[Activity] = relationship(back_populates="needs")
    customer: Mapped[Customer | None] = relationship()


class AIJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default=ontology.JOB_PENDING)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    requester_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[str] = mapped_column(Text, default="")
    error_kind: Mapped[str] = mapped_column(String, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Intake(Stamped, Base):
    """올리기 묶음: 한 번에 올린 파일들과 그 정리 상태."""

    __tablename__ = "intakes"

    id: Mapped[int] = mapped_column(primary_key=True)
    note: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default=ontology.INTAKE_STATUS_DEFAULT)  # uploaded / analyzing / ready / applying(저장 중) / applied / failed / discarded
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[list] = mapped_column(JSON, default=list)  # 저장으로 만들어진 기록 [{"type", "id", "label"}]
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ai_jobs.id"), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    apply_token: Mapped[str | None] = mapped_column(String, nullable=True)  # 저장 중(applying)인 선점의 정체. 그 선점만 저장을 끝내거나 풀 수 있다

    files: Mapped[list["IntakeFile"]] = relationship(
        back_populates="intake", cascade="all, delete-orphan", order_by="IntakeFile.id"
    )
    job: Mapped[AIJob | None] = relationship()


class IntakeFile(Base):
    __tablename__ = "intake_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    intake_id: Mapped[int] = mapped_column(ForeignKey("intakes.id"))
    original_name: Mapped[str] = mapped_column(String, default="")
    stored_name: Mapped[str] = mapped_column(String, default="")
    kind: Mapped[str] = mapped_column(String, default=ontology.INTAKE_FILE_KIND_DEFAULT)  # document / image / audio / other
    read_status: Mapped[str] = mapped_column(String, default="")  # ok / failed / unsupported / needs_transcript / pending_transcript
    read_error: Mapped[str | None] = mapped_column(String, nullable=True)  # 읽지 못한 이유 (받아쓰기 실패 등, 화면에 보이는 한국어)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 받아쓰기 진행 0~100
    size: Mapped[int] = mapped_column(Integer, default=0)
    target_type: Mapped[str] = mapped_column(String, default="")
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)

    intake: Mapped[Intake] = relationship(back_populates="files")


class Proposal(Stamped, Base):
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String, default="")
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    project_id: Mapped[int | None] = mapped_column("business_id", ForeignKey("businesses.id"), nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True)  # 제안 건이 위한 영업기회
    request_memo: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default=ontology.PROPOSAL_STATUS_DEFAULT)
    extra_solution_ids: Mapped[list] = mapped_column(JSON, default=list)
    directions: Mapped[list] = mapped_column(JSON, default=list)
    selected_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outline: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outline_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deck_name: Mapped[str] = mapped_column(String, default="")
    deck_built_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active_job_id: Mapped[int | None] = mapped_column(ForeignKey("ai_jobs.id"), nullable=True)
    chat_from_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 이 번호 이하의 대화는 현재 방향의 것이 아니다

    customer: Mapped[Customer] = relationship(back_populates="proposals")
    project: Mapped[Project | None] = relationship(back_populates="proposals")
    opportunity: Mapped[Opportunity | None] = relationship(back_populates="proposals")
    solutions: Mapped[list[Solution]] = relationship(
        secondary=proposal_solutions, back_populates="proposals"
    )
    messages: Mapped[list[ProposalMessage]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan", order_by="ProposalMessage.id"
    )
    active_job: Mapped[AIJob | None] = relationship(foreign_keys=[active_job_id])


class ProposalMessage(Base):
    __tablename__ = "proposal_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"))
    role: Mapped[str] = mapped_column(String)  # user / assistant
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)

    proposal: Mapped[Proposal] = relationship(back_populates="messages")


class ResearchTopic(Stamped, Base):
    __tablename__ = "research_topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    keywords: Mapped[str] = mapped_column(String, default="")
    area: Mapped[str] = mapped_column(String)  # market / tech / policy
    is_active: Mapped[bool] = mapped_column(default=True)


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    slot: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 예약 실행이 맡은 예정 시각
    trigger: Mapped[str] = mapped_column(String)  # schedule / manual
    status: Mapped[str] = mapped_column(String, default=ontology.JOB_RUNNING)  # running / done / partial / failed
    requester_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    reports: Mapped[list[ResearchReport]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ResearchReport.id"
    )


class ResearchReport(Base):
    __tablename__ = "research_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("research_runs.id"))
    kind: Mapped[str] = mapped_column(String)  # area / suggest
    area: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    trends: Mapped[list] = mapped_column(JSON, default=list)
    insights: Mapped[list] = mapped_column(JSON, default=list)
    suggestions: Mapped[list] = mapped_column(JSON, default=list)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ai_jobs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default=ontology.JOB_PENDING)  # pending / done / failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)

    run: Mapped[ResearchRun] = relationship(back_populates="reports")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSON, nullable=True)

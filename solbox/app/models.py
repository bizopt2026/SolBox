"""DB 모델."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app import ontology
from app.db import Base


class Stamped:
    """작성자와 생성·수정 시각."""

    @declared_attr
    def created_by_id(cls) -> Mapped[int | None]:
        return mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    @declared_attr
    def updated_by_id(cls) -> Mapped[int | None]:
        """마지막으로 고친 사람. 고칠 때마다 `app/guards.py` 의 flush 처리기가 채운다 (옛 행은 비어 있다)."""
        return mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.now, onupdate=datetime.now
    )


class Origin:
    """기록의 유래: 직접 입력 / 올리기 묶음 / 리서치 보고서. 비어 있으면 모른다. `origin_id` 는 짝 표 번호이고 응용 수준에서만 지킨다."""

    origin_type: Mapped[str | None] = mapped_column(String, nullable=True)  # manual / intake / import / research (ontology.ORIGINS)
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

# 다수 고객 제안 (베타 W): 제안 건에 붙인 등록된 고객들 (0..n). 진행 상태는 고객별로 따로 두지 않는다.
proposal_customers = Table(
    "proposal_customers",
    Base.metadata,
    Column("proposal_id", ForeignKey("proposals.id", ondelete="CASCADE"), primary_key=True),
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True),
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
    contact: Mapped[str | None] = mapped_column(String, nullable=True)  # 제안서 마무리 장에 쓰는 연락처 (본인이 계정 화면에서 적는다)
    must_change_password: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 관리자가 정해 준 비밀번호: 다음 로그인 뒤 바꿔야 쓸 수 있다
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)  # 소속 팀 (하나 또는 없음, 관리자가 정한다). 옛 DB 는 migrate 가 칸을 더한다

    team: Mapped[Team | None] = relationship(back_populates="members", foreign_keys=[team_id])


class Team(Base):
    """영업 담당을 묶는 팀 (영업 A 단계). 사람에게 팀이 있고, 영업기회의 팀은 담당의 팀에서 읽는다.
    Stamped 를 쓰지 않는다: 작성자 열(users 를 가리킴)이 있으면 users.team_id 와 외래 키가 서로를 가리켜 표 순서를 정할 수 없다."""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now, onupdate=datetime.now)

    members: Mapped[list[User]] = relationship(back_populates="team", foreign_keys="User.team_id", passive_deletes=True)


class FormToken(Base):
    """한 번만 쓰는 폼 표 (app/guards.py): 같은 표로 다시 제출되면 처음 만든 것의 주소로 보낸다 (연타·동시 제출이 기록을 둘 만들지 않게).
    표는 사용자마다 따로다. 오래된 표는 지운다 (매일 작업·처음 쓸 때)."""

    __tablename__ = "form_tokens"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    token: Mapped[str] = mapped_column(String, primary_key=True)
    result_url: Mapped[str | None] = mapped_column(String, nullable=True)  # 처음 제출이 만든 것의 주소 (끝나기 전에는 비어 있다)
    body_hash: Mapped[str | None] = mapped_column(String, nullable=True)  # 제출한 폼 내용의 해시(표 칸 제외): 같은 표로 내용을 바꿔 다시 내면 새 제출로 본다 (옛 줄은 비어 있고 같은 내용으로 본다)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DuplicateDismissal(Base):
    """"다른 회사임" 으로 표시한 쌍 (베타 AE, 관리 › 중복 정리). 종류(partner/customer) + 작은 번호 + 큰 번호가 기본키다.
    두 기록 중 하나를 지우거나 합치면 그 쌍의 줄도 지운다 (남은 줄은 화면이 건너뛴다)."""

    __tablename__ = "duplicate_dismissals"

    kind: Mapped[str] = mapped_column(String, primary_key=True)
    low_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    high_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    dismissed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)


class IdMark(Base):
    """표마다 지금까지 쓴 가장 큰 번호 (app/idmarks.py). SQLite 는 AUTOINCREMENT 없이 만든 표에서 지운 번호를 다시 쓰므로, 새 줄의 번호를 이 표시보다 크게 직접 정한다."""

    __tablename__ = "id_marks"

    table_name: Mapped[str] = mapped_column(String, primary_key=True)
    last_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Partner(Stamped, Origin, Base):
    __tablename__ = "partners"
    __table_args__ = (Index("ux_partners_customer_id", "customer_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    contact: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(String, default="")
    memo: Mapped[str] = mapped_column(String, default="")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)  # 같은 회사인 고객 (1:1)
    aliases: Mapped[str] = mapped_column(String, default="", server_default="")  # 다른 이름 (한 줄에 하나, 베타 AE: app/aliases.py). 옛 DB 는 migrate 가 칸을 더한다

    solutions: Mapped[list[Solution]] = relationship(back_populates="partner")
    customer: Mapped[Customer | None] = relationship(back_populates="partner")


class Customer(Stamped, Origin, Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    industry: Mapped[str] = mapped_column(String, default="")
    contact: Mapped[str] = mapped_column(String, default="")
    memo: Mapped[str] = mapped_column(String, default="")
    aliases: Mapped[str] = mapped_column(String, default="", server_default="")  # 다른 이름 (한 줄에 하나, 베타 AE: app/aliases.py). 옛 DB 는 migrate 가 칸을 더한다

    projects: Mapped[list[Project]] = relationship(back_populates="customer")
    proposals: Mapped[list[Proposal]] = relationship(back_populates="customer")
    group_proposals: Mapped[list[Proposal]] = relationship(secondary=proposal_customers, back_populates="group_customers")  # 붙은 다수 고객 제안 건
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
    business_type: Mapped[str | None] = mapped_column(String, nullable=True)  # 대표 사업방식 (선택, 코드 하나: ontology.BUSINESS_TYPES). 베타 Y

    solutions: Mapped[list[Solution]] = relationship(secondary=bm_solutions, back_populates="bms")
    projects: Mapped[list[Project]] = relationship(back_populates="bm")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="bm")
    proposals: Mapped[list[Proposal]] = relationship(back_populates="bm")  # 신규 사업으로 삼은 다수 고객 제안 건


class Opportunity(Stamped, Base):
    """영업기회: 한 고객의 니즈들을 묶어 사업으로 만들어 가는 건. 수주하면 프로젝트가 된다."""

    __tablename__ = "opportunities"
    __table_args__ = (Index("ux_opportunities_project_id", "project_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    stage: Mapped[str] = mapped_column(String, default=ontology.OPPORTUNITY_STAGE_DEFAULT, active_history=True)  # 순서 강제 없음 (ontology.OPPORTUNITY_STAGES)
    bm_id: Mapped[int | None] = mapped_column(ForeignKey("bms.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True)  # 이어진 프로젝트
    memo: Mapped[str] = mapped_column(Text, default="")
    business_types: Mapped[str] = mapped_column(String, default="")  # 검토 사업방식 (0..3, 쉼표로 이은 코드, 정의된 순서: ontology.clean_business_types). 베타 Y
    # 영업 A 단계: 진행 칸 (모두 선택). 바뀔 때마다 guards 가 opportunity_field_changes 에 남긴다 (ontology.OPPORTUNITY_TRACKED_FIELDS)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True, active_history=True)  # 예상 금액 (원, app/money.py)
    expected_close: Mapped[date | None] = mapped_column(Date, nullable=True, active_history=True)  # 예상 수주일
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, active_history=True)  # 담당 (만들 때 기본은 만든 사람)
    next_step: Mapped[str] = mapped_column(String, default="", server_default="", active_history=True)  # 다음 할 일
    next_step_due: Mapped[date | None] = mapped_column(Date, nullable=True, active_history=True)
    close_reason: Mapped[str] = mapped_column(String, default="", server_default="", active_history=True)  # 수주·실패 사유 코드 (ontology.close_reasons)
    close_note: Mapped[str] = mapped_column(Text, default="", server_default="")

    @property
    def business_type_codes(self) -> list[str]:
        return ontology.clean_business_types(self.business_types)

    customer: Mapped[Customer] = relationship(back_populates="opportunities")
    bm: Mapped[BM | None] = relationship(back_populates="opportunities")
    project: Mapped[Project | None] = relationship()
    solutions: Mapped[list[Solution]] = relationship(secondary=opportunity_solutions, back_populates="opportunities")
    activities: Mapped[list[Activity]] = relationship(secondary=activity_opportunities, back_populates="opportunities")
    proposals: Mapped[list[Proposal]] = relationship(back_populates="opportunity")
    stage_changes: Mapped[list[OpportunityStageChange]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True, order_by="OpportunityStageChange.changed_at",
    )
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_user_id])
    field_changes: Mapped[list[OpportunityFieldChange]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True, order_by="OpportunityFieldChange.id",
    )
    checks: Mapped[list[OpportunityCheck]] = relationship(back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True)
    insights: Mapped[list[OpportunityInsight]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", passive_deletes=True, order_by="OpportunityInsight.id",
    )


class OpportunityStageChange(Base):
    """영업기회 단계가 바뀐 기록 한 줄 (만들 때는 from_stage 가 빈 글자). guards._stamp_changes 가 남긴다. 팀 업무 보고가 기간별로 센다."""

    __tablename__ = "opportunity_stage_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    from_stage: Mapped[str] = mapped_column(String, default="")
    to_stage: Mapped[str] = mapped_column(String)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String, default="", server_default="")  # "ai" = AI 단계 제안을 반영 (영업 B). 옛 DB 는 migrate 가 칸을 더한다
    evidence: Mapped[str] = mapped_column(Text, default="", server_default="")

    opportunity: Mapped[Opportunity] = relationship(back_populates="stage_changes")


class OpportunityFieldChange(Base):
    """영업기회 진행 칸이 바뀐 기록 한 줄 (영업 A 단계, Salesforce 필드 이력). guards._stamp_report_dates 가 남긴다.
    값은 저장값을 글로: 날짜 ISO, 정수 글, 없음은 빈 글. source 는 빈 글 = 사람, "ai" = AI 제안을 반영 (B 단계)."""

    __tablename__ = "opportunity_field_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String)
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String, default="", server_default="")
    evidence: Mapped[str] = mapped_column(Text, default="", server_default="")  # AI 제안을 반영한 줄의 근거 문장 (영업 B). 옛 DB 는 migrate 가 칸을 더한다

    opportunity: Mapped[Opportunity] = relationship(back_populates="field_changes")


class OpportunityCheck(Base):
    """영업기회의 확인한 항목 한 줄 (영업 B, 항목 목록은 ontology.OPPORTUNITY_CHECKLIST). 체크를 풀면 줄을 지운다.
    줄이 생기거나 지워지면 guards 가 필드 이력(field="check")을 남긴다."""

    __tablename__ = "opportunity_checks"
    __table_args__ = (UniqueConstraint("opportunity_id", "item", name="ux_opportunity_checks_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    item: Mapped[str] = mapped_column(String)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    checked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String, default="", server_default="")  # 빈 글 = 사람, "ai" = AI 제안 반영
    evidence: Mapped[str] = mapped_column(Text, default="", server_default="")

    opportunity: Mapped[Opportunity] = relationship(back_populates="checks")
    checked_by: Mapped[User | None] = relationship()


class OpportunityInsight(Base):
    """AI 분석 한 번의 결과 (영업 B): 브리핑과 제안 목록. 화면은 영업기회마다 최신 줄만 쓴다. JSON 칸은 새 값을 대입한다."""

    __tablename__ = "opportunity_insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    briefing: Mapped[dict] = mapped_column(JSON, default=dict)
    suggestions: Mapped[list] = mapped_column(JSON, default=list)
    basis: Mapped[dict] = mapped_column(JSON, default=dict)

    opportunity: Mapped[Opportunity] = relationship(back_populates="insights")


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
    field_id: Mapped[int | None] = mapped_column(ForeignKey("solution_fields.id"), nullable=True)  # 솔루션 분야 (선택, 비면 "분야 미지정"). 베타 AD

    partner: Mapped[Partner | None] = relationship(back_populates="solutions")
    field: Mapped[SolutionField | None] = relationship(back_populates="solutions")
    business_types: Mapped[list[SolutionBusinessType]] = relationship(  # 가능한 사업방식 (0..3) 과 방식별 메모. 베타 Y
        back_populates="solution", cascade="all, delete-orphan", passive_deletes=True,
    )

    @property
    def business_type_rows(self) -> list[SolutionBusinessType]:
        """가능한 사업방식 줄들, 정의된 순서(구축형·임대형·투자형)로."""
        order = list(ontology.BUSINESS_TYPES)
        return sorted((r for r in self.business_types if r.business_type in order), key=lambda r: order.index(r.business_type))

    @property
    def business_type_codes(self) -> list[str]:
        return [r.business_type for r in self.business_type_rows]
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


class SolutionField(Base):
    """솔루션 분야: 관리자가 고치는 목록 (표 `solution_fields`, 베타 AD). 값이 코드 목록이 아니라 데이터다.
    `active` 가 거짓이면 새로 고를 수 없지만 이미 붙은 솔루션은 그대로 가진다. 쓰는 솔루션이 없을 때만 지울 수 있다."""

    __tablename__ = "solution_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    solutions: Mapped[list[Solution]] = relationship(back_populates="field")


class SolutionBusinessType(Base):
    """솔루션이 지원하는 사업방식 한 줄 (연결 표 `solution_business_types`, 기본키는 두 열). 방식마다 짧은 메모를 붙일 수 있다 (예: "투자형은 100kW 이상만").
    솔루션을 지우면 함께 지워진다. 이 줄이 바뀌면 솔루션의 고친 표시(`updated_at`)도 오른다 (`app/guards.py`)."""

    __tablename__ = "solution_business_types"

    solution_id: Mapped[int] = mapped_column(ForeignKey("solutions.id", ondelete="CASCADE"), primary_key=True)
    business_type: Mapped[str] = mapped_column(String, primary_key=True)  # ontology.BUSINESS_TYPES 의 코드
    memo: Mapped[str] = mapped_column(String, default="")

    solution: Mapped[Solution] = relationship(back_populates="business_types")


class SolutionFile(Stamped, Base):
    __tablename__ = "solution_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    solution_id: Mapped[int] = mapped_column(ForeignKey("solutions.id"))
    original_name: Mapped[str] = mapped_column(String, default="")
    stored_name: Mapped[str] = mapped_column(String, default="")
    kind: Mapped[str] = mapped_column(String, default=ontology.FILE_KIND_DEFAULT)
    extract_status: Mapped[str] = mapped_column(String, default="")  # ok / failed / unsupported
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)  # 파일 내용의 해시 (새로 올린 것만 채운다. 옛 파일은 비교할 때 한 번 채운다)

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
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 완료로 바뀐 때 (guards._stamp_changes 가 채운다). 팀 업무 보고가 기간별로 센다
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
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)  # 상단 줄 AI 상태가 "마지막으로 끝난 작업" 을 이 열로 찾는다

    @property
    def shown_error_kind(self) -> str:
        """화면에 보일 오류 종류: 서버 재시작으로 중단된 작업은 (옛 행이 `unknown` 으로 남아 있어도) `restart`."""
        if self.error_kind == "unknown" and self.error == ontology.RESTART_ERROR_TEXT:
            return "restart"
        return self.error_kind


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
    source: Mapped[str] = mapped_column(String, default="", server_default="")  # "" = 파일을 올려 AI 가 정리 / "cards" = 명함 파일 가져오기(파서가 정리안을 만들고 AI 를 쓰지 않는다, 베타 AA)

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
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)  # 파일 내용의 해시 (새로 올린 것만 채운다. 옛 파일은 비교할 때 한 번 채운다)
    target_type: Mapped[str] = mapped_column(String, default="")
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)

    intake: Mapped[Intake] = relationship(back_populates="files")


class Proposal(Stamped, Base):
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String, default="")
    # 특정 고객 제안(target_kind='customer')은 고객이 필수이고, 다수 고객 제안(target_kind='group')은 고객이 비고 group_name 이 필수다 (응용 수준 규칙: app/proposals.py).
    # 옛 DB 의 NOT NULL 은 app/migrate.py 의 표 다시 만들기로 풀었다.
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
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
    deck_removed: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 마지막으로 만든 고객용 파일에서 뺀 줄 수 (사본 경로가 든 줄). 옛 제안 건은 비어 있다
    deck_quality: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 마지막으로 만든 고객용 파일의 품질 검사 결과 (`deck_gates`). 옛 제안 건·검사 실패는 비어 있다
    active_job_id: Mapped[int | None] = mapped_column(ForeignKey("ai_jobs.id"), nullable=True)
    chat_from_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 이 번호 이하의 대화는 현재 방향의 것이 아니다
    target_kind: Mapped[str] = mapped_column(String, default=ontology.PROPOSAL_TARGET_DEFAULT)  # customer(특정 고객) / group(다수 고객). 만든 뒤 바꾸지 않는다
    group_name: Mapped[str] = mapped_column(String, default="")  # 다수 고객 제안의 대상 그룹 이름 (예: 전국 지자체)
    bm_id: Mapped[int | None] = mapped_column(ForeignKey("bms.id", ondelete="SET NULL"), nullable=True)  # 다수 고객 제안의 신규 사업 (BM)
    business_types: Mapped[str] = mapped_column(String, default="")  # 검토 사업방식 (0..3, 쉼표로 이은 코드). 만들 때 영업기회·BM 에서 가져오고 바꿀 수 있다. 베타 Y

    @property
    def business_type_codes(self) -> list[str]:
        return ontology.clean_business_types(self.business_types)

    customer: Mapped[Customer | None] = relationship(back_populates="proposals")
    group_customers: Mapped[list[Customer]] = relationship(secondary=proposal_customers, back_populates="group_proposals", order_by="Customer.name")
    bm: Mapped[BM | None] = relationship(back_populates="proposals")
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


class Report(Stamped, Base):
    """팀 업무 보고 한 건: 한 단위(일·주·월)·한 기간의 실적과 다음 기간 계획. 같은 단위·같은 시작일의 보고는 하나만."""

    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("unit", "period_start", name="uq_reports_unit_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit: Mapped[str] = mapped_column(String, default=ontology.REPORT_UNIT_DEFAULT)  # day / week / month (ontology.REPORT_UNITS)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, default=ontology.REPORT_STATUS_DEFAULT)  # draft / planning / confirmed (ontology.REPORT_STATUSES)
    summary_md: Mapped[str] = mapped_column(Text, default="")  # 실적 요약 글
    highlights_md: Mapped[str] = mapped_column(Text, default="")  # 이슈·건의
    section_notes: Mapped[dict] = mapped_column(JSON, default=dict)  # 구역별 보고 문구 {kpi, opps, proposals, check, plan, issues: [문장]}
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 확정 때의 집계 결과 (확정 전에는 비어 있고 열 때마다 새로 센다)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active_job_id: Mapped[int | None] = mapped_column(ForeignKey("ai_jobs.id"), nullable=True)
    chat_from_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 이 번호 이하의 대화는 지금 계획의 것이 아니다
    file_names: Mapped[dict] = mapped_column(JSON, default=dict)  # 형식 -> 마지막으로 만든 파일 이름
    files_built_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    plan_draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # AI 가 낸 계획 초안 (사용자가 "이 초안 쓰기"를 눌러야 항목에 반영)
    direction: Mapped[str] = mapped_column(Text, default="")  # 계획 방향 한 줄

    messages: Mapped[list[ReportMessage]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True, order_by="ReportMessage.id"
    )
    plan_items: Mapped[list[ReportPlanItem]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True,
        foreign_keys="ReportPlanItem.report_id", order_by="(ReportPlanItem.position, ReportPlanItem.id)",
    )
    active_job: Mapped[AIJob | None] = relationship(foreign_keys=[active_job_id])


class ReportMessage(Base):
    """업무 보고의 계획 대화 한 줄 (제안 대화와 같은 모양)."""

    __tablename__ = "report_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String)  # user / assistant
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)

    report: Mapped[Report] = relationship(back_populates="messages")


class ReportPlanItem(Base):
    """업무 보고가 세운 계획 항목 한 줄. 다음 보고가 점검한다 (result·checked_in_report_id).
    고객·영업기회·프로젝트를 지우면 연결만 비워진다 (항목 글은 남는다). 한 항목은 한 번만 다음 계획으로 넘어간다 (carried_from_id 유일)."""

    __tablename__ = "report_plan_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)  # 이 계획을 세운 보고
    position: Mapped[int] = mapped_column(Integer, default=0)
    task: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String, default="")  # 담당 (쓴 그대로)
    due: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(String, default="")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[int | None] = mapped_column("business_id", ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True)
    result: Mapped[str] = mapped_column(String, default="")  # 점검 결과 (ontology.PLAN_RESULTS, 빈 값 = 아직 점검 안 함)
    result_note: Mapped[str] = mapped_column(String, default="")
    checked_in_report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id", ondelete="SET NULL"), nullable=True)  # 점검한 보고
    carried_from_id: Mapped[int | None] = mapped_column(ForeignKey("report_plan_items.id", ondelete="SET NULL"), nullable=True, unique=True)  # 넘어온 원래 항목
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.now, onupdate=datetime.now
    )

    report: Mapped[Report] = relationship(back_populates="plan_items", foreign_keys=[report_id])
    customer: Mapped[Customer | None] = relationship()
    opportunity: Mapped[Opportunity | None] = relationship()
    project: Mapped[Project | None] = relationship()


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


# ======================================================================================
# AI 코워크 ①: 사업 방향·과제 공간 (설계 docs/superpowers/specs/2026-11-26-ai-cowork-design.md)
# ======================================================================================

def _initiative_link(name: str, col: str, target: str) -> Table:
    return Table(
        name, Base.metadata,
        Column("initiative_id", ForeignKey("initiatives.id", ondelete="CASCADE"), primary_key=True),
        Column(col, ForeignKey(f"{target}.id", ondelete="CASCADE"), primary_key=True),
    )


initiative_solutions = _initiative_link("initiative_solutions", "solution_id", "solutions")
initiative_partners = _initiative_link("initiative_partners", "partner_id", "partners")
initiative_customers = _initiative_link("initiative_customers", "customer_id", "customers")
initiative_opportunities = _initiative_link("initiative_opportunities", "opportunity_id", "opportunities")
initiative_projects = _initiative_link("initiative_projects", "business_id", "businesses")  # 프로젝트의 물리 표·열 이름 그대로


class Strategy(Stamped, Base):
    """사업 방향: 팀이 합의한 사업개발 방향. 합의 내용(consensus)이 다른 기록을 재는 잣대가 된다 (합의 상태이고 기간 안일 때만)."""

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text, default="")  # 배경·목표
    consensus: Mapped[str] = mapped_column(Text, default="", active_history=True)  # 합의 내용 (바뀌면 이력)
    instructions: Mapped[str] = mapped_column(Text, default="")  # 이 공간 대화에서 AI 가 지킬 것
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String, default=ontology.STRATEGY_STATUS_DEFAULT, active_history=True)  # ontology.STRATEGY_STATUSES

    initiatives: Mapped[list[Initiative]] = relationship(back_populates="strategy", order_by="Initiative.id")
    files: Mapped[list[WorkspaceFile]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan", passive_deletes=True, order_by="WorkspaceFile.id")
    changes: Mapped[list[WorkspaceChange]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan", passive_deletes=True, order_by="WorkspaceChange.id")


class Initiative(Stamped, Base):
    """과제: 이슈 또는 개별 과제 한 건 (종류 칸). 담당·기한·상태·속한 방향을 갖고 다른 기록과 잇는다."""

    __tablename__ = "initiatives"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default=ontology.INITIATIVE_KIND_DEFAULT, active_history=True)  # ontology.INITIATIVE_KINDS
    status: Mapped[str] = mapped_column(String, default=ontology.INITIATIVE_STATUS_DEFAULT, active_history=True)  # ontology.INITIATIVE_STATUSES
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, active_history=True)
    due: Mapped[date | None] = mapped_column(Date, nullable=True, active_history=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, active_history=True)
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[str] = mapped_column(Text, default="")  # 진행 메모 (보고서에 실린다)

    strategy: Mapped[Strategy | None] = relationship(back_populates="initiatives")
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_user_id])
    solutions: Mapped[list[Solution]] = relationship(secondary=initiative_solutions, order_by="Solution.name")
    partners: Mapped[list[Partner]] = relationship(secondary=initiative_partners, order_by="Partner.name")
    customers: Mapped[list[Customer]] = relationship(secondary=initiative_customers, order_by="Customer.name")
    opportunities: Mapped[list[Opportunity]] = relationship(secondary=initiative_opportunities, order_by="Opportunity.name")
    projects: Mapped[list[Project]] = relationship(secondary=initiative_projects, order_by="Project.name")
    files: Mapped[list[WorkspaceFile]] = relationship(
        back_populates="initiative", cascade="all, delete-orphan", passive_deletes=True, order_by="WorkspaceFile.id")
    changes: Mapped[list[WorkspaceChange]] = relationship(
        back_populates="initiative", cascade="all, delete-orphan", passive_deletes=True, order_by="WorkspaceChange.id")


class WorkspaceFile(Stamped, Base):
    """사업 방향·과제 공간에 올린 자료 파일. strategy_id 와 initiative_id 중 정확히 하나가 채워진다 (응용 수준)."""

    __tablename__ = "workspace_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True)
    initiative_id: Mapped[int | None] = mapped_column(ForeignKey("initiatives.id", ondelete="CASCADE"), nullable=True, index=True)
    original_name: Mapped[str] = mapped_column(String, default="")
    stored_name: Mapped[str] = mapped_column(String, default="")
    extract_status: Mapped[str] = mapped_column(String, default="")  # ok / failed / unsupported
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)

    strategy: Mapped[Strategy | None] = relationship(back_populates="files")
    initiative: Mapped[Initiative | None] = relationship(back_populates="files")

    @property
    def owner(self):
        return self.strategy if self.strategy_id is not None else self.initiative


class WorkspaceChange(Base):
    """사업 방향·과제의 칸이 바뀐 기록 한 줄 (ontology.STRATEGY_TRACKED_FIELDS·INITIATIVE_TRACKED_FIELDS). guards 가 남긴다. 보고서가 기간을 센다."""

    __tablename__ = "workspace_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True)
    initiative_id: Mapped[int | None] = mapped_column(ForeignKey("initiatives.id", ondelete="CASCADE"), nullable=True, index=True)
    field: Mapped[str] = mapped_column(String)
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String, default="", server_default="")  # 빈 글 = 사람, "ai" = AI 동료 제안을 반영 (AI 코워크 ②)
    evidence: Mapped[str] = mapped_column(Text, default="", server_default="")  # AI 제안을 반영한 줄의 근거 문장

    strategy: Mapped[Strategy | None] = relationship(back_populates="changes")
    initiative: Mapped[Initiative | None] = relationship(back_populates="changes")


# ======================================================================================
# AI 코워크 ②: AI 동료 칸의 대화 (팀 전체 공개). 대상은 종류+번호 (ontology.COWORK_TARGETS), 대상을 지우면 cowork 가 함께 지운다
# ======================================================================================

class AIThread(Stamped, Base):
    __tablename__ = "ai_threads"
    __table_args__ = (Index("ix_ai_threads_target", "target_type", "target_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String)  # strategy / initiative / solution / partner / opportunity
    target_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String, default="")
    origin_type: Mapped[str] = mapped_column(String, default="")  # 과제로 옮긴 대화의 처음 대상 (보이기만 한다)
    origin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_job_id: Mapped[int | None] = mapped_column(ForeignKey("ai_jobs.id"), nullable=True)

    messages: Mapped[list[AIMessage]] = relationship(
        back_populates="thread", cascade="all, delete-orphan", passive_deletes=True, order_by="AIMessage.id")
    active_job: Mapped[AIJob | None] = relationship(foreign_keys=[active_job_id])
    creator: Mapped[User | None] = relationship(primaryjoin="User.id == AIThread.created_by_id", foreign_keys="AIThread.created_by_id", viewonly=True)


class AIMessage(Base):
    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("ai_threads.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String)  # user / assistant
    content: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)  # 쓴 사람 (AI 답은 물은 사람)
    suggestions: Mapped[list] = mapped_column(JSON, default=list)  # AI 답의 제안 (cowork.clean_suggestions 모양). 새 list 를 대입한다
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)

    thread: Mapped[AIThread] = relationship(back_populates="messages")
    user: Mapped[User | None] = relationship(foreign_keys=[user_id])


class AlignmentNote(Base):
    """정렬 점검 한 번 (AI 코워크 ③): 기록 하나가 합의된 사업 방향과 맞는지 AI 가 근거와 함께 판단한 것. 대상마다 최신 줄만 화면에 쓴다."""

    __tablename__ = "alignment_notes"
    __table_args__ = (Index("ix_alignment_notes_target", "target_type", "target_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String)  # initiative / opportunity / solution (ontology.ALIGNMENT_TARGETS)
    target_id: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String, default="")  # ontology.ALIGNMENT_VERDICTS
    strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 가장 가까운 사업 방향 (제약 없는 번호: 방향을 지우면 cowork 처리기가 비운다)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)  # 근거를 기록 글에서 찾았는지
    basis: Mapped[str] = mapped_column(String, default="")  # 읽은 사업 방향들의 합의 내용 해시 (바뀌면 다시 점검 필요)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)

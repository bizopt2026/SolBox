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
    stage: Mapped[str] = mapped_column(String, default=ontology.OPPORTUNITY_STAGE_DEFAULT)  # 순서 강제 없음 (ontology.OPPORTUNITY_STAGES)
    bm_id: Mapped[int | None] = mapped_column(ForeignKey("bms.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True)  # 이어진 프로젝트
    memo: Mapped[str] = mapped_column(Text, default="")
    business_types: Mapped[str] = mapped_column(String, default="")  # 검토 사업방식 (0..3, 쉼표로 이은 코드, 정의된 순서: ontology.clean_business_types). 베타 Y

    @property
    def business_type_codes(self) -> list[str]:
        return ontology.clean_business_types(self.business_types)

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

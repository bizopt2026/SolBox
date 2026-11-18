"""DB 모델."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Column, Date, DateTime, ForeignKey, Integer, String, Table, Text, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

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


def _link_table(name: str, other: str, other_table: str) -> Table:
    return Table(
        name,
        Base.metadata,
        Column("activity_id", ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True),
        Column(f"{other}_id", ForeignKey(f"{other_table}.id", ondelete="CASCADE"), primary_key=True),
    )


activity_solutions = _link_table("activity_solutions", "solution", "solutions")
activity_customers = _link_table("activity_customers", "customer", "customers")
activity_businesses = _link_table("activity_businesses", "business", "businesses")
activity_partners = _link_table("activity_partners", "partner", "partners")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String, default="")
    password_hash: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="user")  # admin / user
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)


class Partner(Stamped, Base):
    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    contact: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(String, default="")
    memo: Mapped[str] = mapped_column(String, default="")

    solutions: Mapped[list[Solution]] = relationship(back_populates="partner")


class Customer(Stamped, Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    industry: Mapped[str] = mapped_column(String, default="")
    contact: Mapped[str] = mapped_column(String, default="")
    memo: Mapped[str] = mapped_column(String, default="")

    businesses: Mapped[list[Business]] = relationship(back_populates="customer")


class Business(Stamped, Base):
    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    description: Mapped[str] = mapped_column(String, default="")
    period: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="")
    memo: Mapped[str] = mapped_column(String, default="")

    customer: Mapped[Customer] = relationship(back_populates="businesses")


class Solution(Stamped, Base):
    __tablename__ = "solutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(String, default="")
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"), nullable=True)
    stage: Mapped[str] = mapped_column(String, default="intake")
    usage_direction: Mapped[str] = mapped_column(String, default="")
    applicable_types: Mapped[str] = mapped_column(String, default="")
    tags: Mapped[str] = mapped_column(String, default="")  # 쉼표 구분

    partner: Mapped[Partner | None] = relationship(back_populates="solutions")
    files: Mapped[list[SolutionFile]] = relationship(
        back_populates="solution", cascade="all, delete-orphan"
    )
    activities: Mapped[list[Activity]] = relationship(
        secondary=activity_solutions, back_populates="solutions"
    )


class SolutionFile(Stamped, Base):
    __tablename__ = "solution_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    solution_id: Mapped[int] = mapped_column(ForeignKey("solutions.id"))
    original_name: Mapped[str] = mapped_column(String, default="")
    stored_name: Mapped[str] = mapped_column(String, default="")
    kind: Mapped[str] = mapped_column(String, default="related")
    extract_status: Mapped[str] = mapped_column(String, default="")  # ok / failed / unsupported
    size: Mapped[int] = mapped_column(Integer, default=0)

    solution: Mapped[Solution] = relationship(back_populates="files")


class Activity(Stamped, Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, default="meeting")
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    raw_text: Mapped[str] = mapped_column(Text)
    summary_md: Mapped[str] = mapped_column(Text, default="")
    # [{"task","owner","due","done"}]
    follow_ups: Mapped[list] = mapped_column(JSON, default=list)
    # [{"customer","content"}]
    demand_signals: Mapped[list] = mapped_column(JSON, default=list)

    solutions: Mapped[list[Solution]] = relationship(
        secondary=activity_solutions, back_populates="activities"
    )
    customers: Mapped[list[Customer]] = relationship(secondary=activity_customers)
    businesses: Mapped[list[Business]] = relationship(secondary=activity_businesses)
    partners: Mapped[list[Partner]] = relationship(secondary=activity_partners)


class AIJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    requester_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[str] = mapped_column(Text, default="")
    error_kind: Mapped[str] = mapped_column(String, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

from datetime import UTC, date, datetime
from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Enum as SqlEnum, Index, UniqueConstraint
from sqlalchemy.orm import relationship
import enum

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class LeaveStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class LeaveType(str, enum.Enum):
    ANNUAL = "annual"
    SICK = "sick"
    PERSONAL = "personal"
    MATERNITY = "maternity"
    PATERNITY = "paternity"
    UNPAID = "unpaid"


class LeaveSession(str, enum.Enum):
    AM = "am"
    PM = "pm"


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    department = Column(String, nullable=False)
    manager_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    joined_at = Column(Date, default=date.today)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    manager = relationship("Employee", remote_side="Employee.id")
    leave_requests = relationship(
        "LeaveRequest",
        back_populates="employee",
        foreign_keys="LeaveRequest.employee_id",
    )
    leave_balances = relationship("LeaveBalance", back_populates="employee")


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        Index("ix_leave_requests_overlap_lookup", "employee_id", "status", "start_date", "end_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    leave_type = Column(SqlEnum(LeaveType), nullable=False)
    start_date = Column(Date, nullable=False)
    start_session = Column(SqlEnum(LeaveSession), nullable=False, default=LeaveSession.AM)
    end_date = Column(Date, nullable=False)
    end_session = Column(SqlEnum(LeaveSession), nullable=False, default=LeaveSession.PM)
    leave_usage_units = Column(Integer, nullable=False)
    reason = Column(String, nullable=True)
    status = Column(SqlEnum(LeaveStatus), default=LeaveStatus.PENDING)
    approved_by = Column(Integer, ForeignKey("employees.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    employee = relationship("Employee", back_populates="leave_requests", foreign_keys=[employee_id])
    approver = relationship("Employee", foreign_keys=[approved_by])

    @property
    def leave_usage_days(self) -> float:
        return self.leave_usage_units / 2


class LeaveBalance(Base):
    __tablename__ = "leave_balances"
    __table_args__ = (
        UniqueConstraint("employee_id", "leave_type", "year", name="uq_leave_balances_employee_type_year"),
        Index("ix_leave_balances_lookup", "employee_id", "leave_type", "year"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    leave_type = Column(SqlEnum(LeaveType), nullable=False)
    year = Column(Integer, nullable=False)
    total_units = Column(Integer, nullable=False, default=0)
    used_units = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    employee = relationship("Employee", back_populates="leave_balances")

    @property
    def total_days(self) -> float:
        return self.total_units / 2

    @property
    def used_days(self) -> float:
        return self.used_units / 2

    @property
    def remaining_days(self) -> float:
        return (self.total_units - self.used_units) / 2

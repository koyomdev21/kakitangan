"""
Business logic layer for leave management.

Implement the following service functions to handle leave request workflows.
Each function should raise appropriate exceptions for invalid operations
(e.g., overlapping leave, insufficient balance, self-approval).
"""

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.leave_calculator import calculate_leave_usage
from src.models import Employee, LeaveRequest, LeaveBalance, LeaveType, LeaveStatus, LeaveSession, utc_now


MALAYSIA_TZ = ZoneInfo("Asia/Kuala_Lumpur")


class LeaveError(Exception):
    pass


class InsufficientBalanceError(LeaveError):
    pass


class EmployeeNotFoundError(LeaveError):
    pass


class InvalidLeavePeriodError(LeaveError):
    pass


class NoWorkingSessionsError(LeaveError):
    pass


class MissingLeaveBalanceError(LeaveError):
    pass


class OverlappingLeaveError(LeaveError):
    pass


class SelfApprovalError(LeaveError):
    pass


class LeaveRequestNotFoundError(LeaveError):
    pass


class ApproverNotFoundError(LeaveError):
    pass


class ApprovalAuthorityError(LeaveError):
    pass


class InvalidStatusTransitionError(LeaveError):
    pass


class StaleLeaveUsageError(LeaveError):
    pass


class CannotModifyApprovedLeaveError(LeaveError):
    pass


class CancellationAuthorityError(LeaveError):
    pass


class LeaveAlreadyStartedError(LeaveError):
    pass


def malaysia_today() -> date:
    return datetime.now(MALAYSIA_TZ).date()


def create_leave_request(
    db: Session,
    employee_id: int,
    leave_type: LeaveType,
    start_date: date,
    end_date: date,
    start_session: LeaveSession = LeaveSession.AM,
    end_session: LeaveSession = LeaveSession.PM,
    reason: Optional[str] = None,
    today: Optional[date] = None,
) -> LeaveRequest:
    """
    Create a new leave request.
    Must validate:
    - Employee exists
    - start_date <= end_date
    - start_date >= today (no back-dating)
    - No overlapping leave requests for the same employee
    - Employee has sufficient leave balance for the requested type
    - end_date - start_date >= 0 (at least 1 day — or handle half-day logic)
    """
    today = today or malaysia_today()
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise EmployeeNotFoundError("Employee not found")

    if start_date > end_date:
        raise InvalidLeavePeriodError("Start date must be on or before end date")

    if start_date < today:
        raise InvalidLeavePeriodError("Leave request cannot be backdated")

    if start_date == end_date and start_session == LeaveSession.PM and end_session == LeaveSession.AM:
        raise InvalidLeavePeriodError("End session cannot be earlier than start session on the same day")

    usage = calculate_leave_usage(start_date, start_session, end_date, end_session)
    if usage.total_units == 0:
        raise NoWorkingSessionsError("Leave request must include at least one working session")

    pending_requests = _pending_requests_for_balance(db, employee_id, leave_type)
    for year, requested_units in usage.units_by_year.items():
        balance = _get_balance(db, employee_id, leave_type, year)
        if not balance:
            raise MissingLeaveBalanceError(f"Leave balance not found for {leave_type.value} leave in {year}")

        pending_units = _pending_units_for_year(pending_requests, year)
        available_units = balance.total_units - balance.used_units - pending_units
        if requested_units > available_units:
            raise InsufficientBalanceError("Insufficient leave balance")

    _ensure_no_overlap(db, employee_id, start_date, end_date, usage)

    leave_request = LeaveRequest(
        employee_id=employee_id,
        leave_type=leave_type,
        start_date=start_date,
        start_session=start_session,
        end_date=end_date,
        end_session=end_session,
        leave_usage_units=usage.total_units,
        reason=reason,
        status=LeaveStatus.PENDING,
    )
    db.add(leave_request)
    db.commit()
    db.refresh(leave_request)

    return leave_request


def preview_leave_usage(
    start_date: date,
    end_date: date,
    start_session: LeaveSession = LeaveSession.AM,
    end_session: LeaveSession = LeaveSession.PM,
):
    if start_date > end_date:
        raise InvalidLeavePeriodError("Start date must be on or before end date")

    if start_date == end_date and start_session == LeaveSession.PM and end_session == LeaveSession.AM:
        raise InvalidLeavePeriodError("End session cannot be earlier than start session on the same day")

    usage = calculate_leave_usage(start_date, start_session, end_date, end_session)
    if usage.total_units == 0:
        raise NoWorkingSessionsError("Leave request must include at least one working session")

    return usage


def approve_leave_request(
    db: Session,
    leave_request_id: int,
    approver_id: int,
    decision: LeaveStatus,
) -> LeaveRequest:
    """
    Approve or reject a pending leave request.
    Must validate:
    - Leave request exists and is in PENDING status
    - Approver is the employee's manager (or has approval authority)
    - Approver is not the leave requester (no self-approval)
    - On approval: deduct from leave balance
    - On rejection: record reason in comment field if needed
    """
    if decision not in {LeaveStatus.APPROVED, LeaveStatus.REJECTED}:
        raise InvalidStatusTransitionError("Review decision must be approved or rejected")

    leave_request = db.query(LeaveRequest).filter(LeaveRequest.id == leave_request_id).first()
    if not leave_request:
        raise LeaveRequestNotFoundError("Leave request not found")

    approver = db.query(Employee).filter(Employee.id == approver_id).first()
    if not approver:
        raise ApproverNotFoundError("Approver not found")

    if leave_request.employee_id == approver_id:
        raise SelfApprovalError("Employees cannot approve their own leave requests")

    employee = db.query(Employee).filter(Employee.id == leave_request.employee_id).first()
    if employee.manager_id != approver_id:
        raise ApprovalAuthorityError("Only the employee's direct manager can review this leave request")

    if leave_request.status == decision:
        return leave_request

    if leave_request.status != LeaveStatus.PENDING:
        raise InvalidStatusTransitionError("Only pending leave requests can be reviewed")

    if decision == LeaveStatus.REJECTED:
        leave_request.status = LeaveStatus.REJECTED
        db.commit()
        db.refresh(leave_request)
        return leave_request

    usage = calculate_leave_usage(
        leave_request.start_date,
        leave_request.start_session,
        leave_request.end_date,
        leave_request.end_session,
    )
    if usage.total_units != leave_request.leave_usage_units:
        raise StaleLeaveUsageError("Leave usage has changed since submission")

    for year, requested_units in usage.units_by_year.items():
        balance = _get_balance(db, leave_request.employee_id, leave_request.leave_type, year)
        if not balance:
            raise MissingLeaveBalanceError(f"Leave balance not found for {leave_request.leave_type.value} leave in {year}")

        if requested_units > balance.total_units - balance.used_units:
            raise InsufficientBalanceError("Insufficient leave balance")

        balance.used_units += requested_units

    leave_request.status = LeaveStatus.APPROVED
    leave_request.approved_by = approver_id
    leave_request.approved_at = utc_now()
    db.commit()
    db.refresh(leave_request)

    return leave_request


def cancel_leave_request(
    db: Session,
    leave_request_id: int,
    employee_id: int,
    today: Optional[date] = None,
) -> LeaveRequest:
    """
    Cancel a leave request.
    - Only the owner can cancel
    - Can only cancel PENDING or APPROVED leaves
    - Cancelling an approved leave restores balance
    """
    today = today or malaysia_today()
    leave_request = db.query(LeaveRequest).filter(LeaveRequest.id == leave_request_id).first()
    if not leave_request:
        raise LeaveRequestNotFoundError("Leave request not found")

    if leave_request.employee_id != employee_id:
        raise CancellationAuthorityError("Only the requester can cancel this leave request")

    if leave_request.status == LeaveStatus.CANCELLED:
        raise InvalidStatusTransitionError("Leave request is already cancelled")

    if leave_request.status == LeaveStatus.REJECTED:
        raise InvalidStatusTransitionError("Rejected leave requests cannot be cancelled")

    if leave_request.status == LeaveStatus.APPROVED:
        if leave_request.start_date <= today:
            raise LeaveAlreadyStartedError("Approved leave that has started cannot be cancelled")

        usage = calculate_leave_usage(
            leave_request.start_date,
            leave_request.start_session,
            leave_request.end_date,
            leave_request.end_session,
        )
        if usage.total_units != leave_request.leave_usage_units:
            raise StaleLeaveUsageError("Leave usage has changed since submission")

        for year, used_units in usage.units_by_year.items():
            balance = _get_balance(db, leave_request.employee_id, leave_request.leave_type, year)
            if not balance:
                raise MissingLeaveBalanceError(
                    f"Leave balance not found for {leave_request.leave_type.value} leave in {year}"
                )
            balance.used_units -= used_units

    if leave_request.status != LeaveStatus.PENDING and leave_request.status != LeaveStatus.APPROVED:
        raise InvalidStatusTransitionError("Only pending or approved leave requests can be cancelled")

    leave_request.status = LeaveStatus.CANCELLED
    db.commit()
    db.refresh(leave_request)

    return leave_request


def get_leave_requests(
    db: Session,
    employee_id: Optional[int] = None,
    status: Optional[LeaveStatus] = None,
    leave_type: Optional[LeaveType] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    page: int = 1,
    page_size: int = 1,
) -> tuple[list[LeaveRequest], int]:
    """
    List leave requests with filtering and pagination.
    Returns (items, total_count).
    """
    query = db.query(LeaveRequest)

    if employee_id is not None:
        query = query.filter(LeaveRequest.employee_id == employee_id)

    if status is not None:
        query = query.filter(LeaveRequest.status == status)

    if leave_type is not None:
        query = query.filter(LeaveRequest.leave_type == leave_type)

    if from_date is not None:
        query = query.filter(LeaveRequest.end_date >= from_date)

    if to_date is not None:
        query = query.filter(LeaveRequest.start_date <= to_date)

    total = query.count()
    items = (
        query.order_by(LeaveRequest.start_date, LeaveRequest.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return items, total


def get_leave_balances(
    db: Session,
    employee_id: int,
    year: Optional[int] = None,
) -> list[LeaveBalance]:
    """
    Get leave balances for an employee for a given year (defaults to current year).
    """
    year = year or malaysia_today().year

    return (
        db.query(LeaveBalance)
        .filter(
            LeaveBalance.employee_id == employee_id,
            LeaveBalance.year == year,
        )
        .order_by(LeaveBalance.leave_type)
        .all()
    )


def _get_balance(db: Session, employee_id: int, leave_type: LeaveType, year: int) -> Optional[LeaveBalance]:
    return (
        db.query(LeaveBalance)
        .filter(
            LeaveBalance.employee_id == employee_id,
            LeaveBalance.leave_type == leave_type,
            LeaveBalance.year == year,
        )
        .first()
    )


def _ensure_no_overlap(db: Session, employee_id: int, start_date: date, end_date: date, usage) -> None:
    requested_sessions = _session_keys(usage)
    candidates = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status.in_([LeaveStatus.PENDING, LeaveStatus.APPROVED]),
            LeaveRequest.start_date <= end_date,
            LeaveRequest.end_date >= start_date,
        )
        .all()
    )

    for candidate in candidates:
        candidate_usage = calculate_leave_usage(
            candidate.start_date,
            candidate.start_session,
            candidate.end_date,
            candidate.end_session,
        )
        if requested_sessions.intersection(_session_keys(candidate_usage)):
            raise OverlappingLeaveError("Leave request overlaps with an active leave request")


def _session_keys(usage) -> set[tuple[date, LeaveSession]]:
    return {
        (working_session.date, session)
        for working_session in usage.working_sessions
        for session in working_session.sessions
    }


def _pending_units_for_year(pending_requests: list[LeaveRequest], year: int) -> int:
    total_units = 0
    for pending_request in pending_requests:
        if pending_request.start_date.year == year and pending_request.end_date.year == year:
            total_units += pending_request.leave_usage_units
            continue

        usage = calculate_leave_usage(
            pending_request.start_date,
            pending_request.start_session,
            pending_request.end_date,
            pending_request.end_session,
        )
        total_units += usage.units_by_year.get(year, 0)

    return total_units


def _pending_requests_for_balance(db: Session, employee_id: int, leave_type: LeaveType) -> list[LeaveRequest]:
    return (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.leave_type == leave_type,
            LeaveRequest.status == LeaveStatus.PENDING,
        )
        .all()
    )


def seed_demo_data(db: Session) -> None:
    """Seed database with demo employees and leave balances for testing."""
    from src.models import LeaveType, LeaveBalance

    existing = db.query(Employee).first()
    if existing:
        return

    alice = Employee(name="Alice Manager", email="alice@company.com", department="Engineering")
    bob = Employee(name="Bob Engineer", email="bob@company.com", department="Engineering", manager=alice)
    carol = Employee(name="Carol Engineer", email="carol@company.com", department="Engineering", manager=alice)
    db.add_all([alice, bob, carol])
    db.flush()

    year = date.today().year
    balances = [
        LeaveBalance(employee_id=bob.id, leave_type=LeaveType.ANNUAL, year=year, total_units=28),
        LeaveBalance(employee_id=bob.id, leave_type=LeaveType.SICK, year=year, total_units=24),
        LeaveBalance(employee_id=carol.id, leave_type=LeaveType.ANNUAL, year=year, total_units=28),
        LeaveBalance(employee_id=carol.id, leave_type=LeaveType.SICK, year=year, total_units=24),
    ]
    db.add_all(balances)
    db.commit()

"""
FastAPI application for Kakitangan Leave Management System.

This is the entrypoint. Routes are defined but most business logic
in services.py needs to be implemented to make everything work.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.database import engine, get_db, Base
from src.models import LeaveType, LeaveStatus, LeaveSession
from src import services

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = next(get_db())
    try:
        services.seed_demo_data(db)
        yield
    finally:
        db.close()


app = FastAPI(title="Kakitangan Leave Management API", version="0.1.0", lifespan=lifespan)


ERROR_STATUS_CODES = {
    services.EmployeeNotFoundError: 404,
    services.LeaveRequestNotFoundError: 404,
    services.ApproverNotFoundError: 404,
    services.OverlappingLeaveError: 409,
    services.InvalidStatusTransitionError: 409,
    services.StaleLeaveUsageError: 409,
    services.LeaveAlreadyStartedError: 409,
    services.SelfApprovalError: 403,
    services.ApprovalAuthorityError: 403,
    services.CancellationAuthorityError: 403,
}


def raise_leave_http_error(error: services.LeaveError) -> None:
    raise HTTPException(status_code=ERROR_STATUS_CODES.get(type(error), 422), detail=str(error))


# ── Schemas ──────────────────────────────────────────────────────────────

class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    department: str
    manager_id: Optional[int] = None


class LeaveBalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    leave_type: LeaveType
    year: int
    total_days: float
    used_days: float
    remaining_days: float


class LeaveRequestCreate(BaseModel):
    employee_id: int
    leave_type: LeaveType
    start_date: date
    start_session: LeaveSession = LeaveSession.AM
    end_date: date
    end_session: LeaveSession = LeaveSession.PM
    reason: Optional[str] = None


class LeaveRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    leave_type: LeaveType
    start_date: date
    start_session: LeaveSession
    end_date: date
    end_session: LeaveSession
    leave_usage_days: float
    reason: Optional[str]
    status: LeaveStatus
    approved_by: Optional[int]
    approved_at: Optional[datetime]


class LeaveRequestApprove(BaseModel):
    decision: LeaveStatus = Field(description="approved or rejected")


class LeaveUsagePreviewCreate(BaseModel):
    employee_id: int
    leave_type: LeaveType
    start_date: date
    start_session: LeaveSession = LeaveSession.AM
    end_date: date
    end_session: LeaveSession = LeaveSession.PM


class PaginatedLeaveRequests(BaseModel):
    items: list[LeaveRequestOut]
    total: int
    page: int
    page_size: int


# ── Routes ───────────────────────────────────────────────────────────────

@app.get("/employees", response_model=list[EmployeeOut])
def list_employees(db: Session = Depends(get_db)):
    from src.models import Employee
    return db.query(Employee).all()


@app.get("/employees/{employee_id}", response_model=dict)
def get_employee(employee_id: int, db: Session = Depends(get_db)):
    from src.models import Employee
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    balances = services.get_leave_balances(db, employee_id)
    return {
        "employee": EmployeeOut.model_validate(emp),
        "leave_balances": [LeaveBalanceOut.model_validate(b) for b in balances],
    }


@app.post("/leave-requests", response_model=LeaveRequestOut, status_code=201)
def create_leave_request(body: LeaveRequestCreate, db: Session = Depends(get_db)):
    try:
        lr = services.create_leave_request(
            db, employee_id=body.employee_id, leave_type=body.leave_type,
            start_date=body.start_date, start_session=body.start_session,
            end_date=body.end_date, end_session=body.end_session, reason=body.reason,
        )
        return lr
    except services.LeaveError as e:
        raise_leave_http_error(e)


@app.post("/leave-requests/preview")
def preview_leave_usage(body: LeaveUsagePreviewCreate):
    try:
        usage = services.preview_leave_usage(
            start_date=body.start_date,
            start_session=body.start_session,
            end_date=body.end_date,
            end_session=body.end_session,
        )
        return {
            "leave_usage_days": usage.total_days,
            "leave_usage_by_year": {
                str(year): units / 2 for year, units in usage.units_by_year.items()
            },
            "working_sessions": [
                {
                    "date": working_session.date,
                    "sessions": [session.value for session in working_session.sessions],
                    "days": working_session.days,
                }
                for working_session in usage.working_sessions
            ],
            "excluded_dates": [
                {
                    "date": excluded_date.date,
                    "reason": excluded_date.reason,
                    **({"name": excluded_date.name} if excluded_date.name else {}),
                }
                for excluded_date in usage.excluded_dates
            ],
        }
    except services.LeaveError as e:
        raise_leave_http_error(e)


@app.get("/leave-requests", response_model=PaginatedLeaveRequests)
def list_leave_requests(
    employee_id: Optional[int] = Query(None),
    status: Optional[LeaveStatus] = Query(None),
    leave_type: Optional[LeaveType] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items, total = services.get_leave_requests(
        db, employee_id=employee_id, status=status, leave_type=leave_type,
        from_date=from_date, to_date=to_date, page=page, page_size=page_size,
    )
    return PaginatedLeaveRequests(
        items=[LeaveRequestOut.model_validate(i) for i in items],
        total=total, page=page, page_size=page_size,
    )


@app.get("/leave-requests/{leave_request_id}", response_model=LeaveRequestOut)
def get_leave_request(leave_request_id: int, db: Session = Depends(get_db)):
    from src.models import LeaveRequest
    lr = db.query(LeaveRequest).filter(LeaveRequest.id == leave_request_id).first()
    if not lr:
        raise HTTPException(status_code=404, detail="Leave request not found")
    return lr


@app.post("/leave-requests/{leave_request_id}/review", response_model=LeaveRequestOut)
def review_leave_request(
    leave_request_id: int,
    body: LeaveRequestApprove,
    db: Session = Depends(get_db),
):
    try:
        lr = services.approve_leave_request(
            db, leave_request_id=leave_request_id, approver_id=1, decision=body.decision,
        )
        return lr
    except services.LeaveError as e:
        raise_leave_http_error(e)


@app.post("/leave-requests/{leave_request_id}/cancel", response_model=LeaveRequestOut)
def cancel_leave_request(leave_request_id: int, employee_id: int = Query(...), db: Session = Depends(get_db)):
    try:
        lr = services.cancel_leave_request(db, leave_request_id=leave_request_id, employee_id=employee_id)
        return lr
    except services.LeaveError as e:
        raise_leave_http_error(e)


@app.get("/leave-balances/{employee_id}", response_model=list[LeaveBalanceOut])
def get_balance(employee_id: int, year: Optional[int] = Query(None), db: Session = Depends(get_db)):
    balances = services.get_leave_balances(db, employee_id=employee_id, year=year)
    return [LeaveBalanceOut.model_validate(b) for b in balances]


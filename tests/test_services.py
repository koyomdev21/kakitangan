"""
Tests for the leave management service layer.

These tests are currently empty and use placeholder asserts.
When you implement src/services.py, write real tests here.

Consider:
- What happens when you try to create overlapping leave?
- What happens when balance is insufficient?
- Can an employee approve their own leave?
- Can you cancel an already-cancelled leave?
- Does cancelling an approved leave restore the balance?
"""

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models import LeaveBalance, LeaveType, LeaveStatus, LeaveSession
from src.services import (
    seed_demo_data,
    create_leave_request,
    approve_leave_request,
    cancel_leave_request,
    get_leave_requests,
    get_leave_balances,
    InsufficientBalanceError,
    OverlappingLeaveError,
    SelfApprovalError,
    InvalidLeavePeriodError,
    NoWorkingSessionsError,
    MissingLeaveBalanceError,
    ApprovalAuthorityError,
    InvalidStatusTransitionError,
)


class TestLeaveServices(unittest.TestCase):
    """You must implement and expand these tests."""

    @classmethod
    def setUpClass(cls):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        cls.Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def setUp(self):
        self.db = self.Session()
        Base.metadata.drop_all(bind=self.db.get_bind())
        Base.metadata.create_all(bind=self.db.get_bind())
        seed_demo_data(self.db)
        # Fetch demo employees
        from src.models import Employee
        self.alice = self.db.query(Employee).filter(Employee.email == "alice@company.com").first()
        self.bob = self.db.query(Employee).filter(Employee.email == "bob@company.com").first()
        self.carol = self.db.query(Employee).filter(Employee.email == "carol@company.com").first()

    def tearDown(self):
        self.db.close()

    def test_create_leave_request(self):
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            today=date(2026, 6, 1),
        )

        self.assertEqual(leave_request.status, LeaveStatus.PENDING)
        self.assertEqual(leave_request.start_session, LeaveSession.AM)
        self.assertEqual(leave_request.end_session, LeaveSession.PM)
        self.assertEqual(leave_request.leave_usage_units, 2)

        balances = get_leave_balances(self.db, employee_id=self.bob.id, year=2026)
        annual_balance = next(b for b in balances if b.leave_type == LeaveType.ANNUAL)
        self.assertEqual(annual_balance.total_units, 28)
        self.assertEqual(annual_balance.used_units, 0)
        self.assertEqual(annual_balance.remaining_days, 14.0)

    def test_create_leave_request_insufficient_balance(self):
        annual_balance = next(
            balance
            for balance in get_leave_balances(self.db, employee_id=self.bob.id, year=2026)
            if balance.leave_type == LeaveType.ANNUAL
        )
        annual_balance.total_units = 2
        self.db.commit()

        create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            start_session=LeaveSession.AM,
            end_session=LeaveSession.AM,
            today=date(2026, 6, 1),
        )

        with self.assertRaises(InsufficientBalanceError):
            create_leave_request(
                self.db,
                employee_id=self.bob.id,
                leave_type=LeaveType.ANNUAL,
                start_date=date(2026, 6, 11),
                end_date=date(2026, 6, 11),
                today=date(2026, 6, 1),
            )

    def test_create_leave_request_rejects_missing_balance_year(self):
        with self.assertRaises(MissingLeaveBalanceError):
            create_leave_request(
                self.db,
                employee_id=self.bob.id,
                leave_type=LeaveType.ANNUAL,
                start_date=date(2027, 1, 5),
                end_date=date(2027, 1, 5),
                today=date(2026, 12, 1),
            )

    def test_create_leave_request_rejects_backdated_start_date(self):
        with self.assertRaises(InvalidLeavePeriodError):
            create_leave_request(
                self.db,
                employee_id=self.bob.id,
                leave_type=LeaveType.ANNUAL,
                start_date=date(2026, 5, 31),
                end_date=date(2026, 6, 1),
                today=date(2026, 6, 1),
            )

    def test_create_leave_request_overlapping_dates(self):
        create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            start_session=LeaveSession.AM,
            end_session=LeaveSession.AM,
            today=date(2026, 6, 1),
        )

        with self.assertRaises(OverlappingLeaveError):
            create_leave_request(
                self.db,
                employee_id=self.bob.id,
                leave_type=LeaveType.ANNUAL,
                start_date=date(2026, 6, 10),
                end_date=date(2026, 6, 10),
                start_session=LeaveSession.AM,
                end_session=LeaveSession.AM,
                today=date(2026, 6, 1),
            )

    def test_create_leave_request_allows_different_session_same_day(self):
        morning_leave = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            start_session=LeaveSession.AM,
            end_session=LeaveSession.AM,
            today=date(2026, 6, 1),
        )
        afternoon_leave = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            start_session=LeaveSession.PM,
            end_session=LeaveSession.PM,
            today=date(2026, 6, 1),
        )

        self.assertEqual(morning_leave.leave_usage_units, 1)
        self.assertEqual(afternoon_leave.leave_usage_units, 1)

    def test_create_leave_request_rejects_invalid_or_non_working_periods(self):
        with self.assertRaises(InvalidLeavePeriodError):
            create_leave_request(
                self.db,
                employee_id=self.bob.id,
                leave_type=LeaveType.ANNUAL,
                start_date=date(2026, 6, 10),
                end_date=date(2026, 6, 10),
                start_session=LeaveSession.PM,
                end_session=LeaveSession.AM,
                today=date(2026, 6, 1),
            )

        with self.assertRaises(NoWorkingSessionsError):
            create_leave_request(
                self.db,
                employee_id=self.bob.id,
                leave_type=LeaveType.ANNUAL,
                start_date=date(2026, 6, 13),
                end_date=date(2026, 6, 14),
                today=date(2026, 6, 1),
            )

    def test_create_leave_request_counts_half_day_boundaries(self):
        am_leave = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            start_session=LeaveSession.AM,
            end_session=LeaveSession.AM,
            today=date(2026, 6, 1),
        )
        pm_leave = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 11),
            end_date=date(2026, 6, 11),
            start_session=LeaveSession.PM,
            end_session=LeaveSession.PM,
            today=date(2026, 6, 1),
        )
        full_day_leave = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 12),
            end_date=date(2026, 6, 12),
            start_session=LeaveSession.AM,
            end_session=LeaveSession.PM,
            today=date(2026, 6, 1),
        )

        self.assertEqual(am_leave.leave_usage_days, 0.5)
        self.assertEqual(pm_leave.leave_usage_days, 0.5)
        self.assertEqual(full_day_leave.leave_usage_days, 1.0)

    def test_create_leave_request_excludes_holiday_from_mixed_range(self):
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 8, 31),
            end_date=date(2026, 9, 1),
            today=date(2026, 8, 1),
        )

        self.assertEqual(leave_request.leave_usage_units, 2)
        self.assertEqual(leave_request.leave_usage_days, 1.0)

    def test_create_leave_request_splits_cross_year_usage_on_approval(self):
        self.db.add(LeaveBalance(employee_id=self.bob.id, leave_type=LeaveType.ANNUAL, year=2027, total_units=28))
        self.db.commit()
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 12, 31),
            end_date=date(2027, 1, 2),
            start_session=LeaveSession.PM,
            end_session=LeaveSession.PM,
            today=date(2026, 12, 1),
        )

        self.assertEqual(leave_request.leave_usage_units, 3)

        approve_leave_request(
            self.db,
            leave_request_id=leave_request.id,
            approver_id=self.alice.id,
            decision=LeaveStatus.APPROVED,
        )

        balances_2026 = get_leave_balances(self.db, employee_id=self.bob.id, year=2026)
        balances_2027 = get_leave_balances(self.db, employee_id=self.bob.id, year=2027)
        annual_2026 = next(balance for balance in balances_2026 if balance.leave_type == LeaveType.ANNUAL)
        annual_2027 = next(balance for balance in balances_2027 if balance.leave_type == LeaveType.ANNUAL)
        self.assertEqual(annual_2026.used_units, 1)
        self.assertEqual(annual_2027.used_units, 2)

    def test_approve_leave_request(self):
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            today=date(2026, 6, 1),
        )

        reviewed = approve_leave_request(
            self.db,
            leave_request_id=leave_request.id,
            approver_id=self.alice.id,
            decision=LeaveStatus.APPROVED,
        )

        self.assertEqual(reviewed.status, LeaveStatus.APPROVED)
        self.assertEqual(reviewed.approved_by, self.alice.id)
        self.assertIsNotNone(reviewed.approved_at)

        annual_balance = next(
            balance
            for balance in get_leave_balances(self.db, employee_id=self.bob.id, year=2026)
            if balance.leave_type == LeaveType.ANNUAL
        )
        self.assertEqual(annual_balance.used_units, 2)
        self.assertEqual(annual_balance.remaining_days, 13.0)

    def test_approve_leave_request_is_idempotent_for_same_decision(self):
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            today=date(2026, 6, 1),
        )

        approve_leave_request(
            self.db,
            leave_request_id=leave_request.id,
            approver_id=self.alice.id,
            decision=LeaveStatus.APPROVED,
        )
        approve_leave_request(
            self.db,
            leave_request_id=leave_request.id,
            approver_id=self.alice.id,
            decision=LeaveStatus.APPROVED,
        )

        annual_balance = next(
            balance
            for balance in get_leave_balances(self.db, employee_id=self.bob.id, year=2026)
            if balance.leave_type == LeaveType.ANNUAL
        )
        self.assertEqual(annual_balance.used_units, 2)

    def test_reject_leave_request_does_not_change_balance_and_is_idempotent(self):
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            today=date(2026, 6, 1),
        )

        rejected = approve_leave_request(
            self.db,
            leave_request_id=leave_request.id,
            approver_id=self.alice.id,
            decision=LeaveStatus.REJECTED,
        )
        rejected_again = approve_leave_request(
            self.db,
            leave_request_id=leave_request.id,
            approver_id=self.alice.id,
            decision=LeaveStatus.REJECTED,
        )

        self.assertEqual(rejected.status, LeaveStatus.REJECTED)
        self.assertEqual(rejected_again.status, LeaveStatus.REJECTED)
        annual_balance = next(
            balance
            for balance in get_leave_balances(self.db, employee_id=self.bob.id, year=2026)
            if balance.leave_type == LeaveType.ANNUAL
        )
        self.assertEqual(annual_balance.used_units, 0)

    def test_non_manager_approval_rejected(self):
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            today=date(2026, 6, 1),
        )

        with self.assertRaises(ApprovalAuthorityError):
            approve_leave_request(
                self.db,
                leave_request_id=leave_request.id,
                approver_id=self.carol.id,
                decision=LeaveStatus.APPROVED,
            )

    def test_self_approval_rejected(self):
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            today=date(2026, 6, 1),
        )

        with self.assertRaises(SelfApprovalError):
            approve_leave_request(
                self.db,
                leave_request_id=leave_request.id,
                approver_id=self.bob.id,
                decision=LeaveStatus.APPROVED,
            )

    def test_cancel_approved_leave_restores_balance(self):
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            today=date(2026, 6, 1),
        )
        approve_leave_request(
            self.db,
            leave_request_id=leave_request.id,
            approver_id=self.alice.id,
            decision=LeaveStatus.APPROVED,
        )

        cancelled = cancel_leave_request(
            self.db,
            leave_request_id=leave_request.id,
            employee_id=self.bob.id,
            today=date(2026, 6, 1),
        )

        self.assertEqual(cancelled.status, LeaveStatus.CANCELLED)
        annual_balance = next(
            balance
            for balance in get_leave_balances(self.db, employee_id=self.bob.id, year=2026)
            if balance.leave_type == LeaveType.ANNUAL
        )
        self.assertEqual(annual_balance.used_units, 0)
        self.assertEqual(annual_balance.remaining_days, 14.0)

    def test_cancel_pending_leave_does_not_change_balance_and_cannot_repeat(self):
        leave_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            today=date(2026, 6, 1),
        )

        cancelled = cancel_leave_request(
            self.db,
            leave_request_id=leave_request.id,
            employee_id=self.bob.id,
            today=date(2026, 6, 1),
        )

        self.assertEqual(cancelled.status, LeaveStatus.CANCELLED)
        annual_balance = next(
            balance
            for balance in get_leave_balances(self.db, employee_id=self.bob.id, year=2026)
            if balance.leave_type == LeaveType.ANNUAL
        )
        self.assertEqual(annual_balance.used_units, 0)

        with self.assertRaises(InvalidStatusTransitionError):
            cancel_leave_request(
                self.db,
                leave_request_id=leave_request.id,
                employee_id=self.bob.id,
                today=date(2026, 6, 1),
            )

    def test_list_leave_requests_with_filters(self):
        annual_request = create_leave_request(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            today=date(2026, 6, 1),
        )
        create_leave_request(
            self.db,
            employee_id=self.carol.id,
            leave_type=LeaveType.SICK,
            start_date=date(2026, 6, 11),
            end_date=date(2026, 6, 11),
            today=date(2026, 6, 1),
        )

        items, total = get_leave_requests(
            self.db,
            employee_id=self.bob.id,
            leave_type=LeaveType.ANNUAL,
            status=LeaveStatus.PENDING,
            from_date=date(2026, 6, 1),
            to_date=date(2026, 6, 30),
            page=1,
            page_size=10,
        )

        self.assertEqual(total, 1)
        self.assertEqual([item.id for item in items], [annual_request.id])

    def test_get_leave_balances(self):
        balances = get_leave_balances(self.db, employee_id=self.bob.id, year=2026)

        annual_balance = next(balance for balance in balances if balance.leave_type == LeaveType.ANNUAL)
        self.assertEqual(annual_balance.total_units, 28)
        self.assertEqual(annual_balance.used_units, 0)
        self.assertEqual(annual_balance.total_days, 14.0)
        self.assertEqual(annual_balance.used_days, 0.0)
        self.assertEqual(annual_balance.remaining_days, 14.0)


if __name__ == "__main__":
    unittest.main()

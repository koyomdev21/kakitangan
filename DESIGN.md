# Design Document - Leave Management System

**Author:** koyomdev21
**Date:** 2026-06-03

## 1. API Design

### Create Leave Request

```http
POST /leave-requests
```

Request body:

```json
{
  "employee_id": 2,
  "leave_type": "annual",
  "start_date": "2026-06-10",
  "start_session": "pm",
  "end_date": "2026-06-12",
  "end_session": "pm",
  "reason": "Family commitment"
}
```

`start_session` and `end_session` are optional. When omitted, the request defaults to a full-day period by using `start_session = "am"` and `end_session = "pm"`.

Response body:

```json
{
  "id": 10,
  "employee_id": 2,
  "leave_type": "annual",
  "start_date": "2026-06-10",
  "start_session": "pm",
  "end_date": "2026-06-12",
  "end_session": "pm",
  "leave_usage_days": 2.5,
  "reason": "Family commitment",
  "status": "pending",
  "approved_by": null,
  "approved_at": null
}
```

Expected status codes:

- `201 Created` when the leave request is accepted.
- `422 Unprocessable Entity` for invalid date/session ranges, overlapping leave, missing balance, or insufficient available leave.
- `404 Not Found` if the employee does not exist.

### Preview Leave Usage

```http
POST /leave-requests/preview
```

Request body uses the same date and session fields as `POST /leave-requests`.

Response body:

```json
{
  "leave_usage_days": 2.5,
  "leave_usage_by_year": {
    "2026": 2.5
  },
  "working_sessions": [
    { "date": "2026-06-10", "sessions": ["pm"], "days": 0.5 },
    { "date": "2026-06-11", "sessions": ["am", "pm"], "days": 1.0 },
    { "date": "2026-06-12", "sessions": ["am", "pm"], "days": 1.0 }
  ],
  "excluded_dates": [
    { "date": "2026-06-13", "reason": "weekend" },
    { "date": "2026-08-31", "reason": "public_holiday", "name": "National Day" }
  ]
}
```

The preview endpoint does not create a leave request, reserve leave, or mutate balances. It exists so the frontend can show employees exactly how much leave their selected period would consume.

### Review Leave Request

```http
POST /leave-requests/{leave_request_id}/review
```

Request body:

```json
{
  "decision": "approved"
}
```

Only `approved` and `rejected` are valid review decisions.

Expected status codes:

- `200 OK` when the pending request is approved or rejected.
- `200 OK` when retrying the same final decision for an already approved or rejected request.
- `422 Unprocessable Entity` for invalid transitions, self-approval, non-manager approval, insufficient balance, or changed leave usage.
- `404 Not Found` if the leave request or approver does not exist.

### Cancel Leave Request

```http
POST /leave-requests/{leave_request_id}/cancel?employee_id=2
```

Only the leave request owner can cancel. Pending leave can be cancelled without balance changes. Approved leave can be cancelled only before the leave start date, and cancellation restores the deducted balance.

Expected status codes:

- `200 OK` when cancellation succeeds.
- `422 Unprocessable Entity` when the employee is not the owner, the request is already rejected/cancelled, or the approved leave has started.
- `404 Not Found` if the leave request does not exist.

### List Leave Requests

```http
GET /leave-requests
```

Supported filters:

- `employee_id`
- `status`
- `leave_type`
- `from_date`
- `to_date`
- `page`
- `page_size`

The listing response includes `leave_usage_days` so clients do not need to recalculate leave usage for every row.

### Get Leave Balances

```http
GET /leave-balances/{employee_id}?year=2026
```

Balances are exposed in days:

```json
[
  {
    "leave_type": "annual",
    "year": 2026,
    "total_days": 14.0,
    "used_days": 3.5,
    "remaining_days": 10.5
  }
]
```

## 2. Data Model

### employees

Existing employee table:

- `id`
- `name`
- `email`
- `department`
- `manager_id`
- timestamps

`manager_id` identifies the direct manager who can review the employee's leave requests.

### leave_requests

Leave requests should store the selected period and the calculated usage:

- `id`
- `employee_id`
- `leave_type`
- `start_date`
- `start_session`
- `end_date`
- `end_session`
- `leave_usage_days`
- `reason`
- `status`
- `approved_by`
- `approved_at`
- timestamps

`start_session` and `end_session` are enum values: `am` and `pm`.

`leave_usage_days` is persisted because managers and listing screens need to show the amount of leave consumed without recalculating every row. Approval recalculates usage before deduction; if recalculated usage differs from the stored value, approval fails and the employee must resubmit.

### leave_balances

Balances remain stored and exposed in days:

- `id`
- `employee_id`
- `leave_type`
- `year`
- `total_days`
- `used_days`
- timestamps

Cross-year requests split usage by year. Each affected year must have a balance row for the requested leave type.

## 3. Leave Usage Rules

Leave is calculated from working sessions.

A working day has two leave sessions:

- `am` = 0.5 days
- `pm` = 0.5 days

Valid session ranges:

- Same-day `am -> am`
- Same-day `am -> pm`
- Same-day `pm -> pm`
- Multi-day `pm -> am`
- Any multi-day combination where `start_date < end_date`

Invalid session ranges:

- `start_date > end_date`
- Same-day `pm -> am`

Working sessions exclude:

- Saturdays
- Sundays
- Malaysia national public holidays from the `holidays` package

If the selected range contains no working sessions, the request is rejected.

## 4. Balance Rules

The system uses a hybrid balance model:

- Creating a leave request validates against available leave but does not deduct `used_days`.
- Available leave means remaining balance after approved usage and pending requests.
- Approving a leave request deducts from `used_days`.
- Rejecting a leave request does not change balance.
- Cancelling pending leave does not change balance.
- Cancelling approved leave restores the deducted balance.

Approval re-checks balance inside the approval workflow to protect against stale pending requests and concurrent approvals.

Cross-year leave usage is split by leave year. For example, a request from `2026-12-31 pm` to `2027-01-02 pm` consumes the 2026 and 2027 balances separately.

Missing yearly balance rows are validation failures. The leave workflow does not auto-create balances because entitlement creation is an HR setup concern.

## 5. Overlap Rules

Overlap validation is session-based.

Two leave requests overlap when they belong to the same employee and include at least one identical working session.

Statuses considered active:

- `pending`
- `approved`

Statuses ignored for overlap:

- `rejected`
- `cancelled`

Examples:

- Existing `2026-06-10 am` does not block new `2026-06-10 pm`.
- Existing `2026-06-10 am` blocks new `2026-06-10 am`.
- A weekend-only selected range is rejected because it has no working sessions, not because of overlap.
- A public-holiday-only selected range is rejected because it has no working sessions.

## 6. Review and Cancellation Rules

Only the employee's direct manager can approve or reject a leave request.

Review transitions:

- `pending -> approved`
- `pending -> rejected`
- `approved -> approved` is idempotent and does not deduct again.
- `rejected -> rejected` is idempotent.

Invalid review transitions:

- `approved -> rejected`
- `rejected -> approved`
- `cancelled -> approved`
- `cancelled -> rejected`

Cancellation transitions:

- `pending -> cancelled`
- `approved -> cancelled`, only before the leave start date

Rejected and cancelled leave requests are final.

## 7. Edge Cases Identified

- Half-day leave uses explicit `am` and `pm` sessions.
- Full-day leave is represented as `am -> pm`, not as a separate API enum value.
- Existing full-day API requests remain valid because session fields default to `am` and `pm`.
- Weekend and Malaysia public holiday sessions do not consume leave.
- Preview and create use the same leave usage calculator.
- Pending requests block overlapping new requests.
- Pending requests count against available leave but do not mutate balance.
- Approved cancellation restores balance.
- Same-decision review retries are idempotent.
- Cross-year leave validates and deducts each leave year separately.
- Missing balance rows reject the request rather than being auto-created.
- Approval recalculates usage and rejects if holiday/calendar changes would alter the stored usage.

## 8. Tradeoffs and Decisions

### Expose days, calculate by sessions

The API exposes leave usage and balances in days because that is the language employees and managers expect. Internally, the system reasons in half-day sessions so `0.5`, `1.5`, and cross-day ranges are unambiguous.

### Use sessions instead of a `full_day` enum

The API accepts only `am` and `pm` for sessions. A full day is represented by `start_session = "am"` and `end_session = "pm"`. This keeps overlap and duration calculation consistent because every request can be reduced to the same session model.

### Use Malaysia national public holidays from a package

The system uses the `holidays` package for Malaysia national public holidays instead of hardcoding dates. State-specific holidays are deferred because the current employee model has no work location, state, branch, or assigned leave calendar. This keeps the backend as the source of truth for leave usage and lets the frontend ask the backend for a usage preview.

### Use direct-manager-only approval

The current employee model has a `manager_id` but no roles or permission model. Direct-manager-only approval fits the existing model and avoids inventing admin or HR authority outside the challenge scope.

### Reject changed usage on approval

Leave usage is stored when the request is created, but approval recalculates usage before balance deduction. If the value changes, approval is rejected. This avoids silently deducting a different amount from what the employee submitted and what the manager reviewed.

## 9. What I Would Do With More Time

- Add company-specific holiday calendars and state-level Malaysia holiday configuration once employees have a work location or assigned leave calendar.
- Add role-based approval rules for HR/admin overrides.
- Add database-level protection for concurrent approvals, such as row locking or optimistic versioning.
- Add audit events for approval, rejection, cancellation, and balance restoration.
- Add a dedicated entitlement setup workflow for yearly leave balances.
- Add calendar integration so approved leave can appear in shared calendars.

## 10. Running the Project

```bash
# Install
pip install -r requirements.txt

# Run
uvicorn src.app:app --reload

# Test
python -m pytest tests/
```

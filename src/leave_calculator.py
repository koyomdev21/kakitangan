from dataclasses import dataclass
from datetime import date, timedelta

import holidays

from src.models import LeaveSession


@dataclass(frozen=True)
class WorkingSession:
    date: date
    sessions: tuple[LeaveSession, ...]

    @property
    def units(self) -> int:
        return len(self.sessions)

    @property
    def days(self) -> float:
        return self.units / 2


@dataclass(frozen=True)
class ExcludedDate:
    date: date
    reason: str
    name: str | None = None


@dataclass(frozen=True)
class LeaveUsageResult:
    total_units: int
    units_by_year: dict[int, int]
    working_sessions: tuple[WorkingSession, ...]
    excluded_dates: tuple[ExcludedDate, ...]

    @property
    def total_days(self) -> float:
        return self.total_units / 2


SESSION_ORDER = {
    LeaveSession.AM: 0,
    LeaveSession.PM: 1,
}


def calculate_leave_usage(
    start_date: date,
    start_session: LeaveSession,
    end_date: date,
    end_session: LeaveSession,
) -> LeaveUsageResult:
    years = range(start_date.year, end_date.year + 1)
    malaysia_holidays = holidays.country_holidays("MY", years=years)
    working_sessions: list[WorkingSession] = []
    excluded_dates: list[ExcludedDate] = []

    current_date = start_date
    while current_date <= end_date:
        if current_date.weekday() >= 5:
            excluded_dates.append(ExcludedDate(date=current_date, reason="weekend"))
        elif current_date in malaysia_holidays:
            excluded_dates.append(
                ExcludedDate(
                    date=current_date,
                    reason="public_holiday",
                    name=str(malaysia_holidays[current_date]),
                )
            )
        else:
            sessions = _sessions_for_date(current_date, start_date, start_session, end_date, end_session)
            if sessions:
                working_sessions.append(WorkingSession(date=current_date, sessions=tuple(sessions)))

        current_date += timedelta(days=1)

    units_by_year: dict[int, int] = {}
    for working_session in working_sessions:
        units_by_year[working_session.date.year] = (
            units_by_year.get(working_session.date.year, 0) + working_session.units
        )

    return LeaveUsageResult(
        total_units=sum(units_by_year.values()),
        units_by_year=units_by_year,
        working_sessions=tuple(working_sessions),
        excluded_dates=tuple(excluded_dates),
    )


def _sessions_for_date(
    current_date: date,
    start_date: date,
    start_session: LeaveSession,
    end_date: date,
    end_session: LeaveSession,
) -> list[LeaveSession]:
    sessions = [LeaveSession.AM, LeaveSession.PM]

    if current_date == start_date:
        sessions = [session for session in sessions if SESSION_ORDER[session] >= SESSION_ORDER[start_session]]

    if current_date == end_date:
        sessions = [session for session in sessions if SESSION_ORDER[session] <= SESSION_ORDER[end_session]]

    return sessions

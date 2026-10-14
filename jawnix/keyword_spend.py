"""Daily spend reservations committed before native inference requests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .anthropic_generation import KeywordSpendError, money
from .models import KeywordGenerationCharge


class DatabaseKeywordBudget:
    def __init__(
        self, session_factory, cap: Decimal, *, timezone_name="UTC", clock=None
    ):
        self.session_factory = session_factory
        self.cap = money(cap)
        self.timezone = ZoneInfo(timezone_name)
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _lock(session, day):
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                select(
                    func.pg_advisory_xact_lock(0x4A41574E, int(day.strftime("%Y%m%d")))
                )
            )
        elif session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            raise KeywordSpendError(
                "Daily keyword accounting requires PostgreSQL or SQLite"
            )

    def reserve(self, amount, *, trial, payload_sha256):
        amount = money(amount)
        now = self.clock()
        day = now.astimezone(self.timezone).date()
        with self.session_factory.begin() as session:
            self._lock(session, day)
            unknown = session.scalar(
                select(func.count())
                .select_from(KeywordGenerationCharge)
                .where(
                    KeywordGenerationCharge.budget_day == day,
                    KeywordGenerationCharge.status == "uncertain",
                    KeywordGenerationCharge.metered_usd.is_not(None),
                )
            )
            if unknown:
                raise KeywordSpendError(
                    "A request's cost is uncertain; further spending is blocked"
                )
            committed = session.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            func.coalesce(
                                KeywordGenerationCharge.metered_usd,
                                KeywordGenerationCharge.reserved_usd,
                            )
                        ),
                        0,
                    )
                ).where(KeywordGenerationCharge.budget_day == day)
            )
            if money(committed) + amount > self.cap:
                raise KeywordSpendError(
                    "Daily keyword spending limit reached before the next request"
                )
            charge = KeywordGenerationCharge(
                id=uuid.uuid4(),
                budget_day=day,
                model="claude-haiku-5-5",
                purpose=trial,
                reserved_usd=amount,
                status="pending",
                details={"payload_sha256": payload_sha256},
                created_at=now,
            )
            session.add(charge)
            request_id = str(charge.id)
        return request_id

    def settle(self, request_id, *, cost, metadata):
        blocked = None
        with self.session_factory.begin() as session:
            charge = session.get(KeywordGenerationCharge, uuid.UUID(request_id))
            if charge is None:
                raise KeywordSpendError("Keyword request reservation is missing")
            self._lock(session, charge.budget_day)
            session.refresh(charge, with_for_update=True)
            if charge.status != "pending":
                raise KeywordSpendError("Request accounting was already finalized")
            charge.details = {**charge.details, **metadata}
            if cost is None:
                charge.status = "uncertain"
                charge.details = {
                    **charge.details,
                    "accounted_at_reserved_upper_bound": True,
                }
            else:
                cost = money(cost)
                charge.metered_usd = cost
                charge.status = "settled"
                if cost > charge.reserved_usd:
                    charge.status = "uncertain"
                    blocked = "Metered cost exceeded its reserved upper bound"
        if blocked:
            raise KeywordSpendError(blocked)

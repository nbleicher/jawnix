"""The Lead Disposition catalog pins what each choice does (#53).

The load-bearing test here is
``test_the_catalog_cannot_drift_from_what_is_enforced``. The consequences are
irreversible. Copy maintained separately from ``apply_disposition_controls``
would eventually disagree with it, and the failure mode is telling someone
their answer is harmless when it places an Eligibility Hold. So the catalog is
derived from the same constants the controls are materialized from, and this
file pins that.
"""

from __future__ import annotations

from jawnix.dispositions import CATALOG, GROUP_ORDER
from jawnix.feedback import HOLD_DISPOSITIONS, REPORT_DISPOSITIONS
from jawnix.schemas import FeedbackCreate


class TestCatalogIntegrity:
    def test_the_catalog_cannot_drift_from_what_is_enforced(self):
        """Every stated consequence matches the rule that materializes it."""
        for entry in CATALOG:
            creates_report = entry.disposition in REPORT_DISPOSITIONS
            creates_hold = entry.disposition in HOLD_DISPOSITIONS
            assert entry.creates_report is creates_report, entry.disposition
            assert entry.creates_hold is creates_hold, entry.disposition

    def test_a_hold_is_never_claimed_without_a_report(self):
        """A hold hangs off a report, so claiming one without the other lies."""
        for entry in CATALOG:
            if entry.creates_hold:
                assert entry.creates_report, entry.disposition

    def test_wrong_business_reports_without_holding(self):
        """The distinction easiest to collapse, stated explicitly."""
        entry = next(
            item for item in CATALOG if item.disposition == "wrong_business"
        )
        assert entry.creates_report is True
        assert entry.creates_hold is False

    def test_invalid_phone_and_do_not_contact_report_and_hold(self):
        for disposition in ("invalid_phone", "do_not_contact"):
            entry = next(
                item for item in CATALOG if item.disposition == disposition
            )
            assert entry.creates_report is True, disposition
            assert entry.creates_hold is True, disposition

    def test_every_accepted_disposition_is_offered(self):
        """The screen cannot omit a disposition the contract accepts."""
        accepted = set(
            FeedbackCreate.model_fields["disposition"]
            .metadata[0]
            .pattern.strip("^$()")
            .replace("|", " ")
            .split()
        )
        assert {entry.disposition for entry in CATALOG} == accepted

    def test_no_disposition_is_offered_that_would_be_refused(self):
        for entry in CATALOG:
            payload = {
                "distribution_event_id": 1,
                "disposition": entry.disposition,
                "note": "A note, in case this disposition requires one.",
            }
            # Constructing the real request model is the check: an offered
            # disposition the contract rejects would raise here.
            FeedbackCreate(**payload)

    def test_only_other_requires_a_note(self):
        for entry in CATALOG:
            assert entry.requires_note is (entry.disposition == "other"), (
                entry.disposition
            )

    def test_every_group_is_named_and_ordered(self):
        assert [group for group, _ in GROUP_ORDER] == [
            "contact_result",
            "positive_progress",
            "appointment_follow_up",
            "data_compliance_problem",
            "other",
        ]
        grouped = {entry.group for entry in CATALOG}
        assert grouped == {group for group, _ in GROUP_ORDER}

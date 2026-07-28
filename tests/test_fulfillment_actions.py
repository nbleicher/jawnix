"""The single authority on which Fulfillment actions a Batch Request offers.

Issue #57 states the rule these tests exist to hold: an invalid action offered
is a bug, not a UI detail. The offer surface and the enforcement surface must
therefore be the same table, so a screen cannot drift into rendering a button
the domain refuses.
"""

from __future__ import annotations

import pytest

from jawnix.fulfillment import (
    ACTIONS,
    RequestActionContext,
    action_named,
    available_action_names,
    available_actions,
)
from jawnix.models import RequestStatus


def context(status: str, **overrides) -> RequestActionContext:
    """A Batch Request with no artifact and no committed distribution."""
    defaults = {
        "status": status,
        "has_artifact": False,
        "artifact_available": False,
        "distribution_complete": False,
        "telegram_decision_pending": False,
    }
    return RequestActionContext(**{**defaults, **overrides})


class TestValidStates:
    """CONTEXT.md and transitions.py agree on these; the table must too."""

    def test_pending_offers_approve_reject_and_cancel(self):
        assert available_action_names(context(RequestStatus.pending.value)) == (
            "approve",
            "reject",
            "cancel",
        )

    def test_waiting_inventory_offers_retry_reject_and_cancel(self):
        # Approve is spent: Request Approval authorizes the *first* attempt.
        assert available_action_names(
            context(RequestStatus.waiting_inventory.value)
        ) == ("retry", "reject", "cancel")

    def test_approved_offers_only_cancel(self):
        # Nothing to approve or retry while allocation is queued, but a
        # Canceled Request is still reachable before distribution commits.
        assert available_action_names(context(RequestStatus.approved.value)) == (
            "cancel",
        )

    @pytest.mark.parametrize(
        "status",
        [
            RequestStatus.rejected.value,
            RequestStatus.canceled.value,
            RequestStatus.delivered.value,
            RequestStatus.processing.value,
        ],
    )
    def test_terminal_and_in_flight_states_offer_nothing(self, status):
        assert available_action_names(context(status)) == ()

    def test_generated_offers_nothing_until_delivery_resolves(self):
        assert available_action_names(
            context(RequestStatus.generated.value, has_artifact=True, artifact_available=True)
        ) == ()


class TestDeliveryRecovery:
    """The distinction #57 calls out and warns against collapsing."""

    def test_failed_with_an_artifact_retries_the_exact_artifact_only(self):
        # Re-running allocation here would consume *more* inventory for a
        # request whose Distribution Events are already permanent
        # (docs/adr/0003). Only the exact-artifact path is safe.
        names = available_action_names(
            context(
                RequestStatus.failed.value,
                has_artifact=True,
                artifact_available=True,
                distribution_complete=True,
            )
        )
        assert "retry_delivery" in names
        assert "retry" not in names

    def test_failed_without_an_artifact_retries_generation_only(self):
        names = available_action_names(context(RequestStatus.failed.value))
        assert "retry" in names
        assert "retry_delivery" not in names

    def test_the_two_recovery_actions_describe_different_consequences(self):
        retry = action_named("retry")
        retry_delivery = action_named("retry_delivery")

        assert retry.consequence != retry_delivery.consequence
        assert "allocat" in retry.consequence.lower()
        assert "exact" in retry_delivery.consequence.lower()
        # Naming them alike is how the two get collapsed in a UI.
        assert retry.label != retry_delivery.label


class TestArtifactRegeneration:
    """Regeneration rebuilds the exact expired file; it is not a retry."""

    def test_regenerate_needs_complete_distribution_and_an_expired_artifact(self):
        names = available_action_names(
            context(
                RequestStatus.delivered.value,
                has_artifact=True,
                artifact_available=False,
                distribution_complete=True,
            )
        )
        assert "regenerate" in names

    def test_regenerate_is_withheld_while_the_artifact_is_still_available(self):
        names = available_action_names(
            context(
                RequestStatus.delivered.value,
                has_artifact=True,
                artifact_available=True,
                distribution_complete=True,
            )
        )
        assert "regenerate" not in names

    def test_regenerate_is_withheld_when_distribution_is_incomplete(self):
        names = available_action_names(
            context(
                RequestStatus.delivered.value,
                has_artifact=True,
                artifact_available=False,
                distribution_complete=False,
            )
        )
        assert "regenerate" not in names


class TestTelegramOrigin:
    """#57: Telegram-originated state renders without duplicate opportunities.

    Telegram derives its inline keyboard from the same `status` this table
    reads (jawnix/telegram.py `_keyboard`), so both surfaces already offer the
    same set. The duplication to prevent is presentational: one action set,
    with Telegram shown as provenance, rather than a second per-channel copy.
    Withholding the action here instead would leave the workspace unable to act
    on precisely the requests it exists to act on.
    """

    def test_a_live_telegram_decision_does_not_remove_the_action(self):
        assert available_action_names(
            context(RequestStatus.pending.value, telegram_decision_pending=True)
        ) == available_action_names(context(RequestStatus.pending.value))

    def test_no_action_is_offered_twice(self):
        for pending in (False, True):
            names = available_action_names(
                context(RequestStatus.pending.value, telegram_decision_pending=pending)
            )
            assert len(names) == len(set(names))

    def test_a_live_telegram_decision_is_reported_for_display(self):
        ctx = context(RequestStatus.pending.value, telegram_decision_pending=True)
        assert ctx.telegram_decision_pending is True


class TestTableIntegrity:
    def test_every_action_carries_confirmation_copy_and_demands_a_reason(self):
        for action in ACTIONS:
            assert action.label, action.name
            assert action.consequence, action.name
            assert action.requires_reason, action.name

    def test_destructive_actions_are_the_irreversible_ones(self):
        destructive = {action.name for action in ACTIONS if action.destructive}
        assert destructive == {"reject", "cancel"}

    def test_every_offered_action_is_a_known_action(self):
        for status in RequestStatus:
            for name in available_action_names(context(status.value)):
                assert action_named(name).name == name

    def test_an_unknown_action_is_refused(self):
        with pytest.raises(KeyError):
            action_named("delete_everything")


class TestOfferMatchesEnforcement:
    """The guard against the two surfaces drifting apart."""

    @pytest.mark.parametrize("status", [status.value for status in RequestStatus])
    def test_no_offered_transition_is_refused_by_its_own_from_statuses(self, status):
        ctx = context(status, has_artifact=True, artifact_available=True)
        for action in available_actions(ctx):
            if action.from_statuses:
                assert status in action.from_statuses

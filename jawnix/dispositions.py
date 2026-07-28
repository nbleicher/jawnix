"""The Lead Disposition catalog a Customer chooses from (#53).

Every disposition is a visible choice grouped by what it means, not an entry in
a dropdown, and each one states its durable consequence before the Customer
commits to it.

``creates_report`` and ``creates_hold`` are **derived** from the same constants
``feedback.apply_disposition_controls`` materializes controls from, never
restated. A Lead Report and an Eligibility Hold are irreversible from the
Customer's side; copy maintained in parallel with the rule would eventually
disagree with it, and the failure mode is telling someone their answer is
harmless when it withdraws a Lead from their inventory.
"""

from __future__ import annotations

from dataclasses import dataclass

from .feedback import HOLD_DISPOSITIONS, REPORT_DISPOSITIONS


#: Group keys paired with the label a Customer reads.
GROUP_ORDER: tuple[tuple[str, str], ...] = (
    ("contact_result", "Contact result"),
    ("positive_progress", "Positive progress"),
    ("appointment_follow_up", "Appointment follow-up"),
    ("data_compliance_problem", "Data or compliance problem"),
    ("other", "Other"),
)


@dataclass(frozen=True)
class Disposition:
    disposition: str
    group: str
    label: str
    #: What the disposition means, in the Customer's terms.
    description: str
    requires_note: bool = False

    @property
    def creates_report(self) -> bool:
        return self.disposition in REPORT_DISPOSITIONS

    @property
    def creates_hold(self) -> bool:
        return self.disposition in HOLD_DISPOSITIONS

    @property
    def consequence(self) -> str:
        """What submitting this will durably do, or nothing if it does not.

        Deliberately empty rather than reassuring for the ordinary
        dispositions: a screen that explains a consequence for every choice
        trains people to skip the explanation on the three that have one.
        """
        if not self.creates_report:
            return ""
        if self.creates_hold:
            return (
                "Submitting this files a Lead Report and places an "
                "Eligibility Hold, which withdraws this Lead from future "
                "batches. Only an administrator can release the hold."
            )
        return (
            "Submitting this files a Lead Report for an administrator to "
            "review. It does not place an Eligibility Hold, so this Lead "
            "stays eligible for future batches."
        )


CATALOG: tuple[Disposition, ...] = (
    Disposition(
        disposition="no_contact",
        group="contact_result",
        label="No Contact",
        description="You could not reach anyone.",
    ),
    Disposition(
        disposition="not_interested",
        group="contact_result",
        label="Not Interested",
        description="You reached them and they declined.",
    ),
    Disposition(
        disposition="positive_response",
        group="positive_progress",
        label="Positive Response",
        description="They were interested but nothing is scheduled yet.",
    ),
    Disposition(
        disposition="appointment_booked",
        group="appointment_follow_up",
        label="Appointment Booked",
        description="You scheduled a meeting.",
    ),
    Disposition(
        disposition="appointment_canceled",
        group="appointment_follow_up",
        label="Appointment Canceled",
        description="A scheduled meeting was called off.",
    ),
    Disposition(
        disposition="appointment_no_show",
        group="appointment_follow_up",
        label="Appointment No-show",
        description="They did not attend a scheduled meeting.",
    ),
    Disposition(
        disposition="invalid_phone",
        group="data_compliance_problem",
        label="Invalid Phone",
        description="The number is disconnected or not a working line.",
    ),
    Disposition(
        disposition="wrong_business",
        group="data_compliance_problem",
        label="Wrong Business",
        description="The number reaches a different business than listed.",
    ),
    Disposition(
        disposition="do_not_contact",
        group="data_compliance_problem",
        label="Do Not Contact",
        description="They asked not to be contacted again.",
    ),
    Disposition(
        disposition="other",
        group="other",
        label="Other",
        description="Anything the choices above do not cover.",
        requires_note=True,
    ),
)


#: Quality Rating is a separate, optional judgement of Lead quality. It is not
#: a disposition and materializes no controls: a Poor rating feeds Source
#: Segment metrics and nothing else. Saying so on the screen is the point —
#: a Customer who suspects Poor suppresses a Lead will avoid using it.
QUALITY_RATING = (
    (
        "good",
        "Good",
        "Counts this Lead as good quality in your feedback history.",
    ),
    (
        "poor",
        "Poor",
        "Counts this Lead as poor quality in your feedback history. "
        "Nothing is withdrawn and nothing is filed for review.",
    ),
)


def catalog_payload() -> dict:
    """The catalog as the Customer Feedback screen reads it."""
    return {
        "groups": [
            {
                "group": group,
                "label": label,
                "options": [
                    {
                        "disposition": entry.disposition,
                        "label": entry.label,
                        "description": entry.description,
                        "requiresNote": entry.requires_note,
                        "createsReport": entry.creates_report,
                        "createsHold": entry.creates_hold,
                        "consequence": entry.consequence,
                    }
                    for entry in CATALOG
                    if entry.group == group
                ],
            }
            for group, label in GROUP_ORDER
        ],
        "qualityRating": {
            "optional": True,
            "description": (
                "Optional, and separate from your answer above. It records "
                "how good the Lead was and changes nothing else."
            ),
            "options": [
                {
                    "value": value,
                    "label": label,
                    "description": description,
                    "createsReport": False,
                    "createsHold": False,
                }
                for value, label, description in QUALITY_RATING
            ],
        },
    }

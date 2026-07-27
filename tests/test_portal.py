from pathlib import Path


PORTAL = Path(__file__).parents[1] / "portal.html"


def test_portal_exposes_single_phone_feedback_contract():
    html = PORTAL.read_text(encoding="utf-8")

    assert 'id="feedback-phone" type="tel"' in html
    assert 'id="feedback-business"' in html
    assert 'id="feedback-confirm-phone"' in html
    assert 'id="feedback-delivered"' in html
    assert 'id="feedback-batch"' in html
    assert "/api/me/feedback/lookup" in html
    assert "/api/me/feedback" in html
    assert (
        "distribution_event_id:feedbackLead.distributionEventId"
        in html
    )


def test_portal_offers_every_disposition_and_independent_quality_rating():
    html = PORTAL.read_text(encoding="utf-8")
    dispositions = {
        "no_contact",
        "not_interested",
        "positive_response",
        "appointment_booked",
        "appointment_canceled",
        "appointment_no_show",
        "invalid_phone",
        "wrong_business",
        "do_not_contact",
        "other",
    }

    for disposition in dispositions:
        assert f'<option value="{disposition}">' in html
    assert "event.target.value==='other'" in html
    assert 'name="quality-rating" value=""' in html
    assert 'name="quality-rating" value="good"' in html
    assert 'name="quality-rating" value="poor"' in html

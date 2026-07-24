from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from jawnix.schemas import RequestCreate
from jawnix.states import derive_state, normalize_phone, normalize_states
from jawnix.telegram import callback_data, parse_callback_data, verify_telegram_secret


def test_normalization_and_request_limit():
    assert normalize_phone("+1 (215) 555-1212") == "2155551212"
    assert normalize_phone("123") is None
    assert derive_state("4155551212") == "CA"
    assert normalize_states(["tx", "FL", "TX"]) == ["FL", "TX"]
    with pytest.raises(ValueError, match="Unsupported state"):
        normalize_states(["IO"])
    with pytest.raises(ValidationError):
        RequestCreate(lead_count=100_001, state_mode="all_saved")
    with pytest.raises(ValidationError):
        RequestCreate(lead_count=1, state_mode="selected", states=[])


def test_telegram_secret_and_callback_validation():
    request_id = uuid.uuid4()
    encoded = callback_data("retry_delivery", request_id)
    assert parse_callback_data(encoded) == ("retry_delivery", request_id)
    assert verify_telegram_secret("webhook-secret", "webhook-secret")
    assert not verify_telegram_secret("wrong", "webhook-secret")
    assert not verify_telegram_secret("", "webhook-secret")
    with pytest.raises(ValueError, match="Malformed"):
        parse_callback_data("invalid")

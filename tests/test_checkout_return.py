"""Stripe Checkout returns the Customer to where they started, never elsewhere."""

from __future__ import annotations

import pytest

from jawnix.checkout_return import checkout_return_urls, customer_return_path

REQUEST_ID = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # The unfinished request form keeps quantity, states, and file option.
        (
            "/app/requests?quantity=50000&states=TX,FL&rows_per_file=10000",
            "/app/requests?quantity=50000&states=FL,TX&rows_per_file=10000",
        ),
        ("/app/requests?quantity=750", "/app/requests?quantity=750"),
        (f"/app/requests?request={REQUEST_ID}", f"/app/requests?request={REQUEST_ID}"),
        ("/app/requests?page=2", "/app/requests?page=2"),
        (f"/app/feedback?request={REQUEST_ID}", f"/app/feedback?request={REQUEST_ID}"),
        ("/app/overview", "/app/overview"),
        ("/app/account", "/app/account"),
        # Keys a page does not read, and invalid values, are dropped.
        ("/app/requests?quantity=750&next=https://evil.test", "/app/requests?quantity=750"),
        ("/app/requests?quantity=0&states=TX", "/app/requests?states=TX"),
        ("/app/requests?quantity=100001", "/app/requests"),
        ("/app/requests?quantity=1e3", "/app/requests"),
        ("/app/requests?states=TX,ZZ", "/app/requests"),
        ("/app/requests?request=not-a-uuid", "/app/requests"),
        ("/app/overview?quantity=750", "/app/overview"),
        ("/app/requests?purchase=success", "/app/requests"),
        ("/app/requests?quantity=750&quantity=900", "/app/requests?quantity=750"),
        ("/app/requests#review", "/app/requests"),
    ],
)
def test_return_path_is_rebuilt_from_the_allow_list(value, expected):
    assert customer_return_path(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "https://evil.test/app/requests",
        "//evil.test/app/requests",
        "///evil.test",
        "/\\evil.test",
        "/app/requests\\@evil.test",
        "javascript:alert(1)",
        "app/requests",
        "/app/admin/overview",
        "/app/manager/uploads",
        "/app/requests/../admin/overview",
        "/app/requests/",
        "/api/me/billing",
        "/app/requests\r\nLocation: https://evil.test",
        "/app/requests?quantity=" + "1" * 600,
    ],
)
def test_anything_off_the_allow_list_returns_to_account(value):
    assert customer_return_path(value) == "/app/account"


def test_checkout_urls_use_the_configured_origin_and_add_the_outcome():
    success, cancel = checkout_return_urls(
        "https://jawnix.test/",
        "/app/requests?quantity=750&states=TX",
    )

    assert success == "https://jawnix.test/app/requests?quantity=750&states=TX&purchase=success"
    assert cancel == "https://jawnix.test/app/requests?quantity=750&states=TX&purchase=cancelled"
    assert checkout_return_urls("https://jawnix.test", None) == (
        "https://jawnix.test/app/account?purchase=success",
        "https://jawnix.test/app/account?purchase=cancelled",
    )

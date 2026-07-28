"""Integration cover for serving the *real* compiled shell through the real app.

`tests/test_frontend_shell.py` drives `jawnix.frontend` against a fixture build
directory, and the Playwright suite drives the compiled bundle through
`vite preview`. Neither exercises the actual deployment path: the real Vite
output served by the real FastAPI application.

That gap matters because the two sides agree only by convention — Vite's `base`
must match the mount prefix, and its asset filenames must match the pattern the
immutable-caching rule assumes. A change to either would pass both other suites
and still break production.

These tests skip when the build is absent, so `pytest` stays runnable without a
Node toolchain.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.config import Settings, get_settings
from jawnix.frontend import MOUNT_PREFIX

DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

pytestmark = pytest.mark.skipif(
    not (DIST_DIR / "index.html").is_file(),
    reason="frontend build missing; run `npm run build` in frontend/",
)


@pytest.fixture
def client():
    """The real application, with the shell enabled and pointed at the real build."""

    def override() -> Settings:
        return Settings(
            JAWNIX_ENABLE_NEW_UI=True,
            JAWNIX_FRONTEND_DIST_DIR=DIST_DIR,
            JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough",
        )

    app.dependency_overrides[get_settings] = override
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.pop(get_settings, None)


def asset_urls(document: str) -> list[str]:
    return re.findall(r'(?:src|href)="([^"]*/assets/[^"]+)"', document)


def test_serves_the_compiled_document(client):
    response = client.get(f"{MOUNT_PREFIX}/")

    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_the_document_references_assets_under_the_mount_prefix(client):
    """Guards the Vite `base` against the FastAPI mount prefix."""
    document = client.get(f"{MOUNT_PREFIX}/").text
    urls = asset_urls(document)

    assert urls, "the built document referenced no assets"
    for url in urls:
        assert url.startswith(f"{MOUNT_PREFIX}/assets/"), url


def test_every_referenced_asset_is_actually_served(client):
    """A referenced-but-unserved asset is a blank page in production."""
    document = client.get(f"{MOUNT_PREFIX}/").text

    for url in asset_urls(document):
        response = client.get(url)
        assert response.status_code == 200, url


def test_referenced_assets_are_content_hashed_and_cached_immutably(client):
    """Immutable caching is only safe because the filename carries a hash."""
    document = client.get(f"{MOUNT_PREFIX}/").text

    for url in asset_urls(document):
        assert re.search(r"-[A-Za-z0-9_-]{8,}\.(js|css)$", url), url
        assert "immutable" in client.get(url).headers["cache-control"]


@pytest.mark.parametrize(
    "path",
    [
        f"{MOUNT_PREFIX}/overview",
        f"{MOUNT_PREFIX}/sign-in",
        f"{MOUNT_PREFIX}/accept-invitation",
        f"{MOUNT_PREFIX}/requests",
        f"{MOUNT_PREFIX}/admin/fulfillment",
        f"{MOUNT_PREFIX}/design-system",
    ],
)
def test_direct_navigation_serves_the_shell(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_the_flag_gates_the_real_build_too():
    """With the flag off the prefix is absent even though the build exists."""

    def override() -> Settings:
        return Settings(
            JAWNIX_ENABLE_NEW_UI=False,
            JAWNIX_FRONTEND_DIST_DIR=DIST_DIR,
            JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough",
        )

    app.dependency_overrides[get_settings] = override
    with TestClient(app) as client:
        assert client.get(f"{MOUNT_PREFIX}/").status_code == 404
        assert client.get(f"{MOUNT_PREFIX}/overview").status_code == 404
    app.dependency_overrides.pop(get_settings, None)


def test_the_legacy_static_pages_are_not_shadowed(client):
    """The shell must not claim any path the current UI owns."""
    for legacy_path in ["/login.html", "/portal.html", "/admin.html", "/"]:
        assert client.get(legacy_path).status_code == 404, (
            f"{legacy_path} is served by Caddy, not FastAPI; the shell must not answer it"
        )

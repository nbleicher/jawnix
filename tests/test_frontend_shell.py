"""Unit cover for `jawnix.frontend` against a fixture build directory.

The real compiled bundle is covered by `test_frontend_shell_integration.py`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jawnix.config import Settings, get_settings
from jawnix.frontend import register_frontend_shell


@pytest.fixture
def dist_dir(tmp_path):
    """A minimal build output shaped like Vite's."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div>'
        '<script type="module" src="/app/assets/index-abc123.js"></script>'
        "</body></html>",
        encoding="utf-8",
    )
    (dist / "assets" / "index-abc123.js").write_text("console.log('shell')", encoding="utf-8")
    (dist / "assets" / "index-def456.css").write_text(":root{}", encoding="utf-8")
    return dist


def build_client(dist_dir) -> TestClient:
    def override() -> Settings:
        return Settings(
            JAWNIX_FRONTEND_DIST_DIR=dist_dir,
            JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough",
        )

    app = FastAPI()
    register_frontend_shell(app)
    app.dependency_overrides[get_settings] = override
    return TestClient(app)


# --- The shell document -----------------------------------------------------


def test_serves_the_compiled_document(dist_dir):
    response = build_client(dist_dir).get("/app/")

    assert response.status_code == 200
    assert 'id="root"' in response.text
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(
    "path",
    [
        "/app",
        "/app/",
        "/app/sign-in",
        "/app/accept-invitation",
        "/app/overview",
        "/app/admin/fulfillment",
        "/app/requests/1234",
    ],
)
def test_direct_navigation_to_any_application_route_serves_the_shell(dist_dir, path):
    """Deep links must work on a hard refresh, not just via the client router."""
    response = build_client(dist_dir).get(path, follow_redirects=True)

    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_document_is_revalidated_so_a_deploy_is_picked_up(dist_dir):
    response = build_client(dist_dir).get("/app/")

    assert "no-store" in response.headers["cache-control"]


def test_document_is_not_indexed(dist_dir):
    response = build_client(dist_dir).get("/app/")

    assert "noindex" in response.headers["x-robots-tag"]


# --- Hashed assets ----------------------------------------------------------


def test_serves_a_hashed_asset(dist_dir):
    response = build_client(dist_dir).get("/app/assets/index-abc123.js")

    assert response.status_code == 200
    assert response.text == "console.log('shell')"


def test_hashed_assets_are_cached_immutably(dist_dir):
    response = build_client(dist_dir).get("/app/assets/index-def456.css")

    cache_control = response.headers["cache-control"]
    assert "immutable" in cache_control
    assert "max-age=31536000" in cache_control


def test_missing_asset_is_not_found_rather_than_the_shell(dist_dir):
    """An asset 404 must stay a 404: returning index.html would make a stale
    bundle reference surface as an unreadable MIME-type error."""
    response = build_client(dist_dir).get("/app/assets/index-gone.js")

    assert response.status_code == 404
    assert "root" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        # Percent-encoded so the client cannot normalise the traversal away
        # before it reaches the server.
        "/app/assets/%2e%2e/secret.txt",
        "/app/assets/%2e%2e%2f%2e%2e%2fsecret.txt",
        "/app/assets/%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    ],
)
def test_traversal_outside_the_build_directory_is_refused(dist_dir, path):
    secret = dist_dir.parent / "secret.txt"
    secret.write_text("do-not-serve-this", encoding="utf-8")

    response = build_client(dist_dir).get(path)

    assert response.status_code == 404
    assert "do-not-serve-this" not in response.text
    assert "root:" not in response.text


def test_a_symlink_escaping_the_build_directory_is_refused(dist_dir):
    secret = dist_dir.parent / "secret.txt"
    secret.write_text("do-not-serve-this", encoding="utf-8")
    (dist_dir / "assets" / "escape.txt").symlink_to(secret)

    response = build_client(dist_dir).get("/app/assets/escape.txt")

    assert response.status_code == 404
    assert "do-not-serve-this" not in response.text


# --- Degraded states --------------------------------------------------------


def test_absent_build_reports_unavailable_rather_than_crashing(tmp_path):
    """The flag can be on before the build artefact is present; that must be a
    clean 503, not a 500 traceback."""
    response = build_client(tmp_path / "absent").get("/app/")

    assert response.status_code == 503


def test_registering_the_shell_adds_no_route_outside_the_app_prefix():
    """The current static UI keeps the site root until cutover."""
    app = FastAPI()
    before = {route.path for route in app.routes}
    register_frontend_shell(app)
    added = {route.path for route in app.routes} - before

    assert added, "expected the shell to register routes"
    assert all(path.startswith("/app") for path in added), added


def test_config_js_renderers_cover_the_browser_contract():
    """Compose and the legacy Railway entrypoint must cover the checked-in
    browser contract. The deploy-only render script was removed because nothing
    served its output; keeping a dead third renderer was the source of drift, not
    protection against it.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    example = (root / "config.example.js").read_text()
    contract_fields = set(re.findall(r"^  ([a-zA-Z]+):", example, re.M))

    caddyfile = (root / "Caddyfile").read_text()
    block = caddyfile[caddyfile.index("handle /config.js") :]
    block = block[: block.index("` 200")]
    caddy_fields = set(re.findall(r"^  ([a-zA-Z]+):", block, re.M))

    railway = (root / "railway-start.sh").read_text()
    railway_block = railway[railway.index("window.JAWNIX_CONFIG = {") :]
    railway_block = railway_block[: railway_block.index("};")]
    railway_fields = set(re.findall(r"^  ([a-zA-Z]+):", railway_block, re.M))

    assert contract_fields
    assert caddy_fields == contract_fields, (
        "Caddy's served config.js disagrees with config.example.js: "
        f"missing {contract_fields - caddy_fields}, extra {caddy_fields - contract_fields}"
    )
    assert contract_fields <= railway_fields, (
        "the legacy Railway renderer is missing browser fields: "
        f"{contract_fields - railway_fields}"
    )
    assert "billingEnabled: '{$JAWNIX_ENABLE_BILLING:false}' === 'true'" in block
    assert "billingEnabled: $billing_enabled" in railway_block


def test_the_production_edge_adapter_exists_and_names_the_upstream():
    """jawnix.com TLS terminates in another product's Caddy (buzz-prod-caddy-1),
    which proxies to a container named `jawnix-caddy` on :8080.
    docker-compose.edge.yml is what creates that container.

    Deleting it does nothing until something recreates the caddy container, at
    which point every request to jawnix.com becomes 502 with
    `dial tcp: lookup jawnix-caddy: no such host`. That is a two-day-latent
    outage, and it happened on 2026-07-30.

    This asserts the three properties the edge depends on, so the file cannot be
    removed or quietly reshaped without a test failing.
    """
    from pathlib import Path

    import yaml

    adapter = Path(__file__).resolve().parent.parent / "docker-compose.edge.yml"
    assert adapter.is_file(), (
        "docker-compose.edge.yml is missing — buzz-prod's Caddy proxies to the "
        "container it names, so removing it takes jawnix.com down on the next "
        "container recreation"
    )
    class ComposeLoader(yaml.SafeLoader):
        pass

    ComposeLoader.add_constructor(
        "!reset",
        lambda loader, node: loader.construct_sequence(node),
    )
    compose = yaml.load(adapter.read_text(), Loader=ComposeLoader)
    assert set(compose["services"]) == {"caddy"}, (
        "only Caddy may join the edge network; attaching API or Postgres makes "
        "the ambiguous `postgres` hostname reachable again"
    )
    caddy = compose["services"]["caddy"]

    # The upstream hostname buzz-prod's Caddyfile dials.
    assert caddy["container_name"] == "jawnix-caddy"
    # Plain HTTP behind the edge; this Caddy must not try to terminate TLS.
    assert caddy["environment"] == {
        "JAWNIX_DOMAIN": ":8080",
        "JAWNIX_SCRAPER_OPS_DOMAIN": ":8081",
    }
    # 80/443 belong to buzz-prod's Caddy; binding them here fails to start.
    assert caddy["ports"] == []
    # Reachability from the edge network, without exposing other services to
    # the ambiguous `postgres` hostname on that network.
    assert caddy["networks"] == ["private", "edge"]
    assert compose["networks"]["edge"] == {
        "external": True,
        "name": "buzz-prod_buzz-net",
    }


def test_env_example_documents_the_production_compose_file_pin():
    """Production's `.env` must pin COMPOSE_FILE so a bare `docker compose up -d`
    cannot omit the edge adapter and reproduce the 2026-07-30 outage. But the
    pin is production-host-only: the adapter joins the external buzz-prod
    network, so an *uncommented* COMPOSE_FILE in the template breaks staging,
    local dev, and CI outright (`network buzz-prod_buzz-net declared as
    external, but could not be found`). The template therefore carries the
    exact line, commented, next to instructions saying where to uncomment it.
    """
    from pathlib import Path

    lines = (
        (Path(__file__).resolve().parent.parent / ".env.example")
        .read_text()
        .splitlines()
    )

    pin = "COMPOSE_FILE=docker-compose.yml:docker-compose.edge.yml"
    assert f"#{pin}" in lines, (
        ".env.example must carry the production COMPOSE_FILE pin (commented)"
    )
    assert pin not in lines, (
        "an uncommented COMPOSE_FILE in the template breaks every host that "
        "lacks the buzz-prod edge network — production uncomments it in .env"
    )

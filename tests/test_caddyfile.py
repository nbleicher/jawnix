from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_every_legacy_redirect_preserves_the_query_string():
    """Static-page retirement (P8): the legacy URLs redirect unconditionally to
    the React shell and must carry the query string forward — invitation
    ``?code=`` and sign-in ``?next=`` values depend on it."""
    redirect_lines = [
        line.strip()
        for line in (ROOT / "Caddyfile").read_text().splitlines()
        if line.strip().startswith("redir @legacy_")
    ]

    assert len(redirect_lines) == 5
    assert all("?{query}" in line for line in redirect_lines)


def test_no_static_file_server_remains():
    """The legacy pages are gone; nothing serves /srv/static or a bare
    file_server, the flag-gated cutover redirects are now unconditional, and
    every other path falls through to the shell."""
    directives = [
        line.strip()
        for line in (ROOT / "Caddyfile").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert not any(line.startswith("file_server") for line in directives)
    assert not any("/srv/static" in line for line in directives)
    assert not any("JAWNIX_ENABLE_NEW_UI" in line for line in directives)
    assert "redir * /app/?{query} 302" in directives


def test_www_redirects_permanently_to_the_canonical_origin():
    """www.jawnix.com resolves to the application host; it must forward the
    whole path and query to the canonical origin rather than fail TLS."""
    text = (ROOT / "Caddyfile").read_text()

    assert "{$JAWNIX_WWW_DOMAIN:www.jawnix.com} {" in text
    assert "redir {$JAWNIX_PUBLIC_BASE_URL:https://jawnix.com}{uri} permanent" in text

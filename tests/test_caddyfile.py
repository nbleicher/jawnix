from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_every_new_ui_redirect_preserves_the_query_string():
    redirect_lines = [
        line.strip()
        for line in (ROOT / "Caddyfile").read_text().splitlines()
        if line.strip().startswith("redir @newui_")
    ]

    assert len(redirect_lines) == 5
    assert all("?{query}" in line for line in redirect_lines)

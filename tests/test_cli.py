import re

from typer.testing import CliRunner

from jawnix_data.cli import app

# Rich colourises help output whenever it believes it is writing to a terminal,
# and it treats GitHub Actions as one. It styles each option name, so the escape
# codes land *inside* the token and a raw substring check for `--request-id`
# fails even though the option is documented. `NO_COLOR` does not override the
# CI detection, so assert on the visible text instead.
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def visible(text: str) -> str:
    """The help output as an operator reads it, without styling."""
    return ANSI_ESCAPE.sub("", text)


def test_required_request_id_options_match_operator_contract():
    runner = CliRunner()

    redistribute = runner.invoke(app, ["redistribute", "--help"])
    retry = runner.invoke(app, ["retry-delivery", "--help"])

    assert redistribute.exit_code == 0
    assert "--request-id" in visible(redistribute.stdout)
    assert retry.exit_code == 0
    assert "--request-id" in visible(retry.stdout)

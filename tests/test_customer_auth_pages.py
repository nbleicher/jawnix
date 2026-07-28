from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_legacy_authentication_pages_remain_available_for_cutover():
    assert (ROOT / "login.html").is_file()
    assert (ROOT / "portal-accept.html").is_file()


def test_legacy_administration_invites_without_password_controls():
    document = (ROOT / "admin.html").read_text(encoding="utf-8")
    known_password_control = "customer-password"

    assert "Send invitation" in document
    assert "Administrators cannot set or view it." in document
    assert known_password_control not in document
    assert '"password"' not in document

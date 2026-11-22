"""A cashier phone behind the others' app version is flagged."""

from relay.infrastructure.alerts import _version, describe


def test_versions_compare_as_numbers() -> None:
    assert _version("0.8.10") > _version("0.8.7") > _version("0.8.6")
    assert _version("0.8.7-receipts") == (0, 8, 7)
    assert _version(None) == ()


def test_old_app_line_for_the_email() -> None:
    from zoneinfo import ZoneInfo

    line = describe(
        {
            "kind": "old_app",
            "device": "Cashier 2",
            "version": "0.8.5",
            "latest": "0.8.7",
        },
        ZoneInfo("Africa/Cairo"),
    )
    assert "Cashier 2" in line and "0.8.5" in line and "0.8.7" in line

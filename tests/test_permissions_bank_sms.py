"""The Bank SMS list shows raw SMS bodies: admins only."""

from relay.domain.permissions import allowed
from relay.domain.users import Role


def test_only_admins_read_the_bank_sms_list() -> None:
    assert allowed(Role.ADMIN, "GET", "/api/v1/bank-sms")
    assert not allowed(Role.REVIEWER, "GET", "/api/v1/bank-sms")
    # Reviewers still decide a receipt's SMS match.
    assert allowed(Role.REVIEWER, "PUT", "/api/v1/receipts/abc/sms-verification")

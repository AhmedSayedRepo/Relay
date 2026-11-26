"""Which operator may open which page and take which action.

Each role starts from a default set of permissions; an admin can grant or deny
single permissions per user on top of that. Admins always keep everything, so
nobody can lock the portal. Rules are matched on the request path; templates
below stand for one path segment per ``{name}``. A new change route is
admin-only unless it is listed here; reads are open except the areas below.
"""

import re
from collections.abc import Mapping

from relay.domain.users import Role

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

PAGES = (
    "page.dashboard",
    "page.review",
    "page.transactions",
    "page.rejected",
    "page.bank_sms",
    "page.devices",
    "page.users",
    "page.email_settings",
    "page.audit",
)
ACTIONS = (
    "action.review",
    "action.bring_back",
    "action.reject_copies",
    "action.delete_receipts",
    "action.close_shift",
    "action.retry_processing",
    "action.run_sms_check",
    "action.manage_devices",
    "action.manage_users",
    "action.email_settings",
)
PERMISSIONS = PAGES + ACTIONS

ROLE_DEFAULTS: Mapping[Role, frozenset[str]] = {
    Role.ADMIN: frozenset(PERMISSIONS),
    Role.REVIEWER: frozenset(
        {
            "page.dashboard",
            "page.review",
            "page.transactions",
            "page.rejected",
            "action.review",
            "action.bring_back",
            "action.reject_copies",
            "action.close_shift",
            "action.retry_processing",
        }
    ),
    Role.VIEWER: frozenset({"page.dashboard", "page.transactions", "page.rejected"}),
}

_RECEIPT = "/api/v1/receipts/{candidate_id}"
WRITE_RULES: tuple[tuple[str, str, str], ...] = (
    ("POST", _RECEIPT + "/exceptions", "action.review"),
    ("POST", _RECEIPT + "/review", "action.review"),
    ("PUT", _RECEIPT + "/sms-verification", "action.review"),
    ("DELETE", _RECEIPT + "/sms-verification", "action.review"),
    (
        "POST",
        "/api/v1/duplicate-groups/{group_id}/members/{candidate_id}/resolution",
        "action.review",
    ),
    ("POST", _RECEIPT + "/not-a-copy", "action.reject_copies"),
    ("POST", "/api/v1/receipts/reject-copies", "action.reject_copies"),
    ("POST", _RECEIPT + "/reopen", "action.bring_back"),
    ("POST", "/api/v1/receipts/delete", "action.delete_receipts"),
    ("POST", "/api/v1/shifts/close", "action.close_shift"),
    ("POST", "/api/v1/processing/jobs/{job_id}/retry", "action.retry_processing"),
    ("POST", "/api/v1/verification/sms/run", "action.run_sms_check"),
    ("POST", "/api/v1/devices", "action.manage_devices"),
    ("PATCH", "/api/v1/devices/{device_id}", "action.manage_devices"),
    ("DELETE", "/api/v1/devices/{device_id}", "action.manage_devices"),
    ("PUT", "/api/v1/devices/{device_id}/sms-sources", "action.manage_devices"),
    ("POST", "/api/v1/users", "action.manage_users"),
    ("PATCH", "/api/v1/users/{user_id}", "action.manage_users"),
    ("DELETE", "/api/v1/users/{user_id}", "action.manage_users"),
    ("PUT", "/api/v1/settings/email", "action.email_settings"),
    ("POST", "/api/v1/settings/email/test", "action.email_settings"),
    ("PUT", "/api/v1/settings/recipient", "action.email_settings"),
)
# Reads behind a page permission: raw SMS bodies, accounts, email settings.
READ_RULES: tuple[tuple[str, str], ...] = (
    ("/api/v1/bank-sms", "page.bank_sms"),
    ("/api/v1/users", "page.users"),
    ("/api/v1/settings", "page.email_settings"),
    ("/api/v1/audit-trail", "page.audit"),
)
# Reads only an admin may make: raw events and training data.
ADMIN_READ_PREFIXES = ("/api/v1/events", "/api/v1/training")
ADMIN_READ_SUFFIXES = ("/training-consent",)

_WRITE_PATTERNS = tuple(
    (method, re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", template) + "$"), key)
    for method, template, key in WRITE_RULES
)


def clean(overrides: Mapping[str, object] | None) -> dict[str, bool]:
    """Only known permissions with a yes/no value survive."""
    return {
        key: value
        for key, value in (overrides or {}).items()
        if key in PERMISSIONS and isinstance(value, bool)
    }


def granted(
    role: Role, overrides: Mapping[str, object] | None = None
) -> frozenset[str]:
    if role == Role.ADMIN:
        return ROLE_DEFAULTS[Role.ADMIN]
    result = set(ROLE_DEFAULTS[role])
    for key, value in clean(overrides).items():
        if value:
            result.add(key)
        else:
            result.discard(key)
    return frozenset(result)


def allowed(
    role: Role,
    method: str,
    path: str,
    overrides: Mapping[str, object] | None = None,
) -> bool:
    if path.startswith("/api/v1/auth/"):
        return True  # every signed-in user manages their own account
    if role == Role.ADMIN:
        return True
    have = granted(role, overrides)
    verb = method.upper()
    if verb in READ_METHODS:
        if path.startswith(ADMIN_READ_PREFIXES) or path.endswith(ADMIN_READ_SUFFIXES):
            return False
        for prefix, key in READ_RULES:
            if path.startswith(prefix):
                return key in have
        return True
    return any(
        verb == rule_method and pattern.match(path) and key in have
        for rule_method, pattern, key in _WRITE_PATTERNS
    )

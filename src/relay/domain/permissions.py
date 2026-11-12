"""Which operator role may call which API route.

Rules are matched on the request path; templates below stand for one path
segment per ``{name}``. A new route is admin-only for changes unless it is
listed here. Reads are open to every role except the admin-only areas below.
"""

import re

from relay.domain.users import Role

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Changes a reviewer may make: deciding receipts, duplicates and SMS matches.
REVIEW_WRITES = frozenset(
    {
        ("POST", "/api/v1/receipts/{candidate_id}/exceptions"),
        ("POST", "/api/v1/receipts/{candidate_id}/review"),
        (
            "POST",
            "/api/v1/duplicate-groups/{group_id}/members/{candidate_id}/resolution",
        ),
        ("PUT", "/api/v1/receipts/{candidate_id}/sms-verification"),
        ("DELETE", "/api/v1/receipts/{candidate_id}/sms-verification"),
        ("POST", "/api/v1/processing/jobs/{job_id}/retry"),
    }
)
# Reads only an admin may make: user accounts, raw SMS/event bodies, training,
# email settings.
ADMIN_READ_PREFIXES = (
    "/api/v1/users",
    "/api/v1/events",
    "/api/v1/training",
    "/api/v1/settings",
)
ADMIN_READ_SUFFIXES = ("/training-consent",)
_REVIEW_PATTERNS = frozenset(
    (method, re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", template) + "$"))
    for method, template in REVIEW_WRITES
)


def allowed(role: Role, method: str, path: str) -> bool:
    if path.startswith("/api/v1/auth/"):
        return True  # every signed-in user manages their own account
    if role == Role.ADMIN:
        return True
    if method.upper() in READ_METHODS:
        return not (
            path.startswith(ADMIN_READ_PREFIXES) or path.endswith(ADMIN_READ_SUFFIXES)
        )
    return role == Role.REVIEWER and any(
        method.upper() == allowed_method and pattern.match(path)
        for allowed_method, pattern in _REVIEW_PATTERNS
    )

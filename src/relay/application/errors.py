class ApplicationError(Exception):
    """Known failure with a static, public-safe message."""


class AuthenticationFailed(ApplicationError):
    pass


class Forbidden(ApplicationError):
    pass


class NotFound(ApplicationError):
    pass


class Conflict(ApplicationError):
    pass


class RateLimited(ApplicationError):
    """Too much right now; the same request can succeed later (HTTP 429)."""

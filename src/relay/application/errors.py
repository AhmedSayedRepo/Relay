class ApplicationError(Exception):
    """Known failure with a static, public-safe message."""


class AuthenticationFailed(ApplicationError):
    pass


class NotFound(ApplicationError):
    pass


class Conflict(ApplicationError):
    pass

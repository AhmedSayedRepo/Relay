class DomainError(Exception):
    """A rule was violated; messages must not contain user data."""


class InvalidDevice(DomainError):
    pass

from relay.domain.processing import ProcessingFailure


class ProcessingError(Exception):
    def __init__(self, failure: ProcessingFailure, *, transient: bool = False) -> None:
        self.failure = failure
        self.transient = transient
        super().__init__(failure.value)

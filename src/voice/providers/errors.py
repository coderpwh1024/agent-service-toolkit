class SpeechProviderError(Exception):
    """A speech provider rejected or violated the streaming contract."""

    def __init__(self, message: str, *, code: str | None = None, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

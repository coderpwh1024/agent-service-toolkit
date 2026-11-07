class ServiceError(Exception):
    """A controlled service failure translated into the existing API error response."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail

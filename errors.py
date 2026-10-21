class ApiError(Exception):
    """Raise to return {"err": msg}. keep=True commits the transaction first (used to persist rate-limit counters)."""
    def __init__(self, msg, status=400, keep=False, **extra):
        super().__init__(msg); self.msg, self.status, self.keep, self.extra = msg, status, keep, extra

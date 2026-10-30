class BusinessError(Exception):
    def __init__(self, code: str, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


def require_revision(actual: int, expected: int) -> None:
    if actual != expected:
        raise BusinessError("REVISION_CONFLICT", "项目已变更，请重新读取并审查后保存或批准")

"""Public exceptions. Exception messages never include the API key or request body."""

from typing import Any


class GenerAIError(Exception):
    """Base SDK error."""


class APIError(GenerAIError):
    """An HTTP error response; branch on status_code/code, not translated messages."""

    def __init__(
        self, status_code: int, code: str, message: str, *, idempotency_key: str | None = None
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.idempotency_key = idempotency_key
        super().__init__(f"HTTP {status_code} [{code}]: {message}")


class TransportError(GenerAIError):
    """Network failure. A paid request may have been accepted; retain its key."""

    def __init__(self, *, idempotency_key: str | None = None) -> None:
        self.idempotency_key = idempotency_key
        super().__init__("Network request failed; retry with the same idempotency key if creating a job")


class ProtocolError(GenerAIError):
    """Unexpected success response (for example, an HTML page instead of JSON)."""

    def __init__(self, message: str, *, idempotency_key: str | None = None) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(message)


class JobFailed(GenerAIError):
    """A job reached failed/cancelled; job contains error_code and refunded."""

    def __init__(self, job: dict[str, Any]) -> None:
        self.job = job
        super().__init__(f"Job {job.get('id')} ended with status {job.get('status')}")


class PollingTimeout(GenerAIError):
    """Only local polling stopped. The remote job is NOT cancelled."""

    def __init__(self, job_id: int, last_status: dict[str, Any] | None) -> None:
        self.job_id = job_id
        self.last_status = last_status
        super().__init__(f"Timed out waiting for job {job_id}; you can resume polling")

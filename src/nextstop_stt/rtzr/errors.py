"""Safe, user-facing RTZR errors."""

from __future__ import annotations


class RTZRError(Exception):
    """Base error for RTZR integration failures."""


class RTZRCredentialsError(RTZRError):
    """Raised when required RTZR credentials are unavailable."""


class RTZRAuthenticationError(RTZRError):
    """Raised when RTZR rejects authentication or returns an invalid token."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class RTZRStreamingError(RTZRError):
    """Raised when an RTZR Streaming session fails."""


class RTZRResponseError(RTZRStreamingError):
    """Raised when a Streaming response does not match the documented schema."""


class RTZRBatchError(RTZRError):
    """Raised when a Batch STT request or polling job fails."""

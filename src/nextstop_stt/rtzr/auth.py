"""Expiry-aware RTZR authentication with credential-safe errors."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from nextstop_stt.rtzr.errors import RTZRAuthenticationError, RTZRCredentialsError

DEFAULT_API_BASE = "https://openapi.vito.ai"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_REFRESH_MARGIN_SECONDS = 60


class RTZRCredentials(BaseModel):
    """RTZR credentials whose representation never exposes their values."""

    model_config = ConfigDict(frozen=True)

    client_id: SecretStr
    client_secret: SecretStr

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RTZRCredentials:
        """Read credentials from the documented environment variable names."""
        source = os.environ if environ is None else environ
        values = {
            "RTZR_CLIENT_ID": source.get("RTZR_CLIENT_ID", "").strip(),
            "RTZR_CLIENT_SECRET": source.get("RTZR_CLIENT_SECRET", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            joined = ", ".join(missing)
            raise RTZRCredentialsError(f"Missing required environment variables: {joined}")
        return cls(
            client_id=values["RTZR_CLIENT_ID"],
            client_secret=values["RTZR_CLIENT_SECRET"],
        )

    def as_form_data(self) -> dict[str, str]:
        """Return the form fields required by `/v1/authenticate`."""
        return {
            "client_id": self.client_id.get_secret_value(),
            "client_secret": self.client_secret.get_secret_value(),
        }

    def redaction_values(self) -> tuple[str, str]:
        """Return sensitive values only for defensive message redaction."""
        return (
            self.client_id.get_secret_value(),
            self.client_secret.get_secret_value(),
        )


class AuthToken(BaseModel):
    """Documented RTZR authentication response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    access_token: SecretStr
    expire_at: int = Field(gt=0)


class RTZRTokenProvider:
    """Fetch and cache an RTZR access token until shortly before expiry."""

    def __init__(
        self,
        credentials: RTZRCredentials,
        *,
        api_base: str = DEFAULT_API_BASE,
        refresh_margin_seconds: int = DEFAULT_REFRESH_MARGIN_SECONDS,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if refresh_margin_seconds < 0:
            raise ValueError("refresh_margin_seconds must be non-negative")
        self._credentials = credentials
        self._api_base = api_base.rstrip("/")
        self._refresh_margin_seconds = refresh_margin_seconds
        self._clock = clock
        self._token: AuthToken | None = None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)

    async def get_access_token(self) -> str:
        """Return a cached token or authenticate when it is near expiry."""
        if self._token is None or self._should_refresh(self._token):
            self._token = await self._authenticate()
        return self._token.access_token.get_secret_value()

    async def aclose(self) -> None:
        """Close the internally created HTTP client."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> RTZRTokenProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _should_refresh(self, token: AuthToken) -> bool:
        return token.expire_at <= self._clock() + self._refresh_margin_seconds

    async def _authenticate(self) -> AuthToken:
        try:
            response = await self._client.post(
                f"{self._api_base}/v1/authenticate",
                data=self._credentials.as_form_data(),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError:
            raise RTZRAuthenticationError(
                "Could not reach the RTZR authentication service"
            ) from None

        if not response.is_success:
            code, detail = self._safe_error_detail(response)
            status = response.status_code
            message = f"RTZR authentication failed (HTTP {status}"
            if code:
                message += f", code {code}"
            message += ")"
            if detail:
                message += f": {detail}"
            raise RTZRAuthenticationError(message, status_code=status, code=code)

        try:
            return AuthToken.model_validate(response.json())
        except (ValueError, ValidationError):
            raise RTZRAuthenticationError(
                "RTZR authentication returned an invalid token response",
                status_code=response.status_code,
            ) from None

    def _safe_error_detail(self, response: httpx.Response) -> tuple[str | None, str | None]:
        try:
            payload = response.json()
        except ValueError:
            return None, None
        if not isinstance(payload, dict):
            return None, None
        code = payload.get("code")
        message = payload.get("msg") or payload.get("message")
        safe_code = str(code) if code is not None else None
        safe_message = self._redact(str(message)) if message is not None else None
        return safe_code, safe_message

    def _redact(self, message: str) -> str:
        redacted = message
        for value in self._credentials.redaction_values():
            if value:
                redacted = redacted.replace(value, "[REDACTED]")
        return redacted

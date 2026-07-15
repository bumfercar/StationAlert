"""Minimal RTZR Batch STT client for reproducible baseline experiments."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from nextstop_stt.rtzr.errors import RTZRBatchError

DEFAULT_API_BASE = "https://openapi.vito.ai"
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 3_600.0


class BatchModel(StrEnum):
    """Models documented for RTZR Batch STT."""

    SOMMERS = "sommers"
    WHISPER = "whisper"


class BatchDomain(StrEnum):
    """Domains documented for RTZR Batch STT."""

    GENERAL = "GENERAL"
    CALL = "CALL"


class BatchConfig(BaseModel):
    """Explicit parameters recorded with each Batch baseline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_name: BatchModel
    language: str
    domain: BatchDomain = BatchDomain.GENERAL
    use_diarization: bool = False
    use_itn: bool = True
    use_disfluency_filter: bool = True
    use_profanity_filter: bool = False
    use_paragraph_splitter: bool = True
    paragraph_max_characters: int = Field(default=50, ge=1)
    use_word_timestamp: bool = True
    keywords: tuple[str, ...] = Field(default_factory=tuple, max_length=500)

    @model_validator(mode="after")
    def validate_language(self) -> BatchConfig:
        if not self.language.strip():
            raise ValueError("language must be explicit")
        return self

    def to_request_dict(self) -> dict[str, Any]:
        """Render the nested config structure expected by `/v1/transcribe`."""
        config: dict[str, Any] = {
            "model_name": self.model_name.value,
            "language": self.language,
            "domain": self.domain.value,
            "use_diarization": self.use_diarization,
            "use_itn": self.use_itn,
            "use_disfluency_filter": self.use_disfluency_filter,
            "use_profanity_filter": self.use_profanity_filter,
            "use_paragraph_splitter": self.use_paragraph_splitter,
            "paragraph_splitter": {"max": self.paragraph_max_characters},
            "use_word_timestamp": self.use_word_timestamp,
        }
        if self.keywords:
            config["keywords"] = list(self.keywords)
        return config


class AccessTokenProvider(Protocol):
    async def get_access_token(self) -> str:
        """Return a usable RTZR access token."""


class RTZRBatchClient:
    """Submit one file and poll until RTZR returns a terminal status."""

    def __init__(
        self,
        token_provider: AccessTokenProvider,
        *,
        api_base: str = DEFAULT_API_BASE,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._token_provider = token_provider
        self._api_base = api_base.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=60.0)
        self._clock = clock
        self._sleep = sleep

    async def transcribe_file(
        self,
        source_file: Path,
        config: BatchConfig,
        *,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[str, dict[str, Any]]:
        """Submit a private file and return its id plus completed raw response."""
        if not source_file.is_file():
            raise RTZRBatchError("Batch STT source file was not found")
        if poll_interval_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("poll interval and timeout must be positive")

        token = await self._token_provider.get_access_token()
        try:
            with source_file.open("rb") as audio:
                response = await self._client.post(
                    f"{self._api_base}/v1/transcribe",
                    headers={"Authorization": f"Bearer {token}"},
                    data={"config": json.dumps(config.to_request_dict(), ensure_ascii=False)},
                    files={"file": ("private-audio", audio, "application/octet-stream")},
                )
        except httpx.HTTPError:
            raise RTZRBatchError("Could not submit the RTZR Batch STT request") from None

        payload = _response_payload(response, action="submit")
        transcribe_id = payload.get("id")
        if not isinstance(transcribe_id, str) or not transcribe_id:
            raise RTZRBatchError("RTZR Batch STT returned an invalid job id")

        result = await self.wait_for_result(
            transcribe_id,
            token=token,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
        return transcribe_id, result

    async def wait_for_result(
        self,
        transcribe_id: str,
        *,
        token: str,
        poll_interval_seconds: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Poll at the documented interval until completed or failed."""
        deadline = self._clock() + timeout_seconds
        while self._clock() < deadline:
            try:
                response = await self._client.get(
                    f"{self._api_base}/v1/transcribe/{transcribe_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError:
                raise RTZRBatchError("Could not poll the RTZR Batch STT job") from None

            payload = _response_payload(response, action="poll")
            status = payload.get("status")
            if status == "completed":
                return payload
            if status == "failed":
                error = payload.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                suffix = f" (code {code})" if code else ""
                raise RTZRBatchError(f"RTZR Batch STT job failed{suffix}")
            if status != "transcribing":
                raise RTZRBatchError("RTZR Batch STT returned an unknown status")
            await self._sleep(poll_interval_seconds)

        raise RTZRBatchError("RTZR Batch STT polling timed out")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _response_payload(response: httpx.Response, *, action: str) -> dict[str, Any]:
    if not response.is_success:
        raise RTZRBatchError(f"RTZR Batch STT {action} failed (HTTP {response.status_code})")
    try:
        payload = response.json()
    except ValueError:
        raise RTZRBatchError(f"RTZR Batch STT {action} returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise RTZRBatchError(f"RTZR Batch STT {action} returned an invalid response")
    return payload

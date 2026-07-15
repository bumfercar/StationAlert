"""RTZR Streaming STT WebSocket client."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator, Callable
from typing import Any, Protocol
from urllib.parse import urlencode

from pydantic import ValidationError
from websockets.asyncio.client import connect

from nextstop_stt.rtzr.errors import RTZRError, RTZRResponseError, RTZRStreamingError
from nextstop_stt.rtzr.models import StreamingConfig, StreamingTranscript

DEFAULT_STREAMING_ENDPOINT = "wss://openapi.vito.ai/v1/transcribe:streaming"
DEFAULT_OPEN_TIMEOUT_SECONDS = 10.0
DEFAULT_CLOSE_TIMEOUT_SECONDS = 10.0


class AccessTokenProvider(Protocol):
    """Minimal token provider contract used by the Streaming client."""

    async def get_access_token(self) -> str:
        """Return a usable RTZR access token."""


class WebSocketSender(Protocol):
    """Subset of a WebSocket connection required by the audio sender."""

    async def send(self, message: bytes | str) -> None:
        """Send one binary audio frame or the text `EOS` marker."""


class RTZRStreamingClient:
    """Send audio frames and yield validated partial/final transcripts."""

    def __init__(
        self,
        token_provider: AccessTokenProvider,
        config: StreamingConfig,
        *,
        endpoint: str = DEFAULT_STREAMING_ENDPOINT,
        connector: Callable[..., Any] = connect,
    ) -> None:
        self._token_provider = token_provider
        self._config = config
        self._endpoint = endpoint
        self._connector = connector

    @property
    def streaming_url(self) -> str:
        """Return the endpoint with every experiment parameter made explicit."""
        return f"{self._endpoint}?{urlencode(self._config.to_query_params())}"

    async def transcribe(
        self,
        frames: AsyncIterable[bytes],
    ) -> AsyncIterator[StreamingTranscript]:
        """Yield validated RTZR responses while audio is being sent."""
        token = await self._token_provider.get_access_token()
        try:
            async with self._connector(
                self.streaming_url,
                additional_headers={"Authorization": f"Bearer {token}"},
                open_timeout=DEFAULT_OPEN_TIMEOUT_SECONDS,
                close_timeout=DEFAULT_CLOSE_TIMEOUT_SECONDS,
            ) as websocket:
                sender = asyncio.create_task(send_audio_frames(websocket, frames))
                try:
                    async for message in websocket:
                        if sender.done():
                            sender.result()
                        yield parse_streaming_message(message)
                    await sender
                finally:
                    if not sender.done():
                        sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)
        except RTZRError:
            raise
        except Exception:
            raise RTZRStreamingError("RTZR Streaming session failed") from None


async def send_audio_frames(
    websocket: WebSocketSender,
    frames: AsyncIterable[bytes],
) -> None:
    """Send headerless audio frames and terminate with the documented `EOS`."""
    sent_frames = 0
    async for frame in frames:
        if not isinstance(frame, bytes):
            raise RTZRStreamingError("Audio frames must be bytes")
        if not frame:
            continue
        if sent_frames == 0 and frame.startswith(b"RIFF"):
            raise RTZRStreamingError(
                "LINEAR16 must be raw PCM; a WAV RIFF header was detected"
            )
        await websocket.send(frame)
        sent_frames += 1

    if sent_frames == 0:
        raise RTZRStreamingError("Audio source produced no frames")
    await websocket.send("EOS")


def parse_streaming_message(message: str | bytes) -> StreamingTranscript:
    """Parse one documented RTZR response without exposing its transcript on failure."""
    try:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        payload = json.loads(message)
        return StreamingTranscript.model_validate(payload)
    except ValidationError as error:
        issues = ", ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}:{issue['type']}"
            for issue in error.errors(include_input=False, include_url=False)
        )
        raise RTZRResponseError(f"Invalid RTZR Streaming response schema ({issues})") from None
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise RTZRResponseError("Invalid RTZR Streaming response JSON") from None

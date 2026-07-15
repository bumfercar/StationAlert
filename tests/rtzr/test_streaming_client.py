from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from nextstop_stt.rtzr.errors import RTZRResponseError, RTZRStreamingError
from nextstop_stt.rtzr.models import StreamingConfig, StreamingDomain
from nextstop_stt.rtzr.streaming_client import (
    RTZRStreamingClient,
    parse_streaming_message,
    send_audio_frames,
)


class FakeTokenProvider:
    async def get_access_token(self) -> str:
        return "test-access-token"


class FakeWebSocket:
    def __init__(self, messages: list[str | bytes] | None = None) -> None:
        self.messages = messages or []
        self.sent: list[bytes | str] = []

    async def send(self, message: bytes | str) -> None:
        self.sent.append(message)

    async def __aiter__(self) -> AsyncIterator[str | bytes]:
        for message in self.messages:
            await asyncio.sleep(0)
            yield message


class FakeConnectionContext:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *_: object) -> None:
        return None


class RecordingConnector:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket
        self.url: str | None = None
        self.kwargs: dict[str, Any] = {}

    def __call__(self, url: str, **kwargs: Any) -> FakeConnectionContext:
        self.url = url
        self.kwargs = kwargs
        return FakeConnectionContext(self.websocket)


def test_client_sends_frames_eos_and_yields_partial_and_final() -> None:
    websocket = FakeWebSocket(
        [
            _message(final=False, duration=0, text="이번 역은"),
            _message(final=True, duration=2_000, text="이번 역은 군자역입니다."),
        ]
    )
    connector = RecordingConnector(websocket)
    config = StreamingConfig(sample_rate=16_000, domain=StreamingDomain.MEETING)
    client = RTZRStreamingClient(FakeTokenProvider(), config, connector=connector)

    async def scenario() -> list[str]:
        responses = [response async for response in client.transcribe(_frames())]
        return [response.primary_text for response in responses]

    assert asyncio.run(scenario()) == ["이번 역은", "이번 역은 군자역입니다."]
    assert websocket.sent == [b"\x01\x02", b"\x03\x04", "EOS"]
    assert connector.kwargs["additional_headers"] == {
        "Authorization": "Bearer test-access-token"
    }
    assert connector.url is not None
    query = parse_qs(urlparse(connector.url).query)
    assert query["sample_rate"] == ["16000"]
    assert query["encoding"] == ["LINEAR16"]
    assert query["model_name"] == ["sommers_ko"]
    assert query["domain"] == ["MEETING"]
    assert query["use_itn"] == ["true"]
    assert query["use_disfluency_filter"] == ["false"]


def test_audio_sender_rejects_wav_header() -> None:
    websocket = FakeWebSocket()

    async def frames() -> AsyncIterator[bytes]:
        yield b"RIFF\x00\x00\x00\x00WAVE"

    with pytest.raises(RTZRStreamingError, match="RIFF header"):
        asyncio.run(send_audio_frames(websocket, frames()))
    assert websocket.sent == []


def test_audio_sender_rejects_empty_source() -> None:
    websocket = FakeWebSocket()

    async def frames() -> AsyncIterator[bytes]:
        if False:
            yield b""

    with pytest.raises(RTZRStreamingError, match="no frames"):
        asyncio.run(send_audio_frames(websocket, frames()))
    assert websocket.sent == []


def test_invalid_response_does_not_echo_private_transcript() -> None:
    private_text = "승객의 비공개 대화"

    with pytest.raises(RTZRResponseError) as captured:
        parse_streaming_message(f'{{"unexpected": "{private_text}"}}')

    assert private_text not in str(captured.value)


def test_parse_error_reports_only_schema_location_and_type() -> None:
    private_text = "private passenger speech"

    with pytest.raises(RTZRResponseError) as captured:
        parse_streaming_message(
            json.dumps(
                {
                    "seq": 1,
                    "start_at": 0,
                    "duration": 0,
                    "final": False,
                    "alternatives": [{"text": private_text}],
                }
            )
        )

    assert "alternatives.0.confidence:missing" in str(captured.value)
    assert private_text not in str(captured.value)


async def _frames() -> AsyncIterator[bytes]:
    yield b"\x01\x02"
    yield b"\x03\x04"


def _message(*, final: bool, duration: int, text: str) -> str:
    return (
        '{"seq":1,"start_at":100,"duration":'
        f'{duration},"final":{str(final).lower()},'
        f'"alternatives":[{{"text":"{text}","confidence":0.9}}]}}'
    )

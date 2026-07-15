from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from nextstop_stt.rtzr.batch_client import (
    BatchConfig,
    BatchDomain,
    BatchModel,
    RTZRBatchClient,
)
from nextstop_stt.rtzr.errors import RTZRBatchError


class FakeTokenProvider:
    async def get_access_token(self) -> str:
        return "test-token"


def test_batch_config_records_explicit_baseline_parameters() -> None:
    config = BatchConfig(
        model_name=BatchModel.SOMMERS,
        language="ko",
        domain=BatchDomain.GENERAL,
    )

    assert config.to_request_dict() == {
        "model_name": "sommers",
        "language": "ko",
        "domain": "GENERAL",
        "use_diarization": False,
        "use_itn": True,
        "use_disfluency_filter": True,
        "use_profanity_filter": False,
        "use_paragraph_splitter": True,
        "paragraph_splitter": {"max": 50},
        "use_word_timestamp": True,
    }


def test_batch_client_submits_and_polls_to_completion(tmp_path: Path) -> None:
    source = tmp_path / "private.m4a"
    source.write_bytes(b"private-audio-bytes")
    get_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_calls
        assert request.headers["authorization"] == "Bearer test-token"
        if request.method == "POST":
            assert request.url.path == "/v1/transcribe"
            assert b'"model_name": "sommers"' in request.content
            return httpx.Response(200, json={"id": "job-1"})
        get_calls += 1
        if get_calls == 1:
            return httpx.Response(200, json={"id": "job-1", "status": "transcribing"})
        return httpx.Response(
            200,
            json={"id": "job-1", "status": "completed", "results": {"utterances": []}},
        )

    async def scenario() -> tuple[str, dict]:
        client = _client(handler)
        return await client.transcribe_file(
            source,
            BatchConfig(model_name=BatchModel.SOMMERS, language="ko"),
            poll_interval_seconds=0.001,
        )

    transcribe_id, result = asyncio.run(scenario())
    assert transcribe_id == "job-1"
    assert result["status"] == "completed"
    assert get_calls == 2


def test_batch_failure_does_not_echo_server_message(tmp_path: Path) -> None:
    source = tmp_path / "private.m4a"
    source.write_bytes(b"audio")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job-2"})
        return httpx.Response(
            200,
            json={
                "id": "job-2",
                "status": "failed",
                "error": {"code": "E500", "message": "private passenger transcript"},
            },
        )

    async def scenario() -> None:
        client = _client(handler)
        with pytest.raises(RTZRBatchError) as captured:
            await client.transcribe_file(
                source,
                BatchConfig(model_name=BatchModel.SOMMERS, language="ko"),
            )
        assert "E500" in str(captured.value)
        assert "private passenger transcript" not in str(captured.value)

    asyncio.run(scenario())


def test_batch_config_can_record_whisper_keywords() -> None:
    config = BatchConfig(
        model_name=BatchModel.WHISPER,
        language="ko",
        keywords=("어린이대공원역",),
    )

    request = config.to_request_dict()
    assert request["model_name"] == "whisper"
    assert request["language"] == "ko"
    assert request["keywords"] == ["어린이대공원역"]


def _client(handler) -> RTZRBatchClient:
    transport = httpx.MockTransport(handler)
    return RTZRBatchClient(FakeTokenProvider(), client=httpx.AsyncClient(transport=transport))

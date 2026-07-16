import asyncio
import json
import re
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import nextstop_stt.cli as cli_module
from nextstop_stt.cli import app
from nextstop_stt.rtzr.models import StreamingTranscript

runner = CliRunner()
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _unstyle(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def test_help_exposes_only_user_facing_commands() -> None:
    result = runner.invoke(app, ["--help"], terminal_width=160)
    output = _unstyle(result.output)

    assert result.exit_code == 0
    assert "RTZR Streaming STT" in output
    assert "journey-demo" in output
    assert "check-auth" in output
    assert "batch-file" not in output
    assert "stream-file" not in output
    assert "evaluate-run" not in output


def test_demo_scripts_use_fixed_repository_audio() -> None:
    project_root = Path(__file__).resolve().parents[1]
    shell_script = (project_root / "scripts" / "run_demo.sh").read_text(encoding="utf-8")
    powershell_script = (project_root / "scripts" / "run_demo.ps1").read_text(encoding="utf-8")

    assert "subwayaudio.m4a" in shell_script
    assert "subwayaudio.m4a" in powershell_script
    assert "--source-file" not in shell_script
    assert "--source-file" not in powershell_script


def test_version_is_available() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_check_auth_missing_credentials_is_understandable(monkeypatch) -> None:
    monkeypatch.delenv("RTZR_CLIENT_ID", raising=False)
    monkeypatch.delenv("RTZR_CLIENT_SECRET", raising=False)

    result = runner.invoke(app, ["check-auth"])

    assert result.exit_code == 1
    assert "RTZR_CLIENT_ID" in result.output
    assert "RTZR_CLIENT_SECRET" in result.output


def test_check_auth_never_prints_token(monkeypatch) -> None:
    private_token = "private-access-token"

    class FakeCredentials:
        @classmethod
        def from_env(cls):
            return cls()

    class FakeTokenProvider:
        def __init__(self, _credentials) -> None:
            pass

        async def get_access_token(self) -> str:
            return private_token

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(cli_module, "RTZRCredentials", FakeCredentials)
    monkeypatch.setattr(cli_module, "RTZRTokenProvider", FakeTokenProvider)

    result = runner.invoke(app, ["check-auth"])

    assert result.exit_code == 0
    assert "authentication succeeded" in result.output
    assert private_token not in result.output


def test_journey_demo_help_keeps_inputs_minimal() -> None:
    result = runner.invoke(app, ["journey-demo", "--help"], terminal_width=180)
    output = _unstyle(result.output)

    assert result.exit_code == 0
    assert "--destination" in output
    assert "--show-text" in output
    assert "--source-file" not in output
    assert "--domain" not in output
    assert "--keyword-score" not in output
    assert "--preprocess" not in output


def test_journey_demo_uses_fixed_settings_and_shows_arrival(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "subwayaudio.m4a").write_bytes(b"private audio placeholder")
    captured = {}

    async def fake_run_journey_stream(**kwargs):
        captured.update(kwargs)
        update = kwargs["destination_tracker"].observe("어린이대공원")
        cli_module.typer.echo(cli_module._station_row(1, update))
        return 3, 1

    monkeypatch.setattr(cli_module, "_run_journey_stream", fake_run_journey_stream)
    monkeypatch.setattr(cli_module, "probe_audio_duration_ms", lambda _: 60_000)

    result = runner.invoke(
        app,
        ["journey-demo", "--destination", "어린이대공원", "--yes"],
    )

    assert result.exit_code == 0
    assert "곧 도착합니다" in result.output
    assert "이번 역에서 하차하세요" in result.output
    assert "00. 경로 기준 공릉역 출발" in result.output
    assert "인식된 역 1개" in result.output
    assert captured["show_text"] is False
    scores = {boost.text: boost.score for boost in captured["keywords"]}
    assert scores["어린이대공원"] == 2.0
    assert scores["어린이 대공원"] == 2.0
    assert scores["세종대"] == 2.0
    assert scores["군자"] == 1.0


def test_journey_demo_rejects_destination_outside_recording(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "subwayaudio.m4a").write_bytes(b"private audio placeholder")

    result = runner.invoke(
        app,
        ["journey-demo", "--destination", "중계", "--yes"],
    )

    assert result.exit_code == 1
    assert "공릉~어린이대공원" in result.output


def test_journey_demo_requires_fixed_audio_at_repository_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["journey-demo", "--destination", "군자", "--yes"],
    )

    assert result.exit_code == 1
    assert "subwayaudio.m4a" in result.output


def test_replay_clock_formats_short_and_long_audio() -> None:
    assert cli_module._clock_text(20_000) == "00:20"
    assert cli_module._clock_text(1_179_456) == "19:39"
    assert cli_module._clock_text(3_661_000) == "01:01:01"


def test_journey_summary_keeps_station_list_compact() -> None:
    tracker = cli_module.JourneyTracker("어린이대공원")

    first = tracker.observe("먹골")
    second = tracker.observe("중화")

    assert cli_module._journey_summary(first) == (
        "판별 중(다음 역 인식 대기) · 목적지까지 8정거장"
    )
    assert cli_module._journey_summary(second) == (
        "어린이대공원 방향 · 목적지까지 7정거장"
    )


def test_replay_display_exposes_activity_without_transcript() -> None:
    display = cli_module._ReplayDisplay(
        duration_ms=60_000,
        started_at=0.0,
        console=Console(force_terminal=False),
    )

    status = display._status_text(12_000).plain
    assert "RTZR p/f" not in status
    assert "후보" not in status
    assert "역 방송 대기" in status


def test_interrupted_stream_saves_received_responses(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "subwayaudio.m4a").write_bytes(b"private audio placeholder")
    output = tmp_path / "results" / "private" / "interrupted.json"
    output.parent.mkdir(parents=True)
    _install_stream_fakes(monkeypatch, ("공릉",), interrupt=True)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            cli_module._run_journey_stream(
                duration_ms=60_000,
                destination_tracker=cli_module.JourneyTracker(
                    "어린이대공원",
                    initial_station="공릉",
                ),
                keywords=(),
                output_file=output,
                show_text=False,
            )
        )

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["run"]["status"] == "interrupted"
    assert artifact["summary"]["final_count"] == 1
    assert len(artifact["responses"]) == 1


def test_stream_stops_after_destination_arrival(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "subwayaudio.m4a").write_bytes(b"private audio placeholder")
    output = tmp_path / "results" / "private" / "arrived.json"
    output.parent.mkdir(parents=True)
    fake_client = _install_stream_fakes(
        monkeypatch,
        ("이번 역은 어린이대공원역입니다", "이번 역은 군자역입니다"),
    )

    result = asyncio.run(
        cli_module._run_journey_stream(
            duration_ms=60_000,
            destination_tracker=cli_module.JourneyTracker(
                "어린이대공원",
                initial_station="공릉",
            ),
            keywords=(),
            output_file=output,
            show_text=False,
        )
    )

    assert result == (1, 1)
    assert fake_client.emitted == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["run"]["status"] == "completed"
    assert artifact["journey"][0]["status"] == "arrived"


def _install_stream_fakes(monkeypatch, texts: tuple[str, ...], *, interrupt: bool = False):
    class FakeCredentials:
        @classmethod
        def from_env(cls):
            return cls()

    class FakeProvider:
        def __init__(self, _credentials) -> None:
            pass

        async def aclose(self) -> None:
            return None

    class FakeSource:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def frames(self):
            yield b"linear16"

    class FakeClient:
        emitted = 0

        def __init__(self, _provider, _config) -> None:
            pass

        async def transcribe(self, _frames):
            for seq, text in enumerate(texts, start=1):
                FakeClient.emitted += 1
                yield StreamingTranscript.model_validate(
                    {
                        "seq": seq,
                        "start_at": seq * 1_000,
                        "duration": 500,
                        "final": True,
                        "alternatives": [{"text": text, "confidence": 0.8}],
                    }
                )
            if interrupt:
                raise asyncio.CancelledError

    class FakeDisplay:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        def add_transcript(self, *_args, **_kwargs) -> None:
            pass

        def add_station(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(cli_module, "RTZRCredentials", FakeCredentials)
    monkeypatch.setattr(cli_module, "RTZRTokenProvider", FakeProvider)
    monkeypatch.setattr(cli_module, "FFmpegPCMSource", FakeSource)
    monkeypatch.setattr(cli_module, "RTZRStreamingClient", FakeClient)
    monkeypatch.setattr(cli_module, "_ReplayDisplay", FakeDisplay)
    return FakeClient

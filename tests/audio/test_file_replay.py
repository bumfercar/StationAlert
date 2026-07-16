from __future__ import annotations

import asyncio
import shutil
import wave
from pathlib import Path

import pytest

from nextstop_stt.audio.errors import AudioSourceError
from nextstop_stt.audio.file_replay import (
    AudioPreprocessPreset,
    FFmpegPCMSource,
    probe_audio_duration_ms,
)


def test_source_rejects_missing_file_without_exposing_path(tmp_path: Path) -> None:
    private_path = tmp_path / "private-passenger-recording.m4a"

    with pytest.raises(AudioSourceError) as captured:
        FFmpegPCMSource(private_path, sample_rate=16_000)

    assert str(private_path) not in str(captured.value)
    assert private_path.name not in str(captured.value)


def test_command_requests_headerless_mono_linear16(tmp_path: Path) -> None:
    audio_path = _write_silence(tmp_path / "sample.wav")
    source = FFmpegPCMSource(
        audio_path,
        sample_rate=16_000,
        start_ms=1_500,
        duration_ms=2_000,
    )

    command = source._command("ffmpeg")

    assert command[-2:] == ("s16le", "pipe:1")
    assert command[command.index("-ac") : command.index("-ac") + 2] == ("-ac", "1")
    assert command[command.index("-ar") : command.index("-ar") + 2] == ("-ar", "16000")
    assert command[command.index("-ss") : command.index("-ss") + 2] == ("-ss", "1.500")
    assert command[command.index("-t") : command.index("-t") + 2] == ("-t", "2.000")


def test_subway_preprocess_adds_auditable_ffmpeg_filter(tmp_path: Path) -> None:
    audio_path = _write_silence(tmp_path / "sample.wav")
    source = FFmpegPCMSource(
        audio_path,
        sample_rate=16_000,
        preprocess=AudioPreprocessPreset.SUBWAY_SPEECH_V1,
    )

    command = source._command("ffmpeg")

    assert "-af" in command
    assert source.preprocess.filter_graph in command
    assert "highpass=f=100" in source.preprocess.filter_graph
    assert "afftdn=nr=8" in source.preprocess.filter_graph


def test_rumble_cut_keeps_filter_chain_minimal(tmp_path: Path) -> None:
    audio_path = _write_silence(tmp_path / "sample.wav")
    source = FFmpegPCMSource(
        audio_path,
        sample_rate=16_000,
        preprocess=AudioPreprocessPreset.SUBWAY_RUMBLE_CUT_V1,
    )

    assert source.preprocess.filter_graph == "highpass=f=100:p=2"


def test_probe_audio_duration_returns_milliseconds_without_shell(tmp_path: Path) -> None:
    audio_path = _write_silence(tmp_path / "sample.wav")
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0, "stdout": "1.234\n"})()

    duration_ms = probe_audio_duration_ms(
        audio_path,
        tool_finder=lambda name: name,
        runner=runner,
    )

    assert duration_ms == 1_234
    assert captured["command"][0] == "ffprobe"
    assert captured["kwargs"]["check"] is False


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
def test_ffmpeg_output_is_raw_pcm_without_wav_header(tmp_path: Path) -> None:
    audio_path = _write_silence(tmp_path / "sample.wav", sample_rate=48_000, duration_ms=100)
    source = FFmpegPCMSource(audio_path, sample_rate=8_000, realtime=False)

    async def collect() -> bytes:
        return b"".join([frame async for frame in source.frames()])

    pcm = asyncio.run(collect())

    assert not pcm.startswith(b"RIFF")
    assert len(pcm) == 8_000 * 2 * 100 // 1_000


def _write_silence(
    path: Path,
    *,
    sample_rate: int = 8_000,
    duration_ms: int = 100,
) -> Path:
    frame_count = sample_rate * duration_ms // 1_000
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * frame_count)
    return path

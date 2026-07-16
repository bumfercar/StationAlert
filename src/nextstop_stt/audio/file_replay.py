"""FFmpeg-backed, real-time-paced raw PCM file replay."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import StrEnum
from pathlib import Path

from nextstop_stt.audio.errors import AudioSourceError

PCM_SAMPLE_WIDTH_BYTES = 2
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 48_000
DEFAULT_CHUNK_DURATION_MS = 100


class AudioPreprocessPreset(StrEnum):
    """FFmpeg preprocessing used by the fixed subway demonstration."""

    NONE = "none"
    SUBWAY_RUMBLE_CUT_V1 = "subway_rumble_cut_v1"

    @property
    def filter_graph(self) -> str | None:
        if self is AudioPreprocessPreset.SUBWAY_RUMBLE_CUT_V1:
            return "highpass=f=100:p=2"
        return None


def probe_audio_duration_ms(
    input_path: Path,
    *,
    tool_finder: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """Return media duration without exposing the private path in errors."""
    if not input_path.is_file():
        raise AudioSourceError("Audio input file was not found")
    ffprobe = tool_finder("ffprobe")
    if ffprobe is None:
        raise AudioSourceError("FFprobe is required but was not found")
    completed = runner(
        (
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AudioSourceError("FFprobe could not inspect the audio")
    try:
        duration_ms = round(float(completed.stdout.strip()) * 1_000)
    except ValueError:
        raise AudioSourceError("FFprobe returned an invalid audio duration") from None
    if duration_ms <= 0:
        raise AudioSourceError("Audio duration must be positive")
    return duration_ms


class FFmpegPCMSource:
    """Decode a local audio segment into headerless mono LINEAR16 frames."""

    def __init__(
        self,
        input_path: Path,
        *,
        sample_rate: int,
        start_ms: int = 0,
        duration_ms: int | None = None,
        chunk_duration_ms: int = DEFAULT_CHUNK_DURATION_MS,
        preprocess: AudioPreprocessPreset = AudioPreprocessPreset.NONE,
        realtime: bool = True,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not input_path.is_file():
            raise AudioSourceError("Audio input file was not found")
        if not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
            raise AudioSourceError("sample_rate must be between 8000 and 48000 Hz")
        if start_ms < 0:
            raise AudioSourceError("start_ms must be non-negative")
        if duration_ms is not None and duration_ms <= 0:
            raise AudioSourceError("duration_ms must be positive")
        if chunk_duration_ms <= 0:
            raise AudioSourceError("chunk_duration_ms must be positive")

        bytes_per_chunk = sample_rate * PCM_SAMPLE_WIDTH_BYTES * chunk_duration_ms // 1_000
        bytes_per_chunk -= bytes_per_chunk % PCM_SAMPLE_WIDTH_BYTES
        if bytes_per_chunk == 0:
            raise AudioSourceError("chunk_duration_ms is too small for the sample rate")

        self._input_path = input_path
        self.sample_rate = sample_rate
        self.start_ms = start_ms
        self.duration_ms = duration_ms
        self.chunk_duration_ms = chunk_duration_ms
        self.preprocess = preprocess
        self.realtime = realtime
        self.bytes_per_chunk = bytes_per_chunk
        self._bytes_per_second = sample_rate * PCM_SAMPLE_WIDTH_BYTES
        self._clock = clock
        self._sleep = sleep

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield raw PCM frames, paced from the first byte when realtime is enabled."""
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise AudioSourceError("FFmpeg is required but was not found")

        process = await asyncio.create_subprocess_exec(
            *self._command(ffmpeg),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            raise AudioSourceError("FFmpeg pipes could not be created")

        started_at = self._clock()
        sent_bytes = 0
        try:
            while chunk := await process.stdout.read(self.bytes_per_chunk):
                if self.realtime:
                    target_time = started_at + sent_bytes / self._bytes_per_second
                    delay = target_time - self._clock()
                    if delay > 0:
                        await self._sleep(delay)
                yield chunk
                sent_bytes += len(chunk)

            stderr = await process.stderr.read()
            return_code = await process.wait()
            if return_code != 0:
                raise AudioSourceError(
                    f"FFmpeg could not decode the audio (exit code {return_code})"
                ) from None
            if stderr:
                # FFmpeg runs with loglevel=error; a successful run should stay silent.
                raise AudioSourceError("FFmpeg reported an unexpected decoding error")
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()

    def _command(self, ffmpeg: str) -> tuple[str, ...]:
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(self._input_path),
        ]
        if self.start_ms:
            command.extend(("-ss", _seconds(self.start_ms)))
        if self.duration_ms is not None:
            command.extend(("-t", _seconds(self.duration_ms)))
        if self.preprocess.filter_graph is not None:
            command.extend(("-af", self.preprocess.filter_graph))
        command.extend(
            (
                "-vn",
                "-map_metadata",
                "-1",
                "-ac",
                "1",
                "-ar",
                str(self.sample_rate),
                "-acodec",
                "pcm_s16le",
                "-f",
                "s16le",
                "pipe:1",
            )
        )
        return tuple(command)


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1_000:.3f}"

"""Create model-independent audio chunks for human review."""

from __future__ import annotations

import csv
import math
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from nextstop_stt.audio.errors import AudioSourceError

DEFAULT_CHUNK_SECONDS = 60
DEFAULT_SAMPLE_RATE = 16_000
LABEL_FIELDS = (
    "segment_id",
    "start_ms",
    "end_ms",
    "review_status",
    "has_announcement",
    "station",
    "announcement_type",
    "reference_text",
    "overlapping_speech",
    "noise_level",
    "notes",
)


class ReviewChunkPreparer:
    """Split a private recording into fixed windows and create a label sheet."""

    def __init__(
        self,
        *,
        tool_finder: Callable[[str], str | None] = shutil.which,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._tool_finder = tool_finder
        self._runner = runner

    def prepare(
        self,
        source_file: Path,
        output_dir: Path,
        *,
        chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> tuple[int, Path]:
        """Return the number of fixed chunks and the private CSV path."""
        if not source_file.is_file():
            raise AudioSourceError("Audio input file was not found")
        if chunk_seconds <= 0:
            raise AudioSourceError("chunk_seconds must be positive")
        if sample_rate <= 0:
            raise AudioSourceError("sample_rate must be positive")
        if output_dir.exists() and any(output_dir.iterdir()):
            raise AudioSourceError("Review output directory must be empty")

        ffmpeg = self._required_tool("ffmpeg")
        ffprobe = self._required_tool("ffprobe")
        duration_ms = self._duration_ms(ffprobe, source_file)

        output_dir.mkdir(parents=True, exist_ok=True)
        command = self._split_command(
            ffmpeg,
            source_file,
            output_dir,
            chunk_seconds=chunk_seconds,
            sample_rate=sample_rate,
        )
        completed = self._runner(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise AudioSourceError("FFmpeg could not create private review chunks")

        chunk_count = math.ceil(duration_ms / (chunk_seconds * 1_000))
        expected = [output_dir / f"chunk-{index:03d}.wav" for index in range(chunk_count)]
        if not all(path.is_file() for path in expected):
            raise AudioSourceError("FFmpeg produced an incomplete review chunk set")

        label_sheet = output_dir / "labels.csv"
        self._write_label_sheet(
            label_sheet,
            duration_ms=duration_ms,
            chunk_seconds=chunk_seconds,
            chunk_count=chunk_count,
        )
        return chunk_count, label_sheet

    def _required_tool(self, name: str) -> str:
        executable = self._tool_finder(name)
        if executable is None:
            raise AudioSourceError(f"{name} is required but was not found")
        return executable

    def _duration_ms(self, ffprobe: str, source_file: Path) -> int:
        command = (
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source_file),
        )
        completed = self._runner(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise AudioSourceError("FFprobe could not inspect the private audio")
        try:
            duration_ms = round(float(completed.stdout.strip()) * 1_000)
        except ValueError:
            raise AudioSourceError("FFprobe returned an invalid audio duration") from None
        if duration_ms <= 0:
            raise AudioSourceError("Audio duration must be positive")
        return duration_ms

    @staticmethod
    def _split_command(
        ffmpeg: str,
        source_file: Path,
        output_dir: Path,
        *,
        chunk_seconds: int,
        sample_rate: int,
    ) -> Sequence[str]:
        return (
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_file),
            "-map",
            "0:a:0",
            "-vn",
            "-map_metadata",
            "-1",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-acodec",
            "pcm_s16le",
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-reset_timestamps",
            "1",
            str(output_dir / "chunk-%03d.wav"),
        )

    @staticmethod
    def _write_label_sheet(
        path: Path,
        *,
        duration_ms: int,
        chunk_seconds: int,
        chunk_count: int,
    ) -> None:
        chunk_ms = chunk_seconds * 1_000
        temporary = path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=LABEL_FIELDS)
            writer.writeheader()
            for index in range(chunk_count):
                start_ms = index * chunk_ms
                writer.writerow(
                    {
                        "segment_id": f"chunk-{index:03d}",
                        "start_ms": start_ms,
                        "end_ms": min(start_ms + chunk_ms, duration_ms),
                        "review_status": "pending",
                        "has_announcement": "",
                        "station": "",
                        "announcement_type": "",
                        "reference_text": "",
                        "overlapping_speech": "",
                        "noise_level": "",
                        "notes": "",
                    }
                )
        temporary.replace(path)

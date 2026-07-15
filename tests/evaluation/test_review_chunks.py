from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import pytest

from nextstop_stt.audio.errors import AudioSourceError
from nextstop_stt.evaluation.review_chunks import ReviewChunkPreparer


def test_prepare_creates_fixed_private_chunks_and_label_sheet(tmp_path: Path) -> None:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"private audio")
    output_dir = tmp_path / "review"
    commands: list[tuple[str, ...]] = []

    def runner(command, **_kwargs):
        commands.append(tuple(command))
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="125.250\n", stderr="")
        output_dir.mkdir(parents=True, exist_ok=True)
        for index in range(3):
            (output_dir / f"chunk-{index:03d}.wav").write_bytes(b"RIFF")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    preparer = ReviewChunkPreparer(tool_finder=lambda name: name, runner=runner)
    chunk_count, label_sheet = preparer.prepare(source, output_dir, chunk_seconds=60)

    assert chunk_count == 3
    assert commands[1][commands[1].index("-segment_time") + 1] == "60"
    assert commands[1][commands[1].index("-ac") + 1] == "1"
    assert commands[1][commands[1].index("-ar") + 1] == "16000"
    with label_sheet.open(encoding="utf-8-sig", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert [row["segment_id"] for row in rows] == ["chunk-000", "chunk-001", "chunk-002"]
    assert rows[0]["start_ms"] == "0"
    assert rows[-1]["end_ms"] == "125250"
    assert all(row["review_status"] == "pending" for row in rows)


def test_prepare_rejects_nonempty_output_directory(tmp_path: Path) -> None:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"private audio")
    output_dir = tmp_path / "review"
    output_dir.mkdir()
    (output_dir / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(AudioSourceError, match="must be empty"):
        ReviewChunkPreparer().prepare(source, output_dir)


def test_prepare_does_not_expose_ffmpeg_stderr(tmp_path: Path) -> None:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"private audio")

    def runner(command, **_kwargs):
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="10", stderr="")
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="private passenger file and transcript",
        )

    preparer = ReviewChunkPreparer(tool_finder=lambda name: name, runner=runner)
    with pytest.raises(AudioSourceError) as captured:
        preparer.prepare(source, tmp_path / "review")

    assert "private passenger" not in str(captured.value)

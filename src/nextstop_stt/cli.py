"""Command-line entry point for NextStop STT."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from nextstop_stt import __version__
from nextstop_stt.audio.errors import AudioSourceError
from nextstop_stt.audio.file_replay import FFmpegPCMSource
from nextstop_stt.detection import DestinationAlertDetector
from nextstop_stt.evaluation.review_chunks import ReviewChunkPreparer
from nextstop_stt.rtzr.auth import RTZRCredentials, RTZRTokenProvider
from nextstop_stt.rtzr.batch_client import (
    BatchConfig,
    BatchDomain,
    BatchModel,
    RTZRBatchClient,
)
from nextstop_stt.rtzr.errors import RTZRError
from nextstop_stt.rtzr.models import (
    KeywordBoost,
    StreamingConfig,
    StreamingDomain,
    StreamingModel,
)
from nextstop_stt.rtzr.streaming_client import RTZRStreamingClient

app = typer.Typer(
    add_completion=False,
    help="Evaluate destination alerts built on RTZR Streaming STT.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Run NextStop STT commands."""


@app.command()
def version() -> None:
    """Print the installed NextStop STT version."""
    typer.echo(__version__)


@app.command("check-auth")
def check_auth() -> None:
    """Verify RTZR credentials without printing the access token."""
    try:
        asyncio.run(_request_authentication())
    except RTZRError as error:
        typer.echo(f"Authentication failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("RTZR authentication succeeded. The access token was not printed.")


async def _request_authentication() -> None:
    credentials = RTZRCredentials.from_env()
    provider = RTZRTokenProvider(credentials)
    try:
        await provider.get_access_token()
    finally:
        await provider.aclose()


@app.command("batch-file")
def batch_file(
    source_file: Annotated[
        Path,
        typer.Option(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    output_file: Annotated[
        Path,
        typer.Option(help="Private JSON path under results/private/."),
    ],
    model: Annotated[BatchModel, typer.Option()] = BatchModel.SOMMERS,
    language: Annotated[str, typer.Option()] = "ko",
    domain: Annotated[BatchDomain, typer.Option()] = BatchDomain.GENERAL,
    keyword: Annotated[
        list[str] | None,
        typer.Option("--keyword", help="Repeat to add Batch STT keywords."),
    ] = None,
) -> None:
    """Run a Batch STT baseline and save the private raw response."""
    try:
        safe_output = _private_result_path(output_file)
        elapsed_seconds, utterance_count = asyncio.run(
            _batch_file(
                source_file=source_file,
                output_file=safe_output,
                model=model,
                language=language,
                domain=domain,
                keywords=tuple(keyword or ()),
            )
        )
    except (RTZRError, ValueError) as error:
        typer.echo(f"Batch STT failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        f"Batch STT completed: utterances={utterance_count}, "
        f"elapsed_seconds={elapsed_seconds:.1f}"
    )
    typer.echo(f"Private result saved: {safe_output.as_posix()}")


async def _batch_file(
    *,
    source_file: Path,
    output_file: Path,
    model: BatchModel,
    language: str,
    domain: BatchDomain,
    keywords: tuple[str, ...],
) -> tuple[float, int]:
    credentials = RTZRCredentials.from_env()
    provider = RTZRTokenProvider(credentials)
    client = RTZRBatchClient(provider)
    config = BatchConfig(
        model_name=model,
        language=language,
        domain=domain,
        keywords=keywords,
    )
    started_at = time.monotonic()
    try:
        transcribe_id, response = await client.transcribe_file(source_file, config)
    finally:
        await client.aclose()
        await provider.aclose()
    elapsed_seconds = time.monotonic() - started_at
    results = response.get("results")
    utterances = results.get("utterances", []) if isinstance(results, dict) else []
    utterance_count = len(utterances) if isinstance(utterances, list) else 0
    artifact = {
        "schema_version": 1,
        "run": {
            "created_at": datetime.now(UTC).isoformat(),
            "api": "batch",
            "config": config.to_request_dict(),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "transcribe_id": transcribe_id,
        },
        "response": response,
    }
    _write_private_json(output_file, artifact)
    return elapsed_seconds, utterance_count


def _private_result_path(output_file: Path) -> Path:
    private_root = Path("results/private").resolve()
    resolved = output_file.resolve()
    try:
        resolved.relative_to(private_root)
    except ValueError:
        raise ValueError("output_file must be under results/private/") from None
    return resolved


def _write_private_json(output_file: Path, artifact: dict) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_file.with_suffix(f"{output_file.suffix}.tmp")
    temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output_file)


@app.command("prepare-review")
def prepare_review(
    source_file: Annotated[
        Path,
        typer.Option(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    output_dir: Annotated[
        Path,
        typer.Option(help="Empty private directory under private_audio/."),
    ] = Path("private_audio/review"),
    chunk_seconds: Annotated[int, typer.Option(min=1)] = 60,
) -> None:
    """Create fixed, model-independent chunks for human ground-truth review."""
    try:
        safe_output = _private_audio_dir(output_dir)
        chunk_count, label_sheet = ReviewChunkPreparer().prepare(
            source_file,
            safe_output,
            chunk_seconds=chunk_seconds,
        )
    except (AudioSourceError, ValueError) as error:
        typer.echo(f"Review preparation failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"Private review set created: chunks={chunk_count}")
    typer.echo(f"Label sheet: {label_sheet.as_posix()}")


def _private_audio_dir(output_dir: Path) -> Path:
    private_root = Path("private_audio").resolve()
    resolved = output_dir.resolve()
    try:
        resolved.relative_to(private_root)
    except ValueError:
        raise ValueError("output_dir must be under private_audio/") from None
    return resolved


@app.command("stream-file")
def stream_file(
    source_file: Annotated[
        Path,
        typer.Option(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Local audio file. Private files must stay outside Git.",
        ),
    ],
    duration_ms: Annotated[
        int,
        typer.Option(
            min=1,
            help="Required segment length limit to prevent accidental full-file usage.",
        ),
    ],
    start_ms: Annotated[
        int,
        typer.Option(min=0, help="Segment start from the input file."),
    ] = 0,
    sample_rate: Annotated[int, typer.Option(min=8_000, max=48_000)] = 16_000,
    domain: Annotated[StreamingDomain, typer.Option()] = StreamingDomain.MEETING,
    model: Annotated[StreamingModel, typer.Option()] = StreamingModel.SOMMERS_KO,
    language: Annotated[
        str | None,
        typer.Option(help="Required when model=whisper."),
    ] = None,
    target_station: Annotated[
        str | None,
        typer.Option(help="Selected destination, for example 어린이대공원역."),
    ] = None,
    keyword: Annotated[
        list[str] | None,
        typer.Option(
            "--keyword",
            help="Repeat Korean word[:score] boosts; sommers_ko only.",
        ),
    ] = None,
    output_file: Annotated[
        Path | None,
        typer.Option(help="Optional private JSON path under results/private/."),
    ] = None,
    show_text: Annotated[
        bool,
        typer.Option(help="Print transcript text. Keep disabled for private passenger audio."),
    ] = False,
) -> None:
    """Replay a bounded audio segment through RTZR Streaming STT."""
    try:
        safe_output = _private_result_path(output_file) if output_file else None
        keywords = _parse_keyword_boosts(tuple(keyword or ()))
        partial_count, final_count, alert_count = asyncio.run(
            _stream_file(
                source_file=source_file,
                duration_ms=duration_ms,
                start_ms=start_ms,
                sample_rate=sample_rate,
                domain=domain,
                model=model,
                language=language,
                target_station=target_station,
                keywords=keywords,
                output_file=safe_output,
                show_text=show_text,
            )
        )
    except (AudioSourceError, RTZRError, ValueError) as error:
        typer.echo(f"Streaming failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        f"Streaming completed: partial={partial_count}, final={final_count}, alerts={alert_count}"
    )
    if safe_output is not None:
        typer.echo(f"Private result saved: {safe_output.as_posix()}")


def _parse_keyword_boosts(values: tuple[str, ...]) -> tuple[KeywordBoost, ...]:
    boosts = []
    for value in values:
        text, separator, score_text = value.rpartition(":")
        if separator:
            if not text or not score_text:
                raise ValueError("keyword must use Korean word[:score] format")
            try:
                score = float(score_text)
            except ValueError:
                raise ValueError("keyword score must be a number") from None
        else:
            text = value
            score = 2.0
        boosts.append(KeywordBoost(text=text, score=score))
    return tuple(boosts)


async def _stream_file(
    *,
    source_file: Path,
    duration_ms: int,
    start_ms: int,
    sample_rate: int,
    domain: StreamingDomain,
    model: StreamingModel,
    language: str | None,
    target_station: str | None,
    keywords: tuple[KeywordBoost, ...],
    output_file: Path | None,
    show_text: bool,
) -> tuple[int, int, int]:
    credentials = RTZRCredentials.from_env()
    provider = RTZRTokenProvider(credentials)
    source = FFmpegPCMSource(
        source_file,
        sample_rate=sample_rate,
        start_ms=start_ms,
        duration_ms=duration_ms,
    )
    config = StreamingConfig(
        sample_rate=sample_rate,
        model_name=model,
        domain=domain,
        use_itn=True,
        use_disfluency_filter=False,
        use_profanity_filter=False,
        use_punctuation=False,
        keywords=keywords,
        language=language,
    )
    client = RTZRStreamingClient(provider, config)
    detector = DestinationAlertDetector(target_station) if target_station else None
    partial_count = 0
    final_count = 0
    alert_count = 0
    response_records = []
    decision_records = []
    session_started_at = time.monotonic()
    try:
        async for response in client.transcribe(source.frames()):
            received_elapsed_ms = round((time.monotonic() - session_started_at) * 1_000)
            response_records.append(
                {
                    "received_elapsed_ms": received_elapsed_ms,
                    "response": response.model_dump(mode="json"),
                }
            )
            if response.final:
                final_count += 1
            else:
                partial_count += 1
            if detector is not None:
                decision = detector.evaluate(response)
                decision_records.append(
                    {
                        "transcript_seq": decision.transcript_seq,
                        "should_alert": decision.should_alert,
                        "reason": decision.reason.value,
                        "target_station": decision.target_station,
                    }
                )
                if decision.should_alert:
                    alert_count += 1
                    typer.echo(f"ALERT: {decision.target_station}")
            if show_text:
                state = "FINAL" if response.final else "PARTIAL"
                typer.echo(f"[{state}] {response.primary_text}")
    finally:
        await provider.aclose()
    if output_file is not None:
        _write_private_json(
            output_file,
            {
                "schema_version": 1,
                "run": {
                    "created_at": datetime.now(UTC).isoformat(),
                    "api": "streaming",
                    "config": config.to_query_params(),
                    "segment": {
                        "start_ms": start_ms,
                        "duration_ms": duration_ms,
                    },
                    "session_elapsed_ms": round(
                        (time.monotonic() - session_started_at) * 1_000
                    ),
                },
                "responses": response_records,
                "decisions": decision_records,
            },
        )
    return partial_count, final_count, alert_count


if __name__ == "__main__":
    app()

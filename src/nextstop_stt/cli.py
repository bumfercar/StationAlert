"""Command-line entry point for NextStop STT."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from nextstop_stt import __version__
from nextstop_stt.audio.errors import AudioSourceError
from nextstop_stt.audio.file_replay import FFmpegPCMSource
from nextstop_stt.rtzr.auth import RTZRCredentials, RTZRTokenProvider
from nextstop_stt.rtzr.errors import RTZRError
from nextstop_stt.rtzr.models import StreamingConfig, StreamingDomain, StreamingModel
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
    show_text: Annotated[
        bool,
        typer.Option(help="Print transcript text. Keep disabled for private passenger audio."),
    ] = False,
) -> None:
    """Replay a bounded audio segment through RTZR Streaming STT."""
    try:
        partial_count, final_count = asyncio.run(
            _stream_file(
                source_file=source_file,
                duration_ms=duration_ms,
                start_ms=start_ms,
                sample_rate=sample_rate,
                domain=domain,
                model=model,
                language=language,
                show_text=show_text,
            )
        )
    except (AudioSourceError, RTZRError, ValueError) as error:
        typer.echo(f"Streaming failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"Streaming completed: partial={partial_count}, final={final_count}")


async def _stream_file(
    *,
    source_file: Path,
    duration_ms: int,
    start_ms: int,
    sample_rate: int,
    domain: StreamingDomain,
    model: StreamingModel,
    language: str | None,
    show_text: bool,
) -> tuple[int, int]:
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
        language=language,
    )
    client = RTZRStreamingClient(provider, config)
    partial_count = 0
    final_count = 0
    try:
        async for response in client.transcribe(source.frames()):
            if response.final:
                final_count += 1
            else:
                partial_count += 1
            if show_text:
                state = "FINAL" if response.final else "PARTIAL"
                typer.echo(f"[{state}] {response.primary_text}")
    finally:
        await provider.aclose()
    return partial_count, final_count


if __name__ == "__main__":
    app()

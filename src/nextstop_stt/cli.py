"""Command-line entry point for NextStop STT."""

from __future__ import annotations

import asyncio

import typer

from nextstop_stt import __version__
from nextstop_stt.rtzr.auth import RTZRCredentials, RTZRTokenProvider
from nextstop_stt.rtzr.errors import RTZRError

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


if __name__ == "__main__":
    app()

"""Command-line entry point for NextStop STT."""

from __future__ import annotations

import typer

from nextstop_stt import __version__

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


if __name__ == "__main__":
    app()

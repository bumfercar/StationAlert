"""User-facing CLI for the fixed NextStop STT demonstration."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.status import Status
from rich.text import Text

from nextstop_stt import __version__
from nextstop_stt.audio.errors import AudioSourceError
from nextstop_stt.audio.file_replay import (
    AudioPreprocessPreset,
    FFmpegPCMSource,
    probe_audio_duration_ms,
)
from nextstop_stt.journey import (
    LINE_7_DEMO_ROUTE,
    JourneyStatus,
    JourneyTracker,
    JourneyUpdate,
    TravelDirection,
)
from nextstop_stt.rtzr.auth import RTZRCredentials, RTZRTokenProvider
from nextstop_stt.rtzr.errors import RTZRError
from nextstop_stt.rtzr.models import (
    KeywordBoost,
    StreamingConfig,
    StreamingDomain,
    StreamingModel,
)
from nextstop_stt.rtzr.streaming_client import RTZRStreamingClient
from nextstop_stt.station_extraction import (
    extract_line7_station_mentions,
    line7_keyword_vocabulary,
    line7_station_keyword_vocabulary,
)

DEMO_AUDIO_FILE = Path("subwayaudio.m4a")
DEMO_OUTPUT_FILE = Path("results/private/journey-demo.json")
DEMO_SAMPLE_RATE = 16_000
ROUTE_KEYWORD_SCORE = 1.0
DESTINATION_KEYWORD_SCORE = 2.0
DEMO_PREPROCESS = AudioPreprocessPreset.SUBWAY_RUMBLE_CUT_V1

app = typer.Typer(
    add_completion=False,
    help="RTZR Streaming STT로 지하철 현재역과 목적지 도착을 안내합니다.",
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


@app.command("journey-demo")
def journey_demo(
    destination: Annotated[
        str | None,
        typer.Option(help="하차할 역. 입력하지 않으면 실행 중 질문합니다."),
    ] = None,
    show_text: Annotated[
        bool,
        typer.Option(
            "--show-text/--hide-text",
            help="RTZR final 전사 표시. 주변 승객 음성이 포함될 수 있습니다.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="확인 질문 없이 RTZR 유료 호출을 시작합니다.", hidden=True),
    ] = False,
) -> None:
    """Replay the fixed recording and guide the user to a selected station."""
    typer.echo("NextStop STT")
    typer.echo("공릉역부터 어린이대공원역까지의 녹음에서 현재 역을 인식합니다.")
    typer.echo("새 역이 들리면 한 줄씩 추가되고, 목적지역을 찾으면 자동 종료됩니다.")

    if not DEMO_AUDIO_FILE.is_file():
        typer.echo(
            "실행 실패: 저장소 루트에 subwayaudio.m4a 파일을 배치해주세요.",
            err=True,
        )
        raise typer.Exit(code=1)
    if destination is None:
        destination = typer.prompt("목적지역을 입력해주세요 (공릉~어린이대공원)")

    try:
        tracker = JourneyTracker(destination, initial_station=LINE_7_DEMO_ROUTE[0])
        output_file = _private_result_path(DEMO_OUTPUT_FILE)
        duration_ms = probe_audio_duration_ms(DEMO_AUDIO_FILE)
        keywords = _demo_keyword_boosts(tracker.destination)
    except (AudioSourceError, ValueError) as error:
        typer.echo(f"실행 실패: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"목적지역: {tracker.destination}역")
    typer.echo(f"사용 음성: {DEMO_AUDIO_FILE.name} ({_clock_text(duration_ms)})")
    typer.echo(f"인식 구간: {' → '.join(LINE_7_DEMO_ROUTE)}")
    route_context = tracker.route_context()
    if route_context is not None:
        typer.echo(_route_context_row(route_context))
    if not yes and not typer.confirm("RTZR Streaming 인식을 시작할까요?"):
        typer.echo("사용자가 실행을 취소했습니다.")
        raise typer.Exit()

    typer.echo("음성 인식을 시작합니다. 중단하려면 Ctrl+C를 누르세요.")
    try:
        final_count, station_count = asyncio.run(
            _run_journey_stream(
                duration_ms=duration_ms,
                destination_tracker=tracker,
                keywords=keywords,
                output_file=output_file,
                show_text=show_text,
            )
        )
    except KeyboardInterrupt:
        typer.echo("\n[중단] 지금까지 받은 RTZR 응답을 비공개 결과에 저장했습니다.")
        typer.echo(f"실행 근거 저장: {output_file.as_posix()}")
        raise typer.Exit(code=130) from None
    except (AudioSourceError, RTZRError, ValueError) as error:
        typer.echo(f"실행 실패: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"완료: 인식된 역 {station_count}개, RTZR final {final_count}개")
    if station_count == 0:
        typer.echo("결과: 확정 가능한 역명을 찾지 못했습니다.")
    typer.echo(f"실행 근거 저장: {output_file.as_posix()}")


def _demo_keyword_boosts(destination: str) -> tuple[KeywordBoost, ...]:
    """Boost the full route and give the selected destination higher priority."""
    destination_words = set(line7_station_keyword_vocabulary(destination))
    return tuple(
        KeywordBoost(
            text=text,
            score=(
                DESTINATION_KEYWORD_SCORE
                if text in destination_words
                else ROUTE_KEYWORD_SCORE
            ),
        )
        for text in line7_keyword_vocabulary()
    )


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
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output_file)


async def _run_journey_stream(
    *,
    duration_ms: int,
    destination_tracker: JourneyTracker,
    keywords: tuple[KeywordBoost, ...],
    output_file: Path,
    show_text: bool,
) -> tuple[int, int]:
    """Replay the fixed audio and persist private RTZR evidence."""
    credentials = RTZRCredentials.from_env()
    provider = RTZRTokenProvider(credentials)
    source = FFmpegPCMSource(
        DEMO_AUDIO_FILE,
        sample_rate=DEMO_SAMPLE_RATE,
        duration_ms=duration_ms,
        preprocess=DEMO_PREPROCESS,
    )
    config = StreamingConfig(
        sample_rate=DEMO_SAMPLE_RATE,
        model_name=StreamingModel.SOMMERS_KO,
        domain=StreamingDomain.MEETING,
        use_itn=True,
        use_disfluency_filter=False,
        use_profanity_filter=False,
        use_punctuation=False,
        keywords=keywords,
    )
    client = RTZRStreamingClient(provider, config)
    partial_count = 0
    final_count = 0
    station_count = 0
    candidate_count = 0
    response_records: list[dict] = []
    station_records: list[dict] = []
    journey_records: list[dict] = []
    session_started_at = time.monotonic()
    run_created_at = datetime.now(UTC).isoformat()
    run_status = "connecting"

    def save_artifact(status: str) -> None:
        _write_private_json(
            output_file,
            {
                "schema_version": 1,
                "run": {
                    "created_at": run_created_at,
                    "api": "streaming",
                    "status": status,
                    "config": config.to_query_params(),
                    "audio": {
                        "preprocess": DEMO_PREPROCESS.value,
                        "filter_graph": DEMO_PREPROCESS.filter_graph,
                        "duration_ms": duration_ms,
                    },
                    "station_extraction": {"contextual_recovery": True},
                    "session_elapsed_ms": round(
                        (time.monotonic() - session_started_at) * 1_000
                    ),
                },
                "summary": {
                    "partial_count": partial_count,
                    "final_count": final_count,
                    "station_count": station_count,
                    "candidate_count": candidate_count,
                },
                "responses": response_records,
                "station_detections": station_records,
                "journey": journey_records,
            },
        )

    save_artifact(run_status)
    replay_display = _ReplayDisplay(
        duration_ms=duration_ms,
        started_at=session_started_at,
    )
    replay_display.start()
    try:
        async for response in client.transcribe(source.frames()):
            stop_after_response = False
            response_records.append(
                {
                    "received_elapsed_ms": round(
                        (time.monotonic() - session_started_at) * 1_000
                    ),
                    "response": response.model_dump(mode="json"),
                }
            )
            if response.final:
                final_count += 1
            else:
                partial_count += 1

            if show_text and response.final:
                replay_display.add_transcript(
                    response.primary_text,
                    source_time_ms=response.start_at,
                )

            if response.final:
                mentions = extract_line7_station_mentions(
                    response.primary_text,
                    allow_contextual_recovery=True,
                )
                for mention in mentions:
                    accepted = False
                    if mention.is_current_station_evidence:
                        update = destination_tracker.observe(mention.station)
                        accepted = update.status not in {
                            JourneyStatus.DUPLICATE,
                            JourneyStatus.OUT_OF_ORDER,
                        }
                        if accepted:
                            station_count += 1
                            replay_display.add_station(update)
                            stop_after_response = update.status is JourneyStatus.ARRIVED
                        journey_records.append(
                            {
                                "transcript_seq": response.seq,
                                "station": update.station,
                                "destination": update.destination,
                                "stations_remaining": update.stations_remaining,
                                "status": update.status.value,
                                "direction": update.direction.value,
                                "source_time_ms": response.start_at,
                                "extraction_reason": mention.reason.value,
                            }
                        )
                    else:
                        candidate_count += 1
                    station_records.append(
                        {
                            "transcript_seq": response.seq,
                            "station": mention.station,
                            "reason": mention.reason.value,
                            "accepted": accepted,
                            "observed_token": mention.observed_token,
                            "phonetic_distance": mention.phonetic_distance,
                            "source_time_ms": response.start_at,
                        }
                    )

            if response.final:
                run_status = "in_progress"
                save_artifact(run_status)
            if stop_after_response:
                break
        run_status = "completed"
    except asyncio.CancelledError:
        run_status = "interrupted"
        raise
    except Exception:
        run_status = "failed"
        raise
    finally:
        save_artifact(run_status)
        await replay_display.stop()
        await provider.aclose()
    return final_count, station_count


def _direction_text(direction: TravelDirection) -> str:
    if direction is TravelDirection.TOWARD_CHILDRENS_GRAND_PARK:
        return "어린이대공원 방향"
    if direction is TravelDirection.TOWARD_GONGNEUNG:
        return "공릉 방향"
    return "판별 중(다음 역 인식 대기)"


class _ReplayDisplay:
    """Keep one animated travel line and append only newly accepted stations."""

    def __init__(
        self,
        *,
        duration_ms: int,
        started_at: float,
        console: Console | None = None,
    ) -> None:
        self._duration_ms = duration_ms
        self._started_at = started_at
        self._console = console or Console()
        self._current_station: str | None = None
        self._station_number = 0
        self._task: asyncio.Task[None] | None = None
        self._status = Status(
            self._status_text(0),
            console=self._console,
            spinner="arc",
            refresh_per_second=8,
        )

    def start(self) -> None:
        self._status.start()
        self._task = asyncio.create_task(self._refresh())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._status.stop()

    def add_station(self, update: JourneyUpdate) -> None:
        self._station_number += 1
        self._current_station = update.station
        self._console.print(
            _station_row(self._station_number, update),
            markup=False,
        )

    def add_transcript(self, text: str, *, source_time_ms: int) -> None:
        compact = " ".join(text.split())
        if compact:
            self._console.print(
                f"[STT {_clock_text(source_time_ms)}] {compact}",
                markup=False,
            )

    async def _refresh(self) -> None:
        while True:
            elapsed_ms = min(
                round((time.monotonic() - self._started_at) * 1_000),
                self._duration_ms,
            )
            self._status.update(self._status_text(elapsed_ms))
            await asyncio.sleep(0.25)

    def _status_text(self, elapsed_ms: int) -> Text:
        position = (
            f"현재 {self._current_station}역"
            if self._current_station is not None
            else "역 방송 대기"
        )
        return Text(
            f"인식 중 · {position} · "
            f"{_clock_text(elapsed_ms)} / {_clock_text(self._duration_ms)} · "
            f"Ctrl+C 중단",
            style="cyan",
        )


def _journey_summary(update: JourneyUpdate) -> str:
    if update.status is JourneyStatus.PREPARE_TO_EXIT:
        return f"다음 역이 {update.destination}역입니다. 곧 도착합니다."
    if update.status is JourneyStatus.ARRIVED:
        return "목적지역에 곧 도착합니다. 이번 역에서 하차하세요."
    if update.status is JourneyStatus.PASSED_DESTINATION:
        return f"목적지 {update.destination}역을 지났습니다."
    return (
        f"{_direction_text(update.direction)}"
        f" · 목적지까지 {update.stations_remaining}정거장"
    )


def _route_context_row(update: JourneyUpdate) -> str:
    summary = (
        "목적지역이 녹음 시작역입니다."
        if update.status is JourneyStatus.ARRIVED
        else f"목적지까지 {update.stations_remaining}정거장"
    )
    return f"00. 경로 기준 {update.station}역 출발 | {summary}"


def _station_row(number: int, update: JourneyUpdate) -> str:
    return f"{number:02d}. 현재 {update.station}역입니다. | {_journey_summary(update)}"


def _clock_text(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1_000)
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


if __name__ == "__main__":
    app()

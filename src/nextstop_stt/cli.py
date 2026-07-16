"""Command-line entry point for NextStop STT."""

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
from nextstop_stt.detection import DestinationAlertDetector
from nextstop_stt.evaluation.batch_predictions import (
    build_batch_predictions,
    load_batch_artifact,
    write_predictions,
)
from nextstop_stt.evaluation.review_chunks import ReviewChunkPreparer
from nextstop_stt.evaluation.run_evaluation import (
    EvaluationDataError,
    evaluate_run,
    load_ground_truth,
    load_predictions,
)
from nextstop_stt.journey import (
    LINE_7_DEMO_ROUTE,
    JourneyStatus,
    JourneyTracker,
    JourneyUpdate,
    TravelDirection,
)
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
from nextstop_stt.station_extraction import (
    StationMention,
    extract_line7_station_mentions,
    line7_keyword_vocabulary,
    line7_station_keyword_vocabulary,
)

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


@app.command("evaluate-run")
def evaluate_saved_run(
    ground_truth_file: Annotated[
        Path,
        typer.Option(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    predictions_file: Annotated[
        Path,
        typer.Option(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    output_file: Annotated[
        Path,
        typer.Option(help="Private aggregate JSON path under results/private/."),
    ],
    system_name: Annotated[
        str,
        typer.Option(help="Stable experiment name, for example rtzr-sommers-general."),
    ],
) -> None:
    """Evaluate one aligned STT run without printing private transcript text."""
    try:
        safe_ground_truth = _private_audio_file(ground_truth_file)
        safe_predictions = _private_result_path(predictions_file)
        safe_output = _private_result_path(output_file)
        if not system_name.strip():
            raise EvaluationDataError("system_name must not be empty")
        result = evaluate_run(
            load_ground_truth(safe_ground_truth),
            load_predictions(safe_predictions),
        )
        _write_private_json(
            safe_output,
            {
                "schema_version": 1,
                "run": {
                    "created_at": datetime.now(UTC).isoformat(),
                    "system_name": system_name.strip(),
                },
                "result": result,
            },
        )
    except (EvaluationDataError, ValueError) as error:
        typer.echo(f"Evaluation failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        "Evaluation completed: "
        f"samples={result['sample_count']}, "
        f"CER={_metric_text(result['transcription']['cer'])}, "
        f"F1={_metric_text(result['decision']['f1'])}"
    )
    typer.echo(f"Private result saved: {safe_output.as_posix()}")


def _private_audio_file(input_file: Path) -> Path:
    private_root = Path("private_audio").resolve()
    resolved = input_file.resolve()
    try:
        resolved.relative_to(private_root)
    except ValueError:
        raise ValueError("ground_truth_file must be under private_audio/") from None
    return resolved


def _metric_text(value: float | None) -> str:
    return "undefined" if value is None else f"{value:.4f}"


@app.command("prepare-batch-predictions")
def prepare_batch_predictions(
    ground_truth_file: Annotated[
        Path,
        typer.Option(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    batch_result_file: Annotated[
        Path,
        typer.Option(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    output_file: Annotated[
        Path,
        typer.Option(help="Private prediction CSV path under results/private/."),
    ],
    target_station: Annotated[str, typer.Option(help="Destination used by the alert baseline.")],
) -> None:
    """Align a saved RTZR Batch run to reviewed ground-truth segments."""
    try:
        safe_ground_truth = _private_audio_file(ground_truth_file)
        safe_batch_result = _private_result_path(batch_result_file)
        safe_output = _private_result_path(output_file)
        predictions = build_batch_predictions(
            load_ground_truth(safe_ground_truth),
            load_batch_artifact(safe_batch_result),
            target_station=target_station,
        )
        write_predictions(safe_output, predictions)
    except (EvaluationDataError, ValueError) as error:
        typer.echo(f"Prediction preparation failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"Private Batch predictions created: samples={len(predictions)}")
    typer.echo(f"Prediction file: {safe_output.as_posix()}")


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
    preprocess: Annotated[
        AudioPreprocessPreset,
        typer.Option(help="Optional FFmpeg speech preprocessing preset."),
    ] = AudioPreprocessPreset.NONE,
    contextual_recovery: Annotated[
        bool,
        typer.Option(
            "--recover",
            help="Opt in to context-gated Line 7 phonetic station recovery.",
        ),
    ] = False,
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
    line7_keyword_score: Annotated[
        float | None,
        typer.Option(
            min=-5.0,
            max=5.0,
            help="Boost every station and secondary name in the demo corridor equally.",
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
    detect_stations: Annotated[
        bool,
        typer.Option(
            "--detect-station",
            help="Print privacy-safe Line 7 station detections from final responses."
        ),
    ] = False,
) -> None:
    """Replay a bounded audio segment through RTZR Streaming STT."""
    try:
        safe_output = _private_result_path(output_file) if output_file else None
        keywords = _merge_line7_keyword_boosts(
            _parse_keyword_boosts(tuple(keyword or ())),
            score=line7_keyword_score,
        )
        partial_count, final_count, alert_count, station_count, candidate_count = asyncio.run(
            _stream_file(
                source_file=source_file,
                duration_ms=duration_ms,
                start_ms=start_ms,
                sample_rate=sample_rate,
                preprocess=preprocess,
                contextual_recovery=contextual_recovery,
                domain=domain,
                model=model,
                language=language,
                target_station=target_station,
                keywords=keywords,
                output_file=safe_output,
                show_text=show_text,
                detect_stations=detect_stations,
                journey_tracker=None,
            )
        )
    except (AudioSourceError, RTZRError, ValueError) as error:
        typer.echo(f"Streaming failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        "Streaming completed: "
        f"partial={partial_count}, final={final_count}, "
        f"stations={station_count}, candidates={candidate_count}, alerts={alert_count}"
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


def _merge_line7_keyword_boosts(
    boosts: tuple[KeywordBoost, ...],
    *,
    score: float | None,
) -> tuple[KeywordBoost, ...]:
    if score is None:
        return boosts
    existing = {boost.text for boost in boosts}
    corridor = tuple(
        KeywordBoost(text=text, score=score)
        for text in line7_keyword_vocabulary()
        if text not in existing
    )
    return boosts + corridor


@app.command("journey-demo")
def journey_demo(
    source_file: Annotated[
        Path | None,
        typer.Option(help="Owned M4A/WAV recording; prompted when omitted."),
    ] = None,
    destination: Annotated[
        str | None,
        typer.Option(help="Station to exit at; prompted when omitted."),
    ] = None,
    start_seconds: Annotated[
        float,
        typer.Option(min=0.0, help="Replay start in seconds."),
    ] = 0.0,
    duration_seconds: Annotated[
        float | None,
        typer.Option(min=0.1, help="Optional debug limit; the full file is replayed by default."),
    ] = None,
    domain: Annotated[StreamingDomain, typer.Option()] = StreamingDomain.MEETING,
    keyword_score: Annotated[
        float,
        typer.Option(min=-5.0, max=5.0, help="Score for non-destination route stations."),
    ] = 1.0,
    destination_score: Annotated[
        float,
        typer.Option(min=-5.0, max=5.0, help="Higher score for the requested destination."),
    ] = 2.0,
    preprocess: Annotated[
        AudioPreprocessPreset,
        typer.Option(help="Audio preprocessing preset; none keeps the baseline."),
    ] = AudioPreprocessPreset.NONE,
    contextual_recovery: Annotated[
        bool,
        typer.Option(
            "--recover",
            help="Recover one unique station from announcement context and phonetic distance.",
        ),
    ] = False,
    show_text: Annotated[
        bool,
        typer.Option(
            "--show-text/--hide-text",
            help="Print finalized RTZR transcript text; may include nearby speech.",
        ),
    ] = False,
    output_file: Annotated[
        Path,
        typer.Option(help="Private JSON evidence under results/private/."),
    ] = Path("results/private/journey-demo.json"),
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Start the paid RTZR replay without confirmation."),
    ] = False,
) -> None:
    """Interactively replay a recording and show the route to an exit station."""
    typer.echo("NextStop STT")
    typer.echo("공릉역부터 어린이대공원역까지의 녹음에서 현재 역을 인식합니다.")
    typer.echo("새 역이 들리면 한 줄씩 추가되고, 목적지역을 찾으면 자동 종료됩니다.")

    if source_file is None:
        default_source = Path("../subwayaudio.m4a")
        source_file = (
            default_source
            if default_source.is_file()
            else Path(typer.prompt("녹음 파일 경로를 입력해주세요"))
        )
    if not source_file.is_file():
        typer.echo("실행 실패: 녹음 파일을 찾을 수 없습니다.", err=True)
        raise typer.Exit(code=1)

    if destination is None:
        destination = typer.prompt("목적지역을 입력해주세요 (공릉~어린이대공원)")
    try:
        tracker = JourneyTracker(destination, initial_station=LINE_7_DEMO_ROUTE[0])
        safe_output = _private_result_path(output_file)
        total_duration_ms = probe_audio_duration_ms(source_file)
        start_ms = round(start_seconds * 1_000)
        if start_ms >= total_duration_ms:
            raise ValueError("start_seconds must be before the end of the audio")
        available_ms = total_duration_ms - start_ms
        duration_ms = (
            available_ms
            if duration_seconds is None
            else min(round(duration_seconds * 1_000), available_ms)
        )
        destination_boosts = tuple(
            KeywordBoost(text=text, score=destination_score)
            for text in line7_station_keyword_vocabulary(tracker.destination)
        )
        keywords = _merge_line7_keyword_boosts(
            destination_boosts,
            score=keyword_score,
        )
    except (AudioSourceError, ValueError) as error:
        typer.echo(f"실행 실패: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"목적지역: {tracker.destination}역")
    typer.echo(f"사용 음성: {source_file.name} ({_clock_text(total_duration_ms)})")
    typer.echo(f"인식 구간: {' → '.join(LINE_7_DEMO_ROUTE)}")
    route_context = tracker.route_context()
    if route_context is not None:
        typer.echo(_route_context_row(route_context))
    if not yes and not typer.confirm("RTZR Streaming 인식을 시작할까요?"):
        typer.echo("사용자가 실행을 취소했습니다.")
        raise typer.Exit()

    typer.echo("음성 인식을 시작합니다. 중단하려면 Ctrl+C를 누르세요.")
    try:
        _, final, _, stations, _ = asyncio.run(
            _stream_file(
                source_file=source_file,
                duration_ms=duration_ms,
                start_ms=start_ms,
                sample_rate=16_000,
                preprocess=preprocess,
                contextual_recovery=contextual_recovery,
                domain=domain,
                model=StreamingModel.SOMMERS_KO,
                language=None,
                target_station=None,
                keywords=keywords,
                output_file=safe_output,
                show_text=show_text,
                detect_stations=True,
                journey_tracker=tracker,
            )
        )
    except KeyboardInterrupt:
        typer.echo("\n[중단] 지금까지 받은 RTZR 응답을 비공개 결과에 저장했습니다.")
        typer.echo(f"실행 근거 저장: {safe_output.as_posix()}")
        raise typer.Exit(code=130) from None
    except (AudioSourceError, RTZRError, ValueError) as error:
        typer.echo(f"실행 실패: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        "완료: "
        f"인식된 역 {stations}개, RTZR final {final}개"
    )
    if stations == 0:
        typer.echo("결과: 확정 가능한 역명을 찾지 못했습니다.")
    typer.echo(f"실행 근거 저장: {safe_output.as_posix()}")


async def _stream_file(
    *,
    source_file: Path,
    duration_ms: int,
    start_ms: int,
    sample_rate: int,
    preprocess: AudioPreprocessPreset = AudioPreprocessPreset.NONE,
    contextual_recovery: bool = False,
    domain: StreamingDomain,
    model: StreamingModel,
    language: str | None,
    target_station: str | None,
    keywords: tuple[KeywordBoost, ...],
    output_file: Path | None,
    show_text: bool,
    detect_stations: bool,
    journey_tracker: JourneyTracker | None,
) -> tuple[int, int, int, int, int]:
    credentials = RTZRCredentials.from_env()
    provider = RTZRTokenProvider(credentials)
    source = FFmpegPCMSource(
        source_file,
        sample_rate=sample_rate,
        start_ms=start_ms,
        duration_ms=duration_ms,
        preprocess=preprocess,
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
    station_count = 0
    candidate_count = 0
    response_records = []
    decision_records = []
    station_records = []
    journey_records = []
    session_started_at = time.monotonic()
    run_created_at = datetime.now(UTC).isoformat()
    run_status = "connecting"

    def save_artifact(status: str) -> None:
        if output_file is None:
            return
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
                        "preprocess": preprocess.value,
                        "filter_graph": preprocess.filter_graph,
                    },
                    "station_extraction": {
                        "contextual_recovery": contextual_recovery,
                    },
                    "segment": {
                        "start_ms": start_ms,
                        "duration_ms": duration_ms,
                    },
                    "session_elapsed_ms": round(
                        (time.monotonic() - session_started_at) * 1_000
                    ),
                },
                "summary": {
                    "partial_count": partial_count,
                    "final_count": final_count,
                    "station_count": station_count,
                    "candidate_count": candidate_count,
                    "alert_count": alert_count,
                },
                "responses": response_records,
                "decisions": decision_records,
                "station_detections": station_records,
                "journey": journey_records,
            },
        )

    save_artifact(run_status)
    replay_display = None
    if journey_tracker is not None:
        replay_display = _ReplayDisplay(
            duration_ms=duration_ms,
            start_ms=start_ms,
            started_at=session_started_at,
        )
        replay_display.start()
    try:
        async for response in client.transcribe(source.frames()):
            stop_after_response = False
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
            if show_text:
                if journey_tracker is not None:
                    if response.final and replay_display is not None:
                        replay_display.add_transcript(
                            response.primary_text,
                            source_time_ms=start_ms + response.start_at,
                        )
                else:
                    state = "FINAL" if response.final else "PARTIAL"
                    typer.echo(f"[{state}] {response.primary_text}")
            if detect_stations and response.final:
                mentions = extract_line7_station_mentions(
                    response.primary_text,
                    allow_contextual_recovery=contextual_recovery,
                )
                for mention in mentions:
                    if mention.is_current_station_evidence:
                        if journey_tracker is None:
                            typer.echo(
                                f"CURRENT_STATION: {mention.station} "
                                f"reason={mention.reason.value} seq={response.seq}"
                            )
                        else:
                            update = journey_tracker.observe(mention.station)
                            accepted = update.status not in {
                                JourneyStatus.DUPLICATE,
                                JourneyStatus.OUT_OF_ORDER,
                            }
                            if accepted and replay_display is not None:
                                replay_display.add_station(
                                    update,
                                    source_time_ms=start_ms + response.start_at,
                                    mention=mention,
                                )
                            journey_records.append(
                                {
                                    "transcript_seq": response.seq,
                                    "station": update.station,
                                    "destination": update.destination,
                                    "stations_remaining": update.stations_remaining,
                                    "status": update.status.value,
                                    "direction": update.direction.value,
                                    "source_time_ms": start_ms + response.start_at,
                                    "extraction_reason": mention.reason.value,
                                    "observed_token": mention.observed_token,
                                    "phonetic_distance": mention.phonetic_distance,
                                }
                            )
                            if accepted:
                                station_count += 1
                                if update.status is JourneyStatus.ARRIVED:
                                    stop_after_response = True
                        if journey_tracker is None:
                            station_count += 1
                    else:
                        candidate_count += 1
                    station_records.append(
                        {
                            "transcript_seq": response.seq,
                            "station": mention.station,
                            "reason": mention.reason.value,
                            "is_current_station_evidence": (
                                mention.is_current_station_evidence
                            ),
                            "observed_token": mention.observed_token,
                            "phonetic_distance": mention.phonetic_distance,
                            "source_time_ms": start_ms + response.start_at,
                        }
                    )
            if replay_display is not None:
                replay_display.update_activity(
                    partial_count=partial_count,
                    final_count=final_count,
                    candidate_count=candidate_count,
                )
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
            if response.final:
                run_status = "in_progress"
                save_artifact(run_status)
            if stop_after_response:
                run_status = "completed"
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
        if replay_display is not None:
            await replay_display.stop()
        await provider.aclose()
    return partial_count, final_count, alert_count, station_count, candidate_count


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
        start_ms: int,
        started_at: float,
        console: Console | None = None,
    ) -> None:
        self._duration_ms = duration_ms
        self._start_ms = start_ms
        self._started_at = started_at
        self._console = console or Console()
        self._current_station: str | None = None
        self._station_number = 0
        self._partial_count = 0
        self._final_count = 0
        self._candidate_count = 0
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

    def add_station(
        self,
        update: JourneyUpdate,
        *,
        source_time_ms: int,
        mention: StationMention | None = None,
    ) -> None:
        self._station_number += 1
        self._current_station = update.station
        self._console.print(
            _station_row(
                self._station_number,
                update,
                source_time_ms=source_time_ms,
                mention=mention,
            ),
            markup=False,
        )

    def update_activity(
        self,
        *,
        partial_count: int,
        final_count: int,
        candidate_count: int,
    ) -> None:
        self._partial_count = partial_count
        self._final_count = final_count
        self._candidate_count = candidate_count

    def add_transcript(self, text: str, *, source_time_ms: int) -> None:
        compact = " ".join(text.split())
        if not compact:
            return
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
    if update.status is JourneyStatus.ARRIVED:
        summary = "목적지역이 녹음 시작역입니다."
    else:
        summary = f"목적지까지 {update.stations_remaining}정거장"
    return f"00. 경로 기준 {update.station}역 출발 | {summary}"


def _station_row(
    number: int,
    update: JourneyUpdate,
    *,
    source_time_ms: int,
    mention: StationMention | None = None,
) -> str:
    row = (
        f"{number:02d}. 현재 {update.station}역입니다."
        f" | {_journey_summary(update)}"
    )
    return row


def _clock_text(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1_000)
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


if __name__ == "__main__":
    app()

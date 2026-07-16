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
from nextstop_stt.journey import JourneyStatus, JourneyTracker, JourneyUpdate
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
    extract_line7_station_mentions,
    line7_keyword_vocabulary,
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
        typer.Option(min=0.1, help="Required replay duration; prompted when omitted."),
    ] = None,
    domain: Annotated[StreamingDomain, typer.Option()] = StreamingDomain.MEETING,
    keyword_score: Annotated[
        float,
        typer.Option(min=-5.0, max=5.0, help="Equal score for the demo route vocabulary."),
    ] = 1.0,
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
    typer.echo("=" * 64)
    typer.echo(" NextStop STT | 지하철 현재역 인식 및 하차 안내")
    typer.echo(" 파일을 실시간 속도로 전송해 마이크 입력을 재현합니다.")
    typer.echo("=" * 64)

    if source_file is None:
        source_file = Path(typer.prompt("녹음 파일 경로를 입력해주세요"))
    if not source_file.is_file():
        typer.echo("실행 실패: 녹음 파일을 찾을 수 없습니다.", err=True)
        raise typer.Exit(code=1)

    if destination is None:
        destination = typer.prompt("하차하실 역명을 정확히 입력해주세요")
    try:
        tracker = JourneyTracker(destination)
        safe_output = _private_result_path(output_file)
        if duration_seconds is None:
            duration_seconds = typer.prompt(
                "실시간으로 재생할 길이(초)를 입력해주세요",
                default=20.0,
                type=float,
            )
        if duration_seconds <= 0:
            raise ValueError("duration must be positive")
        keywords = _merge_line7_keyword_boosts((), score=keyword_score)
    except ValueError as error:
        typer.echo(f"실행 실패: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"[입력] 파일: {source_file.name}")
    typer.echo(f"[목적지] {tracker.destination}")
    typer.echo(
        f"[RTZR 설정] model=sommers_ko domain={domain.value} "
        f"Line7 keyword score={keyword_score:.1f}"
    )
    typer.echo(
        f"[재생 구간] {start_seconds:.3f}초부터 {duration_seconds:.3f}초 | "
        "실제 시간만큼 소요"
    )
    if not yes and not typer.confirm("RTZR Streaming 인식을 시작할까요?"):
        typer.echo("사용자가 실행을 취소했습니다.")
        raise typer.Exit()

    typer.echo("[연결] RTZR Streaming STT 연결 및 실시간 재생 시작")
    try:
        partial, final, _, stations, candidates = asyncio.run(
            _stream_file(
                source_file=source_file,
                duration_ms=round(duration_seconds * 1_000),
                start_ms=round(start_seconds * 1_000),
                sample_rate=16_000,
                domain=domain,
                model=StreamingModel.SOMMERS_KO,
                language=None,
                target_station=None,
                keywords=keywords,
                output_file=safe_output,
                show_text=False,
                detect_stations=True,
                journey_tracker=tracker,
            )
        )
    except (AudioSourceError, RTZRError, ValueError) as error:
        typer.echo(f"실행 실패: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        "[완료] "
        f"partial={partial}, final={final}, "
        f"현재역={stations}, 보류후보={candidates}"
    )
    typer.echo(f"[근거 저장] {safe_output.as_posix()}")


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
            if detect_stations and response.final:
                mentions = extract_line7_station_mentions(response.primary_text)
                strong_mentions = tuple(
                    mention for mention in mentions if mention.is_current_station_evidence
                )
                if journey_tracker is not None and not strong_mentions:
                    state = "역명 후보 보류" if mentions else "역명 근거 없음"
                    typer.echo(f"[인식] final seq={response.seq} | {state}")
                for mention in mentions:
                    if mention.is_current_station_evidence:
                        if journey_tracker is None:
                            typer.echo(
                                f"CURRENT_STATION: {mention.station} "
                                f"reason={mention.reason.value} seq={response.seq}"
                            )
                        else:
                            update = journey_tracker.observe(mention.station)
                            _render_journey_update(update, seq=response.seq)
                            journey_records.append(
                                {
                                    "transcript_seq": response.seq,
                                    "station": update.station,
                                    "destination": update.destination,
                                    "stations_remaining": update.stations_remaining,
                                    "status": update.status.value,
                                }
                            )
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
                        }
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
                "station_detections": station_records,
                "journey": journey_records,
            },
        )
    return partial_count, final_count, alert_count, station_count, candidate_count


def _render_journey_update(update: JourneyUpdate, *, seq: int) -> None:
    if update.status is JourneyStatus.EN_ROUTE:
        typer.echo(
            f"[현재역] {update.station} | 목적지 {update.destination}까지 "
            f"{update.stations_remaining}정거장 | final seq={seq}"
        )
    elif update.status is JourneyStatus.PREPARE_TO_EXIT:
        typer.echo(
            f"[하차 준비] 현재 {update.station} | 다음 역 {update.destination}에서 "
            f"내리세요 | final seq={seq}"
        )
    elif update.status is JourneyStatus.ARRIVED:
        typer.echo(f"[도착] {update.destination}역입니다. 하차하세요 | final seq={seq}")
    elif update.status is JourneyStatus.PASSED_DESTINATION:
        typer.echo(
            f"[주의] 목적지 {update.destination}을 지나 {update.station}역입니다 "
            f"| final seq={seq}"
        )
    elif update.status is JourneyStatus.OUT_OF_ORDER:
        typer.echo(
            f"[보류] {update.station}역은 진행 순서와 맞지 않아 위치를 갱신하지 않습니다 "
            f"| final seq={seq}"
        )


if __name__ == "__main__":
    app()

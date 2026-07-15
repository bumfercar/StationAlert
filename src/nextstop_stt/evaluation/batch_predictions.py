"""Align one private RTZR Batch artifact to human-labeled time segments."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from nextstop_stt.detection import canonical_station_name, contains_station
from nextstop_stt.evaluation.run_evaluation import (
    EvaluationDataError,
    GroundTruthRecord,
    PredictionRecord,
    PredictionStatus,
)


def load_batch_artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise EvaluationDataError("Batch artifact was not found")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise EvaluationDataError("Batch artifact could not be read") from None
    if not isinstance(payload, dict):
        raise EvaluationDataError("Batch artifact has an invalid structure")
    return payload


def build_batch_predictions(
    ground_truth: list[GroundTruthRecord],
    artifact: dict[str, Any],
    *,
    target_station: str,
) -> list[PredictionRecord]:
    """Join overlapping Batch utterances and apply the production station baseline."""
    canonical_target = canonical_station_name(target_station)
    utterances = _batch_utterances(artifact)
    predictions = []
    for truth in ground_truth:
        overlapping = [
            utterance
            for utterance in utterances
            if utterance["start_ms"] < truth.end_ms
            and utterance["end_ms"] > truth.start_ms
        ]
        hypothesis = " ".join(utterance["text"] for utterance in overlapping).strip()
        predictions.append(
            PredictionRecord(
                segment_id=truth.segment_id,
                hypothesis_text=hypothesis,
                predicted_alert=contains_station(hypothesis, canonical_target),
                status=PredictionStatus.COMPLETED,
            )
        )
    return predictions


def write_predictions(path: Path, predictions: list[PredictionRecord]) -> None:
    """Atomically write private hypotheses for the STT-agnostic evaluator."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=("segment_id", "hypothesis_text", "predicted_alert", "status"),
        )
        writer.writeheader()
        for prediction in predictions:
            writer.writerow(
                {
                    "segment_id": prediction.segment_id,
                    "hypothesis_text": prediction.hypothesis_text,
                    "predicted_alert": str(prediction.predicted_alert).lower(),
                    "status": prediction.status.value,
                }
            )
    temporary.replace(path)


def _batch_utterances(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    run = artifact.get("run")
    response = artifact.get("response")
    if not isinstance(run, dict) or run.get("api") != "batch":
        raise EvaluationDataError("artifact is not an RTZR Batch run")
    if not isinstance(response, dict) or response.get("status") != "completed":
        raise EvaluationDataError("RTZR Batch run is not completed")
    results = response.get("results")
    raw_utterances = results.get("utterances") if isinstance(results, dict) else None
    if not isinstance(raw_utterances, list):
        raise EvaluationDataError("RTZR Batch artifact has no utterance list")

    utterances = []
    for raw in raw_utterances:
        if not isinstance(raw, dict):
            raise EvaluationDataError("RTZR Batch utterance has an invalid structure")
        start_ms = raw.get("start_at")
        duration_ms = raw.get("duration")
        text = raw.get("msg")
        if (
            not isinstance(start_ms, int)
            or start_ms < 0
            or not isinstance(duration_ms, int)
            or duration_ms < 0
            or not isinstance(text, str)
        ):
            raise EvaluationDataError("RTZR Batch utterance has invalid typed fields")
        utterances.append(
            {
                "start_ms": start_ms,
                "end_ms": start_ms + duration_ms,
                "text": text,
            }
        )
    return sorted(utterances, key=lambda item: item["start_ms"])

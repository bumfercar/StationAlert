"""Validated, STT-agnostic evaluation records and aggregate results."""

from __future__ import annotations

import csv
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from nextstop_stt.detection import canonical_station_name, contains_station_token
from nextstop_stt.evaluation.metrics import (
    character_edit_distance,
    evaluate_character_error,
    evaluate_decisions,
    evaluate_station_matches,
    normalize_for_cer,
)


class EvaluationDataError(ValueError):
    """Raised when private evaluation data is incomplete or inconsistent."""


class PredictionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class GroundTruthRecord(BaseModel):
    """One human-labeled transcription and alert decision unit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    station: str = ""
    announcement_type: str = Field(min_length=1)
    reference_text: str
    include_in_cer: bool = True
    expected_alert: bool
    overlapping_speech: bool
    noise_level: str = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def validate_time_range(self) -> GroundTruthRecord:
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class PredictionRecord(BaseModel):
    """One model or pipeline result aligned by ground-truth segment id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str = Field(min_length=1)
    hypothesis_text: str
    predicted_alert: bool
    status: PredictionStatus

    @model_validator(mode="after")
    def validate_failed_prediction(self) -> PredictionRecord:
        if self.status is PredictionStatus.FAILED and self.predicted_alert:
            raise ValueError("failed predictions cannot emit an alert")
        return self


def load_ground_truth(path: Path) -> list[GroundTruthRecord]:
    return _load_records(path, GroundTruthRecord)


def load_predictions(path: Path) -> list[PredictionRecord]:
    return _load_records(path, PredictionRecord)


def evaluate_run(
    ground_truth: list[GroundTruthRecord],
    predictions: list[PredictionRecord],
) -> dict[str, Any]:
    """Evaluate identical segment sets and return a transcript-free artifact."""
    truth_by_id = _unique_by_id(ground_truth, label="ground truth")
    prediction_by_id = _unique_by_id(predictions, label="predictions")
    truth_ids = set(truth_by_id)
    prediction_ids = set(prediction_by_id)
    if truth_ids != prediction_ids:
        missing_count = len(truth_ids - prediction_ids)
        unexpected_count = len(prediction_ids - truth_ids)
        raise EvaluationDataError(
            "segment sets differ "
            f"(missing predictions={missing_count}, unexpected predictions={unexpected_count})"
        )

    ordered_ids = [record.segment_id for record in ground_truth]
    transcript_pairs = [
        (truth_by_id[segment_id].reference_text, prediction_by_id[segment_id].hypothesis_text)
        for segment_id in ordered_ids
        if truth_by_id[segment_id].include_in_cer
    ]
    station_pairs = [
        (truth_by_id[segment_id].station, prediction_by_id[segment_id].hypothesis_text)
        for segment_id in ordered_ids
        if truth_by_id[segment_id].station.strip()
    ]
    decision_pairs = [
        (truth_by_id[segment_id].expected_alert, prediction_by_id[segment_id].predicted_alert)
        for segment_id in ordered_ids
    ]

    character = evaluate_character_error(transcript_pairs)
    station = evaluate_station_matches(station_pairs)
    decision = evaluate_decisions(decision_pairs)
    segments = []
    for segment_id in ordered_ids:
        truth = truth_by_id[segment_id]
        prediction = prediction_by_id[segment_id]
        character_edits = None
        reference_characters = None
        if truth.include_in_cer:
            normalized_reference = normalize_for_cer(truth.reference_text)
            normalized_hypothesis = normalize_for_cer(prediction.hypothesis_text)
            character_edits = character_edit_distance(
                normalized_reference,
                normalized_hypothesis,
            )
            reference_characters = len(normalized_reference)
        station_match = None
        if truth.station.strip():
            station_match = contains_station_token(
                prediction.hypothesis_text,
                canonical_station_name(truth.station),
            )
        segments.append(
            {
                "segment_id": segment_id,
                "status": prediction.status.value,
                "include_in_cer": truth.include_in_cer,
                "character_edits": character_edits,
                "reference_characters": reference_characters,
                "station_match": station_match,
                "expected_alert": truth.expected_alert,
                "predicted_alert": prediction.predicted_alert,
            }
        )

    return {
        "sample_count": len(ground_truth),
        "failed_prediction_count": sum(
            prediction.status is PredictionStatus.FAILED for prediction in predictions
        ),
        "transcription": {
            "sample_count": character.sample_count,
            "character_edits": character.edit_count,
            "reference_characters": character.reference_character_count,
            "cer": character.cer,
        },
        "station": {
            "sample_count": station.sample_count,
            "match_count": station.match_count,
            "exact_match_rate": station.exact_match_rate,
        },
        "decision": {
            "true_positive": decision.true_positive,
            "false_positive": decision.false_positive,
            "false_negative": decision.false_negative,
            "true_negative": decision.true_negative,
            "precision": decision.precision,
            "recall": decision.recall,
            "f1": decision.f1,
        },
        "segments": segments,
    }


def _load_records(path: Path, model: type[GroundTruthRecord] | type[PredictionRecord]):
    if not path.is_file():
        raise EvaluationDataError("evaluation CSV was not found")
    try:
        with path.open(encoding="utf-8-sig", newline="") as input_file:
            rows = list(csv.DictReader(input_file))
    except OSError:
        raise EvaluationDataError("evaluation CSV could not be read") from None
    records = []
    for row_number, row in enumerate(rows, start=2):
        try:
            row = _parse_boolean_fields(row, model)
            records.append(model.model_validate(row))
        except (EvaluationDataError, ValidationError):
            raise EvaluationDataError(f"evaluation CSV row {row_number} is invalid") from None
    return records


def _parse_boolean_fields(
    row: dict[str, str | None],
    model: type[GroundTruthRecord] | type[PredictionRecord],
) -> dict[str, str | bool | None]:
    parsed: dict[str, str | bool | None] = dict(row)
    fields = ("expected_alert", "overlapping_speech")
    if model is GroundTruthRecord and "include_in_cer" in row:
        fields += ("include_in_cer",)
    if model is PredictionRecord:
        fields = ("predicted_alert",)
    for field in fields:
        value = row.get(field)
        if value == "true":
            parsed[field] = True
        elif value == "false":
            parsed[field] = False
        else:
            raise EvaluationDataError(f"{field} must be true or false")
    return parsed


def _unique_by_id(records, *, label: str):
    indexed = {}
    for record in records:
        if record.segment_id in indexed:
            raise EvaluationDataError(f"duplicate segment id in {label}")
        indexed[record.segment_id] = record
    if not indexed:
        raise EvaluationDataError(f"{label} is empty")
    return indexed

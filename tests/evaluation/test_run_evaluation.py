from __future__ import annotations

from pathlib import Path

import pytest

from nextstop_stt.evaluation.run_evaluation import (
    EvaluationDataError,
    GroundTruthRecord,
    PredictionRecord,
    PredictionStatus,
    evaluate_run,
    load_ground_truth,
)


def test_evaluate_run_keeps_failed_and_negative_samples() -> None:
    truth = [
        _truth("segment-001", station="먹골", expected_alert=True),
        _truth("segment-002", station="상봉", expected_alert=False),
    ]
    predictions = [
        _prediction("segment-001", "먹골역", predicted_alert=True),
        _prediction("segment-002", "", status=PredictionStatus.FAILED),
    ]

    result = evaluate_run(truth, predictions)

    assert result["sample_count"] == 2
    assert result["failed_prediction_count"] == 1
    assert result["station"] == {
        "sample_count": 2,
        "match_count": 1,
        "exact_match_rate": 0.5,
    }
    assert result["decision"]["true_positive"] == 1
    assert result["decision"]["true_negative"] == 1
    assert len(result["segments"]) == 2
    assert "hypothesis_text" not in result["segments"][0]


def test_evaluate_run_can_exclude_incomplete_transcript_from_cer_only() -> None:
    truth = [
        _truth("segment-001", station="먹골", expected_alert=True),
        _truth(
            "segment-002",
            station="상봉",
            expected_alert=False,
            include_in_cer=False,
        ),
    ]
    predictions = [
        _prediction("segment-001", "먹골역", predicted_alert=True),
        _prediction("segment-002", "상봉역"),
    ]

    result = evaluate_run(truth, predictions)

    assert result["sample_count"] == 2
    assert result["transcription"]["sample_count"] == 1
    assert result["station"]["sample_count"] == 2
    assert result["decision"]["true_negative"] == 1
    assert result["segments"][1]["include_in_cer"] is False
    assert result["segments"][1]["character_edits"] is None
    assert result["segments"][1]["reference_characters"] is None


def test_evaluate_run_rejects_different_segment_sets() -> None:
    truth = [_truth("segment-001", station="먹골", expected_alert=True)]
    predictions = [_prediction("different", "먹골", predicted_alert=True)]

    with pytest.raises(EvaluationDataError, match="missing predictions=1"):
        evaluate_run(truth, predictions)


def test_evaluate_run_rejects_duplicate_segments() -> None:
    truth = [
        _truth("segment-001", station="먹골", expected_alert=True),
        _truth("segment-001", station="먹골", expected_alert=True),
    ]

    with pytest.raises(EvaluationDataError, match="duplicate segment id"):
        evaluate_run(truth, [_prediction("segment-001", "먹골", predicted_alert=True)])


def test_load_ground_truth_uses_strict_boolean_text(tmp_path: Path) -> None:
    path = tmp_path / "ground-truth.csv"
    path.write_text(
        "segment_id,start_ms,end_ms,station,announcement_type,reference_text,"
        "include_in_cer,expected_alert,overlapping_speech,noise_level,notes\n"
        "segment-001,1000,2000,먹골,NEXT_STATION,먹골입니다,"
        "false,true,false,medium,\n",
        encoding="utf-8",
    )

    records = load_ground_truth(path)

    assert records[0].expected_alert is True
    assert records[0].overlapping_speech is False
    assert records[0].include_in_cer is False


def test_load_ground_truth_hides_private_invalid_row(tmp_path: Path) -> None:
    private_text = "private passenger transcript"
    path = tmp_path / "ground-truth.csv"
    path.write_text(
        "segment_id,start_ms,end_ms,station,announcement_type,reference_text,"
        "expected_alert,overlapping_speech,noise_level,notes\n"
        f"segment-001,2000,1000,먹골,NEXT_STATION,{private_text},yes,false,medium,\n",
        encoding="utf-8",
    )

    with pytest.raises(EvaluationDataError) as captured:
        load_ground_truth(path)

    assert "row 2" in str(captured.value)
    assert private_text not in str(captured.value)


def _truth(
    segment_id: str,
    *,
    station: str,
    expected_alert: bool,
    include_in_cer: bool = True,
) -> GroundTruthRecord:
    return GroundTruthRecord(
        segment_id=segment_id,
        start_ms=1_000,
        end_ms=2_000,
        station=station,
        announcement_type="NEXT_STATION",
        reference_text=f"이번 역은 {station}역입니다",
        include_in_cer=include_in_cer,
        expected_alert=expected_alert,
        overlapping_speech=False,
        noise_level="medium",
    )


def _prediction(
    segment_id: str,
    hypothesis_text: str,
    *,
    predicted_alert: bool = False,
    status: PredictionStatus = PredictionStatus.COMPLETED,
) -> PredictionRecord:
    return PredictionRecord(
        segment_id=segment_id,
        hypothesis_text=hypothesis_text,
        predicted_alert=predicted_alert,
        status=status,
    )

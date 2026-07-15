from __future__ import annotations

from pathlib import Path

from nextstop_stt.evaluation.batch_predictions import (
    build_batch_predictions,
    write_predictions,
)
from nextstop_stt.evaluation.run_evaluation import GroundTruthRecord


def test_batch_predictions_align_overlapping_utterances_and_keep_empty_segment() -> None:
    truth = [
        _truth("segment-001", 1_000, 3_000),
        _truth("segment-002", 4_000, 5_000),
    ]
    artifact = {
        "run": {"api": "batch"},
        "response": {
            "status": "completed",
            "results": {
                "utterances": [
                    {"start_at": 2_000, "duration": 500, "msg": "먹골역입니다"},
                    {"start_at": 900, "duration": 300, "msg": "이번 역은"},
                ]
            },
        },
    }

    predictions = build_batch_predictions(truth, artifact, target_station="먹골")

    assert predictions[0].hypothesis_text == "이번 역은 먹골역입니다"
    assert predictions[0].predicted_alert is True
    assert predictions[1].hypothesis_text == ""
    assert predictions[1].predicted_alert is False


def test_private_prediction_writer_uses_evaluator_contract(tmp_path: Path) -> None:
    predictions = build_batch_predictions(
        [_truth("segment-001", 1_000, 3_000)],
        {
            "run": {"api": "batch"},
            "response": {
                "status": "completed",
                "results": {
                    "utterances": [
                        {"start_at": 1_500, "duration": 200, "msg": "먹골역"},
                    ]
                },
            },
        },
        target_station="먹골",
    )
    output = tmp_path / "predictions.csv"

    write_predictions(output, predictions)

    content = output.read_text(encoding="utf-8-sig")
    assert content.splitlines()[0] == "segment_id,hypothesis_text,predicted_alert,status"
    assert "segment-001,먹골역,true,completed" in content


def _truth(segment_id: str, start_ms: int, end_ms: int) -> GroundTruthRecord:
    return GroundTruthRecord(
        segment_id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        station="먹골",
        announcement_type="NEXT_STATION",
        reference_text="먹골역",
        expected_alert=True,
        overlapping_speech=False,
        noise_level="medium",
    )

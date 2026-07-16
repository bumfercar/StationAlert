from __future__ import annotations

import pytest

from nextstop_stt.evaluation.metrics import (
    character_edit_distance,
    evaluate_character_error,
    evaluate_decisions,
    evaluate_station_matches,
    normalize_for_cer,
)


def test_korean_cer_reuses_normalization_and_ignores_spacing() -> None:
    result = evaluate_character_error(
        [
            ("이번 역은 먹골역입니다.", "이번역은 먹골입니다"),
            ("상봉역", "상봉역"),
        ]
    )

    assert result.sample_count == 2
    assert result.edit_count == 1
    assert result.reference_character_count == 13
    assert result.cer == pytest.approx(1 / 13)


def test_cer_is_undefined_when_reference_has_no_characters() -> None:
    result = evaluate_character_error([("...", "오인식")])

    assert result.edit_count == 3
    assert result.reference_character_count == 0
    assert result.cer is None


def test_station_match_requires_station_token_not_raw_substring() -> None:
    result = evaluate_station_matches(
        [
            ("먹골", "이번 역은 먹골."),
            ("상봉역", "경상봉역사 안에서 안내드립니다"),
        ]
    )

    assert result.match_count == 1
    assert result.exact_match_rate == 0.5


def test_decision_metrics_keep_every_confusion_case() -> None:
    result = evaluate_decisions(
        [
            (True, True),
            (False, True),
            (True, False),
            (False, False),
        ]
    )

    assert result.sample_count == 4
    assert result.true_positive == 1
    assert result.false_positive == 1
    assert result.false_negative == 1
    assert result.true_negative == 1
    assert result.precision == 0.5
    assert result.recall == 0.5
    assert result.f1 == 0.5


def test_undefined_decision_metrics_are_not_reported_as_perfect() -> None:
    result = evaluate_decisions([(False, False)])

    assert result.precision is None
    assert result.recall is None
    assert result.f1 is None


@pytest.mark.parametrize(
    ("reference", "hypothesis", "expected"),
    [
        ("", "", 0),
        ("먹골역", "먹골", 1),
        ("군자", "건대입구", 4),
    ],
)
def test_character_edit_distance(reference: str, hypothesis: str, expected: int) -> None:
    assert character_edit_distance(reference, hypothesis) == expected


def test_normalize_for_cer_is_explicit() -> None:
    assert normalize_for_cer(" 어린이 대공원역! ") == "어린이대공원역"

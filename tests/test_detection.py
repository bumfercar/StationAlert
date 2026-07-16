from __future__ import annotations

import pytest

from nextstop_stt.detection import (
    DecisionReason,
    DestinationAlertDetector,
    canonical_station_name,
    contains_station,
    contains_station_token,
)
from nextstop_stt.rtzr.models import StreamingTranscript


def test_partial_transcript_never_alerts() -> None:
    detector = DestinationAlertDetector("어린이대공원역")

    decision = detector.evaluate(_transcript("어린이대공원역입니다", final=False))

    assert decision.should_alert is False
    assert decision.reason is DecisionReason.PARTIAL


def test_final_target_station_alerts_once() -> None:
    detector = DestinationAlertDetector("어린이대공원")

    first = detector.evaluate(_transcript("이번 역은 어린이대공원 역입니다", final=True))
    duplicate = detector.evaluate(_transcript("어린이대공원역에 도착했습니다", final=True, seq=2))

    assert first.should_alert is True
    assert first.reason is DecisionReason.ALERT
    assert first.target_station == "어린이대공원역"
    assert duplicate.should_alert is False
    assert duplicate.reason is DecisionReason.DUPLICATE


def test_non_target_station_does_not_alert() -> None:
    detector = DestinationAlertDetector("어린이대공원역")

    decision = detector.evaluate(_transcript("이번 역은 군자역입니다", final=True))

    assert decision.should_alert is False
    assert decision.reason is DecisionReason.TARGET_NOT_FOUND


def test_station_match_does_not_accept_inner_substring() -> None:
    assert contains_station("이번 역은 어린이대공원역입니다", "대공원역") is False


def test_station_suffix_may_be_separated_by_stt_spacing() -> None:
    assert contains_station("다음 역은 군자 역입니다", "군자역") is True


def test_station_recognition_accepts_bare_exact_token_without_relaxing_alert() -> None:
    text = "먹골견입니다. 내리실 문은 오른쪽입니다. 이제 먹골."

    assert contains_station_token(text, "먹골역") is True
    assert contains_station(text, "먹골역") is False


@pytest.mark.parametrize("station", ["", " ", "어린이 대공원역"])
def test_invalid_target_station_is_rejected(station: str) -> None:
    with pytest.raises(ValueError, match="target_station"):
        canonical_station_name(station)


def _transcript(text: str, *, final: bool, seq: int = 1) -> StreamingTranscript:
    return StreamingTranscript.model_validate(
        {
            "seq": seq,
            "start_at": 0,
            "duration": 1_000 if final else 0,
            "final": final,
            "alternatives": [{"text": text, "confidence": 0.9}],
        }
    )

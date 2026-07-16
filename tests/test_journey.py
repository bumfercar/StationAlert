import pytest

from nextstop_stt.journey import JourneyStatus, JourneyTracker


def test_journey_reports_remaining_prepare_and_arrival() -> None:
    tracker = JourneyTracker("어린이대공원역")

    en_route = tracker.observe("중곡")
    prepare = tracker.observe("군자")
    arrived = tracker.observe("어린이대공원")

    assert en_route.status is JourneyStatus.EN_ROUTE
    assert en_route.stations_remaining == 2
    assert prepare.status is JourneyStatus.PREPARE_TO_EXIT
    assert prepare.stations_remaining == 1
    assert arrived.status is JourneyStatus.ARRIVED
    assert arrived.stations_remaining == 0


def test_journey_ignores_duplicate_and_backward_station() -> None:
    tracker = JourneyTracker("어린이대공원")
    tracker.observe("중곡")

    assert tracker.observe("중곡").status is JourneyStatus.DUPLICATE
    assert tracker.observe("용마산").status is JourneyStatus.OUT_OF_ORDER
    assert tracker.observe("군자").status is JourneyStatus.PREPARE_TO_EXIT


def test_journey_reports_passed_destination() -> None:
    tracker = JourneyTracker("중곡")

    update = tracker.observe("군자")

    assert update.status is JourneyStatus.PASSED_DESTINATION
    assert update.stations_remaining == -1


def test_journey_rejects_unsupported_destination() -> None:
    with pytest.raises(ValueError, match="destination must be one of"):
        JourneyTracker("강남역")

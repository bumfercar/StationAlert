import pytest

from nextstop_stt.journey import JourneyStatus, JourneyTracker, TravelDirection


def test_journey_reports_remaining_prepare_and_arrival() -> None:
    tracker = JourneyTracker("어린이대공원역")

    en_route = tracker.observe("중곡")
    prepare = tracker.observe("군자")
    arrived = tracker.observe("어린이대공원")

    assert en_route.status is JourneyStatus.EN_ROUTE
    assert en_route.stations_remaining == 2
    assert en_route.direction is TravelDirection.UNKNOWN
    assert prepare.status is JourneyStatus.PREPARE_TO_EXIT
    assert prepare.stations_remaining == 1
    assert prepare.direction is TravelDirection.TOWARD_CHILDRENS_GRAND_PARK
    assert arrived.status is JourneyStatus.ARRIVED
    assert arrived.stations_remaining == 0


def test_journey_ignores_duplicate_and_backward_station() -> None:
    tracker = JourneyTracker("어린이대공원")
    tracker.observe("중곡")
    tracker.observe("군자")

    assert tracker.observe("군자").status is JourneyStatus.DUPLICATE
    assert tracker.observe("용마산").status is JourneyStatus.OUT_OF_ORDER


def test_journey_reports_passed_destination() -> None:
    tracker = JourneyTracker("중곡")
    tracker.observe("용마산")

    update = tracker.observe("군자")

    assert update.status is JourneyStatus.PASSED_DESTINATION
    assert update.stations_remaining == -1


def test_journey_infers_reverse_direction() -> None:
    tracker = JourneyTracker("먹골")
    tracker.observe("면목")

    update = tracker.observe("상봉")

    assert update.direction is TravelDirection.TOWARD_GONGNEUNG
    assert update.status is JourneyStatus.EN_ROUTE
    assert update.stations_remaining == 2


def test_journey_rejects_unsupported_destination() -> None:
    with pytest.raises(ValueError, match="공릉~어린이대공원"):
        JourneyTracker("중계역")


def test_journey_can_start_from_known_recording_route() -> None:
    tracker = JourneyTracker("태릉입구", initial_station="공릉")

    context = tracker.route_context()
    update = tracker.observe("먹골")

    assert context is not None
    assert context.station == "공릉"
    assert context.stations_remaining == 1
    assert update.status is JourneyStatus.PASSED_DESTINATION

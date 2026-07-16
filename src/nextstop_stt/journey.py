"""Ordered Line 7 journey state for the interactive file-replay demo."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nextstop_stt.detection import canonical_station_name
from nextstop_stt.station_extraction import LINE_7_DEMO_STATIONS

LINE_7_DEMO_ROUTE = tuple(station.name for station in LINE_7_DEMO_STATIONS)


class JourneyStatus(StrEnum):
    """One explainable route-state transition."""

    EN_ROUTE = "en_route"
    PREPARE_TO_EXIT = "prepare_to_exit"
    ARRIVED = "arrived"
    PASSED_DESTINATION = "passed_destination"
    DUPLICATE = "duplicate_station"
    OUT_OF_ORDER = "out_of_order_station"


class TravelDirection(StrEnum):
    """Direction inferred from two ordered station observations."""

    UNKNOWN = "unknown"
    TOWARD_CHILDRENS_GRAND_PARK = "toward_childrens_grand_park"
    TOWARD_GONGNEUNG = "toward_gongneung"


@dataclass(frozen=True)
class JourneyUpdate:
    """Current position relative to the selected destination."""

    station: str
    destination: str
    stations_remaining: int
    status: JourneyStatus
    direction: TravelDirection


class JourneyTracker:
    """Infer travel direction and track position within the recorded corridor."""

    def __init__(self, destination: str, *, initial_station: str | None = None) -> None:
        canonical = canonical_station_name(destination).removesuffix("역")
        if canonical not in LINE_7_DEMO_ROUTE:
            supported = ", ".join(LINE_7_DEMO_ROUTE)
            raise ValueError(
                "하차역은 녹음 구간(공릉~어린이대공원) 안에서 선택해주세요: "
                f"{supported}"
            )
        if initial_station is not None and initial_station not in LINE_7_DEMO_ROUTE:
            raise ValueError("initial_station is outside the demo route")
        self.destination = canonical
        self._destination_index = LINE_7_DEMO_ROUTE.index(canonical)
        self._current_index: int | None = (
            LINE_7_DEMO_ROUTE.index(initial_station)
            if initial_station is not None
            else None
        )
        self._direction = TravelDirection.UNKNOWN
        self._has_stt_observation = False

    def route_context(self) -> JourneyUpdate | None:
        """Return the known recording start position, separate from STT evidence."""
        if self._current_index is None:
            return None
        station = LINE_7_DEMO_ROUTE[self._current_index]
        remaining = self._remaining(self._current_index)
        status = JourneyStatus.ARRIVED if remaining == 0 else JourneyStatus.EN_ROUTE
        return self._update(station, remaining, status)

    def observe(self, station: str) -> JourneyUpdate:
        """Apply one strong station extraction and reject direction reversals."""
        if station not in LINE_7_DEMO_ROUTE:
            raise ValueError("observed station is outside the demo route")
        station_index = LINE_7_DEMO_ROUTE.index(station)
        if self._current_index == station_index:
            if not self._has_stt_observation:
                self._has_stt_observation = True
                remaining = self._remaining(station_index)
                status = JourneyStatus.ARRIVED if remaining == 0 else JourneyStatus.EN_ROUTE
                return self._update(station, remaining, status)
            return self._update(
                station,
                self._remaining(station_index),
                JourneyStatus.DUPLICATE,
            )
        if self._current_index is None:
            self._current_index = station_index
            self._has_stt_observation = True
            remaining = abs(self._destination_index - station_index)
            status = JourneyStatus.ARRIVED if remaining == 0 else JourneyStatus.EN_ROUTE
            return self._update(station, remaining, status)

        observed_direction = (
            TravelDirection.TOWARD_CHILDRENS_GRAND_PARK
            if station_index > self._current_index
            else TravelDirection.TOWARD_GONGNEUNG
        )
        if self._direction is TravelDirection.UNKNOWN:
            self._direction = observed_direction
        elif observed_direction is not self._direction:
            return self._update(
                station,
                self._remaining(station_index),
                JourneyStatus.OUT_OF_ORDER,
            )

        self._current_index = station_index
        self._has_stt_observation = True
        remaining = self._remaining(station_index)
        if remaining == 1:
            status = JourneyStatus.PREPARE_TO_EXIT
        elif remaining == 0:
            status = JourneyStatus.ARRIVED
        elif remaining < 0:
            status = JourneyStatus.PASSED_DESTINATION
        else:
            status = JourneyStatus.EN_ROUTE
        return self._update(station, remaining, status)

    def _remaining(self, station_index: int) -> int:
        if self._direction is TravelDirection.TOWARD_GONGNEUNG:
            return station_index - self._destination_index
        return self._destination_index - station_index

    def _update(
        self,
        station: str,
        remaining: int,
        status: JourneyStatus,
    ) -> JourneyUpdate:
        return JourneyUpdate(
            station=station,
            destination=self.destination,
            stations_remaining=remaining,
            status=status,
            direction=self._direction,
        )

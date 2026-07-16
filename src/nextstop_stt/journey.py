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


@dataclass(frozen=True)
class JourneyUpdate:
    """Current position relative to the selected destination."""

    station: str
    destination: str
    stations_remaining: int
    status: JourneyStatus


class JourneyTracker:
    """Track forward travel from Nowon to Children's Grand Park."""

    def __init__(self, destination: str) -> None:
        canonical = canonical_station_name(destination).removesuffix("역")
        if canonical not in LINE_7_DEMO_ROUTE:
            supported = ", ".join(LINE_7_DEMO_ROUTE)
            raise ValueError(f"destination must be one of: {supported}")
        self.destination = canonical
        self._destination_index = LINE_7_DEMO_ROUTE.index(canonical)
        self._current_index: int | None = None

    def observe(self, station: str) -> JourneyUpdate:
        """Apply one strong station extraction without accepting backward jumps."""
        if station not in LINE_7_DEMO_ROUTE:
            raise ValueError("observed station is outside the demo route")
        station_index = LINE_7_DEMO_ROUTE.index(station)
        remaining = self._destination_index - station_index

        if self._current_index == station_index:
            return self._update(station, remaining, JourneyStatus.DUPLICATE)
        if self._current_index is not None and station_index < self._current_index:
            return self._update(station, remaining, JourneyStatus.OUT_OF_ORDER)

        self._current_index = station_index
        if remaining == 1:
            status = JourneyStatus.PREPARE_TO_EXIT
        elif remaining == 0:
            status = JourneyStatus.ARRIVED
        elif remaining < 0:
            status = JourneyStatus.PASSED_DESTINATION
        else:
            status = JourneyStatus.EN_ROUTE
        return self._update(station, remaining, status)

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
        )

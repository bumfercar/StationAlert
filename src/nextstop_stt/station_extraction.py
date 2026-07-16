"""Explainable Line 7 station extraction from finalized STT text."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nextstop_stt.detection import normalize_text


class StationMatchReason(StrEnum):
    """Evidence strength used to expose, not hide, extraction decisions."""

    CANONICAL_SUFFIX = "canonical_with_station_suffix"
    CANONICAL_ALIAS_SUFFIX = "canonical_with_alias_and_station_suffix"
    CANONICAL_ALIAS = "canonical_with_known_alias"
    CANONICAL_ANNOUNCEMENT_CONTEXT = "canonical_with_announcement_context"
    CANONICAL_TOKEN = "canonical_token_only"


@dataclass(frozen=True)
class StationDefinition:
    """One canonical station and optional secondary names heard in announcements."""

    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class StationMention:
    """One normalized station mention without retaining private transcript text."""

    station: str
    reason: StationMatchReason

    @property
    def is_current_station_evidence(self) -> bool:
        return self.reason is not StationMatchReason.CANONICAL_TOKEN


LINE_7_DEMO_STATIONS = (
    StationDefinition("공릉"),
    StationDefinition("태릉입구"),
    StationDefinition("먹골"),
    StationDefinition("중화"),
    StationDefinition("상봉"),
    StationDefinition("면목", aliases=("서일대입구",)),
    StationDefinition("사가정"),
    StationDefinition("용마산", aliases=("용마폭포공원",)),
    StationDefinition("중곡"),
    StationDefinition("군자"),
    StationDefinition("어린이대공원", aliases=("세종대",)),
)

_CURRENT_STATION_CONTEXT = frozenset(
    {
        "이번",
        "내리실",
        "출입문",
        "오른쪽",
        "왼쪽",
        "door",
        "doors",
    }
)


def line7_keyword_vocabulary() -> tuple[str, ...]:
    """Return equal-priority canonical and secondary names for the recorded corridor."""
    return tuple(
        keyword
        for station in LINE_7_DEMO_STATIONS
        for keyword in (station.name, *station.aliases)
    )


def line7_station_keyword_vocabulary(station_name: str) -> tuple[str, ...]:
    """Return the canonical and secondary names for one destination station."""
    for station in LINE_7_DEMO_STATIONS:
        if station.name == station_name:
            return (station.name, *station.aliases)
    raise ValueError("station_name is outside the demo route")


def extract_line7_station_mentions(text: str) -> tuple[StationMention, ...]:
    """Extract exact station tokens and alias-aware station phrases from one final result."""
    tokens = normalize_text(text).split()
    has_announcement_context = any(
        token.casefold().startswith(context)
        for token in tokens
        for context in _CURRENT_STATION_CONTEXT
    )
    matches = []
    for station in LINE_7_DEMO_STATIONS:
        reason = _strongest_reason(
            tokens,
            station,
            has_announcement_context=has_announcement_context,
        )
        if reason is not None:
            matches.append(StationMention(station=station.name, reason=reason))
    return tuple(matches)


def _strongest_reason(
    tokens: list[str],
    station: StationDefinition,
    *,
    has_announcement_context: bool,
) -> StationMatchReason | None:
    fallback = None
    canonical_with_suffix = f"{station.name}역"
    for index, token in enumerate(tokens):
        if token.startswith(canonical_with_suffix):
            return StationMatchReason.CANONICAL_SUFFIX
        if token != station.name:
            continue
        if index + 1 < len(tokens) and tokens[index + 1].startswith("역"):
            return StationMatchReason.CANONICAL_SUFFIX
        if index + 1 < len(tokens) and tokens[index + 1] in station.aliases:
            if index + 2 < len(tokens) and tokens[index + 2].startswith("역"):
                return StationMatchReason.CANONICAL_ALIAS_SUFFIX
            return StationMatchReason.CANONICAL_ALIAS
        fallback = (
            StationMatchReason.CANONICAL_ANNOUNCEMENT_CONTEXT
            if has_announcement_context
            else StationMatchReason.CANONICAL_TOKEN
        )
    return fallback

"""Explainable Line 7 station extraction from finalized STT text."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from nextstop_stt.detection import normalize_text


class StationMatchReason(StrEnum):
    """Evidence strength used to expose, not hide, extraction decisions."""

    CANONICAL_SUFFIX = "canonical_with_station_suffix"
    CANONICAL_ALIAS_SUFFIX = "canonical_with_alias_and_station_suffix"
    CANONICAL_ALIAS = "canonical_with_known_alias"
    CANONICAL_ANNOUNCEMENT_CONTEXT = "canonical_with_announcement_context"
    CONTEXTUAL_PHONETIC_RECOVERY = "contextual_phonetic_recovery"
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
    observed_token: str | None = None
    phonetic_distance: float | None = None

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

_PHONETIC_RECOVERY_CONTEXT = frozenset({"이번", "this", "next", "stop", "station"})
_MAX_PHONETIC_DISTANCE = 1 / 3
_MAX_PHONETIC_EDITS = 2
_MIN_RUNNER_UP_MARGIN = 0.2
_PHONETIC_FOLD = {
    "ᄁ": "ᄀ",
    "ᄏ": "ᄀ",
    "ᄄ": "ᄃ",
    "ᄐ": "ᄃ",
    "ᄈ": "ᄇ",
    "ᄑ": "ᄇ",
    "ᄊ": "ᄉ",
    "ᄍ": "ᄌ",
    "ᄎ": "ᄌ",
    "ᆩ": "ᆨ",
    "ᆿ": "ᆨ",
    "ᆻ": "ᆺ",
    "ᆽ": "ᆮ",
    "ᆾ": "ᆮ",
    "ᇀ": "ᆮ",
    "ᇂ": "ᆮ",
    "ᇁ": "ᆸ",
}


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


def extract_line7_station_mentions(
    text: str,
    *,
    allow_contextual_recovery: bool = False,
) -> tuple[StationMention, ...]:
    """Extract station mentions, with optional context-gated phonetic recovery."""
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
    if matches or not allow_contextual_recovery:
        return tuple(matches)
    if not _has_context(tokens, _PHONETIC_RECOVERY_CONTEXT):
        return ()
    recovered = _recover_phonetic_station(tokens)
    return (recovered,) if recovered is not None else ()


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


def _has_context(tokens: list[str], contexts: frozenset[str]) -> bool:
    return any(
        token.casefold().startswith(context)
        for token in tokens
        for context in contexts
    )


def _recover_phonetic_station(tokens: list[str]) -> StationMention | None:
    recoveries: list[StationMention] = []
    for token in tokens:
        phonemes = _hangul_phonemes(token)
        if len(phonemes) < 4:
            continue
        by_station: dict[str, tuple[float, int]] = {}
        for station in LINE_7_DEMO_STATIONS:
            distances = tuple(
                _edit_measure(phonemes, _hangul_phonemes(spoken_form))
                for spoken_form in (station.name, *station.aliases)
            )
            by_station[station.name] = min(distances)
        ranked = sorted(by_station.items(), key=lambda item: (*item[1], item[0]))
        (best_station, (best_distance, best_edits)), (
            _,
            (runner_up_distance, _),
        ) = ranked[:2]
        if best_distance > _MAX_PHONETIC_DISTANCE:
            continue
        if best_edits > _MAX_PHONETIC_EDITS:
            continue
        if runner_up_distance - best_distance < _MIN_RUNNER_UP_MARGIN:
            continue
        recoveries.append(
            StationMention(
                station=best_station,
                reason=StationMatchReason.CONTEXTUAL_PHONETIC_RECOVERY,
                observed_token=token,
                phonetic_distance=round(best_distance, 3),
            )
        )

    recovered_stations = {mention.station for mention in recoveries}
    if len(recovered_stations) != 1:
        return None
    return min(
        recoveries,
        key=lambda mention: mention.phonetic_distance or 0.0,
    )


def _hangul_phonemes(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(
        _PHONETIC_FOLD.get(character, character)
        for character in decomposed
        if "HANGUL" in unicodedata.name(character, "")
    )


def _edit_measure(left: str, right: str) -> tuple[float, int]:
    if not left or not right:
        return 1.0, max(len(left), len(right))
    edits = _edit_distance(left, right)
    return edits / max(len(left), len(right)), edits


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1]
                    + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]

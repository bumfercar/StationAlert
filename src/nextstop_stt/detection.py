"""Minimal, explainable destination alert baseline."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from nextstop_stt.rtzr.models import StreamingTranscript

_NON_WORD_PATTERN = re.compile(r"[^0-9A-Za-z가-힣]+")


class DecisionReason(StrEnum):
    """Stable reasons for every alert or suppression."""

    ALERT = "target_station_in_final"
    PARTIAL = "partial_transcript"
    TARGET_NOT_FOUND = "target_station_not_found"
    DUPLICATE = "duplicate_alert"


@dataclass(frozen=True)
class AlertDecision:
    """One destination decision without storing private transcript text."""

    should_alert: bool
    reason: DecisionReason
    target_station: str
    transcript_seq: int


class DestinationAlertDetector:
    """Alert once when a final transcript contains the selected station."""

    def __init__(self, target_station: str) -> None:
        self.target_station = canonical_station_name(target_station)
        self._alerted = False

    def evaluate(self, transcript: StreamingTranscript) -> AlertDecision:
        """Apply the final-only, exact station-token baseline."""
        if not transcript.final:
            return self._decision(False, DecisionReason.PARTIAL, transcript.seq)
        if not contains_station(transcript.primary_text, self.target_station):
            return self._decision(False, DecisionReason.TARGET_NOT_FOUND, transcript.seq)
        if self._alerted:
            return self._decision(False, DecisionReason.DUPLICATE, transcript.seq)

        self._alerted = True
        return self._decision(True, DecisionReason.ALERT, transcript.seq)

    def _decision(
        self,
        should_alert: bool,
        reason: DecisionReason,
        transcript_seq: int,
    ) -> AlertDecision:
        return AlertDecision(
            should_alert=should_alert,
            reason=reason,
            target_station=self.target_station,
            transcript_seq=transcript_seq,
        )


def canonical_station_name(station: str) -> str:
    """Return a compact station name with one trailing `역`."""
    tokens = normalize_text(station).split()
    if len(tokens) != 1:
        raise ValueError("target_station must be one station name")
    base = tokens[0].removesuffix("역")
    if not base:
        raise ValueError("target_station must not be empty")
    return f"{base}역"


def contains_station(text: str, target_station: str) -> bool:
    """Match a station at a token start, including a separated `역` suffix."""
    normalized = normalize_text(text)
    tokens = normalized.split()
    target_base = target_station.removesuffix("역")
    for index, token in enumerate(tokens):
        if token.startswith(target_station):
            return True
        if token == target_base and index + 1 < len(tokens) and tokens[index + 1].startswith("역"):
            return True
    return False


def normalize_text(text: str) -> str:
    """Normalize Unicode and punctuation while preserving word boundaries."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _NON_WORD_PATTERN.sub(" ", normalized)
    return " ".join(normalized.split())

"""Shared text normalization for subway station decisions."""

from __future__ import annotations

import re
import unicodedata

_NON_WORD_PATTERN = re.compile(r"[^0-9A-Za-z가-힣]+")


def canonical_station_name(station: str) -> str:
    """Return a compact station name with one trailing `역`."""
    tokens = normalize_text(station).split()
    if len(tokens) != 1:
        raise ValueError("target_station must be one station name")
    base = tokens[0].removesuffix("역")
    if not base:
        raise ValueError("target_station must not be empty")
    return f"{base}역"


def normalize_text(text: str) -> str:
    """Normalize Unicode and punctuation while preserving word boundaries."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _NON_WORD_PATTERN.sub(" ", normalized)
    return " ".join(normalized.split())

"""Small, dependency-free metrics for STT and destination decisions."""

from __future__ import annotations

from dataclasses import dataclass

from nextstop_stt.detection import (
    canonical_station_name,
    contains_station_token,
    normalize_text,
)


@dataclass(frozen=True)
class CharacterErrorResult:
    """Character edit counts for one or more transcription units."""

    sample_count: int
    edit_count: int
    reference_character_count: int

    @property
    def cer(self) -> float | None:
        if self.reference_character_count == 0:
            return None
        return self.edit_count / self.reference_character_count


@dataclass(frozen=True)
class StationMatchResult:
    """Exact station-token matches across labeled announcement units."""

    sample_count: int
    match_count: int

    @property
    def exact_match_rate(self) -> float | None:
        if self.sample_count == 0:
            return None
        return self.match_count / self.sample_count


@dataclass(frozen=True)
class DecisionMetrics:
    """Binary destination-decision counts with undefined metrics preserved."""

    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int

    @property
    def sample_count(self) -> int:
        return self.true_positive + self.false_positive + self.false_negative + self.true_negative

    @property
    def precision(self) -> float | None:
        denominator = self.true_positive + self.false_positive
        if denominator == 0:
            return None
        return self.true_positive / denominator

    @property
    def recall(self) -> float | None:
        denominator = self.true_positive + self.false_negative
        if denominator == 0:
            return None
        return self.true_positive / denominator

    @property
    def f1(self) -> float | None:
        denominator = 2 * self.true_positive + self.false_positive + self.false_negative
        if denominator == 0:
            return None
        return 2 * self.true_positive / denominator


def evaluate_character_error(pairs: list[tuple[str, str]]) -> CharacterErrorResult:
    """Calculate micro-averaged CER after the production text normalization."""
    edit_count = 0
    reference_character_count = 0
    for reference, hypothesis in pairs:
        normalized_reference = normalize_for_cer(reference)
        normalized_hypothesis = normalize_for_cer(hypothesis)
        edit_count += character_edit_distance(normalized_reference, normalized_hypothesis)
        reference_character_count += len(normalized_reference)
    return CharacterErrorResult(
        sample_count=len(pairs),
        edit_count=edit_count,
        reference_character_count=reference_character_count,
    )


def evaluate_station_matches(pairs: list[tuple[str, str]]) -> StationMatchResult:
    """Check whether each hypothesis contains its labeled station as an exact token."""
    match_count = sum(
        contains_station_token(hypothesis, canonical_station_name(station))
        for station, hypothesis in pairs
    )
    return StationMatchResult(sample_count=len(pairs), match_count=match_count)


def evaluate_decisions(pairs: list[tuple[bool, bool]]) -> DecisionMetrics:
    """Count `(expected_alert, predicted_alert)` pairs without hiding negatives."""
    true_positive = false_positive = false_negative = true_negative = 0
    for expected, predicted in pairs:
        if expected and predicted:
            true_positive += 1
        elif not expected and predicted:
            false_positive += 1
        elif expected and not predicted:
            false_negative += 1
        else:
            true_negative += 1
    return DecisionMetrics(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        true_negative=true_negative,
    )


def normalize_for_cer(text: str) -> str:
    """Reuse product normalization and ignore word spacing for Korean CER."""
    return normalize_text(text).replace(" ", "")


def character_edit_distance(reference: str, hypothesis: str) -> int:
    """Return Levenshtein distance using memory proportional to the shorter input."""
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_character in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_character in enumerate(hypothesis, start=1):
            substitution_cost = reference_character != hypothesis_character
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1] + substitution_cost,
                )
            )
        previous = current
    return previous[-1]

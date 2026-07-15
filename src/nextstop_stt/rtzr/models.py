"""Typed RTZR Streaming configuration and response models."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_KOREAN_KEYWORD_PATTERN = re.compile(r"^[가-힣 ]+$")


class AudioEncoding(StrEnum):
    """Streaming encodings used by this project."""

    LINEAR16 = "LINEAR16"


class StreamingModel(StrEnum):
    """Models documented for RTZR Streaming STT."""

    SOMMERS_KO = "sommers_ko"
    SOMMERS_JA = "sommers_ja"
    WHISPER = "whisper"


class StreamingDomain(StrEnum):
    """RTZR Streaming acoustic domains."""

    CALL = "CALL"
    MEETING = "MEETING"


class KeywordBoost(BaseModel):
    """One Korean Streaming keyword and its explicit score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=20)
    score: float = Field(default=2.0, ge=-5.0, le=5.0)

    @field_validator("text")
    @classmethod
    def validate_korean_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or not _KOREAN_KEYWORD_PATTERN.fullmatch(normalized):
            raise ValueError("Streaming keywords must contain only Korean syllables and spaces")
        return normalized

    def to_websocket_value(self) -> str:
        """Render the documented `word:score` WebSocket format."""
        return f"{self.text}:{self.score}"


class StreamingConfig(BaseModel):
    """Explicit decoder parameters sent on every Streaming experiment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_rate: int = Field(ge=8_000, le=48_000)
    encoding: AudioEncoding = AudioEncoding.LINEAR16
    model_name: StreamingModel = StreamingModel.SOMMERS_KO
    domain: StreamingDomain = StreamingDomain.CALL
    use_itn: bool = True
    use_disfluency_filter: bool = False
    use_profanity_filter: bool = False
    use_punctuation: bool = False
    keywords: tuple[KeywordBoost, ...] = Field(default_factory=tuple, max_length=100)
    language: str | None = None

    @model_validator(mode="after")
    def validate_model_specific_options(self) -> StreamingConfig:
        if self.model_name is StreamingModel.WHISPER:
            if not self.language:
                raise ValueError("language must be explicit when model_name is whisper")
            if self.keywords:
                raise ValueError("Streaming keyword boosting is unavailable for whisper")
        elif self.language is not None:
            raise ValueError("language is only applied to the Streaming whisper model")

        if self.keywords and self.model_name is not StreamingModel.SOMMERS_KO:
            raise ValueError("Streaming keyword boosting is available only for sommers_ko")
        return self

    def to_query_params(self) -> dict[str, str]:
        """Return the WebSocket query parameters without relying on API defaults."""
        params = {
            "sample_rate": str(self.sample_rate),
            "encoding": self.encoding.value,
            "model_name": self.model_name.value,
            "domain": self.domain.value,
            "use_itn": _format_bool(self.use_itn),
            "use_disfluency_filter": _format_bool(self.use_disfluency_filter),
            "use_profanity_filter": _format_bool(self.use_profanity_filter),
            "use_punctuation": _format_bool(self.use_punctuation),
        }
        if self.keywords:
            params["keywords"] = ",".join(keyword.to_websocket_value() for keyword in self.keywords)
        if self.language is not None:
            params["language"] = self.language
        return params


class StreamingWord(BaseModel):
    """Word timing returned for a final transcript when available."""

    model_config = ConfigDict(frozen=True, extra="allow")

    text: str
    start_at: int = Field(ge=0)
    duration: int = Field(ge=0)
    confidence: float | None = None


class StreamingAlternative(BaseModel):
    """One RTZR transcript alternative."""

    model_config = ConfigDict(frozen=True, extra="allow")

    text: str
    confidence: float
    words: tuple[StreamingWord, ...] = ()


class StreamingTranscript(BaseModel):
    """Documented RTZR partial or final Streaming response."""

    model_config = ConfigDict(frozen=True, extra="allow")

    seq: int = Field(ge=0)
    start_at: int = Field(ge=0)
    duration: int = Field(ge=0)
    final: bool
    alternatives: tuple[StreamingAlternative, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_partial_duration(self) -> StreamingTranscript:
        if not self.final and self.duration != 0:
            raise ValueError("partial Streaming responses must have duration=0")
        return self

    @property
    def primary_text(self) -> str:
        """Return the highest-ranked alternative documented at index zero."""
        return self.alternatives[0].text


def _format_bool(value: bool) -> str:
    return "true" if value else "false"

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nextstop_stt.rtzr.models import (
    KeywordBoost,
    StreamingConfig,
    StreamingDomain,
    StreamingModel,
    StreamingTranscript,
)


def test_streaming_config_serializes_every_important_parameter() -> None:
    config = StreamingConfig(
        sample_rate=16_000,
        domain=StreamingDomain.MEETING,
        use_itn=True,
        use_disfluency_filter=False,
        use_profanity_filter=False,
        use_punctuation=True,
        keywords=(KeywordBoost(text="어린이대공원역", score=2.5),),
    )

    assert config.to_query_params() == {
        "sample_rate": "16000",
        "encoding": "LINEAR16",
        "model_name": "sommers_ko",
        "domain": "MEETING",
        "use_itn": "true",
        "use_disfluency_filter": "false",
        "use_profanity_filter": "false",
        "use_punctuation": "true",
        "keywords": "어린이대공원역:2.5",
    }


@pytest.mark.parametrize("keyword", ["STT", "군자역:5", "children park"])
def test_streaming_keyword_requires_korean_phonetic_text(keyword: str) -> None:
    with pytest.raises(ValidationError, match="Korean syllables"):
        KeywordBoost(text=keyword)


def test_whisper_requires_explicit_language() -> None:
    with pytest.raises(ValidationError, match="language must be explicit"):
        StreamingConfig(sample_rate=16_000, model_name=StreamingModel.WHISPER)


def test_whisper_rejects_streaming_keyword_boosting() -> None:
    with pytest.raises(ValidationError, match="unavailable for whisper"):
        StreamingConfig(
            sample_rate=16_000,
            model_name=StreamingModel.WHISPER,
            language="ko",
            keywords=(KeywordBoost(text="공릉역"),),
        )


def test_partial_and_final_streaming_responses_are_typed() -> None:
    partial = StreamingTranscript.model_validate(
        {
            "seq": 7,
            "start_at": 1_200,
            "duration": 0,
            "final": False,
            "alternatives": [{"text": "이번 역은", "confidence": 1.2}],
            "future_field": "preserved",
        }
    )
    final = StreamingTranscript.model_validate(
        {
            "seq": 7,
            "start_at": 1_200,
            "duration": 2_400,
            "final": True,
            "alternatives": [
                {
                    "text": "이번 역은 군자역입니다.",
                    "confidence": 0.91,
                    "words": [
                        {
                            "text": "군자역입니다",
                            "start_at": 850,
                            "duration": 600,
                            "confidence": 0.8,
                        }
                    ],
                }
            ],
        }
    )

    assert partial.primary_text == "이번 역은"
    assert partial.model_extra == {"future_field": "preserved"}
    assert final.alternatives[0].words[0].start_at == 850


def test_partial_response_preserves_observed_nonzero_duration() -> None:
    partial = StreamingTranscript.model_validate(
        {
            "seq": 1,
            "start_at": 0,
            "duration": 100,
            "final": False,
            "alternatives": [{"text": "공릉", "confidence": 0.5}],
        }
    )

    assert partial.duration == 100

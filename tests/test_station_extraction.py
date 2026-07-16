from nextstop_stt.station_extraction import (
    StationMatchReason,
    extract_line7_station_mentions,
    line7_keyword_vocabulary,
    line7_station_keyword_vocabulary,
)


def test_extracts_alias_between_station_and_suffix() -> None:
    mentions = extract_line7_station_mentions("어린이대공원 세종대 역")

    assert len(mentions) == 1
    assert mentions[0].station == "어린이대공원"
    assert mentions[0].reason is StationMatchReason.CANONICAL_ALIAS_SUFFIX
    assert mentions[0].is_current_station_evidence is True


def test_extracts_attached_station_suffix() -> None:
    mentions = extract_line7_station_mentions("이번 역은 면목역입니다")

    assert mentions[0].station == "면목"
    assert mentions[0].reason is StationMatchReason.CANONICAL_SUFFIX


def test_extracts_spoken_station_form_with_spaces() -> None:
    mentions = extract_line7_station_mentions("이번 역은 태능 입구역입니다")

    assert len(mentions) == 1
    assert mentions[0].station == "태릉입구"
    assert mentions[0].reason is StationMatchReason.CANONICAL_SUFFIX


def test_known_alias_is_strong_evidence_even_when_station_suffix_is_dropped() -> None:
    mentions = extract_line7_station_mentions("어린이대공원 세종대")

    assert mentions[0].station == "어린이대공원"
    assert mentions[0].reason is StationMatchReason.CANONICAL_ALIAS
    assert mentions[0].is_current_station_evidence is True


def test_bare_station_is_visible_but_not_current_station_evidence() -> None:
    mentions = extract_line7_station_mentions("이제 먹골")

    assert mentions[0].station == "먹골"
    assert mentions[0].reason is StationMatchReason.CANONICAL_TOKEN
    assert mentions[0].is_current_station_evidence is False


def test_bare_station_with_door_context_is_current_station_evidence() -> None:
    mentions = extract_line7_station_mentions(
        "먹골견입니다 리신 분은 오른쪽입니다 이제 먹골"
    )

    assert mentions[0].station == "먹골"
    assert mentions[0].reason is StationMatchReason.CANONICAL_ANNOUNCEMENT_CONTEXT
    assert mentions[0].is_current_station_evidence is True


def test_unrelated_bare_keyword_hallucination_stays_weak() -> None:
    mentions = extract_line7_station_mentions("미리 신문은 오류 입니다 지원금 중화 어")

    assert mentions[0].station == "중화"
    assert mentions[0].reason is StationMatchReason.CANONICAL_TOKEN
    assert mentions[0].is_current_station_evidence is False


def test_does_not_fuzzy_match_station_misrecognition() -> None:
    assert extract_line7_station_mentions("어린이비복원 세동제 역") == ()


def test_contextual_recovery_is_opt_in() -> None:
    text = "이번 저는 범볼 범볼 견입니다 이제 저는 마 마콜"

    assert extract_line7_station_mentions(text) == ()


def test_recovers_unique_phonetic_station_inside_announcement_context() -> None:
    mentions = extract_line7_station_mentions(
        "이번 저는 범볼 범볼 견입니다 이제 저는 마 마콜",
        allow_contextual_recovery=True,
    )

    assert len(mentions) == 1
    assert mentions[0].station == "먹골"
    assert mentions[0].reason is StationMatchReason.CONTEXTUAL_PHONETIC_RECOVERY
    assert mentions[0].observed_token == "마콜"
    assert mentions[0].phonetic_distance == 0.333
    assert mentions[0].is_current_station_evidence is True


def test_does_not_recover_phonetic_station_without_announcement_context() -> None:
    assert (
        extract_line7_station_mentions(
            "친구가 마콜이라고 말했다",
            allow_contextual_recovery=True,
        )
        == ()
    )


def test_contextual_recovery_keeps_distant_misrecognition_rejected() -> None:
    assert (
        extract_line7_station_mentions(
            "이번 역은 어린이비복원 세동제 역",
            allow_contextual_recovery=True,
        )
        == ()
    )


def test_does_not_accept_inner_substring() -> None:
    assert extract_line7_station_mentions("경상봉역사 안에서 안내드립니다") == ()


def test_keyword_vocabulary_contains_canonical_and_secondary_names() -> None:
    vocabulary = line7_keyword_vocabulary()

    assert "공릉" in vocabulary
    assert "공능" in vocabulary
    assert "태릉 입구" in vocabulary
    assert "태능 입구" in vocabulary
    assert "노원" not in vocabulary
    assert "중계" not in vocabulary
    assert "하계" not in vocabulary
    assert "어린이대공원" in vocabulary
    assert "세종대" in vocabulary
    assert len(vocabulary) == len(set(vocabulary))


def test_destination_keyword_vocabulary_contains_secondary_name() -> None:
    assert line7_station_keyword_vocabulary("어린이대공원") == (
        "어린이대공원",
        "세종대",
        "어린이 대공원",
    )

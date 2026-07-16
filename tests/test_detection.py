import pytest

from nextstop_stt.detection import canonical_station_name, normalize_text


def test_station_name_gets_one_suffix() -> None:
    assert canonical_station_name("군자") == "군자역"
    assert canonical_station_name("군자역") == "군자역"


def test_text_normalization_preserves_word_boundaries() -> None:
    assert normalize_text("이번 역은, 군자역입니다!") == "이번 역은 군자역입니다"


@pytest.mark.parametrize("station", ["", " ", "어린이 대공원역"])
def test_invalid_target_station_is_rejected(station: str) -> None:
    with pytest.raises(ValueError, match="target_station"):
        canonical_station_name(station)

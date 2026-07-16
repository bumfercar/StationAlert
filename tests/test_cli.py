import re

from typer.testing import CliRunner

import nextstop_stt.cli as cli_module
from nextstop_stt.cli import app

runner = CliRunner()
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _unstyle(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def test_help_describes_project() -> None:
    result = runner.invoke(app, ["--help"], terminal_width=160)
    output = _unstyle(result.output)

    assert result.exit_code == 0
    assert "RTZR Streaming STT" in output


def test_version_is_available() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_check_auth_missing_credentials_is_understandable(monkeypatch) -> None:
    monkeypatch.delenv("RTZR_CLIENT_ID", raising=False)
    monkeypatch.delenv("RTZR_CLIENT_SECRET", raising=False)

    result = runner.invoke(app, ["check-auth"])

    assert result.exit_code == 1
    assert "RTZR_CLIENT_ID" in result.output
    assert "RTZR_CLIENT_SECRET" in result.output


def test_check_auth_never_prints_token(monkeypatch) -> None:
    private_token = "private-access-token"

    class FakeCredentials:
        @classmethod
        def from_env(cls):
            return cls()

    class FakeTokenProvider:
        def __init__(self, _credentials) -> None:
            pass

        async def get_access_token(self) -> str:
            return private_token

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(cli_module, "RTZRCredentials", FakeCredentials)
    monkeypatch.setattr(cli_module, "RTZRTokenProvider", FakeTokenProvider)

    result = runner.invoke(app, ["check-auth"])

    assert result.exit_code == 0
    assert "authentication succeeded" in result.output
    assert private_token not in result.output


def test_stream_file_help_makes_cost_and_privacy_controls_visible() -> None:
    result = runner.invoke(app, ["stream-file", "--help"], terminal_width=220)
    output = _unstyle(result.output)

    assert result.exit_code == 0
    assert "--duration-ms" in output
    assert "required" in output.lower()
    assert "--show-text" in output
    assert "passenger" in output
    assert "audio" in output
    assert "--target-station" in output
    assert "--keyword" in output
    assert "--detect-station" in output
    assert "--output-file" in output


def test_journey_demo_help_exposes_user_inputs_and_safe_defaults() -> None:
    result = runner.invoke(app, ["journey-demo", "--help"], terminal_width=220)
    output = _unstyle(result.output)

    assert result.exit_code == 0
    assert "--source-file" in output
    assert "--destination" in output
    assert "--start-seconds" in output
    assert "--duration-seconds" in output
    assert "--keyword-score" in output
    assert "--output-file" in output


def test_journey_demo_shows_prepare_and_arrival_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "owned.m4a"
    source.write_bytes(b"private audio placeholder")
    (tmp_path / "results" / "private").mkdir(parents=True)
    captured = {}

    async def fake_stream_file(**kwargs):
        captured.update(kwargs)
        tracker = kwargs["journey_tracker"]
        cli_module._render_journey_update(tracker.observe("중곡"), seq=1)
        cli_module._render_journey_update(tracker.observe("군자"), seq=2)
        cli_module._render_journey_update(tracker.observe("어린이대공원"), seq=3)
        return 0, 3, 0, 3, 0

    monkeypatch.setattr(cli_module, "_stream_file", fake_stream_file)
    monkeypatch.setattr(cli_module, "probe_audio_duration_ms", lambda _: 60_000)

    result = runner.invoke(
        app,
        [
            "journey-demo",
            "--source-file",
            str(source),
            "--destination",
            "어린이대공원",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "[하차 준비]" in result.output
    assert "[도착] 어린이대공원역" in result.output
    assert "현재역=3" in result.output
    assert captured["duration_ms"] == 60_000


def test_streaming_keyword_parser_uses_explicit_or_default_score() -> None:
    boosts = cli_module._parse_keyword_boosts(("먹골역:2.5", "상봉역"))

    assert [(boost.text, boost.score) for boost in boosts] == [
        ("먹골역", 2.5),
        ("상봉역", 2.0),
    ]


def test_line7_keyword_boosts_use_equal_score_without_overriding_explicit_word() -> None:
    explicit = cli_module._parse_keyword_boosts(("먹골:0.5",))

    boosts = cli_module._merge_line7_keyword_boosts(explicit, score=1.0)

    by_text = {boost.text: boost.score for boost in boosts}
    assert by_text["먹골"] == 0.5
    assert by_text["어린이대공원"] == 1.0
    assert by_text["세종대"] == 1.0
    assert len(by_text) == len(boosts)


def test_replay_clock_formats_short_and_long_audio() -> None:
    assert cli_module._clock_text(20_000) == "00:20"
    assert cli_module._clock_text(1_179_456) == "19:39"
    assert cli_module._clock_text(3_661_000) == "01:01:01"


def test_batch_file_help_exposes_model_domain_and_private_output() -> None:
    result = runner.invoke(app, ["batch-file", "--help"], terminal_width=160)
    output = _unstyle(result.output)

    assert result.exit_code == 0
    assert "--model" in output
    assert "--domain" in output
    assert "results/private" in output


def test_batch_output_must_stay_in_private_results(tmp_path) -> None:
    outside = tmp_path / "public-result.json"

    try:
        cli_module._private_result_path(outside)
    except ValueError as error:
        assert "results/private" in str(error)
    else:
        raise AssertionError("public Batch output path was accepted")


def test_prepare_review_help_exposes_fixed_chunks_and_private_output() -> None:
    result = runner.invoke(app, ["prepare-review", "--help"], terminal_width=160)
    output = _unstyle(result.output)

    assert result.exit_code == 0
    assert "model-independent" in output
    assert "private_audio" in output
    assert "--chunk-seconds" in output


def test_review_output_must_stay_in_private_audio(tmp_path) -> None:
    outside = tmp_path / "public-review"

    try:
        cli_module._private_audio_dir(outside)
    except ValueError as error:
        assert "private_audio" in str(error)
    else:
        raise AssertionError("public review output path was accepted")


def test_evaluate_run_help_exposes_private_inputs_and_system_name() -> None:
    result = runner.invoke(app, ["evaluate-run", "--help"], terminal_width=160)
    output = _unstyle(result.output)

    assert result.exit_code == 0
    assert "--ground-truth-file" in output
    assert "--predictions-file" in output
    assert "--system-name" in output
    assert "private" in output.lower()


def test_evaluate_run_writes_only_private_aggregate(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    private_audio = tmp_path / "private_audio"
    private_results = tmp_path / "results" / "private"
    private_audio.mkdir()
    private_results.mkdir(parents=True)
    ground_truth = private_audio / "ground-truth.csv"
    predictions = private_results / "predictions.csv"
    output = private_results / "metrics.json"
    ground_truth.write_text(
        "segment_id,start_ms,end_ms,station,announcement_type,reference_text,"
        "expected_alert,overlapping_speech,noise_level,notes\n"
        "segment-001,1000,2000,먹골,NEXT_STATION,먹골역,true,false,medium,\n",
        encoding="utf-8",
    )
    predictions.write_text(
        "segment_id,hypothesis_text,predicted_alert,status\n"
        "segment-001,먹골역,true,completed\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "evaluate-run",
            "--ground-truth-file",
            str(ground_truth),
            "--predictions-file",
            str(predictions),
            "--output-file",
            str(output),
            "--system-name",
            "test-system",
        ],
    )

    assert result.exit_code == 0
    assert "CER=0.0000" in result.output
    assert "F1=1.0000" in result.output
    artifact = output.read_text(encoding="utf-8")
    assert "test-system" in artifact
    assert "먹골역" not in artifact


def test_prepare_batch_predictions_help_exposes_alignment_inputs() -> None:
    result = runner.invoke(
        app,
        ["prepare-batch-predictions", "--help"],
        terminal_width=160,
    )
    output = _unstyle(result.output)

    assert result.exit_code == 0
    assert "--ground-truth-file" in output
    assert "--batch-result-file" in output
    assert "--target-station" in output
    assert "results/private" in output

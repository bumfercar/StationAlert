from typer.testing import CliRunner

import nextstop_stt.cli as cli_module
from nextstop_stt.cli import app

runner = CliRunner()


def test_help_describes_project() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "RTZR Streaming STT" in result.stdout


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
    result = runner.invoke(app, ["stream-file", "--help"])

    assert result.exit_code == 0
    assert "--duration-ms" in result.output
    assert "required" in result.output.lower()
    assert "--show-text" in result.output
    assert "passenger" in result.output
    assert "audio" in result.output
    assert "--target-station" in result.output
    assert "--keyword" in result.output
    assert "--output-file" in result.output


def test_streaming_keyword_parser_uses_explicit_or_default_score() -> None:
    boosts = cli_module._parse_keyword_boosts(("먹골역:2.5", "상봉역"))

    assert [(boost.text, boost.score) for boost in boosts] == [
        ("먹골역", 2.5),
        ("상봉역", 2.0),
    ]


def test_batch_file_help_exposes_model_domain_and_private_output() -> None:
    result = runner.invoke(app, ["batch-file", "--help"])

    assert result.exit_code == 0
    assert "--model" in result.output
    assert "--domain" in result.output
    assert "results/private" in result.output


def test_batch_output_must_stay_in_private_results(tmp_path) -> None:
    outside = tmp_path / "public-result.json"

    try:
        cli_module._private_result_path(outside)
    except ValueError as error:
        assert "results/private" in str(error)
    else:
        raise AssertionError("public Batch output path was accepted")


def test_prepare_review_help_exposes_fixed_chunks_and_private_output() -> None:
    result = runner.invoke(app, ["prepare-review", "--help"])

    assert result.exit_code == 0
    assert "model-independent" in result.output
    assert "private_audio" in result.output
    assert "--chunk-seconds" in result.output


def test_review_output_must_stay_in_private_audio(tmp_path) -> None:
    outside = tmp_path / "public-review"

    try:
        cli_module._private_audio_dir(outside)
    except ValueError as error:
        assert "private_audio" in str(error)
    else:
        raise AssertionError("public review output path was accepted")


def test_evaluate_run_help_exposes_private_inputs_and_system_name() -> None:
    result = runner.invoke(app, ["evaluate-run", "--help"])

    assert result.exit_code == 0
    assert "--ground-truth-file" in result.output
    assert "--predictions-file" in result.output
    assert "--system-name" in result.output
    assert "private" in result.output.lower()


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
    result = runner.invoke(app, ["prepare-batch-predictions", "--help"])

    assert result.exit_code == 0
    assert "--ground-truth-file" in result.output
    assert "--batch-result-file" in result.output
    assert "--target-station" in result.output
    assert "results/private" in result.output

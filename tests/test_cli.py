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

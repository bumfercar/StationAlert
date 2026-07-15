# NextStop STT

NextStop STT is a reproducible Python demonstration for evaluating destination alerts built on
[RTZR Streaming STT](https://developers.rtzr.ai/docs/stt-streaming/). It replays subway audio in
real time, preserves partial and final transcription evidence, and keeps transcription separate
from the rules that decide whether an alert should fire.

> **Implementation status:** the repository currently contains the tested project foundation and
> CLI only. RTZR authentication, audio streaming, destination detection, and measured experiment
> results are not implemented yet. Planned behavior is labeled as such throughout this document.

## Why this project

Recognizing a station name and deciding that a passenger should be alerted are different problems.
The project is designed to measure both:

| Layer | Question | Planned evidence |
| --- | --- | --- |
| STT | Was the announcement transcribed correctly? | CER, station-name CER, exact-match rate |
| Decision | Did the correct alert fire? | TP/FP/FN/TN, precision, recall, F1 |
| Streaming | Did it fire soon enough? | final-result and end-to-end alert latency |

F1 is therefore defined over labeled **destination-decision opportunities**, not over arbitrary
text fragments. Transcription quality uses character-based metrics instead.

## Five-minute setup

Requirements:

- Python 3.11 (selected by `.python-version`)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- FFmpeg (planned audio normalization step)

```bash
git clone https://github.com/bumfercar/StationAlert.git
cd StationAlert
uv sync --extra dev
uv run nextstop --help
uv run pytest
uv run ruff check .
```

Until the streaming client is implemented, the available command is:

```bash
uv run nextstop version
```

Expected output:

```text
0.1.0
```

## Planned experiment

The comparison is deliberately small so each result answers a specific question.

| ID | RTZR model | Domain | Keyword | Decision layer | Research question |
| --- | --- | --- | --- | --- | --- |
| A | `sommers_ko` | `CALL` | none | exact final match | Baseline |
| B | `sommers_ko` | `MEETING` | none | exact final match | Does distant-mic tuning help? |
| C | `sommers_ko` | `MEETING` | moderate | exact final match | Does boosting recover station names? |
| D | `sommers_ko` | `MEETING` | moderate | context + route state | What false alerts can rules remove? |
| E | `whisper` | explicit Korean | unsupported | context + route state | How do RTZR models differ? |

No values will be added to a results table until they are produced by a saved, auditable run on
manually labeled audio segments. External Whisper is intentionally deferred: RTZR configuration
ablations provide more direct product evidence first.

## Planned runtime architecture

```text
private M4A or public sample
  -> FFmpeg: mono LINEAR16 PCM
  -> real-time-paced raw audio frames
  -> RTZR Streaming STT over WebSocket
  -> typed partial/final response records
  -> Korean text normalization
  -> announcement context classification
  -> Line 7 route-state validation
  -> alert decision with a machine-readable reason
```

M4A is a valid source format for the conversion step. The M4A container is **not** sent directly
to Streaming STT. FFmpeg will decode it to headerless signed 16-bit little-endian PCM, and the
client will send only raw audio frames followed by the documented `EOS` message.

## Security and data handling

Copy `.env.example` to `.env` or export the variables directly:

```bash
export RTZR_CLIENT_ID="..."
export RTZR_CLIENT_SECRET="..."
```

The repository ignores `.env`, `private_audio/`, private results, and the private report. Never
commit credentials, access tokens, full private recordings, identifiable passenger speech, or
absolute local paths. A public sample will be added only when its recording and redistribution
rights are clear.

## Documentation

- [Project and experiment specification](docs/PROJECT_SPEC.md)
- [Git workflow](docs/GIT_WORKFLOW.md)
- [AI-assisted engineering workflow](docs/ai-workflow.md)

## Current limitations

- No RTZR network path is implemented yet.
- No measured STT or alert result is available yet.
- The private Line 7 recording has not yet been segmented or manually labeled.
- Streaming behavior must be verified against real responses before its schema is considered
  stable.

## Sources

- [RTZR authentication](https://developers.rtzr.ai/docs/authentications/)
- [RTZR Streaming STT](https://developers.rtzr.ai/docs/stt-streaming/)
- [RTZR Streaming STT over WebSocket](https://developers.rtzr.ai/docs/stt-streaming/websocket/)
- [RTZR Batch STT](https://developers.rtzr.ai/docs/stt-file/)

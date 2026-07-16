# Project and Experiment Specification

## Product statement

NextStop STT extracts the current Seoul Subway Line 7 station from a realistic audio stream and
measures how RTZR model configuration and subway-specific rules affect recognition, false station
detections, and latency.

It is a proof of concept, not a safety-critical navigation system.

## User behavior

The user replays the full owned Gongneung-to-Children's Grand Park recording by default. A bounded
section is an explicit diagnostic option. For each final transcript, the system
may emit:

- `CURRENT_STATION`: canonical station with an explicit `역` or known secondary-name pattern;
- `STATION_CANDIDATE`: exact bare station token that needs more context;
- `CURRENT_STATION` with `contextual_phonetic_recovery`: opt-in recovery constrained by final
  announcement context, route vocabulary, phonetic distance, and runner-up margin;
- no station: fuzzy or inner-substring matches are rejected by the default policy.

Each extraction includes a stable machine-readable reason. Partial transcripts can be displayed
but do not establish the current station under the default policy. Destination alerts remain an
optional extension after station extraction is measured.

## Input contract

Two sources share one normalized frame interface:

1. mandatory file replay, paced according to audio duration;
2. optional microphone input after file replay is stable.

The initial streaming contract is mono, supported sample rate, signed 16-bit PCM, and headerless
raw LINEAR16 frames. Private M4A input is decoded locally and never committed.

## Research questions

1. How well does default RTZR `sommers_ko` recognize and separate station announcements?
2. How do `CALL` and `MEETING` differ on identical distant, noisy segments?
3. Does moderate `sommers_ko` keyword boosting improve station recall, and at what false-alert
   cost?
4. How much do secondary-name patterns, announcement context, and route order improve extraction
   precision?
5. How do RTZR `sommers_ko` and Streaming `whisper` differ in errors and latency?

## Controlled configuration sequence

| ID | Model | Domain | Keyword | Context | Route state |
| --- | --- | --- | --- | --- | --- |
| A | `sommers_ko` | `CALL` | none | off | off |
| B | `sommers_ko` | `MEETING` | none | off | off |
| C | `sommers_ko` | `MEETING` | moderate | off | off |
| D | `sommers_ko` | `MEETING` | moderate | on | off |
| E | `sommers_ko` | `MEETING` | moderate | on | on |
| F | `whisper` | documented setting | unsupported | on | on |

The same labeled segments must be used for all configurations. This sequence is an ablation, not a
combinatorial hyperparameter search.

## Evaluation units and metrics

### Transcription unit

One manually labeled announcement segment.

- normalized character error rate (CER);
- station-name CER;
- station exact-match rate.

### Decision unit

One labeled current-station extraction opportunity, including station-present and station-absent
hard negatives.

- TP, FP, FN, TN;
- precision, recall, F1;
- false-station count and missed-station count.

### Latency unit

One target-present announcement with aligned audio and monotonic client clocks.

- station-name audio end to final transcript receipt;
- final transcript receipt to decision;
- station-name audio end to alert.

The report must state sample counts and undefined-metric handling. A zero denominator is not
silently converted into a perfect score.

## Data policy

Ground truth is produced by listening to the source recording. The manifest records segment id,
relative time range, station, event type, exact reference, target presence, overlap, subjective
noise label, and public/private status.

Raw private recordings and recognizable passenger speech stay outside Git. Public samples require
clear ownership or redistribution permission. Failed and ambiguous segments remain in the dataset
with explicit labels.

## Architecture

```text
audio source
  -> decode / optional auditable preprocessing / normalize / pace
  -> RTZR WebSocket client
  -> typed response parser and raw JSONL evidence
  -> text normalization
  -> announcement classifier
  -> optional context-gated phonetic station recovery
  -> ordered route-state validator
  -> current-station extraction
  -> station + structured reason
```

RTZR-specific code stays separate from subway decision logic. Evaluation reuses production
components and can replay saved RTZR fixtures without network access.

## Delivery order

### P0: reproducible RTZR path

- safe authentication and token refresh;
- M4A-to-raw-LINEAR16 real-time file replay;
- WebSocket partial/final handling and structured evidence;
- final exact station-token extraction baseline;
- clean-environment README and tests.

### P1: domain evidence

- `CALL`/`MEETING` and keyword experiments;
- context and Line 7 route-state ablations;
- CER, event metrics, latency, and Markdown report;
- documented failures and limitations.

### P2: only after P0/P1

- microphone input and audible notification;
- additional reference STT comparison;
- richer visualization.

## Definition of done

- A fresh clone follows the README successfully.
- A real RTZR Streaming call is captured without exposing credentials.
- File replay sends raw audio at a documented pace.
- Partial and final responses are handled intentionally.
- Every station extraction or suppression has a reason.
- At least two meaningful RTZR configurations are measured on identical data.
- Default tests are offline and pass.
- Public history contains no private input, report, credential, or fabricated result.

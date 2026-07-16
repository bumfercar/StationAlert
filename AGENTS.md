# Engineering Rules

## Mission

Build a reproducible Python demonstration that uses RTZR Streaming STT to extract the current
Line 7 station from announcements and explains every extraction or suppression with auditable
evidence. Destination alerts are a secondary extension, not the primary result.

The priorities are, in order:

1. correct and intentional RTZR API use;
2. reproducibility from the README;
3. concise documentation that separates evidence from hypotheses;
4. privacy-safe handling of recordings and credentials.

## Human authority

The human developer owns product, data, and release decisions. An AI coding agent may inspect,
propose, implement, and test changes, but must not create commits, push, merge, rewrite history,
or publish artifacts without explicit human approval.

Before a branch or commit action, report the branch, exact files, purpose, checks, proposed commit
message, and unresolved risks.

## Sources of truth

Use sources in this order:

1. current official RTZR documentation;
2. actual redacted RTZR responses captured during execution;
3. human-created ground truth made by listening to the source audio;
4. repository tests and decision records;
5. AI suggestions.

Do not infer Streaming capabilities from Batch STT. If documentation and observed behavior differ,
preserve safe evidence, document the discrepancy, and do not guess.

## RTZR contract

- The primary runtime uses RTZR Streaming STT over WebSocket.
- Credentials come only from `RTZR_CLIENT_ID` and `RTZR_CLIENT_SECRET`.
- Never log secrets, access tokens, or unredacted authorization headers.
- Decoder parameters used in experiments must be explicit and saved with results.
- Send LINEAR16 as headerless raw audio frames, not a WAV container.
- Preserve partial responses for observability; use final responses for default alert decisions.
- Preserve `seq`, timing, `final`, alternatives, and returned word data in typed records.
- Treat confidence as a model output, not a calibrated probability.
- Use Streaming keyword boosting only with `sommers_ko` and valid Korean phonetic keywords.

## Architecture boundaries

Keep audio conversion, RTZR transport, transcript parsing, station extraction, evaluation, and
optional notification separate. Extraction code must run without network access, and evaluation
must call the production pipeline instead of reimplementing it.

A raw substring match must not establish the current station. Keep a measurable final-only exact
station-token baseline, accept explicit canonical/known-secondary-name/optional-`역` patterns, then add
announcement context and ordered Line 7 route state as separate ablations. Fuzzy matching is
opt-in only.

## Evaluation integrity

- Create ground truth by listening, never by copying an STT hypothesis.
- Define the evaluation unit before calculating a metric.
- Use CER for text and precision/recall/F1 for current-station extraction decisions.
- Keep failed samples and do not selectively rerun only poor cases.
- Save configuration, segment id, raw response, normalized transcript, decision, timing, code
  revision, and run timestamp.
- Label hypotheses, implemented behavior, and measured findings separately.
- Never fabricate or hand-edit benchmark results.

## Security and privacy

Do not commit `.env`, credentials, private recordings, identifiable passenger speech, private
reports, or private result artifacts. If a secret enters Git history, revoke it; deletion alone is
not sufficient.

## Verification

Default tests must not call a paid API. Real RTZR smoke tests are opt-in and visibly labeled.
Before proposing a commit, run the most relevant tests plus:

```bash
uv run pytest
uv run ruff check .
git diff --check
```

Document exact results and any check that could not run.

# Git Workflow

## Principle

History should expose the engineering sequence: one reviewable behavior, its tests, and its design
rationale. `main` stays runnable. No commit or push is created without human approval.

The repository began empty, so the first runnable project foundation is bootstrapped on `main`.
All subsequent capabilities use short-lived branches.

## Planned sequence

| Order | Branch | Outcome | Example commit |
| --- | --- | --- | --- |
| 0 | `main` | Runnable Python/CLI foundation | `chore(project): initialize typed Python CLI package` |
| 1 | `feat/rtzr-auth` | Safe token provider | `feat(auth): add expiry-aware RTZR token provider` |
| 2 | `feat/streaming-client` | Typed WebSocket responses and EOS | `feat(streaming): stream raw audio and parse transcripts` |
| 3 | `feat/audio-sources` | M4A conversion and paced replay | `feat(audio): add normalized real-time file replay` |
| 4 | `feat/destination-detection` | Exact final baseline | `feat(detection): add explainable destination baseline` |
| 5 | `feat/subway-context` | Context and route state | `feat(route): validate Line 7 destination events` |
| 6 | `feat/evaluation` | Metrics and reports | `feat(evaluation): add auditable experiment runner` |
| 7 | `exp/rtzr-comparison` | Actual controlled results | `exp(rtzr): compare streaming configurations` |
| 8 | `docs/reproducible-readme` | Final public narrative | `docs(readme): document measured behavior and limits` |

Branch names and scopes may change when evidence changes. Any change is recorded with its reason.

## Commit proposal checklist

Before each commit, show:

1. current and target branch;
2. exact paths to stage;
3. purpose and deliberately excluded work;
4. tests and exact results;
5. proposed Conventional Commit subject and body;
6. risks, assumptions, and unresolved questions.

After approval, stage only the listed paths. Do not mix generated private results or unrelated
formatting with functional changes.

## Pre-publication checks

```bash
git status --short
git log --oneline --decorate --graph --all
git diff main...HEAD
git grep -n -E 'RTZR_CLIENT_SECRET|Bearer [A-Za-z0-9._-]+'
git log -p --all -- .env private_audio results/private reports/private
```

Also inspect committed audio, absolute paths, personal data, placeholder claims, and whether README
commands work from a clean environment.

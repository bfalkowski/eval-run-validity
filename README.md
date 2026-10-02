# eval-run-validity

Code and data for the paper **[Is This Run Valid? Attributing Pipeline Failures Before Scoring LLM Evaluations](https://bfalkowski.github.io/writing/run-valid.html)**. Raw runs, traces and ground truth are on the `data` branch.

An eval score only means something if the run that produced it worked. This repo runs a small agent eval many times, injects known faults into the agents, the judge, the data, the scorer and the CI pipeline, and tests whether a validity gate can tell a broken run from a real model regression before any score is reported.

## What is here

| Folder | What it does |
|---|---|
| `agents/` | A support agent and a judge. Jev (TypeSafe) makes every decision. A local orders service provides the tools. A deterministic scorer checks the outcome. |
| `faultd/` | The fault API. Every component asks it, call by call, whether to fail. It logs what it injected to `ground_truth/`, which the gate never reads. |
| `validity/` | Run manifest (fingerprint), preflight checks, OpenTelemetry tracing, attribution rules and the gate. |
| `data/` | Two datasets of 60 support tickets each, with their orders fixtures and expected actions. `v1` (`data/make_data.py`) is straightforward; `v2` (`data/make_data_v2.py`, in `data/v2/`) makes each ticket hard in one deliberate way and is the one used for the paper. Pick with `--dataset` or `DATASET`; the default is `v2`. |
| `plans/` | The fault catalog: one JSON file per fault and dose. Built by `plans/make_plans.py`. |
| `baselines/` | One manifest per dataset (`manifest_v1.json`, `manifest_v2.json`): a clean CI run that other runs are compared against. |
| `.github/workflows/` | `eval` (the reference pipeline: preflight, run, validate, report), `ci` (tests), `secrets` (secret scan), `run` (experiment batches with faults), `collect` (gather runs onto the `data` branch). |

## The eval

For each ticket the agent looks up the order and the stock, asks Jev which action the support policy calls for (refund, reship, escalate or reply), takes it and replies. The scorer checks the side effects against the expected action. The judge asks Jev twice whether the resolution follows the policy.

## Setup

On macOS with Homebrew Python, pip will not install packages globally (PEP 668), so use a virtual environment in the repo. `.venv/` is gitignored.

```bash
cd eval-run-validity
python3 -m venv .venv
source .venv/bin/activate
pip install pre-commit -r requirements.lock
pre-commit install            # gitleaks runs before every commit
pre-commit run --all-files    # run it once now; expect "Detect hardcoded secrets ... Passed"
```

In a new terminal, run `source .venv/bin/activate` again before using `run.py`. The git hook does not need the environment to be active.

Put the Jev key in `.env` (gitignored):

```
TYPESAFE_API_KEY=...
```

## Running it

```bash

# no key needed: local stand-in for Jev, for testing the plumbing
JEV_BACKEND=stub POLICY_VERSION=v1 python run.py --run-id stub-clean

# real Jev (key in .env as TYPESAFE_API_KEY)
POLICY_VERSION=v1 python run.py --run-id clean-01
POLICY_VERSION=v1 python run.py --run-id wk-01 --fault-plan plans/wrong_key_first20.json --error-policy silent

# re-gate a run folder
python -m validity.gate runs/wk-01   # compares against baselines/manifest_<dataset>.json

# every plan once, with the stub
JEV_BACKEND=stub POLICY_VERSION=v1 JEV_BACKOFF_S=0 python sweep.py --prefix stub
```

`POLICY_VERSION` defaults to an old policy on purpose. A missing environment variable that silently falls back to a stale default is one of the pipeline faults the gate should catch.

Each run writes `runs/<run_id>/` with `manifest.json`, `preflight.json`, `traces.jsonl`, `items.jsonl` and `verdict.json`.

## The reference pipeline

`.github/workflows/eval.yml` is the pipeline the paper recommends, with the validity gate as stages between running the eval and publishing its score:

| Job | Stage | Stops the pipeline when |
|---|---|---|
| `preflight` | canaries, checksums, then the run fingerprint against the baseline | a canary or checksum fails, or a critical field changed without being declared. Nothing paid has run yet. |
| `run` | the eval itself, traced | (never; it always uploads what it has) |
| `validate` | integrity, coverage, gate | items are missing, repeated or from another run; an item has no successful model call; the gate says invalid |
| `report` | score on valid items, excluded items, comparison with `baselines/score_v2.json` | (only runs if `validate` passed) |

Each stage is `python -m validity.stages <stage> runs/<id>`, which writes a short Markdown section to the job summary and exits non-zero to stop the pipeline. Start it from the Actions tab and pick a `demo_fault` to watch where each fault is stopped.

## The verdict

- **valid**: report the score.
- **degraded**: report the score on valid items, with the share excluded beside it.
- **invalid**: report no score, only why.

Every failed item gets one cause: `infrastructure`, `config_drift`, `judge_failure`, `task_defect`, `harness_bug` or `model_failure`. The naive score (what a pipeline without these checks would report) is kept for comparison.

## Error policy

`--error-policy loud` raises on a failed Jev call. `--error-policy silent` falls back to a default (the agent replies and does nothing else; the judge votes fail). Silent fallbacks are the common real bug: failed calls look like ordinary wrong answers. The gate finds them from the traces, because the transport records the failure even when the agent code swallows it.

## Keeping the ground truth honest

The gate sees only what a real pipeline would see: the manifest, traces, item records and the committed dataset. Injected faults raise the same errors as real ones. `tests/test_pipeline.py` checks that nothing in `validity/` imports faultd or names its log, and that a verdict is identical with the ground-truth folder deleted.

## CI

Add `TYPESAFE_API_KEY` as a repository secret. Then start **run** from the Actions tab with a list of plans, a backend (`stub` or `api`) and an optional pipeline fault (runner killed, job timeout, partial upload, dependency drift, Python drift, missing env var, wrong commit). Then start **collect** with that run's ID to put the results on the `data` branch, optionally with a collect fault (a run merged twice, or two runs mixed).

## Secrets

The key lives only in `.env` (gitignored) locally and in the `TYPESAFE_API_KEY` repository secret in CI.

- `secrets` workflow: fails if a `.env` or key file is tracked, and runs gitleaks over the whole git history on every push.
- `run` workflow: before uploading, checks that the key does not appear in the run outputs and scans them with gitleaks.
- `tests/test_pipeline.py` runs a wrong-key run with a fake key and checks that it never appears in traces, manifests or item records.
- Local hook: `pre-commit install` (see Setup) runs gitleaks before every commit.

## Not affiliated

Not affiliated with TypeSafe. No employer data or code is used.

## License

MIT

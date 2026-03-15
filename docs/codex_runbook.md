# Codex runbook

## First session

Run:

```bash
make baseline
make validate
make eval
make probe
```

Then ask Codex:

```text
Read README.md, AGENTS.md, TASK.md, EVAL.md, docs/architecture.md, docs/problem_cases.md and PLANS.md.
Run baseline, validate, eval and probe.
Summarize current artifacts, key metrics, and the smallest safe Milestone 1 implementation plan.
```

## Second session

```text
Implement Milestone 1 only:
- keep legacy baseline untouched,
- improve shared models if needed,
- improve PageIR generation and run_report writing,
- keep all tests and reports passing.
Do not implement LLM adjudication yet.
```

## Third session

```text
Implement Milestone 2 only:
add automatic geometric reconnaissance for left/body/right bands and repeated page regions.
Write results into layout_profile.json and add probe metrics to run_report.json.
Do not change clause extraction yet.
```

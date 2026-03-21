# Agent Runbook

## Local Install

Preferred:

```bash
uv sync --extra dev
```

`.env` is optional for the accepted baseline. It is only needed for experimental model-assisted paths.

## Clean Run Sequence

```bash
rm -rf artifacts/runs/latest
make baseline
make validate
make eval
make probe
make unified
make unified-adjudicated
mkdir -p submission
cp artifacts/runs/latest/clauses.json submission/final_clauses.json
cp artifacts/runs/latest/eval_report.json submission/final_eval_report.json
cp artifacts/runs/latest/eval_report.md submission/final_eval_report.md
```

## Submission Artifacts

- Final output JSON: `submission/final_clauses.json`
- Submission-facing evaluation report: `submission/final_eval_report.md`
- Fresh baseline output in latest run dir: `artifacts/runs/latest/clauses.json`

## Docker

If Docker is available:

```bash
docker build -t charter-parser-core .
docker run --rm -v "$PWD:/workspace" -w /workspace charter-parser-core bash -lc "rm -rf artifacts/runs/latest submission && make baseline && make validate && make eval && make probe && make unified && make unified-adjudicated && mkdir -p submission && cp artifacts/runs/latest/clauses.json submission/final_clauses.json && cp artifacts/runs/latest/eval_report.json submission/final_eval_report.json && cp artifacts/runs/latest/eval_report.md submission/final_eval_report.md"
```

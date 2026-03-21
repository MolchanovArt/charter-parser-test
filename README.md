# Charter Parser Core

This project extracts ordered charter-party clauses from a source PDF and writes them as structured JSON. The accepted submission result is the current baseline output from `main`.

## Install

Recommended:

```bash
uv sync --extra dev
```

Alternative:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

The accepted baseline does not require `OPENAI_API_KEY`.

`.env` is optional and is only needed for experimental model-based paths. Baseline extraction, validation, evaluation, probing, and submission artifact generation run without it.

If the sample PDF is missing:

```bash
bash scripts/download_source_pdf.sh
```

## Main Command Sequence

Run the accepted baseline and supporting checks:

```bash
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

Docker alternative:

```bash
docker build -t charter-parser-core .
docker run --rm -v "$PWD:/workspace" -w /workspace charter-parser-core bash -lc "rm -rf artifacts/runs/latest submission && make baseline && make validate && make eval && make probe && make unified && make unified-adjudicated && mkdir -p submission && cp artifacts/runs/latest/clauses.json submission/final_clauses.json && cp artifacts/runs/latest/eval_report.json submission/final_eval_report.json && cp artifacts/runs/latest/eval_report.md submission/final_eval_report.md"
```

## What Gets Written

Accepted baseline output:

- `artifacts/runs/latest/clauses.json`
- `submission/final_clauses.json`

Baseline evaluation against the frozen reference:

- `artifacts/runs/latest/eval_report.json`
- `artifacts/runs/latest/eval_report.md`
- `submission/final_eval_report.json`
- `submission/final_eval_report.md`

Supporting probe and comparison artifacts:

- `artifacts/runs/latest/page_ir.jsonl`
- `artifacts/runs/latest/layout_profile.json`
- `artifacts/runs/latest/clauses_unified.json`
- `artifacts/runs/latest/clauses_unified_adjudicated.json`

`artifacts/golden/clauses_merged.json` is reference material only. It is not the submission output.

## Notes

- Public submission docs: `README.md`, `FINAL_REPORT.md`
- Agent runbook: `docs/AGENT_RUN.md`
- Technical notes: `docs/architecture.md`, `docs/problem_cases.md`
- Internal records: `docs/internal/`

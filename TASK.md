# Current task

Собрать первый общий каркас generalized parser без потери baseline-стабильности.

## Immediate goal

Milestone 1:
- сохранить runnable legacy baseline;
- ввести общий internal data model;
- добавить PyMuPDF-based document probe;
- добавить page IR output;
- добавить automatic geometric reconnaissance scaffold;
- выпускать run/eval reports на каждом прогоне.

## Inputs

Primary input:
- `data/raw/voyage-charter-example.pdf`

Existing context:
- `legacy/_part1_extractor.py`
- `legacy/_part2_extractor.py`
- `legacy/deep-research-report.md`
- `artifacts/golden/clauses_merged.json`

## Desired internal outputs

- `clauses.json`
- `page_ir.jsonl`
- `layout_profile.json`
- `run_report.json`
- `eval_report.json`
- `eval_report.md`

## Hard constraints

- Skip Part I particulars in final clause output.
- Do not invent missing text.
- Do not ask the model to rewrite clause text.
- Prefer adaptive layout inference over document-specific hardcoding.
- Keep the current baseline available while the new parser is being built.

## What “done” means for Milestone 1

- baseline still runs;
- page IR and layout profile are emitted from one unified code path;
- reports are reproducible;
- schemas exist for all core artifacts;
- the repo is ready for deterministic candidate generation in Milestone 2.

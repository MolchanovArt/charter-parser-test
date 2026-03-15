# Evaluation

`artifacts/golden/clauses_merged.json` is the current **seed reference set**.
Use it as a **regression anchor**, not unquestionable truth.

## Evaluation principles

1. Never compare only by total count.
2. Compare by ordered ids where possible.
3. Normalize whitespace before text comparison.
4. Track both exact matches and near matches.
5. Report empty or suspiciously short clauses explicitly.
6. Every normal run should produce JSON and Markdown reports.

## Required reports

Every `make eval` run must produce:
- `artifacts/runs/latest/eval_report.json`
- `artifacts/runs/latest/eval_report.md`

Every `make validate` run must produce or refresh:
- `artifacts/runs/latest/run_report.json`

## Stage-0 / baseline metrics

- `golden_count`
- `candidate_count`
- `count_delta`
- `missing_ids`
- `extra_ids`
- `duplicate_ids`
- `id_sequence_match_ratio`
- `title_exact_match_ratio`
- `text_exact_match_ratio`
- `text_similarity_mean`
- `empty_text_ids`
- `order_violations`
- `banner_leaks`

## Stage-1 probing metrics

- `page_count`
- `page_ir_pages_written`
- `avg_lines_per_page`
- `layout_profile_pages_scored`
- `low_confidence_pages`

## Later-stage metrics to add

- `boundary_f1`
- `title_line_accuracy`
- `body_line_accuracy`
- `struck_leak_rate`
- `noise_leak_rate`
- `split_merge_error_rate`
- `vision_invocation_rate`
- `vision_recovery_rate`

## Blocking conditions

A parser change is not acceptable when it introduces:
- new order violations;
- new duplicate ids;
- schema-invalid output;
- obvious banner leakage;
- higher struck leakage once struck checks are wired.

## Commands

```bash
make baseline
make validate
make eval
make probe
```

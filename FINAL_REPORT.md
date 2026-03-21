# Final Report

## Submission Summary

This submission keeps the accepted `main` baseline unchanged and packages the repository for final review. No parser logic was modified as part of the submission cleanup. The submission result is the baseline clause JSON, surfaced at `submission/final_clauses.json`.

## Accepted Baseline Metrics

Fresh run from `main`:

- `golden_count`: 91
- `candidate_count`: 91
- `count_delta`: 0
- `missing_ids`: `[]`
- `extra_ids`: `[]`
- `duplicate_ids`: `[]`
- `id_sequence_match_ratio`: `1.0`
- `title_exact_match_ratio`: `1.0`
- `text_exact_match_ratio`: `0.5824`
- `text_similarity_mean`: `0.9978`
- `order_violations`: `[]`
- `banner_leaks`: `[]`

Baseline validation also completed cleanly with no schema errors, no duplicate ids, and no order violations.

The accepted baseline does not require `OPENAI_API_KEY`. `.env` is optional and only relevant for experimental model-assisted paths.

## Remaining Known Issues

The accepted baseline is stable and is the submission output. The main remaining limitations are:

- text is not an exact string-for-string match to the frozen reference in every clause, reflected by `text_exact_match_ratio = 0.5824`
- some accepted baseline clauses remain intentionally empty after extraction and cleanup: `part2:21`, `part2:27`, `part2:38`, `essar:16`, `essar:18`
- the unified/adjudicated parser path on `main` is still incomplete and therefore was not promoted to submission output

For reference, the unified/adjudicated path still trails the frozen reference:

- `unified_clause_count`: 84 vs `reference_clause_count`: 91
- missing clause ids: `part2:3`, `part2:4`, `part2:6`, `part2:21`, `part2:27`, `part2:28`, `part2:38`
- no duplicate ids and no extra ids
- `body_text_overlap_proxy_vs_reference`: `0.9653`
- `normalized_title_similarity_vs_reference`: `0.838`
- `split_merge_error_proxy`: `7`

## Why The Final Baseline Was Chosen

The accepted baseline was retained because it is the only path on `main` that currently reruns cleanly with exact count parity against the frozen reference, no missing ids, no extra ids, no duplicate ids, and no order violations.

The unified and strike-oriented experimental paths remain valuable for future work, but they are not yet a safer submission target than the accepted baseline.

## Alternate Implementation Not Merged

A separate earlier deterministic implementation exists in another repository. It includes stronger operational packaging, including Docker/API flow and optional token-based setup.

It was intentionally not merged into this submission repository at the final stage in order to keep the accepted baseline stable, reproducible, and easy to review.

## Docker

A minimal Docker path was added for the accepted baseline only. It installs dependencies and supports the same clean run sequence used for local verification, without adding API server, auth, or deployment extras.

`OPENAI_API_KEY` is not required for this Docker baseline flow either.

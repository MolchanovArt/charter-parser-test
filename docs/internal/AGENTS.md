# Repository instructions

## Read first

Before changing code, read in this order:
1. `README.md`
2. `TASK.md`
3. `EVAL.md`
4. `docs/architecture.md`
5. `docs/problem_cases.md`
6. `PLANS.md`

## Non-negotiable rules

- Deterministic PDF geometry is the source of truth.
- Never use an LLM to rewrite clause text from scratch.
- Ask the model for `line_ids`, `block_ids`, boundaries, labels, and confidence.
- Preserve document order.
- Remove strike-through text.
- Treat `artifacts/golden/clauses_merged.json` as a regression anchor, not absolute truth.
- Keep changes small and reversible.
- Do not change golden data in the same task as parser changes.

## Parsing policy

- Start with automatic geometric reconnaissance.
- Infer layout per document/page instead of hardcoding x/y cutoffs where possible.
- Support mixed layouts: left title + center body + right noise on the same row.
- Prefer page-local or local-window adjudication over whole-document freeform prompting.

## LLM / VLM policy

- Use one main family first: `gpt-5.4`.
- `gpt-5-mini` is optional and only for later cost-down work.
- LLM is allowed only for local ambiguity resolution and repair loops.
- VLM is fallback-only for low-confidence pages or image-like pages.
- All model outputs used by the pipeline must be schema-constrained.

## Validation

After any logic change, run:

```bash
make test
make baseline
make validate
make eval
```

If the change touches probing / layout / line grouping, also run:

```bash
make probe
```

## Implementation style

- Favor a single unified parser under `src/charter_parser/`.
- Keep legacy scripts frozen under `legacy/` unless the task is explicitly about baseline recovery.
- Put thresholds in config, not inline constants, unless they are true invariants.
- Keep this file short. If a mistake repeats, update it.

## ExecPlans

For any non-trivial refactor, architecture change, or task likely to take more than ~30 minutes, update `PLANS.md` first and work milestone by milestone.

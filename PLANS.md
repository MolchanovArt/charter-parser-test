# ExecPlans for this repository

Use this file for multi-step changes touching architecture, layout logic, strike handling, LLM integration, or eval policy.

## Active plan — generalized geometry-first parser

### Goal

Replace the current two-script solution with one pipeline that preserves or improves output quality while remaining auditable and generalized to similar layouts.

### Constraints

- legacy baseline must remain runnable;
- seed reference set stays frozen during parser changes;
- clause text must come from extracted spans/lines, not model generation;
- one main model family first: `gpt-5.4`.

### Milestones

#### M0 — freeze baseline
- keep `make baseline`, `make validate`, `make eval` green.

#### M1 — probe + IR shell
- shared models;
- PyMuPDF backend;
- document probe;
- page IR output;
- run report.

##### Current M1 implementation slice
- declare frozen legacy runtime deps so `make baseline` is reproducible in a clean env;
- add artifact provenance and fail-closed freshness checks for baseline consumers;
- validate `page_ir.jsonl` and `layout_profile.json` against schemas during probe;
- replace constant-confidence layout guessing with adaptive page-local reconnaissance;
- separate latest-by-mode reports from per-run archived reports.

#### M2 — automatic geometric reconnaissance
- infer left/title band;
- infer body band;
- infer right-noise band;
- infer repeated header/footer bands;
- infer page types and confidence.

#### M3 — deterministic candidate generation
- clause-start candidates;
- continuation candidates;
- title candidates;
- noise candidates;
- strike filtering hooks.

##### Current experiment — `m2_unified_candidate_generation_v1`
- consume `page_ir.jsonl` + `layout_profile.json`;
- emit deterministic `candidate_blocks.jsonl` with traceable line ids and routing decisions;
- assemble a unified draft clause output from source lines only, with no LLM/VLM use;
- emit candidate-level and assembly-level reports with explicit proxy metrics and concrete failures;
- compare unified output against both legacy baseline and frozen reference without changing legacy behavior.

#### M4 — assembly + validators
- assemble clauses deterministically;
- add leakage and split/merge checks;
- emit richer eval reports.

#### M5 — structured line/block adjudication
- use `gpt-5.4` only for ambiguous local windows;
- return `line_ids`, labels, attach decisions, confidence;
- keep deterministic final text assembly.

##### Current experiment — `m3_structured_ambiguity_adjudication_v1`
- keep the M2 unified deterministic path as the default source of candidate text and ordering;
- extract only ambiguous local windows for two buckets: nested numbering starts and false banner / section switches;
- call `gpt-5.4` with schema-constrained outputs limited to `candidate_start`, `attach_to_previous`, `section_hint`, optional `title_line_ids`, optional `body_line_ids`, `confidence`, and `reason_short`;
- apply adjudication only when the deterministic signal is ambiguous enough to justify intervention;
- emit adjudication request/response artifacts plus reports that compare deterministic-only vs adjudicated unified output;
- do not add VLM fallback, clause-text generation, multi-model orchestration, or cost-optimization work in this slice.

#### M6 — selective visual fallback
- only on flagged pages or cropped regions;
- compare recovery vs cost;
- keep invocation rate low.

#### M7 — active loop
- collect low-confidence cases;
- expand reference/eval set from failures;
- block merges on regressions.

### Validation after each milestone

```bash
make test
make baseline
make validate
make eval
```

If the milestone touches probing / layout:

```bash
make probe
```

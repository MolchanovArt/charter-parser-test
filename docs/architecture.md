# Target architecture

## Design goal

One parser, one pipeline, one output contract.

## Principle

The parser is **geometry-first** and **model-assisted only where needed**.

## Why geometric reconnaissance comes first

This document family can contain rows like:
- left margin title or clause label,
- body text beginning on the same row,
- right-side internal numbers or line numbers,
- repeated header/footer bands,
- mixed title styles across sections.

A generalized solution therefore cannot start from hardcoded x-thresholds.
It must first infer a runtime layout profile.

## Target stages

1. **Document probe**
   - page count;
   - page sizes / rotations;
   - whether extractable text exists;
   - suspicious or image-like pages.

2. **Automatic geometric reconnaissance**
   - repeated header/footer bands;
   - left/title band;
   - body band;
   - right-noise band;
   - page type hints;
   - confidence per page.

3. **Page IR extraction**
   - words;
   - lines;
   - bboxes;
   - raw geometry refs;
   - optional vector lines/rectangles for strike analysis.

4. **Strike filter**
   - remove struck spans / words via geometry;
   - avoid regex-only cleanup as the main mechanism.

5. **Candidate generation**
   - new clause;
   - continue previous;
   - title lines;
   - body lines;
   - noise / banner / header / footer.

6. **Structured adjudication**
   - only for ambiguous local windows;
   - ask for `line_ids`, labels, attach decisions, confidence.

7. **Deterministic assembly**
   - final clause text is built from source lines/spans only.

8. **Validation + repair**
   - schema checks;
   - order checks;
   - leakage checks;
   - selective retry;
   - visual fallback last.

## Model policy

### Allowed uses
- local boundary adjudication;
- title/body separation;
- attach / split / merge decisions;
- repair of a small set of flagged pages.

### Disallowed uses
- reading the whole PDF and rewriting every clause;
- generating final clause text from scratch;
- vision as the default path.

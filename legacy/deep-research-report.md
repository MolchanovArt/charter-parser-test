# Deep technical research for clause extraction from a voyage charter party PDF

## Problem framing and decomposition

**Problem reframing (requested structure item: “Problem reframing”).**  
Build a local Python pipeline that ingests a single PDF voyage charter party, skips Part I, and extracts *Part II legal clauses* into ordered JSON objects `{id, title, text}`, while **excluding any strike-through text**. The key engineering requirement is *reliable document-structure reconstruction* (where clauses start/end; which title belongs to which clause) from a PDF whose visual formatting matters more than the raw text stream. citeturn0search0turn3view2turn4view1

**Task decomposition (requested structure item: “Task decomposition”).**  
A robust solution naturally breaks into these subproblems:

- **PDF modality check (native text vs scanned/OCR-needed):** decide whether to rely on embedded text objects or render+OCR fallback. Tools like pdfplumber explicitly work best on “machine-generated rather than scanned PDFs,” which is relevant for strategy selection. citeturn5search12turn2view0  
- **Layout & reading-order reconstruction:** recover “what the reader sees” (single column text + margin headings + right-margin line numbers). Advanced parsers emphasize reading order as a first-class signal. citeturn6view5turn13view4turn11view0  
- **Section boundary detection:** reliably start at Part II and ignore Part I, ideally by searching for the “PART II” header rather than hard-coding page numbers. (Part II is clearly labeled in the PDF.) citeturn3view2turn0search0  
- **Strike-through detection and exclusion:** identify struck text even when it is *not an annotation* but drawn as line art/rectangles (common for Word→PDF). This is the most unique requirement and the biggest risk. citeturn3view2turn6view2turn9search6  
- **Clause boundary detection:** detect clause starts/ends across pages, including long clauses spanning multiple pages. citeturn3view3turn3view4turn4view3  
- **Title extraction:** capture clause headings. In the standard Part II, headings are in a left margin column, not inline with the clause body. citeturn3view2turn3view4  
- **Structured JSON generation:** emit a validated schema (Pydantic) and serialized JSON in document order. If an LLM is used, enforce schema adherence with constrained outputs. citeturn6view3turn5search10  
- **Validation / post-processing:** check ordering, missing clauses, duplicated ids (there is a numbering restart later), and empty/near-empty clause bodies after strike-through removal. citeturn4view0turn4view1turn4view4  
- **Evaluation strategy:** define measurable checks (coverage, ordering, content sanity, regression snapshots).  

## What makes this PDF difficult

The Part II pages combine *legal formatting* plus *edit-history artifacts* that break naive parsing.

**Document structure is visually multi-region, not plain text.**  
On Part II’s first page, the PDF clearly shows a **“PART II”** header and clause numbering where the **clause title sits in a left margin column** (e.g., “Condition Of vessel”, “Cleanliness Of tanks”), while the clause body starts to the right with “1.”, “2.” etc. citeturn3view2

**Strike-through is pervasive and semantically critical.**  
Clause 2 illustrates a typical negotiation artifact: an earlier version of the clause is fully struck through, and then the clause appears again with replacement text. A parser that simply extracts text will likely include both versions unless it can detect strikethrough as a visual style. citeturn3view2turn6view2

**Long clauses span pages and include nested numbering.**  
Many clauses contain internal subparagraphs (a), (b), (i), (ii), etc., and they may continue across pages. Clause-boundary logic must distinguish “Clause 12.” from “(ii)” or “26(2)” references inside clause text. citeturn3view3turn3view4turn2view2

**Right-margin line numbers and headers/footers are noise.**  
The PDF contains right-margin numbers (line numbers) and repeating headers (“Issued July 1987”, “SHELLVOY 5”) that are not part of clause text and should be filtered. citeturn3view2turn3view3

**The clause numbering resets in a rider section.**  
Later pages introduce “Essar Rider Clauses (1st Dec 2006)” with a fresh numbering starting again at **1**, and continuing through at least **22** (“STS TRANSFER CLAUSE”, etc.). This implies duplicate `id` values across the full Part II extraction if you preserve the literal clause numbers—as the task statement suggests you should. citeturn4view1turn4view4turn4view0

**Strike-through is not guaranteed to be an annotation.**  
In PDFs, strike-through may appear as an annotation *or* as vector graphics (rectangles/lines) or even embedded in glyph shapes; you often must handle multiple mechanisms. The most practical and common Word→PDF case is a thin rectangle/line drawn over text (line art), which you can detect via vector extraction. citeturn6view2turn5search13turn9search6

## Research landscape

This task sits at the intersection of “document ingestion” and “structured extraction.” The ecosystem has converged on a few common paradigms.

**Document conversion libraries with layout awareness.**  
Docling positions itself as advanced PDF understanding with reading order, layout, OCR support, and “lossless JSON” export via a unified document model (DoclingDocument). It also advertises local execution (useful for reproducibility). citeturn13view4turn12search6turn12search0  
Marker focuses on fast conversion to Markdown/JSON and exposes a block-based document structure; it has an optional “hybrid mode” that can use an LLM to improve accuracy and includes a Python API that returns structured Pydantic models. It is heavier (PyTorch, models) but can be strong for layout-heavy PDFs. citeturn13view2turn14view3  
Unstructured frames itself as open-source ETL for turning documents into canonical JSON-like “elements” with metadata and supports different partitioning strategies (e.g., `hi_res`) using layout detection models. It also explicitly distinguishes the open-source library as a prototyping baseline with “less sophisticated document hierarchy detection” than its paid tiers, which matters if you rely on it for headings/sections. citeturn11view3turn12search19turn12search27

**Low-level PDF plumbing for precise control (often SOTA for bespoke rules).**  
For strike-through exclusion and title-column extraction, low-level libraries with coordinates and vector access often outperform “one-click parsers,” because the requirement is specific and visual. PyMuPDF (built on MuPDF from entity["company","Artifex","mupdf developer"]) provides both text extraction and vector graphics extraction (via `Page.get_drawings()`), which enables programmatic detection of thin rectangles/lines used for strike-through. citeturn5search9turn5search13turn9search6turn6view2

**LLM structured extraction toolchains.**  
If you use entity["company","OpenAI","ai company"] models, “Structured Outputs” (JSON Schema-constrained decoding) can make the final JSON generation deterministic in shape—useful for clause objects and for repair loops. citeturn6view3turn5search10  
If you want a fully local option, Ollama documents a comparable “structured outputs” capability that enforces a JSON schema for model responses. citeturn12search37

**Legal-specific NLP libraries exist but are not the bottleneck here.**  
LexNLP by entity["company","LexPredict","legal analytics company"] provides legal-text segmentation and title/heading identification, but it assumes you already have a reasonably clean text stream—whereas this assignment’s hardest part is getting the *correct* text stream (and filtering strikethrough) from PDF layout. citeturn11view5turn10search4  
Clause-type datasets like CUAD (from entity["organization","The Atticus Project","legal dataset nonprofit"]) are SOTA for *semantic clause classification/extraction by type*, not for clause boundary reconstruction in a single bespoke PDF with strike-through edits. citeturn10search1turn10search2

## Candidate approaches compared

Below is an opinionated comparison aligned to your exact constraints: one PDF, correctness over novelty, local run, and handling strike-through.

**A. Pure deterministic / regex / rule-based pipeline (low-level PDF → rules only).**  
Works well when the PDF is machine-text, the clause numbering is regular, and you can isolate layout regions (title column vs body column vs line numbers) via coordinates. It is the most explainable in a code review and easiest to make reproducible. It fails if strike-through is not detectable from PDF primitives (e.g., baked into bitmap) or if reading order is badly scrambled. citeturn5search12turn6view2turn3view2

**B. PDF parser + rule-based segmentation + LLM cleanup (recommended hybrid for “strong submission”).**  
Use low-level extraction to get *correct* content (especially “remove strike-through”), then apply an LLM only for normalization/format repair (whitespace, hyphenation, paragraph joins) under a strict schema. This reduces LLM surface area (and risk) while still demonstrating “LLM capabilities.” With schema constraints, you reduce JSON brittleness. citeturn6view3turn5search10turn6view2

**C. PDF parser + LLM clause extraction (LLM does the segmentation).**  
You can dump per-page text/markdown and ask an LLM to output clauses. This can work quickly for clean legal PDFs, but in this PDF the LLM will not know which spans were struck unless the parser marks them explicitly (or you provide images). It can also hallucinate clause titles (especially for the margin-title style). citeturn3view2turn6view2

**D. Multimodal/VLM page-level extraction (render pages → vision model reads what’s visible).**  
This can be the most robust way to obey “do not include strike-through,” because a vision model can literally see what is crossed out. But it’s costlier, slower, harder to run deterministically, and less code-review-friendly for a short assignment. It also complicates long-clause merging across pages. (Docling’s VLM-oriented extraction modes show how vendors think about this class of problem.) citeturn13view3turn13view4

**E. Hybrid pipelines with validation and repair loops (best production pattern).**  
A robust architecture runs an initial parse, validates constraints (ordering, missing ids, empty clause bodies), and selectively re-parses or re-asks an LLM for only the failing areas. This yields high reliability and clean code separation, but it’s slightly more work to implement. Structured outputs are a natural fit for “repair only what failed.” citeturn6view3turn5search10

## Repository and tool review

This section prioritizes repositories you can realistically start from and that map to the task’s pain points.

**Docling (open source).**  
What it solves: layout-aware parsing across formats; exports to Markdown/HTML and “lossless JSON”; emphasizes reading order and local execution. It also provides a beta structured information extraction API that accepts templates defined as strings/dicts/Pydantic models and returns standardized output organized by page. This is attractive if you want a “parser layer” and then implement your own clause segmentation on top. citeturn13view4turn12search6turn13view3  
Strengths: strong layout model + reading order; explicit JSON representation; active ecosystem integrations. citeturn13view4turn12search6  
Limitations for this assignment: (1) you still must solve clause segmentation yourself, (2) strike-through exclusion is not guaranteed unless Docling encodes that style in output, and (3) the extraction API is explicitly “beta.” citeturn13view3  
Suitability: **great as a dependency for parsing**, but you still need bespoke logic for strike-through + clause/title mapping.

**Marker (open source).**  
What it solves: conversion to Markdown/JSON with a block hierarchy; claims header/footer removal; exposes a Python API that yields Pydantic models and supports optional “use_llm” mode. It advertises structured extraction given a JSON schema (beta). citeturn13view2turn14view3  
Strengths: strong “document conversion” focus; flexible pipeline knobs; JSON/block output gives you structure to work with. citeturn13view2turn14view3  
Limitations: heavier dependencies (PyTorch, OCR/model stack); licensing constraints (GPL code + specific model license terms) which may matter outside a coding test. citeturn14view0turn13view2  
Suitability: **strong parsing engine**, but for “fastest clean submission” it may be overkill unless you already have it in your toolbox.

**Unstructured (open source).**  
What it solves: partitions PDFs into typed elements with metadata; supports layout-based `hi_res` strategy and exposes a clear partitioning API surface in code. citeturn11view3turn12search27turn12search19  
Strengths: good ETL abstraction; convenient element metadata; flexible strategies. citeturn11view3turn12search19  
Limitations: open-source docs explicitly position it as a “starting point for quick prototyping” with “less sophisticated document hierarchy detection” than paid offerings; if you rely on it to identify headings/titles, you may still need custom logic. citeturn11view3  
Suitability: good **inspiration** and a usable dependency, but you still need custom strike-through and title mapping.

**PyMuPDF + MuPDF primitives (low-level control).**  
What it solves: direct access to text geometry (words/spans) and vector drawings (`get_drawings`) to detect “line art” that can represent strike-through. MuPDF release notes explicitly mention style collection to detect “underlines, strike-through, etc.” and Stack Overflow provides a concrete method for strike-through detection using drawing rectangles/lines intersecting word boxes. citeturn9search6turn6view2turn5search13  
Strengths: precise; minimal dependencies; highly reproducible; easy to unit test; best fit to “exclude strike-through text” as a first-class constraint. citeturn6view2turn5search9turn5search13  
Limitations: you must implement reading-order reconstruction and clause/title mapping yourself (but that is arguably the point of this assignment).  
Suitability: **best direct dependency** for the core requirement.

**OpenContracts (platform).**  
What it solves: a self-hosted platform for document annotation + knowledge base construction, with an emphasis on structured extraction and format preservation; it references precise text-to-coordinate mapping (via PAWLS) as part of its “format preservation” story. citeturn11view4  
Strengths: useful reference design for “human-in-the-loop” contract analytics systems. citeturn11view4  
Limitations: far too heavy for a single-PDF coding assignment; not the fastest path to implement clause extraction locally.  
Suitability: **inspiration**, not a dependency for this test.

## Recommended architecture for this assignment

This is the most practical architecture if you optimize for “strongest submission within limited time,” while still using LLMs appropriately.

### Pipeline stages

**Stage 1: Locate Part II page range (robust, not hard-coded).**  
Scan pages for the literal header “PART II” and start from the first match; the document clearly contains a “PART II” title page where clauses start. citeturn3view2turn0search0  
Stop at end-of-document (page count is 39). citeturn2view0turn0search0

**Stage 2: Extract word-level tokens with coordinates (PyMuPDF).**  
Use `page.get_text("words")` (word tokens with bounding boxes) plus `page.get_drawings()` (vector paths) to enable strike-through detection from line art. citeturn5search13turn6view2turn9search11

**Stage 3: Detect strike-through shapes and drop struck words deterministically.**  
Implement a geometric filter:

- Build candidate “strike lines” as thin rectangles from:
  - true lines (`"l"` draw ops) that are horizontal
  - thin rectangles (`"re"`) whose width ≫ height (Word often uses rectangles for lines) citeturn6view2
- For each word bounding box, drop it if:
  - it overlaps a strike-line rectangle in X, and
  - the strike-line Y lies near the **vertical middle** of the word box (to avoid deleting underlined headings in the rider section).  
This extra “middle-band” test is critical because rider clause titles are underlined rather than struck, and you must not delete them. citeturn4view1turn4view2turn6view2

**Stage 4: Remove layout noise without LLMs.**  
Filter:
- right-margin line numbers (digits-only tokens near page right edge) citeturn3view2turn3view3  
- repeating headers/footers (tokens in top/bottom bands, plus known repeated strings like “Issued July 1987”, “SHELLVOY 5”) citeturn3view2turn3view3

**Stage 5: Reconstruct readable lines and a global text stream.**  
Group words into lines by Y proximity, then sort within a line by X. This preserves titles in the left margin and body text in the main column.

**Stage 6: Clause boundary detection (rules first).**  
Detect clause starts using a conservative regex on reconstructed lines:

- Standard shell form clauses: line begins with `^\s*\d+\.\s` (e.g., “1. Owners shall…”) citeturn3view2  
- Rider clauses: same, but titles are inline (e.g., “1. INTERNATIONAL REGULATIONS CLAUSE”). citeturn4view1turn4view4  

Split the global stream into clause segments from start marker to next start marker, across pages.

**Stage 7: Title extraction (hybrid deterministic strategy).**  
Because this PDF has two title styles, implement two title strategies:

- **Shell form pages:** title comes from the left margin column aligned with the clause’s first line (e.g., “Condition Of vessel”). citeturn3view2turn3view4  
- **Rider pages:** title is inline on the clause start line, after the number (often underlined / uppercase). citeturn4view1turn4view4  

A practical deterministic method is:  
(1) compute column clusters from word X positions (left-title column vs main-body column vs right-line-number column), then (2) for each clause start line, if a left-column text exists in the same Y band, use it as the title; else parse inline title until end-of-line.

**Stage 8: Optional LLM “cleanup + normalization” under schema constraints (small surface area).**  
Use an LLM only for:
- whitespace normalization
- hyphenation repair
- ensuring the clause `text` is a clean string without page artifacts  

Constrain it with JSON Schema “Structured Outputs” so the return always matches `{id, title, text}`. citeturn6view3turn5search10  
If you want local/offline, swap to an Ollama model with schema-constrained outputs. citeturn12search37

### Fallback strategy if parsing is messy

If word-level extraction + draw-line strike detection becomes unexpectedly brittle:

1. Parse the PDF with Docling into Markdown or lossless JSON for reading order normalization. citeturn13view4turn12search6  
2. Still run strike-through detection using PyMuPDF geometry on the original PDF, and remove struck spans from the Docling text by coordinate-to-text alignment (more work, but robust). The fact that OpenContracts and similar systems emphasize coordinate mapping underscores why “text ↔ coords” is a power tool in document work. citeturn11view4

## Suggested Python project structure

A clean, code-review-friendly layout:

```text
charter_clause_extractor/
  README.md
  pyproject.toml
  src/
    charter_extract/
      __init__.py
      cli.py                    # entrypoint: download/process/export JSON
      pdf_io.py                 # load PDF, iterate pages
      tokens.py                 # word token + geometry data structures
      strikeout.py              # strike-through detection and filtering
      layout.py                 # line reconstruction + column clustering
      segment.py                # Part II detection + clause segmentation
      titles.py                 # title extraction logic (margin vs inline)
      schema.py                 # Pydantic models for Clause + Document
      normalize.py              # deterministic cleanup; optional LLM wrappers
      validate.py               # completeness + sanity checks
  tests/
    test_strikeout.py
    test_segmentation.py
    test_titles.py
    fixtures/
      voyage-charter-example.pdf  # or downloaded in CI if allowed
  outputs/
    clauses.json
```

This separation makes it obvious you understand the pipeline: I/O → geometry → filtering → segmentation → validation.

## Suggested schemas and data contracts

Use Pydantic for strict typing and easy JSON export. If you later use an LLM, the same schema becomes your JSON Schema target for constrained decoding. citeturn6view3turn12search30

```python
from pydantic import BaseModel, Field

class Clause(BaseModel):
    id: str = Field(..., description="Clause identifier exactly as shown in the document (may repeat across rider sections).")
    title: str = Field(..., description="Clause heading/title as shown in the document.")
    text: str = Field(..., description="Full clause body text, excluding any strike-through text.")

class ClauseExtractionResult(BaseModel):
    source_pdf: str
    part_ii_page_start: int
    part_ii_page_end: int
    clauses: list[Clause]
```

Note on duplicate ids: the rider section restarts numbering at 1, so `Clause.id` should be treated as a display identifier, not a primary key. citeturn4view1turn4view4

## Example code patterns and references

### Strike-through detection pattern (the hardest part)

The most practical reference implementation approach is:

- extract vector paths (`get_drawings`)
- treat horizontal lines and thin rectangles as candidate strike lines
- intersect with word bounding boxes

This “line art intersection” approach is described with concrete PyMuPDF code in a Stack Overflow answer and is explicitly motivated by Word’s habit of using rectangles for strike lines. citeturn6view2turn5search13

Key enhancement for this assignment: **don’t confuse underlines with strike-through.**  
Because rider titles are underlined, only drop a word if the intersecting line crosses the vertical middle of the word (not the baseline area). citeturn4view1turn4view2turn6view2

### Parsing Part II boundaries

Part II is explicitly labeled “PART II” at the start of the clause section; searching for that string is more robust than hard-coding “page 6.” citeturn3view2turn0search0

### Constrained JSON if you use an LLM

Use schema-constrained outputs instead of “just output JSON.” OpenAI documents Structured Outputs as a way to guarantee adherence to your supplied JSON Schema. citeturn6view3turn5search10

## Evaluation plan

A credible evaluation plan for a single-document extraction is mostly “invariants + spot checks”:

**Document coverage invariants.**
- Start page should be the first page containing “PART II”. citeturn3view2  
- End page should be the last PDF page (39 pages total). citeturn2view0turn0search0

**Clause completeness checks.**
- Detect that the standard Part II includes at least up to clause “43. Address Commission Clause” (so you know you didn’t truncate early). citeturn4view0  
- Detect that “Essar Rider Clauses” exists and includes clauses restarting at 1 and continuing through at least 22. citeturn4view1turn4view4  

**Strike-through exclusion checks.**
- On pages with obvious strike-through blocks (e.g., Clause 2’s crossed out paragraph), verify those strings are absent from output while the non-struck replacement remains. citeturn3view2turn3view4  
- On rider pages with underlined headings, verify the title strings are present (avoid false strike detection). citeturn4view1turn4view4  

**Structural sanity checks.**
- Clauses are emitted in document order (stable list order).  
- No clause `text` is only a handful of characters unless the clause is fully struck (flag for review). (Clause 6 “BROKERAGE” appears struck, which can legitimately result in near-empty body if you strictly remove struck text.) citeturn4view2

## Ranked recommendations

### Top recommended approaches (for this assignment)

**Best overall (submission-optimized):**  
**Hybrid B — PyMuPDF geometry extraction + deterministic segmentation + optional LLM cleanup with schema constraints.**  
Reason: this is the only approach that squarely addresses the assignment’s hardest requirement (exclude strike-through) with deterministic, testable logic, while still leaving room to use an LLM in a controlled way. citeturn6view2turn6view3turn3view2

**Second best (parser-first):**  
**Docling → your segmentation logic (plus PyMuPDF strike filtering if needed).**  
Reason: Docling is strong on reading order and offers lossless JSON + local execution, which can speed you up if PyMuPDF line reconstruction becomes annoying. citeturn13view4turn12search6turn13view3

**Third best (heavy but powerful):**  
**Marker → JSON blocks → your segmentation logic.**  
Reason: Marker exposes a structured block tree in JSON/Pydantic and supports optional LLM mode; but it’s heavier and licensing can be more complicated. citeturn13view2turn14view3turn14view0

### Top repos/tools to study first

1) PyMuPDF vector + text extraction recipes and `get_drawings()` documentation (core to strike-through detection). citeturn5search13turn5search9turn6view2  
2) Docling repository and docs (layout + reading order + lossless JSON). citeturn13view4turn12search6  
3) Marker repository (JSON output + Python API returning Pydantic models). citeturn14view3turn13view2  
4) Unstructured partitioning API and `partition_pdf` implementation notes (alternative ETL approach). citeturn12search27turn12search19turn11view3  
5) OpenAI Structured Outputs docs (if you use an LLM, do it with constraints). citeturn6view3turn5search10  

### Fastest path to a strong submission

- Implement the deterministic pipeline end-to-end with PyMuPDF:
  - find Part II start,
  - extract words,
  - remove struck words via vector intersection,
  - remove margin noise,
  - segment clauses by regex,
  - extract titles (margin vs inline),
  - validate and write JSON.  
- Add an *optional* LLM “normalize clause text” step behind a flag, using schema constrained output, so reviewers see LLM usage but your correctness doesn’t depend on it. citeturn6view3turn6view2

### Most robust (production-style) path

- Keep the deterministic core for strike-through and segmentation.  
- Add validation gates and targeted reprocessing (repair loop) if:
  - clause titles are missing,
  - a clause body is suspiciously empty,
  - ordering breaks,
  - the clause count deviates from expected (Shellvoy up to 43 + rider up to 22). citeturn4view0turn4view1turn6view3

### Key risks and mitigations

**Risk: underlines mistaken for strike-through → titles deleted.**  
Mitigation: require intersection near the vertical midpoint of word boxes; do not delete words when the line is near the bottom edge (underline zone). citeturn4view1turn6view2

**Risk: strike-through rendered as bitmap or custom glyph strokes.**  
Mitigation: if vector detection fails on some pages, fall back to page rendering + OCR/vision for those pages only (not the entire document). MuPDF’s own release notes acknowledge that style detection is a structured-text concern, but you still want a fallback in case the PDF is encoded oddly. citeturn9search6turn11view0

**Risk: clause numbering resets (duplicate ids).**  
Mitigation: allow duplicates in the output list; treat `id` as a display identifier; validate by order not by uniqueness. citeturn4view1turn4view4

## Final conclusion

For this specific charter party PDF, the “SOTA” move is not to throw a big LLM at raw PDF text; it is to **make the PDF geometry do the hard work**—especially for strike-through exclusion and margin-title reconstruction—then optionally use an LLM only where it is safe and additive (normalization/formatting, schema-validated output). This aligns with modern document-AI practice: strong parsing + constrained structured outputs beats “prompt-only extraction” when layout and edit-history artifacts matter. citeturn6view2turn6view3turn13view4turn13view2
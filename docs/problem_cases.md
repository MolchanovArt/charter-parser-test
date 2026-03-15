# Known problem cases

## Layout / structure

- Left-side title and center-starting body can appear on the same row.
- Right-side numeric noise can leak into text.
- Some titles are in a left margin band, others inline.
- Rider sections may restart numbering.
- Repeated headers, footers, and section banners can leak into clauses.
- Exact detection of the first clause section is not the main problem, but page gating still matters.

## Strike-through

- Some lines are fully struck and should disappear.
- Some lines are partially struck and only specific words should disappear.
- Regex cleanup alone is not enough.

## Clause boundary issues

- Some clauses span pages.
- Some clauses are title-like or nearly empty in the current reference set.
- Numbering gaps can be legitimate because a clause was fully removed.

## Current reference quirks

- Empty text exists for some ids.
- Some titles are partially embedded in `text`.
- Some extracted text contains encoding artifacts like `(cid:131)`.

## Policy

Do not “solve” these problems by inventing text.
Instead:
1. preserve evidence;
2. measure failures;
3. improve structure selection and source fidelity.

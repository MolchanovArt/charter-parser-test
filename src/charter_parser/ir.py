from __future__ import annotations

from statistics import median

from charter_parser.models import LineIR, PageIR, WordIR
from charter_parser.utils import normalize_ws


def _overlaps_y(a: WordIR, b: WordIR, y_tol: float) -> bool:
    ac = (a.y0 + a.y1) / 2
    bc = (b.y0 + b.y1) / 2
    return abs(ac - bc) <= y_tol


def group_words_into_lines(words: list[WordIR], y_tol: float = 2.5) -> list[LineIR]:
    if not words:
        return []
    words = sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
    rows: list[list[WordIR]] = []
    for word in words:
        placed = False
        for row in rows:
            if _overlaps_y(row[0], word, y_tol):
                row.append(word)
                placed = True
                break
        if not placed:
            rows.append([word])

    lines: list[LineIR] = []
    for i, row in enumerate(rows):
        row = sorted(row, key=lambda w: w.x0)
        x0 = min(w.x0 for w in row)
        y0 = min(w.y0 for w in row)
        x1 = max(w.x1 for w in row)
        y1 = max(w.y1 for w in row)
        page = row[0].page
        text = normalize_ws(" ".join(w.text for w in row))
        lines.append(
            LineIR(
                line_id=f"p{page}_l{i:04d}",
                page=page,
                text=text,
                bbox=(x0, y0, x1, y1),
                word_ids=[w.word_id for w in row],
            )
        )
    return lines


def page_ir_with_lines(page_ir: PageIR, y_tol: float = 2.5) -> PageIR:
    return PageIR(
        page_index=page_ir.page_index,
        width=page_ir.width,
        height=page_ir.height,
        words=page_ir.words,
        lines=group_words_into_lines(page_ir.words, y_tol=y_tol),
    )

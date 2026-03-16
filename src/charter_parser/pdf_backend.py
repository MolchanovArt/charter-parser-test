from __future__ import annotations

from pathlib import Path

import fitz

from charter_parser.models import PageIR, WordIR
from charter_parser.strike_filter import mark_struck_words


class PyMuPDFBackend:
    def __init__(self, pdf_path: str | Path):
        self.pdf_path = str(pdf_path)
        self.doc = fitz.open(self.pdf_path)

    def page_count(self) -> int:
        return self.doc.page_count

    def page_size(self, page_index: int) -> tuple[float, float]:
        page = self.doc.load_page(page_index)
        rect = page.rect
        return float(rect.width), float(rect.height)

    def extract_page_words(self, page_index: int) -> list[WordIR]:
        page = self.doc.load_page(page_index)
        words = page.get_text("words") or []
        out: list[WordIR] = []
        for i, item in enumerate(words):
            x0, y0, x1, y1, text, *_rest = item
            out.append(
                WordIR(
                    word_id=f"p{page_index}_w{i:04d}",
                    page=page_index,
                    text=str(text),
                    x0=float(x0),
                    y0=float(y0),
                    x1=float(x1),
                    y1=float(y1),
                )
            )
        return mark_struck_words(out, page.get_drawings() or [])

    def extract_page_ir(self, page_index: int) -> PageIR:
        width, height = self.page_size(page_index)
        return PageIR(page_index=page_index, width=width, height=height, words=self.extract_page_words(page_index), lines=[])

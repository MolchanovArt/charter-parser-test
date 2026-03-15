from charter_parser.layout_profile import infer_layout_profile, infer_page_layout
from charter_parser.models import LineIR, PageIR


def make_line(line_id: str, x0: float, y0: float, x1: float, y1: float, text: str, page: int = 0) -> LineIR:
    return LineIR(
        line_id=line_id,
        page=page,
        text=text,
        bbox=(x0, y0, x1, y1),
        word_ids=[],
    )


def test_infer_page_layout_margin_title_like():
    page = PageIR(
        page_index=0,
        width=600,
        height=800,
        words=[],
        lines=[
            make_line("l1", 55, 40, 140, 52, "10. DEMURRAGE"),
            make_line("l2", 180, 70, 520, 84, "Body line one"),
            make_line("l3", 182, 92, 518, 106, "Body line two"),
            make_line("l4", 184, 114, 521, 128, "Body line three"),
        ],
    )
    layout = infer_page_layout(page)
    assert layout.page_type == "margin_title_like"
    assert any(band.name == "left_band_hint" for band in layout.bands)
    assert layout.confidence > 0.45


def test_infer_page_layout_mixed_when_right_noise_exists():
    page = PageIR(
        page_index=0,
        width=600,
        height=800,
        words=[],
        lines=[
            make_line("l1", 55, 40, 140, 52, "11. LAYTIME"),
            make_line("l2", 180, 70, 520, 84, "Body line one"),
            make_line("l3", 184, 92, 517, 106, "Body line two"),
            make_line("l4", 542, 92, 588, 106, "123"),
        ],
    )
    layout = infer_page_layout(page)
    assert layout.page_type == "mixed"
    assert any(band.name == "right_band_hint" for band in layout.bands)
    assert layout.confidence > 0.45


def test_infer_layout_profile_keeps_page_types_distinct():
    inline_page = PageIR(
        page_index=0,
        width=600,
        height=800,
        words=[],
        lines=[
            make_line("l1", 170, 40, 520, 54, "12. NOTICE OF READINESS"),
            make_line("l2", 174, 68, 518, 82, "Body line one"),
            make_line("l3", 176, 90, 516, 104, "Body line two"),
        ],
    )
    mixed_page = PageIR(
        page_index=1,
        width=600,
        height=800,
        words=[],
        lines=[
            make_line("l1", 50, 40, 142, 54, "13. CARGO", page=1),
            make_line("l2", 184, 70, 518, 84, "Body line one", page=1),
            make_line("l3", 186, 92, 517, 106, "Body line two", page=1),
            make_line("l4", 544, 92, 590, 106, "101", page=1),
        ],
    )
    profile = infer_layout_profile([inline_page, mixed_page])
    types = [page.page_type for page in profile.pages]
    assert types == ["inline_title_like", "mixed"]
    assert profile.pages[0].confidence != profile.pages[1].confidence

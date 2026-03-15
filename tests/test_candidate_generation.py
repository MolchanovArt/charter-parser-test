from charter_parser.candidate_generation import generate_candidate_blocks
from charter_parser.config import Settings
from charter_parser.models import LayoutBand, LayoutProfile, LineIR, PageIR, PageLayoutProfile, WordIR


def make_word(word_id: str, text: str, x0: float, x1: float, y0: float, y1: float, page: int = 0) -> WordIR:
    return WordIR(word_id=word_id, page=page, text=text, x0=x0, y0=y0, x1=x1, y1=y1)


def make_line(line_id: str, text: str, bbox: tuple[float, float, float, float], word_ids: list[str], page: int = 0) -> LineIR:
    return LineIR(line_id=line_id, page=page, text=text, bbox=bbox, word_ids=word_ids)


def test_margin_page_candidate_splits_left_title_body_and_right_noise():
    words = [
        make_word("w1", "Condition", 42.0, 78.0, 58.0, 68.0),
        make_word("w2", "1.", 125.0, 132.0, 58.0, 68.0),
        make_word("w3", "Owners", 135.0, 163.0, 58.0, 68.0),
        make_word("w4", "shall", 166.0, 183.0, 58.0, 68.0),
        make_word("w5", "68", 526.0, 535.0, 58.0, 68.0),
        make_word("w6", "Of", 42.0, 52.0, 70.0, 80.0),
        make_word("w7", "vessel", 55.0, 84.0, 70.0, 80.0),
        make_word("w8", "loading", 135.0, 165.0, 70.0, 80.0),
        make_word("w9", "port", 168.0, 185.0, 70.0, 80.0),
        make_word("w10", "69", 526.0, 535.0, 70.0, 80.0),
    ]
    lines = [
        make_line("l1", "Condition 1. Owners shall 68", (42.0, 58.0, 535.0, 68.0), ["w1", "w2", "w3", "w4", "w5"]),
        make_line("l2", "Of vessel loading port 69", (42.0, 70.0, 535.0, 80.0), ["w6", "w7", "w8", "w9", "w10"]),
    ]
    page = PageIR(page_index=0, width=612.0, height=792.0, words=words, lines=lines)
    profile = LayoutProfile(
        page_count=1,
        pages=[
            PageLayoutProfile(
                page_index=0,
                page_type="margin_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=113.0, x1=535.0, confidence=0.8)],
            )
        ],
    )

    blocks, diagnostics = generate_candidate_blocks([page], profile, Settings())
    start_blocks = [block for block in blocks if block.block_type == "candidate_clause_start"]
    assert len(start_blocks) == 1
    block = start_blocks[0]
    assert block.candidate_clause_id == "part2:1"
    assert block.title_text == "Condition Of vessel"
    assert block.body_text == "Owners shall\nloading port"
    assert diagnostics["metrics"]["right_noise_suppression_rate"] == 1.0


def test_inline_page_candidate_uses_title_line_then_body_continuation():
    words = [
        make_word("w1", "1.", 50.0, 58.0, 60.0, 72.0),
        make_word("w2", "INDEMNITY", 86.0, 140.0, 60.0, 72.0),
        make_word("w3", "CLAUSE", 142.0, 178.0, 60.0, 72.0),
        make_word("w4", "If", 86.0, 94.0, 90.0, 102.0),
        make_word("w5", "Charterers", 96.0, 150.0, 90.0, 102.0),
    ]
    lines = [
        make_line("l1", "1. INDEMNITY CLAUSE", (50.0, 60.0, 178.0, 72.0), ["w1", "w2", "w3"]),
        make_line("l2", "If Charterers", (86.0, 90.0, 150.0, 102.0), ["w4", "w5"]),
    ]
    page = PageIR(page_index=0, width=612.0, height=792.0, words=words, lines=lines)
    profile = LayoutProfile(
        page_count=1,
        pages=[
            PageLayoutProfile(
                page_index=0,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=86.0, x1=576.0, confidence=0.8)],
            )
        ],
    )

    blocks, _diagnostics = generate_candidate_blocks([page], profile, Settings())
    block = next(block for block in blocks if block.block_type == "candidate_clause_start")
    assert block.title_text == "INDEMNITY CLAUSE"
    assert block.body_text == "If Charterers"
    assert block.title_line_ids == ["l1"]

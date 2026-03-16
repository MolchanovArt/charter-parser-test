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


def test_inline_reference_to_part_ii_does_not_create_false_section_banner():
    banner_words = [
        make_word("b1", "PART", 86.0, 112.0, 40.0, 52.0, page=5),
        make_word("b2", "II", 114.0, 124.0, 40.0, 52.0, page=5),
    ]
    words = banner_words + [
        make_word("w1", "Clause", 86.0, 116.0, 60.0, 72.0, page=37),
        make_word("w2", "13", 118.0, 128.0, 60.0, 72.0, page=37),
        make_word("w3", "of", 130.0, 140.0, 60.0, 72.0, page=37),
        make_word("w4", "Shellvoy", 142.0, 185.0, 60.0, 72.0, page=37),
        make_word("w5", "5", 187.0, 192.0, 60.0, 72.0, page=37),
        make_word("w6", "Part", 194.0, 214.0, 60.0, 72.0, page=37),
        make_word("w7", "II.", 216.0, 228.0, 60.0, 72.0, page=37),
        make_word("w8", "14.", 86.0, 90.0, 90.0, 102.0, page=37),
        make_word("w9", "SIGNED", 100.0, 142.0, 90.0, 102.0, page=37),
        make_word("w10", "CHARTER", 144.0, 192.0, 90.0, 102.0, page=37),
        make_word("w11", "PARTY", 194.0, 224.0, 90.0, 102.0, page=37),
        make_word("w12", "Charterers", 100.0, 154.0, 108.0, 120.0, page=37),
        make_word("w13", "require", 156.0, 190.0, 108.0, 120.0, page=37),
    ]
    banner_lines = [
        make_line("bline", "PART II", (86.0, 40.0, 124.0, 52.0), ["b1", "b2"], page=5),
    ]
    lines = banner_lines + [
        make_line(
            "l1",
            "Clause 13 of Shellvoy 5 Part II.",
            (86.0, 60.0, 228.0, 72.0),
            ["w1", "w2", "w3", "w4", "w5", "w6", "w7"],
            page=37,
        ),
        make_line("l2", "14. SIGNED CHARTER PARTY", (86.0, 90.0, 224.0, 102.0), ["w8", "w9", "w10", "w11"], page=37),
        make_line("l3", "Charterers require", (100.0, 108.0, 190.0, 120.0), ["w12", "w13"], page=37),
    ]
    banner_page = PageIR(page_index=5, width=612.0, height=792.0, words=banner_words, lines=banner_lines)
    page = PageIR(page_index=37, width=612.0, height=792.0, words=words[2:], lines=lines[1:])
    profile = LayoutProfile(
        page_count=2,
        pages=[
            PageLayoutProfile(
                page_index=5,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=86.0, x1=576.0, confidence=0.8)],
            ),
            PageLayoutProfile(
                page_index=37,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=86.0, x1=576.0, confidence=0.8)],
            )
        ],
    )
    settings = Settings()

    blocks, _diagnostics = generate_candidate_blocks([banner_page, page], profile, settings)

    section_banners = [block for block in blocks if block.block_type == "section_banner"]
    assert [block.section_hint for block in section_banners] == ["part2"]
    start_block = next(block for block in blocks if block.block_type == "candidate_clause_start")
    assert start_block.candidate_clause_id == "part2:14"


def test_low_number_restart_after_high_clause_stays_in_current_clause_body():
    words = [
        make_word("w1", "42.", 86.0, 100.0, 60.0, 72.0, page=32),
        make_word("w2", "Japan", 108.0, 138.0, 60.0, 72.0, page=32),
        make_word("w3", "Clause", 140.0, 176.0, 60.0, 72.0, page=32),
        make_word("w4", "2.", 86.0, 96.0, 90.0, 102.0, page=33),
        make_word("w5", "Supervisor", 104.0, 168.0, 90.0, 102.0, page=33),
        make_word("w6", "If", 104.0, 112.0, 108.0, 120.0, page=33),
        make_word("w7", "requested", 114.0, 160.0, 108.0, 120.0, page=33),
    ]
    lines = [
        make_line("l1", "42. Japan Clause", (86.0, 60.0, 176.0, 72.0), ["w1", "w2", "w3"], page=32),
        make_line("l2", "2. Supervisor", (86.0, 90.0, 168.0, 102.0), ["w4", "w5"], page=33),
        make_line("l3", "If requested", (104.0, 108.0, 160.0, 120.0), ["w6", "w7"], page=33),
    ]
    page_32 = PageIR(page_index=32, width=612.0, height=792.0, words=words[:3], lines=[lines[0]])
    page_33 = PageIR(page_index=33, width=612.0, height=792.0, words=words[3:], lines=lines[1:])
    profile = LayoutProfile(
        page_count=2,
        pages=[
            PageLayoutProfile(
                page_index=32,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=86.0, x1=576.0, confidence=0.8)],
            ),
            PageLayoutProfile(
                page_index=33,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=86.0, x1=576.0, confidence=0.8)],
            ),
        ],
    )
    settings = Settings()

    blocks, _diagnostics = generate_candidate_blocks([page_32, page_33], profile, settings)

    start_blocks = [block for block in blocks if block.block_type == "candidate_clause_start"]
    assert len(start_blocks) == 1
    assert start_blocks[0].candidate_clause_id == "part2:42"
    assert "2. Supervisor" in start_blocks[0].body_text


def test_real_clause_after_false_high_restart_is_not_swallowed():
    banner_words = [
        make_word("b1", "PART", 86.0, 112.0, 40.0, 52.0, page=5),
        make_word("b2", "II", 114.0, 124.0, 40.0, 52.0, page=5),
    ]
    words = banner_words + [
        make_word("w1", "21.50", 86.0, 112.0, 60.0, 72.0, page=19),
        make_word("w2", "MIO)", 114.0, 140.0, 60.0, 72.0, page=19),
        make_word("w3", "4.", 86.0, 94.0, 90.0, 102.0, page=20),
        make_word("w4", "Early", 104.0, 134.0, 90.0, 102.0, page=20),
        make_word("w5", "Loading", 136.0, 176.0, 90.0, 102.0, page=20),
        make_word("w6", "Clause", 178.0, 214.0, 90.0, 102.0, page=20),
        make_word("w7", "If", 104.0, 112.0, 108.0, 120.0, page=20),
        make_word("w8", "vessel", 114.0, 144.0, 108.0, 120.0, page=20),
    ]
    banner_lines = [
        make_line("bline", "PART II", (86.0, 40.0, 124.0, 52.0), ["b1", "b2"], page=5),
    ]
    lines = banner_lines + [
        make_line("l1", "21.50 MIO)", (86.0, 60.0, 140.0, 72.0), ["w1", "w2"], page=19),
        make_line("l2", "4. Early Loading Clause", (86.0, 90.0, 214.0, 102.0), ["w3", "w4", "w5", "w6"], page=20),
        make_line("l3", "If vessel", (104.0, 108.0, 144.0, 120.0), ["w7", "w8"], page=20),
    ]
    banner_page = PageIR(page_index=5, width=612.0, height=792.0, words=banner_words, lines=banner_lines)
    page_19 = PageIR(page_index=19, width=612.0, height=792.0, words=words[2:4], lines=[lines[1]])
    page_20 = PageIR(page_index=20, width=612.0, height=792.0, words=words[4:], lines=lines[2:])
    profile = LayoutProfile(
        page_count=3,
        pages=[
            PageLayoutProfile(
                page_index=5,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=86.0, x1=576.0, confidence=0.8)],
            ),
            PageLayoutProfile(
                page_index=19,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=86.0, x1=576.0, confidence=0.8)],
            ),
            PageLayoutProfile(
                page_index=20,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=86.0, x1=576.0, confidence=0.8)],
            ),
        ],
    )
    settings = Settings()

    blocks, _diagnostics = generate_candidate_blocks([banner_page, page_19, page_20], profile, settings)

    start_blocks = [block for block in blocks if block.block_type == "candidate_clause_start"]
    assert [block.candidate_clause_id for block in start_blocks] == ["part2:21", "part2:4"]


def test_embedded_inline_start_recovers_clause_number_after_prefix_label():
    banner_words = [
        make_word("b1", "PART", 86.0, 112.0, 40.0, 52.0, page=5),
        make_word("b2", "II", 114.0, 124.0, 40.0, 52.0, page=5),
    ]
    words = banner_words + [
        make_word("w1", "Vessel", 42.0, 78.0, 60.0, 72.0, page=9),
        make_word("w2", "16.", 86.0, 98.0, 60.0, 72.0, page=9),
        make_word("w3", "Charterers", 102.0, 156.0, 60.0, 72.0, page=9),
        make_word("w4", "inspection", 42.0, 88.0, 78.0, 90.0, page=9),
        make_word("w5", "loading", 92.0, 128.0, 78.0, 90.0, page=9),
    ]
    banner_lines = [make_line("bline", "PART II", (86.0, 40.0, 124.0, 52.0), ["b1", "b2"], page=5)]
    lines = banner_lines + [
        make_line("l1", "Vessel 16. Charterers", (42.0, 60.0, 156.0, 72.0), ["w1", "w2", "w3"], page=9),
        make_line("l2", "inspection loading", (42.0, 78.0, 128.0, 90.0), ["w4", "w5"], page=9),
    ]
    banner_page = PageIR(page_index=5, width=612.0, height=792.0, words=banner_words, lines=banner_lines)
    page = PageIR(page_index=9, width=612.0, height=792.0, words=words[2:], lines=lines[1:])
    profile = LayoutProfile(
        page_count=2,
        pages=[
            PageLayoutProfile(
                page_index=5,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=42.0, x1=535.0, confidence=0.8)],
            ),
            PageLayoutProfile(
                page_index=9,
                page_type="inline_title_like",
                confidence=0.7,
                bands=[LayoutBand(name="body_band_hint", x0=42.0, x1=535.0, confidence=0.8)],
            ),
        ],
    )

    blocks, _diagnostics = generate_candidate_blocks([banner_page, page], profile, Settings())

    start_blocks = [block for block in blocks if block.block_type == "candidate_clause_start"]
    assert len(start_blocks) == 1
    assert start_blocks[0].candidate_clause_id == "part2:16"
    assert start_blocks[0].title_text == "Vessel inspection"


def test_embedded_inline_start_allows_partially_struck_line():
    words = [
        WordIR(word_id="w0", page=10, text="Agency", x0=42.3, y0=0.0, x1=70.3, y1=1.0),
        WordIR(word_id="w1", page=10, text="24.", x0=125.6, y0=0.0, x1=136.8, y1=1.0),
        WordIR(word_id="w2", page=10, text="The", x0=140.9, y0=0.0, x1=154.8, y1=1.0),
        WordIR(word_id="w3", page=10, text="vessel's", x0=158.9, y0=0.0, x1=186.0, y1=1.0),
        WordIR(word_id="w4", page=10, text="agents", x0=190.0, y0=0.0, x1=213.1, y1=1.0),
        WordIR(word_id="w5", page=10, text="shall", x0=217.1, y0=0.0, x1=234.0, y1=1.0),
        WordIR(word_id="w6", page=10, text="be", x0=238.1, y0=0.0, x1=246.6, y1=1.0),
        WordIR(word_id="w7", page=10, text="nominated", x0=250.6, y0=0.0, x1=288.6, y1=1.0),
        WordIR(word_id="w8", page=10, text="by", x0=292.6, y0=0.0, x1=301.5, y1=1.0),
        WordIR(word_id="w9", page=10, text="Charterers", x0=305.7, y0=0.0, x1=343.1, y1=1.0),
        WordIR(word_id="w10", page=10, text="at", x0=347.3, y0=0.0, x1=353.8, y1=1.0),
        WordIR(word_id="w11", page=10, text="nominated", x0=357.9, y0=0.0, x1=395.9, y1=1.0),
        WordIR(word_id="w12", page=10, text="loading", x0=399.9, y0=0.0, x1=426.9, y1=1.0),
        WordIR(word_id="w13", page=10, text="ports", x0=431.0, y0=0.0, x1=449.0, y1=1.0),
        WordIR(word_id="w14", page=10, text="of", x0=453.0, y0=0.0, x1=460.5, y1=1.0, is_struck=True),
        WordIR(word_id="w15", page=10, text="loading", x0=464.6, y0=0.0, x1=491.5, y1=1.0, is_struck=True),
        WordIR(word_id="w16", page=10, text="and", x0=495.5, y0=0.0, x1=508.5, y1=1.0),
        WordIR(word_id="w17", page=10, text="304", x0=522.0, y0=0.0, x1=535.5, y1=1.0),
        WordIR(word_id="w18", page=10, text="discharge", x0=114.3, y0=2.0, x1=150.0, y1=3.0),
        WordIR(word_id="w19", page=10, text="ports,provided", x0=151.0, y0=2.0, x1=220.0, y1=3.0),
        WordIR(word_id="w20", page=10, text="competitive.", x0=221.0, y0=2.0, x1=250.3, y1=3.0),
    ]
    lines = [
        make_line(
            "l1",
            "Agency 24. The vessel's agents shall be nominated by Charterers at nominated loading ports of loading and 304",
            (42.3, 266.36, 535.5, 276.33),
            [f"w{i}" for i in range(18)],
            page=10,
        ),
        make_line("l2", "discharge ports,provided competitive.", (114.3, 276.74, 250.32, 286.70), ["w18", "w19", "w20"], page=10),
    ]
    page = PageIR(page_index=10, width=612.0, height=792.0, words=words, lines=lines)
    profile = LayoutProfile(
        page_count=1,
        pages=[
            PageLayoutProfile(
                page_index=10,
                page_type="margin_title_like",
                confidence=0.8,
                bands=[
                    LayoutBand(name="body_band_hint", x0=114.3, x1=535.5, confidence=0.8),
                    LayoutBand(name="left_band_hint", x0=0.0, x1=90.0, confidence=0.5),
                ],
            )
        ],
    )

    blocks, _diagnostics = generate_candidate_blocks([page], profile, Settings())
    start = next(block for block in blocks if block.candidate_clause_id == "part2:24")
    assert start.title_text == "Agency"
    assert start.body_text.startswith("The vessel's agents shall be nominated")

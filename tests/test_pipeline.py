from charter_parser.pipeline import _residual_recommendation, _suppressed_body_line_indexes


def test_suppressed_body_line_indexes_expands_numbered_subitem_span():
    rows = [
        {"raw_text": "(1) Pulan Bukom Berth Freeboard Clause", "struck_word_count": 0},
        {"raw_text": "Owners warrant that vessel will not exceed a maximum freeboard.", "struck_word_count": 3},
        {"raw_text": "All time, costs and expenses shall be for Owners' account.", "struck_word_count": 0},
        {"raw_text": "(2) Agency", "struck_word_count": 0},
        {"raw_text": "If Charterers nominates SIETCO as Agents, as per", "struck_word_count": 0},
        {"raw_text": "required in the Singapore Income Tax Act\".", "struck_word_count": 2},
    ]

    assert _suppressed_body_line_indexes(rows, section="part2") == {0, 1, 2, 3, 4, 5}


def test_suppressed_body_line_indexes_stops_at_embedded_inline_start():
    rows = [
        {"raw_text": "Inert gas 19. The vessel's inert gas system shall comply.", "struck_word_count": 0},
        {"raw_text": "Should the inert gas system fail, time lost shall not count.", "struck_word_count": 0},
        {"raw_text": "Crude oil 20. If the vessel is equipped for crude oil washing", "struck_word_count": 4},
        {"raw_text": "washing crude oil wash those tanks in which the cargo is carried.", "struck_word_count": 3},
        {"raw_text": "20. If the vessel is equipped for crude oil washing", "struck_word_count": 0},
        {"raw_text": "Over age 21. Any additional insurance shall be for Owners' account.", "struck_word_count": 4},
    ]

    assert _suppressed_body_line_indexes(rows, section="part2") == {2, 3, 5}


def test_suppressed_body_line_indexes_keeps_small_partial_strike_local():
    rows = [
        {"raw_text": "20. If the vessel is equipped for crude oil washing", "struck_word_count": 0},
        {"raw_text": "laytime or, if the vessel is on demurrage, for demurrage.", "struck_word_count": 0},
        {"raw_text": "increase of 8 hours 12 HRS IF ALL TANKS WASHED OR PRORATA.", "struck_word_count": 2},
        {"raw_text": "Over age 21. Any additional insurance shall be for Owners' account.", "struck_word_count": 5},
    ]

    assert _suppressed_body_line_indexes(rows, section="part2") == {2, 3}


def test_suppressed_body_line_indexes_applies_line_coverage_block_suppression():
    rows = [
        {"raw_text": "35. United States of America (U.S) Clause", "struck_word_count": 0, "line_strike_coverage": 0.91},
        {"raw_text": "1) Customs Regulations", "struck_word_count": 0, "line_strike_coverage": 0.88},
        {"raw_text": "Owners warrant that vessel shall comply with all U.S. regulations.", "struck_word_count": 0, "line_strike_coverage": 0.82},
        {"raw_text": "36. War Cancellation Clause", "struck_word_count": 0, "line_strike_coverage": 0.02},
    ]

    assert _suppressed_body_line_indexes(rows, section="part2") == {0, 1, 2}


def test_suppressed_body_line_indexes_riders_do_not_expand_backwards():
    rows = [
        {"raw_text": "If vessel is able to load earlier than commencement of laydays.", "struck_word_count": 0, "line_strike_coverage": 0.0, "full_line_struck": False},
        {"raw_text": "ANY TIME SAVED TO BE SHARED 50/50", "struck_word_count": 0, "line_strike_coverage": 0.0, "full_line_struck": False},
        {"raw_text": "Charterers shall have the benefit of such time saved.", "struck_word_count": 12, "line_strike_coverage": 0.99, "full_line_struck": True},
        {"raw_text": "between commencement of loading until the original laydays.", "struck_word_count": 11, "line_strike_coverage": 0.99, "full_line_struck": True},
    ]

    assert _suppressed_body_line_indexes(rows, section="shell") == {2, 3}


def test_residual_recommendation_allows_rider_heading_only_survival():
    residual = _residual_recommendation(
        "18. CLINGAGE – NOT APPLICABLE FOR THIS CHARTER",
        "Owners and charterer recognise that the vessel has been positioned for loading after Drydock.",
        "18. CLINGAGE – NOT APPLICABLE FOR THIS CHARTER",
        "",
        section="essar",
    )

    assert residual["recommendation"] == "keep"
    assert residual["reason"] == "heading_only_survival"

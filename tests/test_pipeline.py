from charter_parser.pipeline import _suppressed_body_line_indexes


def test_suppressed_body_line_indexes_expands_numbered_subitem_span():
    rows = [
        {"raw_text": "(1) Pulan Bukom Berth Freeboard Clause", "struck_word_count": 0},
        {"raw_text": "Owners warrant that vessel will not exceed a maximum freeboard.", "struck_word_count": 3},
        {"raw_text": "All time, costs and expenses shall be for Owners' account.", "struck_word_count": 0},
        {"raw_text": "(2) Agency", "struck_word_count": 0},
        {"raw_text": "If Charterers nominates SIETCO as Agents, as per", "struck_word_count": 0},
        {"raw_text": "required in the Singapore Income Tax Act\".", "struck_word_count": 2},
    ]

    assert _suppressed_body_line_indexes(rows) == {0, 1, 2, 3, 4, 5}


def test_suppressed_body_line_indexes_stops_at_embedded_inline_start():
    rows = [
        {"raw_text": "Inert gas 19. The vessel's inert gas system shall comply.", "struck_word_count": 0},
        {"raw_text": "Should the inert gas system fail, time lost shall not count.", "struck_word_count": 0},
        {"raw_text": "Crude oil 20. If the vessel is equipped for crude oil washing", "struck_word_count": 4},
        {"raw_text": "washing crude oil wash those tanks in which the cargo is carried.", "struck_word_count": 3},
        {"raw_text": "20. If the vessel is equipped for crude oil washing", "struck_word_count": 0},
        {"raw_text": "Over age 21. Any additional insurance shall be for Owners' account.", "struck_word_count": 4},
    ]

    assert _suppressed_body_line_indexes(rows) == {2, 3, 5}


def test_suppressed_body_line_indexes_keeps_small_partial_strike_local():
    rows = [
        {"raw_text": "20. If the vessel is equipped for crude oil washing", "struck_word_count": 0},
        {"raw_text": "laytime or, if the vessel is on demurrage, for demurrage.", "struck_word_count": 0},
        {"raw_text": "increase of 8 hours 12 HRS IF ALL TANKS WASHED OR PRORATA.", "struck_word_count": 2},
        {"raw_text": "Over age 21. Any additional insurance shall be for Owners' account.", "struck_word_count": 5},
    ]

    assert _suppressed_body_line_indexes(rows) == {2, 3}

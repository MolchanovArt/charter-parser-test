from charter_parser.adjudication import _structured_decision_schema, apply_adjudication_to_blocks, extract_ambiguity_cases
from charter_parser.config import Settings
from charter_parser.models import AdjudicationResult, CandidateBlock, LineIR, PageIR, StructuredAdjudicationDecision


def make_line(line_id: str, text: str, y0: float, y1: float, page: int = 0) -> LineIR:
    return LineIR(line_id=line_id, page=page, text=text, bbox=(40.0, y0, 540.0, y1), word_ids=[])


def block(
    *,
    block_id: str,
    page: int,
    section_hint: str,
    block_type: str,
    line_ids: list[str],
    candidate_clause_id: str | None = None,
    candidate_local_num: int | None = None,
    title_line_ids: list[str] | None = None,
    body_line_ids: list[str] | None = None,
    title_text: str = "",
    body_text: str = "",
    line_texts: dict[str, str] | None = None,
) -> CandidateBlock:
    texts = line_texts or {}
    title_ids = title_line_ids or []
    body_ids = body_line_ids or []
    line_decisions = []
    for line_id in line_ids:
        text = texts.get(line_id, line_id)
        line_decisions.append(
            {
                "line_id": line_id,
                "raw_text": text,
                "extracted_text": text,
                "clean_text": text,
                "title_text": text if line_id in title_ids else "",
                "body_text": text if line_id in body_ids else "",
                "labels": ["candidate_clause_start"] if block_type == "candidate_clause_start" else [],
                "reasons": ["fixture"],
            }
        )
    return CandidateBlock(
        block_id=block_id,
        page=page,
        page_type="inline_title_like",
        routing_mode="inline_title_like",
        section_hint=section_hint,
        block_type=block_type,
        line_ids=line_ids,
        title_line_ids=title_ids,
        body_line_ids=body_ids,
        noise_line_ids=line_ids if block_type == "section_banner" else [],
        line_decisions=line_decisions,
        candidate_clause_id=candidate_clause_id,
        candidate_local_num=candidate_local_num,
        title_text=title_text,
        body_text=body_text,
        support_score=0.8,
        reasons=["fixture"],
    )


def test_extract_ambiguity_cases_flags_false_banner_and_nested_restart():
    page = PageIR(
        page_index=32,
        width=612.0,
        height=792.0,
        lines=[
            make_line("l1", "42. Japan Clause", 60.0, 70.0, page=32),
            make_line(
                "l2",
                "B. Freight payment Clause 5 of Part II of Shellvoy 5 delete word 'upon' in line 104 and",
                280.0,
                290.0,
                page=32,
            ),
            make_line("l3", "1. Drawing", 320.0, 330.0, page=32),
            make_line("l4", "Owners shall supply Charterers with copies of:-", 332.0, 342.0, page=32),
        ],
    )
    blocks = [
        block(
            block_id="b1",
            page=32,
            section_hint="part2",
            block_type="candidate_clause_start",
            line_ids=["l1"],
            candidate_clause_id="part2:42",
            candidate_local_num=42,
            body_line_ids=["l1"],
            body_text="42. Japan Clause",
            line_texts={"l1": "42. Japan Clause"},
        ),
        block(
            block_id="b2",
            page=32,
            section_hint="part2",
            block_type="section_banner",
            line_ids=["l2"],
            line_texts={"l2": "B. Freight payment Clause 5 of Part II of Shellvoy 5 delete word 'upon' in line 104 and"},
        ),
        block(
            block_id="b3",
            page=32,
            section_hint="part2",
            block_type="candidate_clause_start",
            line_ids=["l3", "l4"],
            candidate_clause_id="part2:1",
            candidate_local_num=1,
            title_line_ids=["l3"],
            body_line_ids=["l4"],
            title_text="Drawing",
            body_text="Owners shall supply Charterers with copies of:-",
            line_texts={"l3": "1. Drawing", "l4": "Owners shall supply Charterers with copies of:-"},
        ),
    ]

    cases = extract_ambiguity_cases([page], blocks, Settings())
    assert {case.bucket for case in cases} == {"false_banner_section", "nested_numbering"}


def test_apply_adjudication_to_blocks_rewrites_section_and_attaches_numbered_item():
    blocks = [
        block(
            block_id="shell11",
            page=22,
            section_hint="shell",
            block_type="candidate_clause_start",
            line_ids=["l1"],
            candidate_clause_id="shell:11",
            candidate_local_num=11,
            title_line_ids=["l1"],
            title_text="Adherence to Voyage Instruction Clause",
            line_texts={"l1": "11. Adherence to Voyage Instruction Clause"},
        ),
        block(
            block_id="banner",
            page=22,
            section_hint="part2",
            block_type="section_banner",
            line_ids=["l2"],
            line_texts={"l2": "This clause shall have effect notwithstanding the provision of Clause 32(a) of Part II of Shellvoy 5 or"},
        ),
        block(
            block_id="cont",
            page=22,
            section_hint="part2",
            block_type="candidate_continuation",
            line_ids=["l3"],
            body_line_ids=["l3"],
            body_text="Owners' defenses under the Hague-Visby Rules.",
            line_texts={"l3": "Owners' defenses under the Hague-Visby Rules."},
        ),
        block(
            block_id="shell12",
            page=22,
            section_hint="part2",
            block_type="candidate_clause_start",
            line_ids=["l4"],
            candidate_clause_id="part2:12",
            candidate_local_num=12,
            title_line_ids=["l4"],
            title_text="Administration Clause",
            line_texts={"l4": "12. Administration Clause"},
        ),
        block(
            block_id="nested",
            page=32,
            section_hint="shell",
            block_type="candidate_clause_start",
            line_ids=["l5", "l6"],
            candidate_clause_id="shell:1",
            candidate_local_num=1,
            title_line_ids=["l5"],
            body_line_ids=["l6"],
            title_text="Drawing",
            body_text="Owners shall supply Charterers with copies",
            line_texts={"l5": "1. Drawing", "l6": "Owners shall supply Charterers with copies"},
        ),
    ]
    results = [
        AdjudicationResult(
            case_id="banner:false_banner_section",
            bucket="false_banner_section",
            page=22,
            block_id="banner",
            status="accepted",
            applied=True,
            effect="attach_to_previous",
            decision=StructuredAdjudicationDecision(
                candidate_start=False,
                attach_to_previous=True,
                section_hint="shell",
                title_line_ids=[],
                body_line_ids=["l2"],
                confidence=0.94,
                reason_short="inline shellvoy reference",
            ),
        ),
        AdjudicationResult(
            case_id="nested:nested_numbering",
            bucket="nested_numbering",
            page=32,
            block_id="nested",
            status="accepted",
            applied=True,
            effect="attach_to_previous",
            decision=StructuredAdjudicationDecision(
                candidate_start=False,
                attach_to_previous=True,
                section_hint="shell",
                title_line_ids=[],
                body_line_ids=["l5", "l6"],
                confidence=0.93,
                reason_short="sub-item within current clause",
            ),
        ),
    ]

    adjusted, metrics = apply_adjudication_to_blocks(blocks, results)

    assert adjusted[1].block_type == "candidate_continuation"
    assert adjusted[1].section_hint == "shell"
    assert "Part II of Shellvoy 5" in adjusted[1].body_text
    assert adjusted[2].section_hint == "shell"
    assert adjusted[3].candidate_clause_id == "shell:12"
    assert adjusted[4].block_type == "candidate_continuation"
    assert adjusted[4].candidate_clause_id is None
    assert adjusted[4].section_hint == "shell"
    assert adjusted[4].body_text.startswith("1. Drawing")
    assert metrics["effects"]["banner_attach_to_previous"] == 1
    assert metrics["effects"]["start_attach_to_previous"] == 1


def test_structured_adjudication_decision_schema_forbids_additional_properties():
    schema = _structured_decision_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "candidate_start",
        "attach_to_previous",
        "section_hint",
        "title_line_ids",
        "body_line_ids",
        "confidence",
        "reason_short",
    ]


def test_apply_adjudication_to_blocks_downgrades_nested_paren_start_to_continuation():
    blocks = [
        block(
            block_id="shell3",
            page=18,
            section_hint="shell",
            block_type="candidate_clause_start",
            line_ids=["l1"],
            candidate_clause_id="shell:3",
            candidate_local_num=3,
            title_line_ids=["l1"],
            title_text="Insurance Clause",
            line_texts={"l1": "3. Insurance Clause"},
        ),
        block(
            block_id="nested",
            page=19,
            section_hint="shell",
            block_type="candidate_clause_start",
            line_ids=["l2", "l3"],
            candidate_clause_id="shell:21",
            candidate_local_num=21,
            title_line_ids=["l2"],
            body_line_ids=["l3"],
            title_text="50 MIO)",
            body_text="Owners hereby warrant",
            line_texts={"l2": "5) Year 2000", "l3": "Owners hereby warrant"},
        ),
    ]
    results = [
        AdjudicationResult(
            case_id="nested:nested_numbering",
            bucket="nested_numbering",
            page=19,
            block_id="nested",
            status="accepted",
            applied=True,
            effect="candidate_start",
            decision=StructuredAdjudicationDecision(
                candidate_start=True,
                attach_to_previous=False,
                section_hint="shell",
                title_line_ids=["l2"],
                body_line_ids=["l3"],
                confidence=0.86,
                reason_short="genuine content starts at 5) Year 2000",
            ),
        )
    ]

    adjusted, metrics = apply_adjudication_to_blocks(blocks, results)

    assert adjusted[1].block_type == "candidate_continuation"
    assert adjusted[1].candidate_clause_id is None
    assert adjusted[1].body_text.startswith("5) Year 2000")
    assert metrics["effects"]["start_attach_to_previous"] == 1

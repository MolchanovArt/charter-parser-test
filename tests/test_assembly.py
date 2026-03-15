from charter_parser.assembly import assemble_draft_clauses
from charter_parser.models import CandidateBlock


def test_assemble_draft_clauses_merges_continuations():
    blocks = [
        CandidateBlock(
            block_id="b1",
            page=5,
            page_type="margin_title_like",
            routing_mode="margin_title_like",
            section_hint="part2",
            block_type="candidate_clause_start",
            line_ids=["l1", "l2"],
            title_line_ids=["l1", "l2"],
            body_line_ids=["l1", "l2"],
            line_decisions=[],
            candidate_clause_id="part2:1",
            candidate_local_num=1,
            title_text="Condition Of vessel",
            body_text="Owners shall\nexercise due diligence",
            support_score=0.8,
            reasons=["start"],
        ),
        CandidateBlock(
            block_id="b2",
            page=6,
            page_type="margin_title_like",
            routing_mode="margin_title_like",
            section_hint="part2",
            block_type="candidate_continuation",
            line_ids=["l3"],
            title_line_ids=[],
            body_line_ids=["l3"],
            line_decisions=[],
            body_text="throughout the charter service",
            support_score=0.6,
            reasons=["continuation"],
        ),
    ]

    clauses, diag = assemble_draft_clauses(blocks)
    assert len(clauses) == 1
    assert clauses[0].id == "part2:1"
    assert clauses[0].page_start == 5
    assert clauses[0].page_end == 6
    assert clauses[0].title == "Condition Of vessel"
    assert "throughout the charter service" in clauses[0].text
    assert diag["metrics"]["orphan_continuation_count"] == 0

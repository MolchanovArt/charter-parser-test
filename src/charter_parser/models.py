from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SpanRef(BaseModel):
    page: int = Field(ge=0)
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


class WordIR(BaseModel):
    word_id: str
    page: int = Field(ge=0)
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


class LineIR(BaseModel):
    line_id: str
    page: int = Field(ge=0)
    text: str
    bbox: tuple[float, float, float, float]
    word_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    segment_hint: str | None = None


class PageIR(BaseModel):
    page_index: int = Field(ge=0)
    width: float
    height: float
    words: list[WordIR] = Field(default_factory=list)
    lines: list[LineIR] = Field(default_factory=list)


class LayoutBand(BaseModel):
    name: str
    x0: float
    x1: float
    confidence: float = Field(ge=0.0, le=1.0)


class PageLayoutProfile(BaseModel):
    page_index: int = Field(ge=0)
    page_type: Literal["unknown", "mixed", "margin_title_like", "inline_title_like"] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bands: list[LayoutBand] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LayoutProfile(BaseModel):
    page_count: int = Field(ge=1)
    pages: list[PageLayoutProfile] = Field(default_factory=list)


class CandidateLineDecision(BaseModel):
    line_id: str
    raw_text: str
    extracted_text: str = ""
    labels: list[
        Literal[
            "title_line",
            "body_line",
            "noise_line",
            "candidate_clause_start",
            "candidate_continuation",
            "section_banner",
        ]
    ] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class CandidateBlock(BaseModel):
    block_id: str
    page: int = Field(ge=0)
    page_type: str = "unknown"
    routing_mode: str = "unknown"
    section_hint: str = "part2"
    block_type: Literal["candidate_clause_start", "candidate_continuation", "noise_block", "section_banner"] = "noise_block"
    line_ids: list[str] = Field(default_factory=list)
    title_line_ids: list[str] = Field(default_factory=list)
    body_line_ids: list[str] = Field(default_factory=list)
    noise_line_ids: list[str] = Field(default_factory=list)
    line_decisions: list[CandidateLineDecision] = Field(default_factory=list)
    candidate_clause_id: str | None = None
    candidate_local_num: int | None = Field(default=None, ge=1)
    title_text: str = ""
    body_text: str = ""
    support_score: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class BoundaryDecision(BaseModel):
    page: int = Field(ge=0)
    block_id: str
    decision: Literal["new_clause", "continue_previous", "noise", "section_banner"]
    display_id: str | None = None
    title_line_ids: list[str] = Field(default_factory=list)
    body_line_ids: list[str] = Field(default_factory=list)
    attach_to_previous: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason_short: str


class Clause(BaseModel):
    order: int = Field(ge=1)
    section: str
    local_num: int = Field(ge=1)
    id: str
    title: str = ""
    text: str = ""
    page_start: int = Field(ge=0)
    page_end: int = Field(ge=0)


class DraftClause(BaseModel):
    order: int = Field(ge=1)
    section: str
    local_num: int = Field(ge=1)
    id: str
    title: str = ""
    text: str = ""
    page_start: int = Field(ge=0)
    page_end: int = Field(ge=0)
    candidate_block_ids: list[str] = Field(default_factory=list)
    title_line_ids: list[str] = Field(default_factory=list)
    body_line_ids: list[str] = Field(default_factory=list)
    support_score: float = Field(default=0.0, ge=0.0, le=1.0)


class RunReport(BaseModel):
    run_id: str
    mode: str
    command: str = ""
    started_at: str = ""
    finished_at: str = ""
    pdf_path: str
    archived_report_path: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    artifact_provenance: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    freshness: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    run_id: str
    mode: str = "eval"
    command: str = ""
    started_at: str = ""
    finished_at: str = ""
    golden_path: str
    candidate_path: str
    archived_report_path: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    artifact_provenance: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    freshness: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

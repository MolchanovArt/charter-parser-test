from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ProjectConfig(BaseModel):
    pdf_path: str = "data/raw/voyage-charter-example.pdf"
    output_path: str = "artifacts/runs/latest/clauses.json"
    golden_path: str = "artifacts/golden/clauses_merged.json"


class LegacyConfig(BaseModel):
    part2_page_from: int = 5
    part2_page_to: int = 16
    riders_page_from: int = 17
    riders_page_to: int = 38


class ParsingConfig(BaseModel):
    infer_layout: bool = True
    infer_sections: bool = True
    prefer_margin_titles: bool = True
    drop_strikethrough: bool = True
    source_of_truth: str = "lines"
    line_group_y_tol: float = 2.5
    low_confidence_page_threshold: float = 0.55


class CandidateConfig(BaseModel):
    body_band_left_tolerance: float = 12.0
    right_noise_min_x_ratio: float = 0.83
    right_noise_max_chars: int = 4
    header_max_y_ratio: float = 0.10
    footer_min_y_ratio: float = 0.94
    title_continuation_max_gap: float = 26.0
    inline_title_max_chars: int = 90
    inline_title_upper_ratio: float = 0.55
    inline_title_max_commas: int = 1
    title_suspicious_max_chars: int = 140
    body_suspicious_short_chars: int = 12
    title_body_overlap_gap: float = 10.0


class LLMConfig(BaseModel):
    enabled: bool = True
    model_primary: str = os.getenv("OPENAI_MODEL_PRIMARY", "gpt-5.4")
    model_fast: str = os.getenv("OPENAI_MODEL_FAST", "gpt-5-mini")
    reasoning_effort_primary: str = "medium"
    reasoning_effort_fast: str = "low"
    use_structured_outputs: bool = True
    store: bool = False
    accept_confidence: float = 0.90
    adjudicate_confidence: float = 0.65


class VisionConfig(BaseModel):
    enabled: bool = True
    only_on_fallback: bool = True
    min_page_confidence: float = 0.50


class Settings(BaseModel):
    project: ProjectConfig = ProjectConfig()
    legacy: LegacyConfig = LegacyConfig()
    parsing: ParsingConfig = ParsingConfig()
    candidate: CandidateConfig = CandidateConfig()
    llm: LLMConfig = LLMConfig()
    vision: VisionConfig = VisionConfig()


def load_settings(path: str | Path = "configs/default.yaml") -> Settings:
    p = Path(path)
    if not p.exists():
        return Settings()
    data: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Settings(**data)

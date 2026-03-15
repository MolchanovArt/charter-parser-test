from __future__ import annotations

from typing import Any

from charter_parser.models import BoundaryDecision


def adjudicate_candidates(payload: dict[str, Any]) -> BoundaryDecision:
    raise NotImplementedError("Structured adjudication is intentionally deferred until after deterministic candidate generation exists.")

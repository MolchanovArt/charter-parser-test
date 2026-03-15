from __future__ import annotations

from collections import Counter
from pathlib import Path

from charter_parser.models import Clause
from charter_parser.schema_tools import validate_json_data
from charter_parser.utils import read_json


BANNER_TOKENS = [
    "shell additional clauses",
    "essar rider clauses",
]


def duplicate_ids(clauses: list[Clause]) -> list[str]:
    counts = Counter(c.id for c in clauses)
    return sorted([key for key, value in counts.items() if value > 1])


def empty_text_ids(clauses: list[Clause]) -> list[str]:
    return [c.id for c in clauses if not c.text.strip()]


def order_violations(clauses: list[Clause]) -> list[str]:
    problems: list[str] = []
    last = 0
    for clause in clauses:
        if clause.order <= last:
            problems.append(clause.id)
        last = clause.order
    return problems


def banner_leaks(clauses: list[Clause]) -> list[str]:
    leaks: list[str] = []
    for clause in clauses:
        payload = f"{clause.title} {clause.text}".lower()
        if any(token in payload for token in BANNER_TOKENS):
            leaks.append(clause.id)
    return leaks


def validate_clause_file(path: str | Path) -> dict:
    data = read_json(path)
    schema_errors = validate_json_data(data, "clauses.schema.json")
    clauses = [Clause(**item) for item in data] if not schema_errors else []
    return {
        "schema_errors": schema_errors,
        "count": len(clauses),
        "duplicate_ids": duplicate_ids(clauses) if clauses else [],
        "empty_text_ids": empty_text_ids(clauses) if clauses else [],
        "order_violations": order_violations(clauses) if clauses else [],
        "banner_leaks": banner_leaks(clauses) if clauses else [],
    }

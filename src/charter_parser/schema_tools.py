from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate_json_data(data, schema_name: str) -> list[str]:
    schema = load_schema(schema_name)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors]


def assert_json_data_valid(data, schema_name: str, *, label: str) -> None:
    errors = validate_json_data(data, schema_name)
    if errors:
        message = "; ".join(errors[:5])
        raise ValueError(f"{label} is not valid against {schema_name}: {message}")

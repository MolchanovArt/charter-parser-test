from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from charter_parser.schema_tools import assert_json_data_valid
from charter_parser.utils import sha256_file, write_json


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / "artifacts" / "runs"
LATEST_DIR = RUNS_ROOT / "latest"
LATEST_REPORTS_DIR = LATEST_DIR / "reports"
HISTORY_DIR = RUNS_ROOT / "history"


def _dump(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    raise TypeError(f"Unsupported report type: {type(obj)!r}")


def new_run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{mode}"


def repo_rel(path: str | Path) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def fingerprint(path: str | Path, *, role: str) -> dict[str, Any]:
    p = Path(path)
    stat = p.stat()
    return {
        "path": repo_rel(p),
        "role": role,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(p),
    }


def ensure_fresh_output(path: str | Path, upstream_paths: list[str | Path]) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Expected artifact was not produced: {p}")
    artifact_mtime = p.stat().st_mtime_ns
    stale_against: list[str] = []
    for upstream in upstream_paths:
        src = Path(upstream)
        if src.exists() and artifact_mtime < src.stat().st_mtime_ns:
            stale_against.append(repo_rel(src))
    if stale_against:
        raise RuntimeError(f"Artifact {repo_rel(p)} is stale relative to {', '.join(stale_against)}")
    return {"artifact": repo_rel(p), "stale_against": stale_against}


def load_latest_mode_report(mode: str) -> dict[str, Any]:
    path = LATEST_REPORTS_DIR / f"{mode}_latest.json"
    if not path.exists():
        raise FileNotFoundError(f"No latest {mode} report found at {repo_rel(path)}")
    return json.loads(path.read_text(encoding="utf-8"))


def assert_report_matches_artifact(
    *,
    mode: str,
    artifact_key: str,
    artifact_path: str | Path,
    input_keys: list[str] | None = None,
) -> dict[str, Any]:
    report = load_latest_mode_report(mode)
    provenance = report.get("artifact_provenance", {}).get(artifact_key)
    if provenance is None:
        raise RuntimeError(f"Latest {mode} report does not describe artifact '{artifact_key}'")

    current = fingerprint(artifact_path, role=provenance.get("role", "generated"))
    mismatches = []
    for field in ("path", "size", "mtime_ns", "sha256"):
        if provenance.get(field) != current.get(field):
            mismatches.append(field)
    if mismatches:
        raise RuntimeError(
            f"Artifact freshness check failed for {repo_rel(artifact_path)} against latest {mode} report; "
            f"mismatched fields: {', '.join(mismatches)}"
        )

    for key in input_keys or []:
        input_record = report.get("inputs", {}).get(key)
        if not input_record:
            raise RuntimeError(f"Latest {mode} report is missing input provenance for '{key}'")
        input_path = REPO_ROOT / input_record["path"]
        if not input_path.exists():
            raise FileNotFoundError(f"Recorded input for latest {mode} report is missing: {input_record['path']}")
        live = fingerprint(input_path, role=input_record.get("role", "input"))
        mismatches = []
        for field in ("path", "size", "mtime_ns", "sha256"):
            if input_record.get(field) != live.get(field):
                mismatches.append(field)
        if mismatches:
            raise RuntimeError(
                f"Input freshness check failed for latest {mode} report on {input_record['path']}; "
                f"mismatched fields: {', '.join(mismatches)}"
            )
    return report


def _update_latest_index(mode: str, latest_report_path: Path, archived_report_path: Path) -> None:
    index_path = LATEST_DIR / "run_report.json"
    index = {"latest_reports": {}}
    if index_path.exists():
        existing = json.loads(index_path.read_text(encoding="utf-8"))
        index["latest_reports"] = existing.get("latest_reports", {})
    index["latest_reports"][mode] = {
        "latest_path": repo_rel(latest_report_path),
        "archived_path": repo_rel(archived_report_path),
    }
    write_json(index_path, index)


def publish_run_report(mode: str, report: Any) -> Path:
    payload = _dump(report)
    run_id = payload["run_id"]
    archived_report_path = HISTORY_DIR / run_id / f"{mode}_report.json"
    latest_report_path = LATEST_REPORTS_DIR / f"{mode}_latest.json"
    payload["archived_report_path"] = repo_rel(archived_report_path)
    assert_json_data_valid(payload, "run_report.schema.json", label=f"{mode}_report.json")
    write_json(latest_report_path, payload)
    write_json(archived_report_path, payload)
    _update_latest_index(mode, latest_report_path, archived_report_path)
    return latest_report_path


def publish_eval_report(report: Any) -> Path:
    payload = _dump(report)
    run_id = payload["run_id"]
    archived_report_path = HISTORY_DIR / run_id / "eval_report.json"
    latest_report_path = LATEST_DIR / "eval_report.json"
    payload["archived_report_path"] = repo_rel(archived_report_path)
    assert_json_data_valid(payload, "eval_report.schema.json", label="eval_report.json")
    write_json(latest_report_path, payload)
    write_json(archived_report_path, payload)
    _update_latest_index("eval", latest_report_path, archived_report_path)
    return latest_report_path

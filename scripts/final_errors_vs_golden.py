from __future__ import annotations

import argparse
import json
from pathlib import Path


TARGET_CASE_IDS = [
    "essar:6",
    "shell:2",
    "shell:35",
    "part2:2",
    "part2:20",
    "part2:36",
    "part2:28",
    "shell:20",
    "shell:42",
    "part2:4",
    "part2:6",
]


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _clause_index(rows: list[dict]) -> dict[str, dict]:
    return {row["id"]: row for row in rows}


def _resolve_run_file(run_dir: Path, candidates: list[str]) -> Path:
    for name in candidates:
        path = run_dir / name
        if path.exists():
            return path
    raise FileNotFoundError(f"No matching file in {run_dir}: {candidates}")


def _case_snapshot(clause: dict | None) -> dict | None:
    if clause is None:
        return None
    return {
        "title": clause.get("title", ""),
        "text_preview": clause.get("text", "")[:200],
        "page_span": [clause.get("page_start"), clause.get("page_end")],
    }


def _run_summary(run_dir: Path, golden_by_id: dict[str, dict]) -> dict:
    clauses = _load_json(_resolve_run_file(run_dir, ["clauses_strike_fallback.json", "clauses_unified_adjudicated.json"]))
    report = _load_json(
        _resolve_run_file(run_dir, ["assembly_report_strike_fallback.json", "assembly_report_adjudicated.json"])
    )
    by_id = _clause_index(clauses)
    comparison = report["comparisons"]["vs_reference"]
    metrics = report["metrics"]
    return {
        "run_dir": str(run_dir),
        "metrics": {
            "clause_count": metrics["unified_clause_count"],
            "duplicate_ids": metrics["duplicate_ids"],
            "missing_ids": comparison["missing_ids"],
            "extra_ids": comparison["extra_ids"],
            "boundary_alignment_proxy": metrics["boundary_alignment_proxy_vs_reference"],
            "body_overlap_proxy": metrics["body_text_overlap_proxy_vs_reference"],
            "title_similarity": metrics["normalized_title_similarity_vs_reference"],
            "split_merge_proxy": metrics["split_merge_error_proxy"],
        },
        "target_cases": {
            case_id: {
                "present": case_id in by_id,
                "candidate": _case_snapshot(by_id.get(case_id)),
                "golden": _case_snapshot(golden_by_id.get(case_id)),
            }
            for case_id in TARGET_CASE_IDS
        },
    }


def _markdown(payload: dict) -> str:
    before = payload["before"]
    after = payload["after"]
    lines = [
        "# Final Errors Vs Golden",
        "",
        "## Metrics",
        "",
        "| Metric | Before | M7 | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    metric_order = [
        "clause_count",
        "boundary_alignment_proxy",
        "body_overlap_proxy",
        "title_similarity",
        "split_merge_proxy",
    ]
    for key in metric_order:
        left = before["metrics"][key]
        right = after["metrics"][key]
        delta = round(right - left, 4)
        lines.append(f"| {key} | {left} | {right} | {delta} |")
    lines.extend(
        [
            "",
            f"- before duplicate_ids: {before['metrics']['duplicate_ids']}",
            f"- before missing_ids: {before['metrics']['missing_ids']}",
            f"- before extra_ids: {before['metrics']['extra_ids']}",
            f"- m7 duplicate_ids: {after['metrics']['duplicate_ids']}",
            f"- m7 missing_ids: {after['metrics']['missing_ids']}",
            f"- m7 extra_ids: {after['metrics']['extra_ids']}",
            "",
            "## Target Cases",
            "",
            "| Clause | Before | M7 | Golden |",
            "| --- | --- | --- | --- |",
        ]
    )
    for case_id in TARGET_CASE_IDS:
        before_case = before["target_cases"][case_id]
        after_case = after["target_cases"][case_id]
        golden_case = before_case["golden"] or after_case["golden"]
        before_label = "present" if before_case["present"] else "missing"
        after_label = "present" if after_case["present"] else "missing"
        golden_label = "present" if golden_case else "missing"
        lines.append(f"| {case_id} | {before_label} | {after_label} | {golden_label} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-run", required=True)
    parser.add_argument("--after-run", required=True)
    parser.add_argument("--golden", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    golden = _load_json(Path(args.golden))
    golden_by_id = _clause_index(golden)
    payload = {
        "before": _run_summary(Path(args.before_run), golden_by_id),
        "after": _run_summary(Path(args.after_run), golden_by_id),
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_md.write_text(_markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()

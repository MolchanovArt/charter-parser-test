from __future__ import annotations

import argparse
from pathlib import Path

from rapidfuzz import fuzz

from charter_parser.models import Clause, EvalReport
from charter_parser.reporting import assert_report_matches_artifact, fingerprint, new_run_id, publish_eval_report
from charter_parser.utils import normalize_ws, read_json, utc_now_iso
from charter_parser.validators import banner_leaks, duplicate_ids, empty_text_ids, order_violations


def ratio(a: str, b: str) -> float:
    return round(fuzz.ratio(normalize_ws(a), normalize_ws(b)) / 100.0, 4)


def md_list(items) -> str:
    if not items:
        return "-"
    return ", ".join(str(x) for x in items)


def to_clauses(rows: list[dict]) -> list[Clause]:
    return [Clause(**item) for item in rows]


def build_report(golden_path: str, candidate_path: str) -> EvalReport:
    started_at = utc_now_iso()
    baseline_report = assert_report_matches_artifact(
        mode="baseline",
        artifact_key="clauses",
        artifact_path=candidate_path,
        input_keys=["pdf"],
    )
    golden_rows = read_json(golden_path)
    candidate_rows = read_json(candidate_path)

    golden = to_clauses(golden_rows)
    candidate = to_clauses(candidate_rows)

    golden_ids = [item.id for item in golden]
    candidate_ids = [item.id for item in candidate]
    golden_by_id = {item.id: item for item in golden}
    candidate_by_id = {item.id: item for item in candidate}

    overlap = [cid for cid in candidate_ids if cid in golden_by_id]
    title_exact = 0
    text_exact = 0
    text_sim: list[float] = []
    for cid in overlap:
        g = golden_by_id[cid]
        c = candidate_by_id[cid]
        if normalize_ws(g.title) == normalize_ws(c.title):
            title_exact += 1
        if normalize_ws(g.text) == normalize_ws(c.text):
            text_exact += 1
        text_sim.append(ratio(g.text, c.text))

    sequence_matches = sum(1 for g, c in zip(golden_ids, candidate_ids) if g == c)
    sequence_ratio = round(sequence_matches / max(1, min(len(golden_ids), len(candidate_ids))), 4)
    mean_text_sim = round(sum(text_sim) / max(1, len(text_sim)), 4)

    metrics = {
        "golden_count": len(golden),
        "candidate_count": len(candidate),
        "count_delta": len(candidate) - len(golden),
        "missing_ids": [gid for gid in golden_ids if gid not in candidate_by_id],
        "extra_ids": [cid for cid in candidate_ids if cid not in golden_by_id],
        "duplicate_ids": duplicate_ids(candidate),
        "id_sequence_match_ratio": sequence_ratio,
        "title_exact_match_ratio": round(title_exact / max(1, len(overlap)), 4),
        "text_exact_match_ratio": round(text_exact / max(1, len(overlap)), 4),
        "text_similarity_mean": mean_text_sim,
        "empty_text_ids": empty_text_ids(candidate),
        "order_violations": order_violations(candidate),
        "banner_leaks": banner_leaks(candidate),
    }
    return EvalReport(
        run_id=new_run_id("eval"),
        mode="eval",
        command=f"python scripts/eval_against_reference.py --golden {golden_path} --candidate {candidate_path}",
        started_at=started_at,
        finished_at=utc_now_iso(),
        golden_path=golden_path,
        candidate_path=candidate_path,
        inputs={
            "golden": fingerprint(golden_path, role="reference"),
            "candidate_pdf": baseline_report["inputs"]["pdf"],
            "baseline_report": {
                "path": baseline_report["archived_report_path"],
                "role": "derived",
                "run_id": baseline_report["run_id"],
            },
        },
        artifact_provenance={
            "candidate": baseline_report["artifact_provenance"]["clauses"],
            "golden": fingerprint(golden_path, role="reference"),
        },
        metrics=metrics,
        freshness={
            "status": "fresh",
            "consumed_mode": "baseline",
            "consumed_run_id": baseline_report["run_id"],
        },
        notes=[],
    )


def write_markdown(path: str | Path, report: EvalReport) -> None:
    m = report.metrics
    text = f"""# Eval report

Golden: `{report.golden_path}`
Candidate: `{report.candidate_path}`

## Metrics

- golden_count: {m['golden_count']}
- candidate_count: {m['candidate_count']}
- count_delta: {m['count_delta']}
- id_sequence_match_ratio: {m['id_sequence_match_ratio']}
- title_exact_match_ratio: {m['title_exact_match_ratio']}
- text_exact_match_ratio: {m['text_exact_match_ratio']}
- text_similarity_mean: {m['text_similarity_mean']}

## Issues

- missing_ids: {md_list(m['missing_ids'])}
- extra_ids: {md_list(m['extra_ids'])}
- duplicate_ids: {md_list(m['duplicate_ids'])}
- empty_text_ids: {md_list(m['empty_text_ids'])}
- order_violations: {md_list(m['order_violations'])}
- banner_leaks: {md_list(m['banner_leaks'])}
"""
    Path(path).write_text(text, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--candidate", required=True)
    args = ap.parse_args()

    report = build_report(args.golden, args.candidate)
    out_json = publish_eval_report(report)
    out_md = Path("artifacts/runs/latest/eval_report.md")
    write_markdown(out_md, report)
    print(out_json.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

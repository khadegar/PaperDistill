from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .common import project_path, read_json, read_jsonl, utc_now, write_json, write_jsonl
from .evaluation import load_manual_scores, median_or_zero


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def aggregate_scores(config: dict[str, Any], run_label: str = "main") -> dict[str, Any]:
    score_path = project_path(config, "scores", f"automatic-{run_label}.jsonl")
    rows = list(read_jsonl(score_path))
    manual = load_manual_scores(config)
    adjudication_path = project_path(config, "scores", "boundary-adjudication.jsonl")
    adjudications = (
        {
            (str(row.get("tool")), str(row.get("sample_id"))): row
            for row in read_jsonl(adjudication_path)
        }
        if adjudication_path.is_file()
        else {}
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["tool"]].append(row)
    run_index_path = project_path(config, "runs", "index.jsonl")
    run_index = list(read_jsonl(run_index_path)) if run_index_path.is_file() else []
    fallback_attempts = {
        (str(row.get("tool")), str(row.get("sample_id")))
        for row in run_index
        if row.get("run_label") == run_label and row.get("mode") == "fallback"
    }
    fallback_successes = {
        (str(row.get("tool")), str(row.get("sample_id")))
        for row in run_index
        if row.get("run_label") == run_label
        and row.get("mode") == "fallback"
        and row.get("status") == "success"
    }
    summaries: list[dict[str, Any]] = []
    minimum_successes = int(config["selection_policy"]["minimum_clean_successes"])
    allowed_truncations = int(config["selection_policy"]["allow_silent_truncations"])
    for tool in config["tools"]:
        tool_rows = grouped.get(tool, [])
        successful = [row for row in tool_rows if row.get("run_status") == "success"]
        manual_values = [manual[(tool, row["sample_id"])] for row in tool_rows if (tool, row["sample_id"]) in manual]
        automatic = _mean([float(row.get("automatic_score") or 0) for row in tool_rows])
        manual_score = _mean(manual_values) if manual_values else None
        total_fidelity = automatic + (manual_score if manual_score is not None else 0.0)
        def effective_truncation(row: dict[str, Any]) -> bool:
            adjudicated = adjudications.get((tool, str(row.get("sample_id"))))
            if adjudicated is not None and "silent_truncation_confirmed" in adjudicated:
                return bool(adjudicated["silent_truncation_confirmed"])
            return bool(row.get("silent_truncation"))

        truncations = sum(effective_truncation(row) for row in tool_rows)
        identifier_risks = sum(bool(row.get("identifier_integrity_risk")) for row in tool_rows)
        clean = sum(
            row.get("run_status") == "success" and not effective_truncation(row)
            for row in tool_rows
        )
        systematic_identifier_alteration = bool(
            identifier_risks >= 5
            or median_or_zero((row.get("identifiers") or {}).get("recall", 0) for row in successful) < 0.75
        )
        eligible = bool(
            clean >= minimum_successes
            and truncations <= allowed_truncations
            and not systematic_identifier_alteration
            and len(tool_rows) == 50
        )
        blockers: list[str] = []
        if len(tool_rows) != 50:
            blockers.append(f"primary score rows {len(tool_rows)}/50")
        if clean < minimum_successes:
            blockers.append(f"clean completions {clean}/{minimum_successes} required")
        if truncations > allowed_truncations:
            blockers.append(f"silent truncations {truncations}/{allowed_truncations} allowed")
        if systematic_identifier_alteration:
            blockers.append("systematic scientific-identifier integrity risk")
        stratum_summaries: list[dict[str, Any]] = []
        for stratum in config["sampling"]["strata"]:
            stratum_rows = [row for row in tool_rows if row.get("benchmark_stratum") == stratum]
            stratum_success = [row for row in stratum_rows if row.get("run_status") == "success"]
            stratum_summaries.append(
                {
                    "stratum": stratum,
                    "rows": len(stratum_rows),
                    "process_successes": len(stratum_success),
                    "clean_successes": sum(bool(row.get("clean_success")) for row in stratum_rows),
                    "automatic_fidelity_0_80": round(
                        _mean([float(row.get("automatic_score") or 0) for row in stratum_rows]), 4
                    ),
                    "median_pages_per_second": round(
                        median_or_zero(row.get("pages_per_second", 0) for row in stratum_success), 6
                    ),
                }
            )
        cold_durations = [
            float(row.get("duration_seconds") or 0)
            for row in successful
            if row.get("timing_phase") == "cold_start"
        ]
        steady_durations = [
            float(row.get("duration_seconds") or 0)
            for row in successful
            if row.get("timing_phase") == "steady_state"
        ]
        summaries.append(
            {
                "tool": tool,
                "version": config["tool_versions"][tool],
                "score_rows": len(tool_rows),
                "process_successes": len(successful),
                "clean_successes": clean,
                "fallback_attempts": sum(key[0] == tool for key in fallback_attempts),
                "fallback_successes": sum(key[0] == tool for key in fallback_successes),
                "silent_truncations": truncations,
                "identifier_integrity_risks": identifier_risks,
                "systematic_identifier_alteration": systematic_identifier_alteration,
                "automatic_fidelity_0_80": round(automatic, 4),
                "manual_fidelity_0_20": round(manual_score, 4) if manual_score is not None else None,
                "manual_scores_present": len(manual_values),
                "total_fidelity_0_100": round(total_fidelity, 4) if manual_score is not None else None,
                "median_pages_per_second": round(median_or_zero(row.get("pages_per_second", 0) for row in successful), 6),
                "median_peak_vram_mib": round(median_or_zero(row.get("peak_vram_mib", 0) for row in successful), 2),
                "median_peak_ram_gib": round(median_or_zero(float(row.get("peak_ram_bytes", 0)) / (1024**3) for row in successful), 3),
                "median_cold_start_seconds": round(median_or_zero(cold_durations), 3),
                "median_steady_state_seconds": round(median_or_zero(steady_durations), 3),
                "strata": stratum_summaries,
                "gate_blockers": blockers,
                "eligible": eligible,
            }
        )
    eligible = [row for row in summaries if row["eligible"]]
    manual_complete = all(row["manual_scores_present"] == 15 for row in summaries)
    if eligible and manual_complete:
        winner = sorted(
            eligible,
            key=lambda row: (
                -float(row["total_fidelity_0_100"] or 0),
                -int(row["clean_successes"]),
                -float(row["median_pages_per_second"]),
                row["tool"],
            ),
        )[0]["tool"]
        recommendation_status = "SELECTED"
    elif eligible:
        winner = None
        recommendation_status = "MANUAL_REVIEW_PENDING"
    else:
        winner = None
        recommendation_status = "NO_TOOL_PASSED_GATE"
    ranked_observed = sorted(
        summaries,
        key=lambda row: (
            -float(
                row["total_fidelity_0_100"]
                if manual_complete and row["total_fidelity_0_100"] is not None
                else row["automatic_fidelity_0_80"]
            ),
            -int(row["clean_successes"]),
            -float(row["median_pages_per_second"]),
            row["tool"],
        ),
    )
    return {
        "schema_version": "1.0",
        "benchmark_id": config["benchmark_id"],
        "run_label": run_label,
        "generated_at": utc_now(),
        "tools": summaries,
        "manual_review_complete": manual_complete,
        "recommendation_status": recommendation_status,
        "winner": winner,
        "best_observed_candidate": ranked_observed[0]["tool"] if ranked_observed else None,
    }


def render_report(config: dict[str, Any], run_label: str = "main") -> dict[str, Any]:
    root = Path(config["_project_root"])
    aggregate = aggregate_scores(config, run_label)
    rows = list(read_jsonl(project_path(config, "scores", f"automatic-{run_label}.jsonl")))
    manifest = list(read_jsonl(project_path(config, "data", "manifest.jsonl")))
    selection_summary_path = project_path(config, "data", "selection-summary.json")
    selection_summary = read_json(selection_summary_path) if selection_summary_path.is_file() else {}
    manifest_validation_path = project_path(config, "data", "manifest-validation.json")
    manifest_validation = read_json(manifest_validation_path) if manifest_validation_path.is_file() else {}
    bundle_summary_path = project_path(config, "offline", "bundle-summary.json")
    bundle_summary = read_json(bundle_summary_path) if bundle_summary_path.is_file() else {}
    run_index_path = project_path(config, "runs", "index.jsonl")
    run_index = list(read_jsonl(run_index_path)) if run_index_path.is_file() else []
    run_bundle_hashes = sorted(
        {
            str(row.get("bundle_manifest_sha256"))
            for row in run_index
            if row.get("run_label") == run_label and row.get("bundle_manifest_sha256")
        }
    )
    repeatability_path = project_path(config, "reports", "repeatability.json")
    repeatability = read_json(repeatability_path) if repeatability_path.is_file() else None
    stratum_counts = Counter(row["benchmark_stratum"] for row in manifest)
    worst = sorted(rows, key=lambda row: (float(row.get("automatic_score") or 0), row["tool"], row["sample_id"]))[:10]
    lines = [
        "# A100 PDF-to-Markdown Benchmark Report",
        "",
        f"- Benchmark: `{config['benchmark_id']}`",
        f"- Generated: `{aggregate['generated_at']}`",
        f"- Host: `{config['remote']['host']}`",
        f"- GPU gate: average utilization ≤ {config['remote']['maximum_average_gpu_utilization_percent']}%, free VRAM ≥ {config['remote']['minimum_free_vram_mib']} MiB",
        f"- Sample: {len(manifest)} exact DOI-matched Zotero PDF ↔ PMC/JATS pairs",
        f"- Pages: {sum(int(row.get('page_count') or 0) for row in manifest)}",
        f"- Fixed manifest SHA-256: `{manifest_validation.get('manifest_sha256', 'unknown')}`",
        f"- Offline bundle manifest SHA-256: `{bundle_summary.get('manifest_sha256', 'unknown')}`",
        f"- Bundle SHA-256 values recorded by `{run_label}` runs: "
        + (", ".join(f"`{value}`" for value in run_bundle_hashes) if run_bundle_hashes else "none"),
        f"- Zotero snapshot: {selection_summary.get('existing_zotero_pdfs', 'unknown')} existing PDFs; "
        f"{selection_summary.get('exact_pdf_doi_attachment_matches', 'unknown')} PDF-internal DOI matches; "
        f"{selection_summary.get('eligible_unique_doi_pdf_candidates', 'unknown')} eligible unique candidates",
        "",
        "## Sample composition",
        "",
    ]
    for stratum in config["sampling"]["strata"]:
        lines.append(f"- `{stratum}`: {stratum_counts[stratum]}")
    lines.extend(
        [
            "",
            "## Reproducibility and execution contract",
            "",
            "- Windows Server, Python 3.11, one document at a time, eight CPU threads.",
            f"- Every run starts only after a {config['remote']['preflight_duration_seconds']}-second GPU gate and is sampled every {config['remote']['monitor_interval_seconds']} seconds.",
            f"- Per-document timeout: {config['remote']['per_document_timeout_seconds'] // 60} minutes; one tool-specific OCR fallback is retained separately after a primary failure.",
            "- Inherited external-API credentials are removed and non-local HTTP(S) traffic is routed to a closed loopback endpoint.",
            "- Raw output trees and Markdown files are hashed before evaluation normalization.",
            "- MinerU 3.4.4: local `hybrid-engine/high` primary; local pipeline fallback.",
            "- Marker 2.0.0: local Surya GGUF with CUDA llama.cpp, `balanced` primary; `force_ocr` fallback.",
            "- Docling 2.114.0: local GraniteDocling VLM/CUDA primary; local standard CUDA OCR/table fallback.",
            "- Official references: [MinerU](https://github.com/opendatalab/MinerU), [Marker](https://github.com/datalab-to/marker), [Docling](https://docling-project.github.io/docling/).",
            "",
            "## Scoring contract",
            "",
            "Automatic fidelity contributes 80 points: body token/character fidelity 25, scientific identifiers 15, headings/reading order 10, tables 15, formulas 10, and figure captions 5.",
            "The blinded review contributes 20 points across reading order (5), tables (5), formulas (4), captions (3), and overall completeness (3). Speed is excluded from fidelity and used only after fidelity and success rate.",
            "A recommendation requires at least 48 clean primary completions, zero silent truncations, and no systematic scientific-identifier integrity risk.",
            "",
            "## Tool comparison",
            "",
            "| Tool | Version | Primary success | Clean / 50 | Fallback success | ID risks | Trunc. | Auto / 80 | Manual / 20 | Total / 100 | Median pages/s | Peak VRAM MiB | Peak RAM GiB | Gate |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in aggregate["tools"]:
        manual = "pending" if row["manual_fidelity_0_20"] is None else f"{row['manual_fidelity_0_20']:.2f}"
        total = "pending" if row["total_fidelity_0_100"] is None else f"{row['total_fidelity_0_100']:.2f}"
        lines.append(
            f"| {row['tool']} | {row['version']} | {row['process_successes']} | {row['clean_successes']} | "
            f"{row['fallback_successes']}/{row['fallback_attempts']} | {row['identifier_integrity_risks']} | "
            f"{row['silent_truncations']} | {row['automatic_fidelity_0_80']:.2f} | {manual} | {total} | "
            f"{row['median_pages_per_second']:.4f} | {row['median_peak_vram_mib']:.0f} | "
            f"{row['median_peak_ram_gib']:.2f} | {'PASS' if row['eligible'] else 'FAIL'} |"
        )
    lines.extend(["", "## Stratified automatic fidelity", ""])
    lines.extend(
        [
            "| Tool | Stratum | Clean / 10 | Auto / 80 | Median pages/s |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in aggregate["tools"]:
        for stratum in row["strata"]:
            lines.append(
                f"| {row['tool']} | {stratum['stratum']} | {stratum['clean_successes']} | "
                f"{stratum['automatic_fidelity_0_80']:.2f} | {stratum['median_pages_per_second']:.4f} |"
            )
    lines.extend(["", "## Recommendation", ""])
    if aggregate["winner"]:
        lines.append(f"**Selected parser: `{aggregate['winner']}`.** It passed all gates and ranked first by fidelity, then clean success and throughput.")
    elif aggregate["recommendation_status"] == "MANUAL_REVIEW_PENDING":
        lines.append("Automatic evaluation is complete, but the blinded 15-paper manual review is still pending; final selection is intentionally withheld.")
    else:
        candidate = aggregate.get("best_observed_candidate") or "none"
        lines.append(
            f"No parser passed the production gate. The best observed candidate was `{candidate}`, "
            "but it is not recommended for production until the blocking gates below are resolved."
        )
    lines.extend(["", "### Gate blockers", ""])
    for row in aggregate["tools"]:
        blockers = "; ".join(row["gate_blockers"]) if row["gate_blockers"] else "none"
        lines.append(f"- `{row['tool']}`: {blockers}")
    if repeatability:
        lines.extend(
            [
                "",
                "## Five-paper repeatability",
                "",
                "| Tool | Both successful / 5 | Exact Markdown | Median token F1 | Max score Δ | Stable |",
                "|---|---:|---:|---:|---:|---|",
            ]
        )
        for row in repeatability.get("tools", []):
            lines.append(
                f"| {row['tool']} | {row['both_successful']} | {row['exact_markdown_matches']} | "
                f"{row['median_normalized_token_f1']:.4f} | {row['maximum_automatic_score_delta']:.3f} | "
                f"{'PASS' if row['stable'] else 'REVIEW'} |"
            )
    lines.extend(
        [
            "",
            "## Lowest-scoring conversions",
            "",
            "| Sample | Tool | Stratum | Auto score | Failure / boundary |",
            "|---|---|---|---:|---|",
        ]
    )
    for row in worst:
        boundary = row.get("failure_reason") or ("silent truncation" if row.get("silent_truncation") else "low fidelity")
        lines.append(f"| {row['sample_id']} | {row['tool']} | {row['benchmark_stratum']} | {float(row.get('automatic_score') or 0):.2f} | {boundary} |")
    error_cases: list[dict[str, Any]] = []
    for row in rows:
        reasons: list[str] = []
        if row.get("run_status") != "success":
            reasons.append("primary failure")
        if row.get("silent_truncation"):
            reasons.append("silent truncation")
        if row.get("identifier_integrity_risk"):
            reasons.append("scientific identifier drift")
        for key, label in (("tables", "table loss"), ("formulas", "formula damage"), ("figures", "caption loss")):
            metric = row.get(key) or {}
            if metric.get("reference_count", 0) and float(metric.get("score") or 0) < 0.5:
                reasons.append(label)
        if reasons:
            error_cases.append(
                {
                    "sample_id": row["sample_id"],
                    "tool": row["tool"],
                    "stratum": row["benchmark_stratum"],
                    "automatic_score": row.get("automatic_score", 0),
                    "reasons": reasons,
                    "markdown_relpath": row.get("markdown_relpath", ""),
                }
            )
    write_jsonl(project_path(config, "reports", "typical-error-cases.jsonl"), error_cases)
    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "JATS is an exact-DOI textual reference, not a pixel-identical representation of the publisher PDF; equations, tables, captions, and reference formatting may differ by publication version. The automatic score therefore measures recoverable scholarly content against JATS, while the blinded PDF review covers page reading order and placement-sensitive defects.",
            "A primary process exit is not treated as semantic success: truncation and identifier gates remain separate. Conversely, a single missing identifier does not count as a process failure; only repeated risks can trigger the systematic-integrity gate.",
            "",
            "## Integrity statement",
            "",
            "This benchmark is isolated from the 10,000-paper production corpus and semantic-card workflow. Raw parser outputs are hashed before normalization; no benchmark result updates the writing Skill or corpus state.",
            "",
        ]
    )
    report_path = project_path(config, "reports", "benchmark-report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    aggregate["report_path"] = str(report_path)
    aggregate["report_sha256"] = __import__("hashlib").sha256(report_path.read_bytes()).hexdigest()
    write_json(project_path(config, "reports", "benchmark-summary.json"), aggregate)
    return aggregate

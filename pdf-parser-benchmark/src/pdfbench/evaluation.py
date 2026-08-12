from __future__ import annotations

import csv
import gzip
import html
import math
import re
import shutil
import statistics
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .common import (
    normalize_text,
    project_path,
    read_jsonl,
    sha256_file,
    sha256_text,
    utc_now,
    write_csv,
    write_json,
    write_jsonl,
)


TOKEN_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z0-9]+)*|\d+(?:\.\d+)?(?:e[+-]?\d+)?|[^\W\s]", re.I | re.U)
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
NUMBER_UNIT_RE = re.compile(
    r"(?<!\w)[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*(?:±|\+/-)\s*\d+(?:\.\d+)?)?\s*"
    r"(?:%|mm(?:\^?[23]|²|³)?|cm(?:\^?[23]|²|³)?|µm|μm|um|nm|mpa|gpa|kpa|pa|"
    r"kn(?:[·*]?mm|/mm)?|mn|n(?:[·*]?mm|/mm)?|hz|khz|mhz|s|min|h|day|days|week|weeks|"
    r"month|months|year|years|°c|°|kg|mg|µg|μg|ug|g|ml|µl|μl|ul|m)\b",
    re.I,
)
CITATION_RE = re.compile(
    r"(?:\[(?:\d+(?:\s*[-–—,;]\s*\d+)*)\]|"
    r"\([A-Z][A-Za-z-]+(?:\s+(?:(?:and|&)\s+[A-Z][A-Za-z-]+|et\s+al\.))?,?\s+\d{4}[a-z]?\s*\))"
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_child_text(element: ET.Element, name: str) -> str:
    for child in element:
        if _local_name(child.tag) == name:
            return normalize_text(" ".join(child.itertext()))
    return ""


def _element_text(element: ET.Element) -> str:
    return normalize_text(" ".join(element.itertext()))


def extract_jats(jats_path: Path) -> dict[str, Any]:
    with gzip.open(jats_path, "rb") as handle:
        root = ET.parse(handle).getroot()
    body = next((element for element in root.iter() if _local_name(element.tag) == "body"), None)
    article_title_element = next(
        (element for element in root.iter() if _local_name(element.tag) == "article-title"), None
    )
    article_title = _element_text(article_title_element) if article_title_element is not None else ""
    abstracts = [
        _element_text(element)
        for element in root.iter()
        if _local_name(element.tag) == "abstract" and _element_text(element)
    ]
    body_descendants = {id(element) for element in body.iter()} if body is not None else set()
    back_matter: list[str] = []
    backs = [element for element in root.iter() if _local_name(element.tag) == "back"]
    if backs:
        back_matter.extend(_element_text(element) for element in backs if _element_text(element))
    else:
        back_matter.extend(
            _element_text(element)
            for element in root.iter()
            if _local_name(element.tag) == "ref-list"
            and id(element) not in body_descendants
            and _element_text(element)
        )
    body_text = normalize_text(
        " ".join(
            value
            for value in [article_title, *abstracts, _element_text(body) if body is not None else "", *back_matter]
            if value
        )
    )
    headings: list[str] = []
    if article_title:
        headings.append(article_title)
    if abstracts:
        headings.append("Abstract")
    if body is not None:
        for section in body.iter():
            if _local_name(section.tag) == "sec":
                heading = _direct_child_text(section, "title")
                if heading:
                    headings.append(heading)
    for back in backs:
        for section in back.iter():
            if _local_name(section.tag) == "sec":
                heading = _direct_child_text(section, "title")
                if heading:
                    headings.append(heading)
    ref_lists = [element for element in root.iter() if _local_name(element.tag) == "ref-list"]
    if ref_lists and not any(heading.casefold() in {"reference", "references"} for heading in headings):
        reference_heading = _direct_child_text(ref_lists[0], "title") or "References"
        headings.append(reference_heading)
    tables: list[str] = []
    formulas: list[str] = []
    captions: list[str] = []
    for element in root.iter():
        name = _local_name(element.tag)
        if name == "table-wrap":
            cells = [
                _element_text(child)
                for child in element.iter()
                if _local_name(child.tag) in {"th", "td"} and _element_text(child)
            ]
            value = " | ".join(cells) or _element_text(element)
            if value:
                tables.append(value)
        elif name in {"disp-formula", "inline-formula"}:
            value = _element_text(element)
            if value:
                formulas.append(value)
        elif name == "fig":
            caption = next((child for child in element.iter() if _local_name(child.tag) == "caption"), None)
            value = _element_text(caption) if caption is not None else ""
            if value:
                captions.append(value)
    return {
        "body_text": body_text,
        "headings": headings,
        "tables": tables,
        "formulas": formulas,
        "captions": captions,
    }


def extract_markdown(markdown_path: Path) -> dict[str, Any]:
    raw = markdown_path.read_text(encoding="utf-8", errors="replace")
    headings = []
    for line in raw.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append(normalize_text(match.group(1)))
    tables: list[str] = []
    current: list[str] = []
    for line in raw.splitlines() + [""]:
        if line.count("|") >= 2:
            current.append(line)
        elif current:
            if len(current) >= 2:
                tables.append(normalize_text(" ".join(current)))
            current = []
    for match in re.finditer(r"<table\b[^>]*>(.*?)</table>", raw, re.I | re.S):
        value = re.sub(r"<[^>]+>", " ", match.group(1))
        tables.append(normalize_text(html.unescape(value)))
    formula_patterns = [
        r"\$\$(.+?)\$\$",
        r"\\\[(.+?)\\\]",
        r"\\\((.+?)\\\)",
        r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)",
    ]
    formulas: list[str] = []
    for pattern in formula_patterns:
        formulas.extend(normalize_text(match.group(1)) for match in re.finditer(pattern, raw, re.S) if normalize_text(match.group(1)))
    captions = [
        normalize_text(line)
        for line in raw.splitlines()
        if re.match(r"^\s*(?:#{1,6}\s*)?(?:fig(?:ure)?\.?\s*\d+|scheme\s*\d+)", line, re.I)
    ]
    text_without_markup = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", raw)
    text_without_markup = re.sub(r"<[^>]+>", " ", text_without_markup)
    text_without_markup = re.sub(r"^\s{0,3}#{1,6}\s+", "", text_without_markup, flags=re.M)
    return {
        "raw": raw,
        "body_text": normalize_text(text_without_markup),
        "headings": headings,
        "tables": tables,
        "formulas": formulas,
        "captions": captions,
    }


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(normalize_text(text))]


def counter_f1(reference: Iterable[str], prediction: Iterable[str]) -> dict[str, float]:
    reference_counter = Counter(reference)
    prediction_counter = Counter(prediction)
    overlap = sum((reference_counter & prediction_counter).values())
    reference_total = sum(reference_counter.values())
    prediction_total = sum(prediction_counter.values())
    recall = overlap / reference_total if reference_total else (1.0 if not prediction_total else 0.0)
    precision = overlap / prediction_total if prediction_total else (1.0 if not reference_total else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def ngrams(text: str, size: int) -> list[str]:
    compact = re.sub(r"\s+", " ", normalize_text(text).lower())
    if len(compact) < size:
        return [compact] if compact else []
    return [compact[index : index + size] for index in range(len(compact) - size + 1)]


def _best_match_scores(reference_items: list[str], predicted_items: list[str], mode: str = "tokens") -> tuple[float, list[float]]:
    if not reference_items:
        return (1.0 if not predicted_items else max(0.0, 1.0 - 0.25 * len(predicted_items))), []
    if not predicted_items:
        return 0.0, [0.0] * len(reference_items)
    scores: list[float] = []
    for reference in reference_items:
        if mode == "chars":
            candidate_scores = [counter_f1(ngrams(reference, 2), ngrams(prediction, 2))["f1"] for prediction in predicted_items]
        else:
            candidate_scores = [counter_f1(tokenize(reference), tokenize(prediction))["f1"] for prediction in predicted_items]
        scores.append(max(candidate_scores or [0.0]))
    count_ratio = min(len(reference_items), len(predicted_items)) / max(len(reference_items), len(predicted_items), 1)
    return statistics.fmean(scores) * (0.75 + 0.25 * count_ratio), scores


def heading_metrics(reference: list[str], prediction: list[str]) -> dict[str, float]:
    if not reference:
        return {"f1": 1.0 if not prediction else 0.75, "order": 1.0}
    matches: list[tuple[int, int, float]] = []
    used: set[int] = set()
    for ref_index, heading in enumerate(reference):
        candidates = []
        for pred_index, predicted in enumerate(prediction):
            if pred_index in used:
                continue
            score = counter_f1(tokenize(heading), tokenize(predicted))["f1"]
            candidates.append((score, pred_index))
        score, pred_index = max(candidates, default=(0.0, -1))
        if score >= 0.55 and pred_index >= 0:
            used.add(pred_index)
            matches.append((ref_index, pred_index, score))
    precision = len(matches) / len(prediction) if prediction else 0.0
    recall = len(matches) / len(reference)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    ordered = [pred_index for _, pred_index, _ in sorted(matches)]
    if len(ordered) < 2:
        order = 1.0 if len(reference) <= 1 and matches else 0.0
    else:
        pairs = 0
        in_order = 0
        for left in range(len(ordered)):
            for right in range(left + 1, len(ordered)):
                pairs += 1
                in_order += ordered[left] < ordered[right]
        order = in_order / pairs if pairs else 0.0
    return {"f1": f1, "order": order}


def reading_order_metric(reference_text: str, prediction_text: str, maximum_anchors: int = 40) -> dict[str, float]:
    sentences = [
        normalize_text(value)
        for value in re.split(r"(?<=[.!?])\s+", reference_text)
        if len(tokenize(value)) >= 12
    ]
    if not sentences:
        return {"coverage": 1.0, "order": 1.0, "score": 1.0}
    if len(sentences) > maximum_anchors:
        indices = sorted({round(index * (len(sentences) - 1) / (maximum_anchors - 1)) for index in range(maximum_anchors)})
        sentences = [sentences[index] for index in indices]
    prediction_normalized = normalize_text(prediction_text).lower()
    positions: list[int] = []
    for sentence in sentences:
        tokens = tokenize(sentence)
        anchor = " ".join(tokens[: min(10, len(tokens))])
        positions.append(prediction_normalized.find(anchor))
    found = [position for position in positions if position >= 0]
    coverage = len(found) / len(positions)
    pairs = 0
    in_order = 0
    for left in range(len(positions)):
        if positions[left] < 0:
            continue
        for right in range(left + 1, len(positions)):
            if positions[right] < 0:
                continue
            pairs += 1
            in_order += positions[left] < positions[right]
    order = in_order / pairs if pairs else (1.0 if len(found) == 1 else 0.0)
    return {"coverage": coverage, "order": order, "score": coverage * order}


def extract_identifier_groups(text: str) -> dict[str, list[str]]:
    return {
        "doi": [match.group(0).lower().rstrip(".,;:)]}") for match in DOI_RE.finditer(text)],
        "number_unit": [
            re.sub(r"\s+", "", match.group(0).lower()).replace("μ", "µ")
            for match in NUMBER_UNIT_RE.finditer(text)
        ],
        "citation": [re.sub(r"\s+", "", match.group(0).lower()) for match in CITATION_RE.finditer(text)],
    }


def extract_identifiers(text: str) -> list[str]:
    groups = extract_identifier_groups(text)
    return [value for kind in ("doi", "number_unit", "citation") for value in groups[kind]]


def _truncated_doi_pairs(reference_dois: Iterable[str], prediction_dois: Iterable[str]) -> list[dict[str, str]]:
    reference = set(reference_dois)
    prediction = set(prediction_dois)
    pairs: list[dict[str, str]] = []
    for predicted in sorted(prediction - reference):
        if len(predicted) < 8:
            continue
        for expected in sorted(reference - prediction):
            if predicted.startswith("10.") and (expected.startswith(predicted) or predicted.startswith(expected)):
                pairs.append({"reference": expected, "prediction": predicted})
                break
    return pairs


def score_document(reference: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    reference_tokens = tokenize(reference["body_text"])
    prediction_tokens = tokenize(prediction["body_text"])
    token_metrics = counter_f1(reference_tokens, prediction_tokens)
    character_metrics = counter_f1(ngrams(reference["body_text"], 3), ngrams(prediction["body_text"], 3))
    reference_identifier_groups = extract_identifier_groups(reference["body_text"])
    prediction_identifier_groups = extract_identifier_groups(prediction["body_text"])
    identifier_metrics_by_kind = {
        kind: counter_f1(reference_identifier_groups[kind], prediction_identifier_groups[kind])
        for kind in reference_identifier_groups
    }
    identifier_metrics = counter_f1(
        extract_identifiers(reference["body_text"]), extract_identifiers(prediction["body_text"])
    )
    truncated_doi_pairs = _truncated_doi_pairs(
        reference_identifier_groups["doi"], prediction_identifier_groups["doi"]
    )
    headings = heading_metrics(reference["headings"], prediction["headings"])
    reading_order = reading_order_metric(reference["body_text"], prediction["body_text"])
    tables, table_items = _best_match_scores(reference["tables"], prediction["tables"])
    formulas, formula_items = _best_match_scores(reference["formulas"], prediction["formulas"], mode="chars")
    figures, figure_items = _best_match_scores(reference["captions"], prediction["captions"])
    points = {
        "body_text": 15.0 * token_metrics["f1"] + 10.0 * character_metrics["f1"],
        "scientific_identifiers": 15.0 * identifier_metrics["f1"],
        "heading_and_order": 5.0 * headings["f1"] + 2.5 * headings["order"] + 2.5 * reading_order["score"],
        "tables": 15.0 * tables,
        "formulas": 10.0 * formulas,
        "figure_captions": 5.0 * figures,
    }
    tail_tokens = tokenize(reference["body_text"])[-40:]
    tail_recall = counter_f1(tail_tokens, prediction_tokens)["recall"] if tail_tokens else 1.0
    length_ratio = len(prediction_tokens) / max(len(reference_tokens), 1)
    silent_truncation = bool(
        not prediction_tokens
        or length_ratio < 0.75
        or (tail_recall < 0.25 and token_metrics["recall"] < 0.85)
    )
    sufficiently_populated_category_risk = any(
        len(reference_identifier_groups[kind]) >= 4 and metrics["recall"] < 0.75
        for kind, metrics in identifier_metrics_by_kind.items()
    )
    identifier_risk = bool(
        (extract_identifiers(reference["body_text"]) and identifier_metrics["recall"] < 0.75)
        or sufficiently_populated_category_risk
        or truncated_doi_pairs
    )
    return {
        "automatic_score": round(sum(points.values()), 6),
        "component_points": {key: round(value, 6) for key, value in points.items()},
        "body_token": token_metrics,
        "character_trigram": character_metrics,
        "identifiers": identifier_metrics,
        "identifiers_by_kind": identifier_metrics_by_kind,
        "truncated_doi_pairs": truncated_doi_pairs,
        "headings": headings,
        "reading_order": reading_order,
        "tables": {"score": tables, "reference_count": len(reference["tables"]), "prediction_count": len(prediction["tables"]), "item_scores": table_items},
        "formulas": {"score": formulas, "reference_count": len(reference["formulas"]), "prediction_count": len(prediction["formulas"]), "item_scores": formula_items},
        "figures": {"score": figures, "reference_count": len(reference["captions"]), "prediction_count": len(prediction["captions"]), "item_scores": figure_items},
        "reference_token_count": len(reference_tokens),
        "prediction_token_count": len(prediction_tokens),
        "prediction_reference_length_ratio": length_ratio,
        "tail_token_recall": tail_recall,
        "silent_truncation": silent_truncation,
        "identifier_integrity_risk": identifier_risk,
    }


def latest_primary_runs(config: dict[str, Any], run_label: str = "main") -> dict[tuple[str, str], dict[str, Any]]:
    index_path = project_path(config, "runs", "index.jsonl")
    if not index_path.is_file():
        return {}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_jsonl(index_path):
        if row.get("mode") != "primary" or row.get("run_label") != run_label:
            continue
        key = (str(row.get("tool")), str(row.get("sample_id")))
        if key not in latest or str(row.get("started_at")) > str(latest[key].get("started_at")):
            latest[key] = row
    return latest


def evaluate_runs(
    config: dict[str, Any], run_label: str = "main", sample_set: str = "full"
) -> dict[str, Any]:
    root = Path(config["_project_root"])
    manifest = list(read_jsonl(project_path(config, "data", "manifest.jsonl")))
    if sample_set == "pilot":
        manifest = [row for row in manifest if row.get("pilot")]
    elif sample_set == "repeat":
        seed = str(config["selection_policy"]["manual_blind_seed"])
        manifest = sorted(manifest, key=lambda row: sha256_text(seed + row["sample_id"]))[:5]
    elif sample_set != "full":
        raise ValueError(f"Unsupported evaluation sample set: {sample_set}")
    latest = latest_primary_runs(config, run_label)
    rows: list[dict[str, Any]] = []
    for tool in config["tools"]:
        for paper in manifest:
            run = latest.get((tool, paper["sample_id"]))
            base = {
                "schema_version": "1.0",
                "sample_id": paper["sample_id"],
                "paper_id": paper["paper_id"],
                "pmcid": paper["pmcid"],
                "doi": paper["doi"],
                "benchmark_stratum": paper["benchmark_stratum"],
                "tool": tool,
                "tool_version": config["tool_versions"][tool],
                "run_label": run_label,
            }
            if not run or run.get("status") != "success" or not run.get("markdown_relpath"):
                rows.append(
                    {
                        **base,
                        "run_status": run.get("status") if run else "missing",
                        "automatic_score": 0.0,
                        "clean_success": False,
                        "silent_truncation": False,
                        "identifier_integrity_risk": False,
                        "failure_reason": run.get("failure_reason") if run else "run_missing",
                    }
                )
                continue
            markdown_path = root / run["markdown_relpath"]
            jats_path = root / paper["jats_relpath"]
            try:
                reference = extract_jats(jats_path)
                prediction = extract_markdown(markdown_path)
                metrics = score_document(reference, prediction)
                rows.append(
                    {
                        **base,
                        "run_status": run["status"],
                        "failure_reason": "",
                        "duration_seconds": run.get("duration_seconds", 0),
                        "timing_phase": run.get("timing_phase", ""),
                        "pages_per_second": run.get("pages_per_second", 0),
                        "peak_vram_mib": run.get("peak_vram_mib", 0),
                        "peak_ram_bytes": run.get("peak_ram_bytes", 0),
                        "markdown_relpath": run["markdown_relpath"],
                        "markdown_sha256": run["markdown_sha256"],
                        **metrics,
                        "clean_success": not metrics["silent_truncation"],
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        **base,
                        "run_status": "evaluation_failed",
                        "automatic_score": 0.0,
                        "clean_success": False,
                        "silent_truncation": False,
                        "identifier_integrity_risk": False,
                        "failure_reason": f"evaluation_error: {exc}",
                    }
                )
    scores_path = project_path(config, "scores", f"automatic-{run_label}.jsonl")
    write_jsonl(scores_path, rows)
    flat_rows: list[dict[str, Any]] = []
    for row in rows:
        flat_rows.append(
            {
                "sample_id": row["sample_id"],
                "tool": row["tool"],
                "stratum": row["benchmark_stratum"],
                "run_status": row["run_status"],
                "clean_success": row["clean_success"],
                "automatic_score": row["automatic_score"],
                "silent_truncation": row["silent_truncation"],
                "identifier_integrity_risk": row["identifier_integrity_risk"],
                "body_token_f1": (row.get("body_token") or {}).get("f1"),
                "identifier_f1": (row.get("identifiers") or {}).get("f1"),
                "heading_f1": (row.get("headings") or {}).get("f1"),
                "reading_order": (row.get("reading_order") or {}).get("score"),
                "table_score": (row.get("tables") or {}).get("score"),
                "formula_score": (row.get("formulas") or {}).get("score"),
                "figure_score": (row.get("figures") or {}).get("score"),
                "pages_per_second": row.get("pages_per_second"),
                "failure_reason": row.get("failure_reason"),
            }
        )
    write_csv(project_path(config, "scores", f"automatic-{run_label}.csv"), flat_rows)
    result = {
        "status": "COMPLETE",
        "run_label": run_label,
        "sample_set": sample_set,
        "papers": len(manifest),
        "tools": list(config["tools"]),
        "score_rows": len(rows),
        "scores_path": str(scores_path),
        "completed_at": utc_now(),
    }
    write_json(project_path(config, "scores", f"evaluation-{run_label}.json"), result)
    return result


def summarize_repeatability_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for tool in sorted({str(row["tool"]) for row in rows}):
        tool_rows = [row for row in rows if row["tool"] == tool]
        successful = [row for row in tool_rows if row.get("both_successful")]
        exact = sum(bool(row.get("exact_markdown_match")) for row in tool_rows)
        score_deltas = [float(row["automatic_score_delta"]) for row in successful if row.get("automatic_score_delta") is not None]
        token_scores = [float(row["normalized_token_f1"]) for row in successful]
        summaries.append(
            {
                "tool": tool,
                "requested": len(tool_rows),
                "both_successful": len(successful),
                "exact_markdown_matches": exact,
                "median_normalized_token_f1": round(median_or_zero(token_scores), 6),
                "median_automatic_score_delta": round(median_or_zero(score_deltas), 6),
                "maximum_automatic_score_delta": round(max(score_deltas or [0.0]), 6),
                "stable": bool(
                    len(tool_rows) == 5
                    and len(successful) == 5
                    and median_or_zero(token_scores) >= 0.99
                    and max(score_deltas or [0.0]) <= 0.5
                ),
            }
        )
    return summaries


def assess_repeatability(
    config: dict[str, Any], baseline_label: str = "main", repeat_label: str = "repeat"
) -> dict[str, Any]:
    root = Path(config["_project_root"])
    manifest = list(read_jsonl(project_path(config, "data", "manifest.jsonl")))
    seed = str(config["selection_policy"]["manual_blind_seed"])
    repeat_papers = sorted(manifest, key=lambda row: sha256_text(seed + row["sample_id"]))[:5]
    baseline_runs = latest_primary_runs(config, baseline_label)
    repeat_runs = latest_primary_runs(config, repeat_label)

    def score_index(label: str) -> dict[tuple[str, str], dict[str, Any]]:
        path = project_path(config, "scores", f"automatic-{label}.jsonl")
        if not path.is_file():
            raise FileNotFoundError(f"Evaluate run label '{label}' before repeatability analysis: {path}")
        return {(str(row["tool"]), str(row["sample_id"])): row for row in read_jsonl(path)}

    baseline_scores = score_index(baseline_label)
    repeat_scores = score_index(repeat_label)
    rows: list[dict[str, Any]] = []
    for tool in config["tools"]:
        for paper in repeat_papers:
            key = (tool, paper["sample_id"])
            baseline = baseline_runs.get(key)
            repeated = repeat_runs.get(key)
            baseline_ok = bool(baseline and baseline.get("status") == "success" and baseline.get("markdown_relpath"))
            repeat_ok = bool(repeated and repeated.get("status") == "success" and repeated.get("markdown_relpath"))
            both = baseline_ok and repeat_ok
            token_f1 = 0.0
            exact_match = False
            if both:
                baseline_path = root / str(baseline["markdown_relpath"])
                repeat_path = root / str(repeated["markdown_relpath"])
                if baseline_path.is_file() and repeat_path.is_file():
                    baseline_hash = sha256_file(baseline_path)
                    repeat_hash = sha256_file(repeat_path)
                    exact_match = baseline_hash == repeat_hash
                    baseline_text = extract_markdown(baseline_path)["body_text"]
                    repeat_text = extract_markdown(repeat_path)["body_text"]
                    token_f1 = counter_f1(tokenize(baseline_text), tokenize(repeat_text))["f1"]
                else:
                    both = False
            baseline_score = baseline_scores.get(key, {}).get("automatic_score")
            repeat_score = repeat_scores.get(key, {}).get("automatic_score")
            score_delta = (
                abs(float(baseline_score) - float(repeat_score))
                if baseline_score is not None and repeat_score is not None
                else None
            )
            rows.append(
                {
                    "schema_version": "1.0",
                    "tool": tool,
                    "sample_id": paper["sample_id"],
                    "benchmark_stratum": paper["benchmark_stratum"],
                    "baseline_label": baseline_label,
                    "repeat_label": repeat_label,
                    "baseline_status": baseline.get("status") if baseline else "missing",
                    "repeat_status": repeated.get("status") if repeated else "missing",
                    "both_successful": both,
                    "exact_markdown_match": exact_match,
                    "normalized_token_f1": round(token_f1, 6),
                    "baseline_automatic_score": baseline_score,
                    "repeat_automatic_score": repeat_score,
                    "automatic_score_delta": round(score_delta, 6) if score_delta is not None else None,
                    "baseline_markdown_sha256": baseline.get("markdown_sha256", "") if baseline else "",
                    "repeat_markdown_sha256": repeated.get("markdown_sha256", "") if repeated else "",
                }
            )
    summaries = summarize_repeatability_rows(rows)
    details_path = project_path(config, "scores", "repeatability.jsonl")
    write_jsonl(details_path, rows)
    result = {
        "schema_version": "1.0",
        "status": "COMPLETE",
        "baseline_label": baseline_label,
        "repeat_label": repeat_label,
        "sample_ids": [paper["sample_id"] for paper in repeat_papers],
        "rows": len(rows),
        "tools": summaries,
        "completed_at": utc_now(),
        "details_path": details_path.relative_to(root).as_posix(),
    }
    write_json(project_path(config, "reports", "repeatability.json"), result)
    return result


def build_blind_package(config: dict[str, Any], run_label: str = "main", overwrite: bool = False) -> dict[str, Any]:
    root = Path(config["_project_root"])
    manifest = [row for row in read_jsonl(project_path(config, "data", "manifest.jsonl")) if row.get("manual_review")]
    latest = latest_primary_runs(config, run_label)
    destination = project_path(config, "manual-review")
    files_dir = destination / "files"
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"Manual-review package exists; pass --overwrite-blind-package: {destination}")
    files_dir.mkdir(parents=True, exist_ok=True)
    seed = str(config["selection_policy"]["manual_blind_seed"])
    mapping_rows: list[dict[str, Any]] = []
    template_rows: list[dict[str, Any]] = []
    for paper in sorted(manifest, key=lambda row: row["sample_id"]):
        ordered_tools = sorted(config["tools"], key=lambda tool: sha256_text(f"{seed}|{paper['sample_id']}|{tool}"))
        for index, tool in enumerate(ordered_tools):
            label = chr(ord("A") + index)
            blind_id = f"{paper['sample_id']}-{label}"
            run = latest.get((tool, paper["sample_id"]))
            source = root / run["markdown_relpath"] if run and run.get("markdown_relpath") else None
            target = files_dir / f"{blind_id}.md"
            if source and source.is_file():
                shutil.copy2(source, target)
                output_hash = sha256_file(target)
                status = "available"
            else:
                target.write_text("# Conversion unavailable\n", encoding="utf-8")
                output_hash = sha256_file(target)
                status = "missing"
            mapping_rows.append(
                {
                    "blind_id": blind_id,
                    "sample_id": paper["sample_id"],
                    "tool": tool,
                    "source_markdown_relpath": run.get("markdown_relpath") if run else "",
                    "blind_markdown_relpath": target.relative_to(root).as_posix(),
                    "blind_markdown_sha256": output_hash,
                    "status": status,
                }
            )
            template_rows.append(
                {
                    "blind_id": blind_id,
                    "sample_id": paper["sample_id"],
                    "stratum": paper["benchmark_stratum"],
                    "reading_order_0_5": "",
                    "tables_0_5": "",
                    "formulas_0_4": "",
                    "captions_0_3": "",
                    "completeness_0_3": "",
                    "notes": "",
                }
            )
    write_jsonl(destination / "blind-map.jsonl", mapping_rows)
    write_csv(destination / "manual-scores.csv", template_rows)
    readme = (
        "# Blinded manual review\n\n"
        "Score each Markdown file without opening `blind-map.jsonl`. Compare with the corresponding staged PDF. "
        "Allowed ranges are encoded in the CSV headers; the maximum is 20 points. Do not edit blind IDs.\n"
    )
    (destination / "README.md").write_text(readme, encoding="utf-8")
    result = {"status": "COMPLETE", "papers": len(manifest), "outputs": len(mapping_rows), "path": str(destination)}
    write_json(destination / "package.json", result)
    return result


def load_manual_scores(config: dict[str, Any]) -> dict[tuple[str, str], float]:
    path = project_path(config, "manual-review", "manual-scores.csv")
    mapping_path = project_path(config, "manual-review", "blind-map.jsonl")
    if not path.is_file() or not mapping_path.is_file():
        return {}
    mapping = {row["blind_id"]: row for row in read_jsonl(mapping_path)}
    scores: dict[tuple[str, str], float] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("blind_id") not in mapping:
                continue
            fields = [
                ("reading_order_0_5", 5),
                ("tables_0_5", 5),
                ("formulas_0_4", 4),
                ("captions_0_3", 3),
                ("completeness_0_3", 3),
            ]
            if any(str(row.get(name, "")).strip() == "" for name, _ in fields):
                continue
            values = []
            valid = True
            for name, maximum in fields:
                try:
                    value = float(row[name])
                except (TypeError, ValueError):
                    valid = False
                    break
                if value < 0 or value > maximum:
                    valid = False
                    break
                values.append(value)
            if valid:
                mapped = mapping[row["blind_id"]]
                scores[(mapped["tool"], mapped["sample_id"])] = sum(values)
    return scores


def median_or_zero(values: Iterable[float]) -> float:
    items = [float(value) for value in values]
    return statistics.median(items) if items else 0.0

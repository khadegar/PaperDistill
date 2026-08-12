#!/usr/bin/env python3
"""Validate corpus, claim, bibliography, and manuscript cross-references."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from _common import ACCESS_LEVELS, normalize_doi, normalize_title, read_json, read_jsonl


PROJECT_FIELDS = {
    "schema_version",
    "title",
    "research_question",
    "manuscript_mode",
    "analysis_language",
    "manuscript_language",
    "domains",
    "created_at",
    "updated_at",
}
PROJECT_MODES = {"original", "narrative_review", "systematic_review", "evidence_brief"}
DOMAINS = {
    "biomechanics",
    "bone_implant",
    "topology_optimization",
    "porous_scaffold",
    "additive_manufacturing",
    "mechanobiology",
    "osseointegration",
    "patient_specific_implant",
    "bone_regeneration_biomaterial",
}
CLAIM_TYPES = {"background", "method", "result", "comparison", "mechanism", "gap", "limitation", "recommendation"}
CLAIM_DIRECTIONS = {"supports", "refutes", "mixed", "context"}
CLAIM_DIRECTNESS = {"direct", "indirect", "extrapolated"}
CLAIM_CONFIDENCE = {"high", "moderate", "low", "uncertain"}
CLAIM_STATUS = {"verified", "unverified", "stale", "conflict"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args()


def issue(issues: list[dict[str, Any]], severity: str, code: str, message: str, location: str | None = None) -> None:
    value: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if location:
        value["location"] = location
    issues.append(value)


def has_locator(claim: dict[str, Any]) -> bool:
    locator = claim.get("locator")
    return isinstance(locator, dict) and any(value not in (None, "", []) for value in locator.values())


def find_bib_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return {match.strip() for match in re.findall(r"(?m)^@\w+\{([^,\s]+)\s*,", text)}


def find_tex_links(path: Path) -> tuple[set[str], set[str], list[int]]:
    if not path.exists():
        return set(), set(), []
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    citekeys: set[str] = set()
    claim_ids: set[str] = set()
    unlinked_lines: list[int] = []
    cite_pattern = re.compile(r"\\cite\w*(?:\[[^\]]*\]){0,2}\{([^}]+)\}")
    claim_pattern = re.compile(r"%\s*CLAIMS\s*:\s*([A-Za-z0-9_, -]+)", re.IGNORECASE)
    for index, line in enumerate(lines):
        citations = cite_pattern.findall(line)
        if citations:
            for group in citations:
                citekeys.update(key.strip() for key in group.split(",") if key.strip())
            window = "\n".join(lines[index : index + 3])
            matches = claim_pattern.findall(window)
            if not matches:
                unlinked_lines.append(index + 1)
            for group in matches:
                claim_ids.update(value.strip() for value in group.split(",") if value.strip())
        else:
            for group in claim_pattern.findall(line):
                claim_ids.update(value.strip() for value in group.split(",") if value.strip())
    return citekeys, claim_ids, unlinked_lines


def manifest_identity(row: dict[str, Any]) -> str:
    doi = normalize_doi(row.get("doi"))
    if doi:
        return "doi:" + doi
    if row.get("pmid"):
        return "pmid:" + str(row["pmid"]).strip()
    if row.get("arxiv_id"):
        return "arxiv:" + str(row["arxiv_id"]).strip().casefold()
    return "title:" + normalize_title(row.get("title")) + ":" + str(row.get("year") or "")


def main() -> int:
    args = parse_args()
    project_root = args.project.resolve()
    issues: list[dict[str, Any]] = []

    required_files = (
        "project.json",
        "corpus/manifest.jsonl",
        "evidence/claims.jsonl",
        "manuscript/main.tex",
        "manuscript/references.bib",
    )
    for relative in required_files:
        if not (project_root / relative).exists():
            issue(issues, "BLOCKING", "MISSING_FILE", f"Required file is missing: {relative}", relative)

    try:
        project = read_json(project_root / "project.json")
    except Exception as exc:
        issue(issues, "BLOCKING", "PROJECT_JSON", str(exc), "project.json")
        project = {}

    missing_project = sorted(PROJECT_FIELDS - set(project))
    if missing_project:
        issue(issues, "BLOCKING", "PROJECT_FIELDS", f"Missing project fields: {', '.join(missing_project)}", "project.json")
    if project.get("manuscript_mode") not in PROJECT_MODES:
        issue(issues, "BLOCKING", "PROJECT_MODE", f"Invalid manuscript_mode: {project.get('manuscript_mode')!r}", "project.json")
    unknown_domains = sorted(set(project.get("domains") or []) - DOMAINS)
    if unknown_domains:
        issue(issues, "BLOCKING", "PROJECT_DOMAINS", f"Unknown domains: {', '.join(unknown_domains)}", "project.json")
    if not str(project.get("research_question") or "").strip():
        issue(issues, "WARN", "EMPTY_RQ", "Research question has not been finalized", "project.json")

    try:
        manifest = read_jsonl(project_root / "corpus/manifest.jsonl")
    except Exception as exc:
        issue(issues, "BLOCKING", "MANIFEST_JSONL", str(exc), "corpus/manifest.jsonl")
        manifest = []

    identities = [manifest_identity(row) for row in manifest]
    duplicate_identities = sorted(key for key, count in Counter(identities).items() if key and count > 1)
    if duplicate_identities:
        issue(issues, "BLOCKING", "DUPLICATE_PAPERS", f"Duplicate logical papers: {', '.join(duplicate_identities[:10])}", "corpus/manifest.jsonl")

    manifest_ids: set[str] = set()
    manifest_citekeys: set[str] = set()
    for index, row in enumerate(manifest, 1):
        location = f"corpus/manifest.jsonl:{index}"
        paper_id = str(row.get("paper_id") or "").strip()
        if not paper_id:
            issue(issues, "BLOCKING", "MANIFEST_PAPER_ID", "paper_id is required", location)
        else:
            manifest_ids.add(paper_id)
        access = row.get("access_level")
        if access not in ACCESS_LEVELS:
            issue(issues, "BLOCKING", "MANIFEST_ACCESS", f"Invalid access_level: {access!r}", location)
        citekey = str(row.get("citekey") or "").strip()
        if citekey:
            if citekey in manifest_citekeys:
                issue(issues, "WARN", "DUPLICATE_CITEKEY", f"citekey appears more than once: {citekey}", location)
            manifest_citekeys.add(citekey)

    records: list[tuple[Path, dict[str, Any]]] = []
    records_dir = project_root / "corpus/records"
    if records_dir.is_dir():
        for path in sorted(records_dir.glob("*.json")):
            try:
                records.append((path, read_json(path)))
            except Exception as exc:
                issue(issues, "BLOCKING", "RECORD_JSON", str(exc), str(path.relative_to(project_root)))

    record_ids: set[str] = set()
    record_citekeys: set[str] = set()
    for path, record in records:
        location = str(path.relative_to(project_root))
        paper_id = str(record.get("paper_id") or "").strip()
        if not paper_id:
            issue(issues, "BLOCKING", "RECORD_PAPER_ID", "paper_id is required", location)
        else:
            record_ids.add(paper_id)
        bibliography = record.get("bibliography") or {}
        if not str(bibliography.get("title") or "").strip():
            issue(issues, "BLOCKING", "RECORD_TITLE", "bibliography.title is required", location)
        citekey = str(bibliography.get("citekey") or "").strip()
        if not citekey:
            issue(issues, "BLOCKING", "RECORD_CITEKEY", "bibliography.citekey is required", location)
        else:
            record_citekeys.add(citekey)
        provenance = record.get("provenance") or {}
        access = provenance.get("access_level")
        if access not in ACCESS_LEVELS:
            issue(issues, "BLOCKING", "RECORD_ACCESS", f"Invalid provenance.access_level: {access!r}", location)
        if access in {"full_text_read", "supplement_read"} and not provenance.get("source_hash"):
            issue(issues, "WARN", "MISSING_SOURCE_HASH", "Full-text record has no source_hash", location)

    included_without_record = sorted(
        str(row.get("paper_id"))
        for row in manifest
        if row.get("screening_decision") == "include" and row.get("paper_id") not in record_ids
    )
    if included_without_record:
        issue(issues, "WARN", "UNDISTILLED_INCLUDED", f"Included papers without records: {len(included_without_record)}", "corpus/records")

    try:
        claims = read_jsonl(project_root / "evidence/claims.jsonl")
    except Exception as exc:
        issue(issues, "BLOCKING", "CLAIMS_JSONL", str(exc), "evidence/claims.jsonl")
        claims = []

    claim_ids: set[str] = set()
    claim_by_id: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(claims, 1):
        location = f"evidence/claims.jsonl:{index}"
        claim_id = str(claim.get("claim_id") or "").strip()
        if not claim_id:
            issue(issues, "BLOCKING", "CLAIM_ID", "claim_id is required", location)
        elif claim_id in claim_ids:
            issue(issues, "BLOCKING", "DUPLICATE_CLAIM", f"Duplicate claim_id: {claim_id}", location)
        else:
            claim_ids.add(claim_id)
            claim_by_id[claim_id] = claim
        if claim.get("claim_type") not in CLAIM_TYPES:
            issue(issues, "BLOCKING", "CLAIM_TYPE", f"Invalid claim_type: {claim.get('claim_type')!r}", location)
        if claim.get("direction") not in CLAIM_DIRECTIONS:
            issue(issues, "BLOCKING", "CLAIM_DIRECTION", f"Invalid direction: {claim.get('direction')!r}", location)
        if claim.get("directness") not in CLAIM_DIRECTNESS:
            issue(issues, "BLOCKING", "CLAIM_DIRECTNESS", f"Invalid directness: {claim.get('directness')!r}", location)
        if claim.get("confidence") not in CLAIM_CONFIDENCE:
            issue(issues, "BLOCKING", "CLAIM_CONFIDENCE", f"Invalid confidence: {claim.get('confidence')!r}", location)
        if claim.get("verification_status") not in CLAIM_STATUS:
            issue(issues, "BLOCKING", "CLAIM_STATUS", f"Invalid verification_status: {claim.get('verification_status')!r}", location)
        if claim.get("access_level") not in ACCESS_LEVELS:
            issue(issues, "BLOCKING", "CLAIM_ACCESS", f"Invalid access_level: {claim.get('access_level')!r}", location)
        paper_id = str(claim.get("paper_id") or "").strip()
        if paper_id not in manifest_ids | record_ids:
            issue(issues, "BLOCKING", "CLAIM_PAPER", f"Unknown paper_id: {paper_id}", location)
        if claim.get("claim_type") in {"result", "comparison"}:
            if claim.get("access_level") not in {"full_text_read", "supplement_read"}:
                issue(issues, "BLOCKING", "RESULT_ACCESS", "Result/comparison claim requires full text or supplement", location)
            if not has_locator(claim):
                issue(issues, "BLOCKING", "RESULT_LOCATOR", "Result/comparison claim requires a locator", location)

    bib_path = project_root / "manuscript/references.bib"
    tex_path = project_root / "manuscript/main.tex"
    bib_keys = find_bib_keys(bib_path)
    tex_citekeys, tex_claim_ids, unlinked_lines = find_tex_links(tex_path)

    for citekey in sorted(tex_citekeys - bib_keys):
        issue(issues, "BLOCKING", "ORPHAN_CITATION", f"LaTeX citekey is missing from BibTeX: {citekey}", "manuscript/main.tex")
    for claim_id in sorted(tex_claim_ids - claim_ids):
        issue(issues, "BLOCKING", "ORPHAN_CLAIM_LINK", f"LaTeX claim link is missing from claims.jsonl: {claim_id}", "manuscript/main.tex")
    for line_number in unlinked_lines:
        issue(issues, "BLOCKING", "CITATION_WITHOUT_CLAIM", "Citation lacks a nearby % CLAIMS link", f"manuscript/main.tex:{line_number}")

    for claim_id in sorted(tex_claim_ids & claim_ids):
        claim = claim_by_id[claim_id]
        citekey = str(claim.get("citekey") or "").strip()
        if not citekey or citekey not in bib_keys:
            issue(issues, "BLOCKING", "CLAIM_BIB", f"Used claim {claim_id} has no resolvable BibTeX citekey: {citekey!r}", "evidence/claims.jsonl")
        if claim.get("verification_status") != "verified":
            issue(issues, "BLOCKING", "UNVERIFIED_USED_CLAIM", f"Used claim {claim_id} is not verified", "evidence/claims.jsonl")

    severity_counts = Counter(item["severity"] for item in issues)
    report = {
        "project": str(project_root),
        "verdict": "FAIL" if severity_counts["BLOCKING"] else ("PASS_WITH_WARNINGS" if severity_counts["WARN"] else "PASS"),
        "counts": {
            "manifest_records": len(manifest),
            "paper_records": len(records),
            "claims": len(claims),
            "bib_entries": len(bib_keys),
            "tex_citations": len(tex_citekeys),
            "tex_claim_links": len(tex_claim_ids),
            "blocking": severity_counts["BLOCKING"],
            "warnings": severity_counts["WARN"],
        },
        "issues": issues,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['verdict']}: {project_root}")
        for item in issues:
            suffix = f" [{item['location']}]" if item.get("location") else ""
            print(f"- {item['severity']} {item['code']}: {item['message']}{suffix}")
        print(json.dumps(report["counts"], ensure_ascii=False))
    return 1 if severity_counts["BLOCKING"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

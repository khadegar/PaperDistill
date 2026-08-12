#!/usr/bin/env python3
"""Build, resume, index, and query a large biomechanics full-text corpus.

The production corpus is stored outside the Skill directory. Discovery uses
Europe PMC metadata, and full-text acquisition is limited to open-access JATS
XML exposed by Europe PMC. The workflow is checkpointed and resumable.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from _common import normalize_doi, normalize_title, read_jsonl, utc_now, write_json, write_jsonl


EUROPE_PMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest"
DEFAULT_PROFILE = Path(__file__).resolve().parent.parent / "assets" / "corpus-profile-10k.json"
WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)?")
SPACE_RE = re.compile(r"\s+")
SECTION_TYPES = {
    "abstract": "abstract",
    "background": "introduction",
    "introduction": "introduction",
    "methods": "methods",
    "method": "methods",
    "methodology": "methods",
    "materials and methods": "methods",
    "materials & methods": "methods",
    "methods and materials": "methods",
    "patients and methods": "methods",
    "subjects and methods": "methods",
    "experimental": "methods",
    "experimental section": "methods",
    "experimental procedures": "methods",
    "results": "results",
    "results and discussion": "results_discussion",
    "results & discussion": "results_discussion",
    "results and discussions": "results_discussion",
    "discussion": "discussion",
    "discussion and conclusions": "discussion",
    "limitations": "limitations",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "concluding remarks": "conclusion",
    "summary and conclusions": "conclusion",
    "references": "references",
    "bibliography": "references",
    "literature cited": "references",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="Corpus profile JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    estimate = subparsers.add_parser("estimate", help="Report current hit counts for every configured stratum")
    estimate.add_argument("--timeout", type=int, default=60)

    discover = subparsers.add_parser("discover", help="Create a deduplicated target manifest")
    discover.add_argument("--root", required=True, type=Path, help="External corpus directory")
    discover.add_argument("--target", type=int, help="Override target unique paper count")
    discover.add_argument("--page-size", type=int, default=1000)
    discover.add_argument("--timeout", type=int, default=60)

    fetch = subparsers.add_parser("fetch", help="Fetch and section open full-text JATS XML; resumable")
    fetch.add_argument("--root", required=True, type=Path)
    fetch.add_argument("--limit", type=int, help="Maximum new or reparsed records; omit for all")
    fetch.add_argument("--requests-per-second", type=float, help="Override profile rate")
    fetch.add_argument("--timeout", type=int, help="Override profile timeout")
    fetch.add_argument("--max-retries", type=int, help="Override profile retry count")
    fetch.add_argument("--workers", type=int, help="Concurrent requests while preserving the global start rate")
    fetch.add_argument(
        "--reparse-existing",
        action="store_true",
        help="Rebuild parsed records from existing raw XML without downloading it again",
    )
    fetch.add_argument("--progress-every", type=int, default=25)

    index = subparsers.add_parser("index", help="Rebuild the SQLite/FTS section index")
    index.add_argument("--root", required=True, type=Path)

    stats = subparsers.add_parser("stats", help="Report corpus acquisition and indexing statistics")
    stats.add_argument("--root", required=True, type=Path)
    stats.add_argument("--json", action="store_true", help="Emit JSON only")

    audit = subparsers.add_parser("audit", help="Audit manifest uniqueness, parsed records, and index reconciliation")
    audit.add_argument("--root", required=True, type=Path)

    replace = subparsers.add_parser("replace-failures", help="Replace permanent fetch failures from the reserve pool")
    replace.add_argument("--root", required=True, type=Path)
    replace.add_argument("--max-replacements", type=int, default=100)
    replace.add_argument(
        "--pmcid",
        action="append",
        help="Explicit failed or content-invalid PMCID to replace; repeatable",
    )
    replace.add_argument(
        "--reason",
        help="Reason recorded for explicit replacements (for example, zero-content retraction notice)",
    )

    query = subparsers.add_parser("query", help="Search indexed full-text sections")
    query.add_argument("--root", required=True, type=Path)
    query.add_argument("--query", required=True)
    query.add_argument("--top", type=int, default=20)
    query.add_argument("--section", action="append", help="Restrict to section type; repeatable")
    return parser.parse_args()


def load_profile(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    with resolved.open("r", encoding="utf-8-sig") as stream:
        profile = json.load(stream)
    if not isinstance(profile, dict) or not isinstance(profile.get("strata"), list):
        raise ValueError(f"Invalid corpus profile: {resolved}")
    return profile


def user_agent() -> str:
    email = os.environ.get("EUROPEPMC_EMAIL", "").strip()
    suffix = f"; mailto:{email}" if email else ""
    return f"biomechanics-fulltext-corpus/1.0 ({suffix.lstrip('; ') or 'research corpus builder'})"


def http_get(url: str, timeout: int, retries: int = 3) -> bytes:
    headers = {
        "Accept": "application/json, application/xml;q=0.9, */*;q=0.1",
        "Accept-Encoding": "gzip",
        "User-Agent": user_agent(),
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding", "").casefold() == "gzip":
                    payload = gzip.decompress(payload)
                return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 30)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt >= retries:
                raise
            delay = min(2**attempt, 30)
        time.sleep(delay)
    raise RuntimeError(f"Request failed: {url}: {last_error}")


def search_url(query: str, page_size: int, cursor: str | None = None) -> str:
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": str(page_size),
    }
    if cursor:
        params["cursorMark"] = cursor
    return f"{EUROPE_PMC_API}/search?{urllib.parse.urlencode(params)}"


def search_page(query: str, page_size: int, cursor: str | None, timeout: int) -> dict[str, Any]:
    payload = http_get(search_url(query, page_size, cursor), timeout=timeout)
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Europe PMC returned a non-object search response")
    return value


def compact_text(value: Any) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def aliases_for(record: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    doi = normalize_doi(record.get("doi"))
    if doi:
        aliases.append(f"doi:{doi}")
    if record.get("pmcid"):
        aliases.append(f"pmcid:{str(record['pmcid']).upper()}")
    if record.get("pmid"):
        aliases.append(f"pmid:{record['pmid']}")
    title = normalize_title(record.get("title"))
    if title:
        aliases.append(f"title:{title}:{record.get('year') or 0}")
    return aliases


def paper_id_for(record: dict[str, Any]) -> str:
    aliases = aliases_for(record)
    if aliases:
        return aliases[0]
    digest = hashlib.sha256(str(record.get("title", "")).encode("utf-8")).hexdigest()[:16]
    return f"unknown:{digest}"


def publication_type(result: dict[str, Any]) -> str:
    value = result.get("pubType") or result.get("pubTypeList") or ""
    if isinstance(value, dict):
        value = value.get("pubType", "")
    if isinstance(value, list):
        return "; ".join(compact_text(item) for item in value if compact_text(item))
    return compact_text(value)


def candidate_from_result(result: dict[str, Any], stratum: str) -> dict[str, Any] | None:
    pmcid = compact_text(result.get("pmcid")).upper()
    if not pmcid.startswith("PMC"):
        return None
    title = compact_text(result.get("title"))
    if not title:
        return None
    year = integer(result.get("pubYear"))
    record = {
        "paper_id": "",
        "title": title,
        "year": year or None,
        "doi": normalize_doi(result.get("doi")),
        "pmid": compact_text(result.get("pmid")) or None,
        "pmcid": pmcid,
        "authors": compact_text(result.get("authorString")),
        "journal": compact_text(result.get("journalTitle")),
        "publication_type": publication_type(result),
        "abstract": compact_text(result.get("abstractText")),
        "cited_by_count": integer(result.get("citedByCount")),
        "first_publication_date": compact_text(result.get("firstPublicationDate")) or None,
        "is_open_access": str(result.get("isOpenAccess", "")).upper() == "Y",
        "has_full_text": str(result.get("inEPMC", "")).upper() == "Y",
        "discovery_source": "europe_pmc",
        "discovery_strata": [stratum],
        "source_url": f"https://europepmc.org/articles/{pmcid}",
        "fulltext_url": f"{EUROPE_PMC_API}/{pmcid}/fullTextXML",
        "discovered_at": utc_now(),
        "ingest_state": "discovered",
    }
    record["paper_id"] = paper_id_for(record)
    return record


def merge_record(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for field in (
        "doi",
        "pmid",
        "pmcid",
        "authors",
        "journal",
        "publication_type",
        "abstract",
        "first_publication_date",
    ):
        if not target.get(field) and incoming.get(field):
            target[field] = incoming[field]
    target["cited_by_count"] = max(integer(target.get("cited_by_count")), integer(incoming.get("cited_by_count")))
    target["discovery_strata"] = sorted(set(target.get("discovery_strata", [])) | set(incoming.get("discovery_strata", [])))
    target["is_open_access"] = bool(target.get("is_open_access") or incoming.get("is_open_access"))
    target["has_full_text"] = bool(target.get("has_full_text") or incoming.get("has_full_text"))
    target["paper_id"] = paper_id_for(target)


def candidate_score(record: dict[str, Any]) -> float:
    score = len(record.get("discovery_strata", [])) * 12.0
    score += 4.0 if record.get("abstract") else 0.0
    score += 2.0 if record.get("doi") else 0.0
    year = integer(record.get("year"))
    if year >= 2020:
        score += 4.0
    elif year >= 2010:
        score += 2.0
    pub_type = str(record.get("publication_type", "")).casefold()
    if "research" in pub_type or "journal article" in pub_type:
        score += 3.0
    if "review" in pub_type:
        score += 1.0
    score += min(math.log1p(max(integer(record.get("cited_by_count")), 0)), 7.0)
    return round(score, 3)


def estimate_command(args: argparse.Namespace, profile: dict[str, Any]) -> int:
    rows = []
    for stratum in profile["strata"]:
        response = search_page(stratum["query"], 1, None, args.timeout)
        rows.append(
            {
                "id": stratum["id"],
                "hit_count": integer(response.get("hitCount")),
                "candidate_limit": integer(stratum.get("candidate_limit")),
                "selection_quota": integer(stratum.get("selection_quota")),
            }
        )
    print(json.dumps({"profile_id": profile.get("profile_id"), "strata": rows}, ensure_ascii=False, indent=2))
    return 0


def discover_command(args: argparse.Namespace, profile: dict[str, Any]) -> int:
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target_count = args.target or integer(profile.get("target_unique_fulltexts"), 10000)
    page_size = min(max(args.page_size, 1), 1000)
    records: dict[str, dict[str, Any]] = {}
    alias_index: dict[str, str] = {}
    stratum_report: list[dict[str, Any]] = []

    for stratum in profile["strata"]:
        stratum_id = stratum["id"]
        candidate_limit = max(integer(stratum.get("candidate_limit")), integer(stratum.get("selection_quota")))
        cursor: str | None = "*"
        seen_for_stratum = 0
        hit_count = 0
        while seen_for_stratum < candidate_limit:
            batch_size = min(page_size, candidate_limit - seen_for_stratum)
            response = search_page(stratum["query"], batch_size, cursor, args.timeout)
            hit_count = integer(response.get("hitCount"))
            results = response.get("resultList", {}).get("result", [])
            if not isinstance(results, list) or not results:
                break
            accepted_in_page = 0
            for result in results:
                if not isinstance(result, dict):
                    continue
                incoming = candidate_from_result(result, stratum_id)
                if not incoming:
                    continue
                aliases = aliases_for(incoming)
                existing_key = next((alias_index[alias] for alias in aliases if alias in alias_index), None)
                if existing_key is None:
                    existing_key = incoming["paper_id"]
                    records[existing_key] = incoming
                else:
                    merge_record(records[existing_key], incoming)
                for alias in aliases_for(records[existing_key]):
                    alias_index[alias] = existing_key
                accepted_in_page += 1
            seen_for_stratum += len(results)
            cursor = response.get("nextCursorMark")
            if not cursor or len(results) < batch_size:
                break
            print(
                f"[discover] {stratum_id}: scanned={seen_for_stratum} accepted_page={accepted_in_page} unique={len(records)}",
                file=sys.stderr,
            )
        stratum_report.append(
            {
                "id": stratum_id,
                "hit_count": hit_count,
                "scanned": seen_for_stratum,
                "unique_union_so_far": len(records),
            }
        )

    all_records = list(records.values())
    for record in all_records:
        record["selection_score"] = candidate_score(record)
    ranked = sorted(
        all_records,
        key=lambda item: (
            item["selection_score"],
            integer(item.get("year")),
            integer(item.get("cited_by_count")),
            item.get("title", ""),
        ),
        reverse=True,
    )

    selected_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    for stratum in profile["strata"]:
        quota = integer(stratum.get("selection_quota"))
        pool = [record for record in ranked if stratum["id"] in record.get("discovery_strata", [])]
        added = 0
        for record in pool:
            identity = record["paper_id"]
            if identity in selected_ids:
                continue
            selected_ids.add(identity)
            selected.append(record)
            added += 1
            if added >= quota or len(selected) >= target_count:
                break
        if len(selected) >= target_count:
            break
    if len(selected) < target_count:
        for record in ranked:
            if record["paper_id"] in selected_ids:
                continue
            selected_ids.add(record["paper_id"])
            selected.append(record)
            if len(selected) >= target_count:
                break

    selected = selected[:target_count]
    for rank, record in enumerate(selected, 1):
        record["selection_rank"] = rank
    final_selected_ids = {record["paper_id"] for record in selected}
    for record in ranked:
        record["selected"] = record["paper_id"] in final_selected_ids
    manifest_path = root / "manifest.jsonl"
    candidates_path = root / "candidates.jsonl"
    write_jsonl(manifest_path, selected)
    write_jsonl(candidates_path, ranked)
    report = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "profile_id": profile.get("profile_id"),
        "target": target_count,
        "candidate_union": len(all_records),
        "selected": len(selected),
        "target_met": len(selected) >= target_count,
        "strata": stratum_report,
        "manifest": str(manifest_path),
        "candidate_pool": str(candidates_path),
    }
    write_json(root / "reports" / "discovery.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["target_met"] else 2


def read_manifest(root: Path) -> list[dict[str, Any]]:
    path = root / "manifest.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"Run discover first; missing {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {path}:{line_number}")
            rows.append(value)
    return rows


def permanent_fetch_error(message: str) -> bool:
    normalized = message.casefold()
    return any(
        marker in normalized
        for marker in (
            "http error 404",
            "http error 410",
            "does not contain a jats article",
            "no open access full text",
        )
    )


def archive_replaced_artifacts(root: Path, pmcid: str) -> list[dict[str, str]]:
    """Move retained source artifacts out of the active corpus without deleting them."""
    normalized = pmcid.upper()
    sources = (
        root / "raw" / "jats" / f"{normalized}.xml.gz",
        root / "records" / f"{normalized}.json.gz",
    )
    archive_root = root / "excluded" / "replacements" / normalized
    moved: list[tuple[Path, Path]] = []
    try:
        for source in sources:
            if not source.is_file():
                continue
            relative = source.relative_to(root)
            destination = archive_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError(f"Archive target already exists: {destination}")
            source.replace(destination)
            moved.append((source, destination))
    except Exception:
        for source, destination in reversed(moved):
            destination.replace(source)
        raise
    return [
        {"source": str(source), "archive": str(destination)}
        for source, destination in moved
    ]


def replace_failures_command(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    manifest = read_manifest(root)
    candidate_path = root / "candidates.jsonl"
    if not candidate_path.is_file():
        raise FileNotFoundError(f"Missing {candidate_path}; rerun discover to create the reserve pool")
    candidates = read_jsonl(candidate_path)
    status_rows = read_jsonl(root / "logs" / "fetch-status.jsonl")
    last_status: dict[str, dict[str, Any]] = {}
    for status in status_rows:
        pmcid = str(status.get("pmcid") or "").upper()
        if pmcid:
            last_status[pmcid] = status

    explicit = {str(value).upper() for value in (args.pmcid or [])}
    failed: list[tuple[int, dict[str, Any], str]] = []
    for index, record in enumerate(manifest):
        pmcid = str(record.get("pmcid") or "").upper()
        record_path = root / "records" / f"{pmcid}.json.gz"
        if record_path.is_file() and pmcid not in explicit:
            continue
        status = last_status.get(pmcid, {})
        error = str(status.get("error") or "")
        if pmcid in explicit or (status.get("status") == "error" and permanent_fetch_error(error)):
            explicit_reason = str(args.reason or "explicit replacement request") if pmcid in explicit else ""
            failed.append((index, record, error or explicit_reason))
    failed = failed[: max(args.max_replacements, 0)]
    if not failed:
        result = {"replaced": 0, "manifest": str(root / "manifest.jsonl"), "reason": "no permanent failures"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    used_aliases = {alias for record in manifest for alias in aliases_for(record)}
    replacement_rows = read_jsonl(root / "logs" / "replacements.jsonl")
    rejected_pmcids = {
        str(row.get("removed_pmcid") or "").upper() for row in replacement_rows if row.get("removed_pmcid")
    }
    available = [
        record
        for record in candidates
        if str(record.get("pmcid") or "").upper() not in rejected_pmcids
        and not any(alias in used_aliases for alias in aliases_for(record))
    ]
    replacements: list[dict[str, Any]] = []
    for index, removed, reason in failed:
        removed_strata = set(removed.get("discovery_strata", []))
        if not available:
            raise RuntimeError("Reserve candidate pool exhausted")
        available.sort(
            key=lambda record: (
                len(removed_strata & set(record.get("discovery_strata", []))),
                float(record.get("selection_score") or 0),
                integer(record.get("year")),
                integer(record.get("cited_by_count")),
            ),
            reverse=True,
        )
        selected = dict(available.pop(0))
        selected["selected"] = True
        selected["selection_rank"] = removed.get("selection_rank") or index + 1
        selected["replaces_paper_id"] = removed.get("paper_id")
        selected["replacement_reason"] = reason
        selected["replaced_at"] = utc_now()
        manifest[index] = selected
        used_aliases.update(aliases_for(selected))
        event = {
            "at": utc_now(),
            "selection_rank": selected["selection_rank"],
            "removed_paper_id": removed.get("paper_id"),
            "removed_pmcid": removed.get("pmcid"),
            "replacement_paper_id": selected.get("paper_id"),
            "replacement_pmcid": selected.get("pmcid"),
            "reason": reason,
            "shared_strata": sorted(removed_strata & set(selected.get("discovery_strata", []))),
        }
        replacements.append(event)

    manifest_path = root / "manifest.jsonl"
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    write_jsonl(temporary, manifest)
    archived_moves: list[tuple[Path, Path]] = []
    try:
        for event in replacements:
            removed_pmcid = str(event.get("removed_pmcid") or "").upper()
            archived = archive_replaced_artifacts(root, removed_pmcid)
            event["archived_artifacts"] = archived
            archived_moves.extend((Path(item["source"]), Path(item["archive"])) for item in archived)
        temporary.replace(manifest_path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        for source, destination in reversed(archived_moves):
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(source)
        raise
    for event in replacements:
        append_jsonl(root / "logs" / "replacements.jsonl", event)
    result = {
        "replaced": len(replacements),
        "manifest_papers": len(manifest),
        "manifest": str(manifest_path),
        "replacement_log": str(root / "logs" / "replacements.jsonl"),
        "events": replacements,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return compact_text(" ".join(node.itertext()))


def first_node(root: ET.Element, tag: str, **attributes: str) -> ET.Element | None:
    for node in root.iter():
        if local_tag(node.tag) != tag:
            continue
        if all(node.attrib.get(name) == value for name, value in attributes.items()):
            return node
    return None


def child_nodes(node: ET.Element, tag: str) -> Iterable[ET.Element]:
    return (child for child in list(node) if local_tag(child.tag) == tag)


def canonical_section(heading: str, sec_type: str = "") -> str:
    normalized = compact_text(heading).casefold().strip(" .:")
    normalized = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", normalized)
    sec_type = sec_type.casefold()
    if sec_type in {"intro", "introduction"}:
        return "introduction"
    if sec_type in {"methods", "materials|methods", "method"}:
        return "methods"
    if sec_type in {"results", "discussion", "conclusions", "conclusion"}:
        return SECTION_TYPES.get(sec_type, sec_type)
    if sec_type in {"references", "ref-list"}:
        return "references"
    for name, section_type in SECTION_TYPES.items():
        if normalized == name or normalized.startswith(name + " "):
            return section_type
    return "other"


def direct_section_text(sec: ET.Element) -> str:
    chunks: list[str] = []
    for child in list(sec):
        tag = local_tag(child.tag)
        if tag in {"title", "sec", "ref-list"}:
            continue
        text = node_text(child)
        if text:
            chunks.append(text)
    return compact_text(" ".join(chunks))


def parse_jats(payload: bytes, manifest_record: dict[str, Any]) -> dict[str, Any]:
    root = ET.fromstring(payload)
    title = node_text(first_node(root, "article-title")) or manifest_record.get("title", "")
    journal = node_text(first_node(root, "journal-title")) or manifest_record.get("journal", "")
    ids: dict[str, str] = {}
    for node in root.iter():
        if local_tag(node.tag) == "article-id":
            id_type = node.attrib.get("pub-id-type", "unknown")
            ids[id_type] = node_text(node)
    year_node = first_node(root, "year")
    license_node = first_node(root, "license")
    license_url = ""
    if license_node is not None:
        for name, value in license_node.attrib.items():
            if name.endswith("href"):
                license_url = value
                break
    sections: list[dict[str, Any]] = []
    abstract_node = first_node(root, "abstract")
    abstract = node_text(abstract_node)
    if abstract:
        sections.append(
            {
                "ordinal": 0,
                "depth": 0,
                "heading": "Abstract",
                "section_type": "abstract",
                "text": abstract,
                "words": len(WORD_RE.findall(abstract)),
            }
        )

    body = first_node(root, "body")
    ordinal = 1

    def visit(sec: ET.Element, depth: int, inherited_type: str = "") -> None:
        nonlocal ordinal
        title_node = next(child_nodes(sec, "title"), None)
        heading = node_text(title_node) or f"Untitled section {ordinal}"
        text = direct_section_text(sec)
        detected_type = canonical_section(heading, sec.attrib.get("sec-type", ""))
        section_type = inherited_type if detected_type == "other" and inherited_type not in {"", "other"} else detected_type
        if section_type == "references":
            return
        if text:
            sections.append(
                {
                    "ordinal": ordinal,
                    "depth": depth,
                    "heading": heading,
                    "section_type": section_type,
                    "text": text,
                    "words": len(WORD_RE.findall(text)),
                }
            )
            ordinal += 1
        for child in child_nodes(sec, "sec"):
            visit(child, depth + 1, section_type)

    if body is not None:
        body_sections = list(child_nodes(body, "sec"))
        if body_sections:
            for section in body_sections:
                visit(section, 1)
        else:
            text = node_text(body)
            if text:
                sections.append(
                    {
                        "ordinal": ordinal,
                        "depth": 1,
                        "heading": "Body",
                        "section_type": "other",
                        "text": text,
                        "words": len(WORD_RE.findall(text)),
                    }
                )

    return {
        "schema_version": "1.0",
        "paper_id": manifest_record.get("paper_id") or paper_id_for(manifest_record),
        "title": title,
        "authors": manifest_record.get("authors", ""),
        "journal": journal,
        "year": integer(node_text(year_node), integer(manifest_record.get("year"))) or None,
        "doi": normalize_doi(ids.get("doi") or manifest_record.get("doi")),
        "pmid": ids.get("pmid") or manifest_record.get("pmid"),
        "pmcid": (ids.get("pmc") or manifest_record.get("pmcid") or "").upper(),
        "publication_type": manifest_record.get("publication_type", ""),
        "discovery_strata": manifest_record.get("discovery_strata", []),
        "license": node_text(license_node),
        "license_url": license_url,
        "source_url": manifest_record.get("source_url"),
        "source_acquired": True,
        "source_acquisition_date": utc_now(),
        "source_verified_against_original": False,
        "source_verification_method": "none",
        "description_source": "original_jats_xml",
        "abstract": abstract,
        "sections": sections,
        "section_count": len(sections),
        "word_count": sum(integer(section.get("words")) for section in sections),
    }


def atomic_gzip_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wb", compresslevel=6) as stream:
        stream.write(payload)
    temporary.replace(path)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")


class RateLimiter:
    """Thread-safe request-start limiter."""

    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1.0 / requests_per_second
        self.next_start = time.monotonic()
        self.lock = Lock()

    def wait(self) -> None:
        with self.lock:
            start_at = max(self.next_start, time.monotonic())
            self.next_start = start_at + self.interval
        delay = start_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)


def fetch_command(args: argparse.Namespace, profile: dict[str, Any]) -> int:
    root = args.root.resolve()
    manifest = read_manifest(root)
    retrieval = profile.get("retrieval", {})
    rate = args.requests_per_second or float(retrieval.get("requests_per_second", 2.0))
    timeout = args.timeout or integer(retrieval.get("timeout_seconds"), 60)
    retries = args.max_retries if args.max_retries is not None else integer(retrieval.get("max_retries"), 5)
    workers = args.workers or integer(retrieval.get("workers"), 4)
    if rate <= 0:
        raise ValueError("requests-per-second must be positive")
    if workers <= 0 or workers > 16:
        raise ValueError("workers must be between 1 and 16")
    raw_dir = root / "raw" / "jats"
    record_dir = root / "records"
    status_path = root / "logs" / "fetch-status.jsonl"
    pending: list[dict[str, Any]] = []
    for manifest_record in manifest:
        pmcid = str(manifest_record.get("pmcid") or "").upper()
        if not pmcid.startswith("PMC"):
            continue
        raw_path = raw_dir / f"{pmcid}.xml.gz"
        record_path = record_dir / f"{pmcid}.json.gz"
        if raw_path.is_file() and record_path.is_file() and not args.reparse_existing:
            continue
        pending.append(manifest_record)
        if args.limit is not None and len(pending) >= args.limit:
            break

    limiter = RateLimiter(rate)

    def acquire(manifest_record: dict[str, Any]) -> dict[str, Any]:
        pmcid = str(manifest_record.get("pmcid") or "").upper()
        raw_path = raw_dir / f"{pmcid}.xml.gz"
        record_path = record_dir / f"{pmcid}.json.gz"
        mode = "reparsed"
        try:
            if raw_path.is_file():
                with gzip.open(raw_path, "rb") as stream:
                    payload = stream.read()
            else:
                mode = "fetched"
                limiter.wait()
                payload = http_get(manifest_record["fulltext_url"], timeout=timeout, retries=retries)
                if b"<article" not in payload[:20000]:
                    raise ValueError("Response does not contain a JATS article")
                atomic_gzip_write(raw_path, payload)
            parsed = parse_jats(payload, manifest_record)
            atomic_gzip_write(
                record_path,
                json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            )
            return {
                "pmcid": pmcid,
                "status": "ok",
                "mode": mode,
                "at": utc_now(),
                "words": parsed["word_count"],
            }
        except Exception as exc:
            return {
                "pmcid": pmcid,
                "status": "error",
                "mode": mode,
                "at": utc_now(),
                "error": str(exc)[:500],
            }

    completed = 0
    fetched = 0
    reparsed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(acquire, record) for record in pending]
        for future in as_completed(futures):
            result = future.result()
            append_jsonl(status_path, result)
            completed += 1
            if result["status"] == "error":
                failed += 1
            elif result["mode"] == "fetched":
                fetched += 1
            else:
                reparsed += 1
            if args.progress_every > 0 and completed % args.progress_every == 0:
                print(
                    f"[fetch] processed={completed} fetched={fetched} reparsed={reparsed} failed={failed}",
                    file=sys.stderr,
                )

    result = {
        "processed_this_run": completed,
        "fetched": fetched,
        "reparsed": reparsed,
        "failed": failed,
        "raw_total": len(list(raw_dir.glob("*.xml.gz"))) if raw_dir.exists() else 0,
        "record_total": len(list(record_dir.glob("*.json.gz"))) if record_dir.exists() else 0,
        "manifest_total": len(manifest),
        "workers": workers,
        "requests_per_second": rate,
        "status_log": str(status_path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 3


def read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected object in {path}")
    return value


def replace_database_with_retry(temporary: Path, database: Path, wait_seconds: int = 60) -> None:
    """Atomically publish an index after transient readers release the old file."""
    deadline = time.monotonic() + max(wait_seconds, 0)
    while True:
        try:
            temporary.replace(database)
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise PermissionError(
                    f"Could not publish {temporary} because {database} remained open for "
                    f"{wait_seconds} seconds. Close corpus readers and rerun index."
                ) from exc
            time.sleep(1)


def index_command(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    record_paths = sorted((root / "records").glob("*.json.gz"))
    if not record_paths:
        raise FileNotFoundError("No parsed records found; run fetch first")
    database = root / "corpus.sqlite"
    temporary = root / "corpus.sqlite.tmp"
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE papers (
            paper_id TEXT PRIMARY KEY,
            pmcid TEXT UNIQUE,
            pmid TEXT,
            doi TEXT,
            title TEXT NOT NULL,
            authors TEXT,
            journal TEXT,
            year INTEGER,
            publication_type TEXT,
            abstract TEXT,
            license TEXT,
            license_url TEXT,
            source_url TEXT,
            discovery_strata TEXT,
            word_count INTEGER,
            section_count INTEGER
        );
        CREATE TABLE sections (
            section_id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            depth INTEGER NOT NULL,
            section_type TEXT NOT NULL,
            heading TEXT,
            text TEXT NOT NULL,
            words INTEGER NOT NULL,
            FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
        );
        CREATE INDEX sections_paper_idx ON sections(paper_id);
        CREATE INDEX sections_type_idx ON sections(section_type);
        CREATE INDEX papers_doi_idx ON papers(doi);
        CREATE INDEX papers_year_idx ON papers(year);
        CREATE VIRTUAL TABLE sections_fts USING fts5(
            paper_id UNINDEXED,
            section_type UNINDEXED,
            title,
            heading,
            text,
            tokenize='porter unicode61'
        );
        """
    )
    inserted_papers = 0
    inserted_sections = 0
    for path in record_paths:
        record = read_gzip_json(path)
        paper_values = (
            record.get("paper_id"),
            record.get("pmcid"),
            record.get("pmid"),
            record.get("doi"),
            record.get("title") or "Untitled",
            record.get("authors"),
            record.get("journal"),
            record.get("year"),
            record.get("publication_type"),
            record.get("abstract"),
            record.get("license"),
            record.get("license_url"),
            record.get("source_url"),
            json.dumps(record.get("discovery_strata", []), ensure_ascii=False),
            integer(record.get("word_count")),
            integer(record.get("section_count")),
        )
        connection.execute(
            "INSERT OR REPLACE INTO papers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            paper_values,
        )
        inserted_papers += 1
        for section in record.get("sections", []):
            cursor = connection.execute(
                "INSERT INTO sections(paper_id,ordinal,depth,section_type,heading,text,words) VALUES (?,?,?,?,?,?,?)",
                (
                    record.get("paper_id"),
                    integer(section.get("ordinal")),
                    integer(section.get("depth")),
                    section.get("section_type") or "other",
                    section.get("heading"),
                    section.get("text") or "",
                    integer(section.get("words")),
                ),
            )
            connection.execute(
                "INSERT INTO sections_fts(rowid,paper_id,section_type,title,heading,text) VALUES (?,?,?,?,?,?)",
                (
                    cursor.lastrowid,
                    record.get("paper_id"),
                    section.get("section_type") or "other",
                    record.get("title") or "Untitled",
                    section.get("heading"),
                    section.get("text") or "",
                ),
            )
            inserted_sections += 1
    connection.commit()
    connection.close()
    replace_database_with_retry(temporary, database)
    result = {
        "database": str(database),
        "papers": inserted_papers,
        "sections": inserted_sections,
        "indexed_at": utc_now(),
    }
    write_json(root / "reports" / "index.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def corpus_stats(root: Path) -> dict[str, Any]:
    manifest = read_manifest(root)
    raw_dir = root / "raw" / "jats"
    record_dir = root / "records"
    raw_paths = list(raw_dir.glob("*.xml.gz")) if raw_dir.exists() else []
    record_paths = list(record_dir.glob("*.json.gz")) if record_dir.exists() else []
    manifest_pmcids = {str(record.get("pmcid") or "").upper() for record in manifest if record.get("pmcid")}
    record_pmcids = {path.name[:-8].upper() for path in record_paths}
    active_parsed_records = len(manifest_pmcids & record_pmcids)
    candidate_pool_path = root / "candidates.jsonl"
    reserve_candidates = max(len(read_jsonl(candidate_pool_path)) - len(manifest), 0) if candidate_pool_path.is_file() else 0
    replacement_events = len(read_jsonl(root / "logs" / "replacements.jsonl"))
    strata = Counter()
    years = Counter()
    for record in manifest:
        strata.update(record.get("discovery_strata", []))
        if record.get("year"):
            years[str(record["year"])] += 1
    indexed_papers = 0
    indexed_sections = 0
    indexed_words = 0
    database = root / "corpus.sqlite"
    if database.is_file():
        connection = sqlite3.connect(database)
        indexed_papers = connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        indexed_sections = connection.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
        indexed_words = connection.execute("SELECT COALESCE(SUM(words),0) FROM sections").fetchone()[0]
        connection.close()
    return {
        "generated_at": utc_now(),
        "root": str(root),
        "manifest_papers": len(manifest),
        "reserve_candidates": reserve_candidates,
        "replacement_events": replacement_events,
        "fulltext_xml": len(raw_paths),
        "parsed_records": len(record_paths),
        "active_parsed_records": active_parsed_records,
        "records_not_in_manifest": len(record_pmcids - manifest_pmcids),
        "indexed_papers": indexed_papers,
        "indexed_sections": indexed_sections,
        "indexed_words": indexed_words,
        "completion_ratio": round(active_parsed_records / max(len(manifest), 1), 4),
        "compressed_bytes": sum(path.stat().st_size for path in raw_paths + record_paths),
        "strata": dict(strata.most_common()),
        "years": dict(sorted(years.items(), reverse=True)),
    }


def stats_command(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    report = corpus_stats(root)
    write_json(root / "reports" / "stats.json", report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Manifest papers: {report['manifest_papers']}")
        print(f"Reserve candidates: {report['reserve_candidates']}")
        print(f"Replacement events: {report['replacement_events']}")
        print(f"Full-text XML: {report['fulltext_xml']}")
        print(f"Parsed records (active/total): {report['active_parsed_records']}/{report['parsed_records']}")
        print(f"Indexed papers/sections: {report['indexed_papers']}/{report['indexed_sections']}")
        print(f"Indexed words: {report['indexed_words']}")
        print(f"Completion: {report['completion_ratio']:.2%}")
        print(f"Compressed storage: {report['compressed_bytes'] / (1024**2):.1f} MiB")
    return 0


def duplicate_values(records: list[dict[str, Any]], field: str) -> dict[str, int]:
    values = Counter()
    for record in records:
        raw = record.get(field)
        if raw is None or str(raw).strip() == "":
            continue
        value = str(raw).strip().casefold()
        values[value] += 1
    return {value: count for value, count in values.items() if count > 1}


def audit_command(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    manifest = read_manifest(root)
    raw_dir = root / "raw" / "jats"
    record_dir = root / "records"
    raw_paths = sorted(raw_dir.glob("*.xml.gz")) if raw_dir.exists() else []
    record_paths = sorted(record_dir.glob("*.json.gz")) if record_dir.exists() else []
    raw_ids = {path.name[:-7].upper() for path in raw_paths}
    record_file_ids = {path.name[:-8].upper() for path in record_paths}
    manifest_pmcids = {str(record.get("pmcid") or "").upper() for record in manifest if record.get("pmcid")}

    duplicate_paper_ids = duplicate_values(manifest, "paper_id")
    duplicate_dois = duplicate_values(manifest, "doi")
    duplicate_pmcids = duplicate_values(manifest, "pmcid")
    title_years = Counter(
        f"{normalize_title(str(record.get('title') or ''))}|{record.get('year') or ''}"
        for record in manifest
        if record.get("title")
    )
    duplicate_title_years = {value: count for value, count in title_years.items() if count > 1}
    missing_required = [
        str(record.get("paper_id") or f"row:{index}")
        for index, record in enumerate(manifest, 1)
        if not record.get("title") or not str(record.get("pmcid") or "").upper().startswith("PMC")
        or not record.get("fulltext_url")
    ]

    corrupt_records: list[dict[str, str]] = []
    mismatched_record_ids: list[dict[str, str]] = []
    zero_content: list[str] = []
    missing_license = 0
    section_types = Counter()
    records_with_methods = 0
    records_with_results = 0
    parsed_paper_ids = Counter()
    for path in record_paths:
        try:
            record = read_gzip_json(path)
        except Exception as exc:
            corrupt_records.append({"file": path.name, "error": str(exc)[:300]})
            continue
        paper_id = str(record.get("paper_id") or "")
        parsed_paper_ids[paper_id] += 1
        expected_pmcid = path.name[:-8].upper()
        actual_pmcid = str(record.get("pmcid") or "").upper()
        if actual_pmcid != expected_pmcid:
            mismatched_record_ids.append(
                {"file": path.name, "expected_pmcid": expected_pmcid, "actual_pmcid": actual_pmcid}
            )
        sections = [section for section in (record.get("sections") or []) if isinstance(section, dict)]
        types = {str(section.get("section_type") or "other") for section in sections}
        section_types.update(str(section.get("section_type") or "other") for section in sections)
        records_with_methods += int("methods" in types)
        records_with_results += int(bool(types & {"results", "results_discussion"}))
        if not sections or integer(record.get("word_count")) <= 0:
            zero_content.append(expected_pmcid)
        if not record.get("license") and not record.get("license_url"):
            missing_license += 1

    database = root / "corpus.sqlite"
    indexed_papers = 0
    indexed_sections = 0
    if database.is_file():
        connection = sqlite3.connect(database)
        indexed_papers = connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        indexed_sections = connection.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
        connection.close()

    duplicate_parsed_paper_ids = {value: count for value, count in parsed_paper_ids.items() if count > 1}

    structural_invalid = any(
        (
            duplicate_paper_ids,
            duplicate_dois,
            duplicate_pmcids,
            missing_required,
            corrupt_records,
            mismatched_record_ids,
            zero_content,
            duplicate_parsed_paper_ids,
            record_file_ids - raw_ids,
            record_file_ids - manifest_pmcids,
        )
    )
    acquisition_complete = manifest_pmcids == record_file_ids and raw_ids == record_file_ids
    index_reconciled = indexed_papers == len(record_paths) and indexed_papers > 0
    status = "invalid" if structural_invalid else "ready" if acquisition_complete and index_reconciled else "partial"
    report = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "root": str(root),
        "status": status,
        "counts": {
            "manifest": len(manifest),
            "raw_xml": len(raw_paths),
            "parsed_records": len(record_paths),
            "indexed_papers": indexed_papers,
            "indexed_sections": indexed_sections,
            "records_with_methods": records_with_methods,
            "records_with_results_or_combined": records_with_results,
            "records_missing_license_label": missing_license,
        },
        "checks": {
            "acquisition_complete": acquisition_complete,
            "index_reconciled": index_reconciled,
            "duplicate_paper_ids": duplicate_paper_ids,
            "duplicate_dois": duplicate_dois,
            "duplicate_pmcids": duplicate_pmcids,
            "duplicate_parsed_paper_ids": duplicate_parsed_paper_ids,
            "duplicate_normalized_title_years": duplicate_title_years,
            "missing_required_manifest_fields": missing_required[:100],
            "raw_without_record": sorted(raw_ids - record_file_ids)[:100],
            "record_without_raw": sorted(record_file_ids - raw_ids)[:100],
            "record_not_in_manifest": sorted(record_file_ids - manifest_pmcids)[:100],
            "manifest_without_record": sorted(manifest_pmcids - record_file_ids)[:100],
            "corrupt_records": corrupt_records[:100],
            "mismatched_record_ids": mismatched_record_ids[:100],
            "zero_content_records": zero_content[:100],
            "section_type_counts": dict(section_types.most_common()),
        },
    }
    write_json(root / "reports" / "audit.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "ready" else 4 if status == "invalid" else 3


def fts_expression(query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", query)
    if not tokens:
        raise ValueError("Query contains no searchable tokens")
    return " AND ".join(f'"{token}"' for token in tokens[:20])


def query_command(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    database = root / "corpus.sqlite"
    if not database.is_file():
        raise FileNotFoundError("Missing corpus.sqlite; run index first")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    parameters: list[Any] = [fts_expression(args.query)]
    section_filter = ""
    if args.section:
        placeholders = ",".join("?" for _ in args.section)
        section_filter = f" AND s.section_type IN ({placeholders})"
        parameters.extend(args.section)
    parameters.append(max(args.top, 1))
    rows = connection.execute(
        f"""
        SELECT p.paper_id, p.pmcid, p.doi, p.title, p.journal, p.year,
               s.section_type, s.heading,
               snippet(sections_fts, 4, '[', ']', ' … ', 28) AS snippet,
               bm25(sections_fts, 2.0, 1.0, 0.8) AS rank
        FROM sections_fts
        JOIN sections s ON s.section_id = sections_fts.rowid
        JOIN papers p ON p.paper_id = s.paper_id
        WHERE sections_fts MATCH ? {section_filter}
        ORDER BY rank
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    connection.close()
    result = [dict(row) for row in rows]
    print(json.dumps({"query": args.query, "results": result}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    try:
        profile = load_profile(args.profile)
        if args.command == "estimate":
            return estimate_command(args, profile)
        if args.command == "discover":
            return discover_command(args, profile)
        if args.command == "fetch":
            return fetch_command(args, profile)
        if args.command == "index":
            return index_command(args)
        if args.command == "stats":
            return stats_command(args)
        if args.command == "audit":
            return audit_command(args)
        if args.command == "replace-failures":
            return replace_failures_command(args)
        if args.command == "query":
            return query_command(args)
        raise ValueError(f"Unknown command: {args.command}")
    except KeyboardInterrupt:
        print("Interrupted; completed files and logs are resumable.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

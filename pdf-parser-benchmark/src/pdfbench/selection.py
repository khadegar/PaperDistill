from __future__ import annotations

import gzip
import html
import json
import re
import shutil
import sqlite3
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .common import (
    normalize_doi,
    normalize_text,
    project_path,
    read_jsonl,
    relative_posix,
    sha256_file,
    utc_now,
    write_csv,
    write_json,
    write_jsonl,
)


FORMULA_KEYWORDS = {
    "finite element",
    "finite-element",
    "topology optimization",
    "computational",
    "numerical model",
    "sensitivity analysis",
    "mechanical model",
    "simulation",
    "simo",
    "simp",
}
TABLE_KEYWORDS = {
    "systematic review",
    "meta-analysis",
    "scoping review",
    "clinical",
    "cohort",
    "randomized",
    "retrospective",
    "case series",
}
FIGURE_KEYWORDS = {
    "additive manufacturing",
    "3d print",
    "three-dimensional print",
    "scaffold",
    "biomaterial",
    "porous",
    "lattice",
    "tpms",
    "implant",
}
PDF_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _run_text(command: list[str], timeout: int = 120) -> str:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    if completed.returncode:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}: {stderr}")
    return completed.stdout.decode("utf-8", errors="replace")


def _field_value(connection: sqlite3.Connection, item_id: int, field_name: str) -> str:
    row = connection.execute(
        """
        SELECT v.value
        FROM itemData d
        JOIN fields f ON f.fieldID = d.fieldID
        JOIN itemDataValues v ON v.valueID = d.valueID
        WHERE d.itemID = ? AND lower(f.fieldName) = lower(?)
        LIMIT 1
        """,
        (item_id, field_name),
    ).fetchone()
    return str(row[0]).strip() if row and row[0] is not None else ""


def load_zotero_pdf_rows(db_path: Path, storage_root: Path, linked_base: Path | None = None) -> list[dict[str, Any]]:
    uri = f"file:{db_path.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            """
            SELECT a.itemID, a.parentItemID, a.path, a.contentType,
                   ai.key, pi.key
            FROM itemAttachments a
            JOIN items ai ON ai.itemID = a.itemID
            LEFT JOIN items pi ON pi.itemID = a.parentItemID
            LEFT JOIN deletedItems di ON di.itemID = a.itemID
            LEFT JOIN deletedItems dp ON dp.itemID = a.parentItemID
            WHERE lower(COALESCE(a.contentType, '')) = 'application/pdf'
              AND di.itemID IS NULL AND dp.itemID IS NULL
            ORDER BY a.itemID
            """
        ).fetchall()
        output: list[dict[str, Any]] = []
        for attachment_id, parent_id, raw_path, content_type, attachment_key, parent_key in rows:
            logical_id = int(parent_id or attachment_id)
            path = resolve_zotero_attachment_path(
                str(raw_path or ""), str(attachment_key), storage_root, linked_base
            )
            output.append(
                {
                    "attachment_item_id": int(attachment_id),
                    "parent_item_id": int(parent_id) if parent_id else None,
                    "attachment_key": str(attachment_key),
                    "parent_key": str(parent_key or ""),
                    "content_type": str(content_type or ""),
                    "attachment_path_raw": str(raw_path or ""),
                    "pdf_path": str(path) if path else "",
                    "pdf_exists": bool(path and path.is_file()),
                    "doi": normalize_doi(_field_value(connection, logical_id, "DOI")),
                    "title": _field_value(connection, logical_id, "title"),
                    "date": _field_value(connection, logical_id, "date"),
                    "attachment_title": _field_value(connection, int(attachment_id), "title"),
                }
            )
        return output
    finally:
        connection.close()


def resolve_zotero_attachment_path(
    raw_path: str, attachment_key: str, storage_root: Path, linked_base: Path | None
) -> Path | None:
    value = raw_path.strip()
    if not value:
        return None
    if value.lower().startswith("storage:"):
        return (storage_root / attachment_key / value.split(":", 1)[1]).resolve()
    if value.lower().startswith("attachments:"):
        if linked_base is None:
            return None
        return (linked_base / value.split(":", 1)[1]).resolve()
    if value.lower().startswith("file://"):
        parsed = urllib.parse.urlparse(value)
        value = urllib.parse.unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:/", value):
            value = value[1:]
    return Path(value).expanduser().resolve()


def load_corpus_by_doi(corpus_root: Path) -> dict[str, dict[str, Any]]:
    by_doi: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(corpus_root / "manifest.jsonl"):
        doi = normalize_doi(row.get("doi"))
        pmcid = str(row.get("pmcid") or "").strip()
        if doi and pmcid:
            by_doi.setdefault(doi, row)
    return by_doi


def analyze_jats(jats_path: Path) -> dict[str, Any]:
    with gzip.open(jats_path, "rb") as handle:
        root = ET.parse(handle).getroot()
    counts = Counter(_local_name(element.tag) for element in root.iter())
    body = next((element for element in root.iter() if _local_name(element.tag) == "body"), None)
    body_text = normalize_text(" ".join(body.itertext()) if body is not None else "")
    headings = [
        normalize_text(" ".join(element.itertext()))
        for element in root.iter()
        if _local_name(element.tag) == "title" and normalize_text(" ".join(element.itertext()))
    ]
    return {
        "jats_body_chars": len(body_text),
        "jats_body_words": len(body_text.split()),
        "jats_table_count": counts["table-wrap"],
        "jats_formula_count": counts["disp-formula"] + counts["inline-formula"],
        "jats_figure_count": counts["fig"],
        "jats_section_count": counts["sec"],
        "jats_heading_count": len(headings),
    }


def _parse_key_values(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip().lower()] = value.strip()
    return result


def _count_command_rows(text: str) -> int:
    lines = [line.rstrip() for line in text.splitlines()]
    separator = next((index for index, line in enumerate(lines) if re.match(r"^-{3,}", line.strip())), None)
    if separator is None:
        return 0
    return sum(bool(line.strip()) for line in lines[separator + 1 :])


def extract_pdf_dois_first_pages(pdf_path: Path, pdftotext: str, pages: int = 3) -> list[str]:
    text = _run_text(
        [pdftotext, "-f", "1", "-l", str(pages), "-enc", "UTF-8", "-nopgbrk", str(pdf_path), "-"],
        timeout=90,
    )
    values: list[str] = []
    for match in PDF_DOI_RE.finditer(text):
        doi = normalize_doi(match.group(0)).rstrip(".,;:)]}")
        if doi and doi not in values:
            values.append(doi)
    return values


def analyze_pdf(pdf_path: Path, tools: dict[str, str]) -> dict[str, Any]:
    info_text = _run_text([tools["pdfinfo"], str(pdf_path)])
    info = _parse_key_values(info_text)
    pages = int(re.search(r"\d+", info.get("pages", "0")).group()) if re.search(r"\d+", info.get("pages", "")) else 0
    encrypted = info.get("encrypted", "no").lower().startswith("yes")
    extracted = _run_text([tools["pdftotext"], "-enc", "UTF-8", "-nopgbrk", str(pdf_path), "-"], timeout=300)
    plain = normalize_text(extracted)
    fonts_text = _run_text([tools["pdffonts"], str(pdf_path)])
    images_text = _run_text([tools["pdfimages"], "-list", str(pdf_path)])
    font_count = _count_command_rows(fonts_text)
    image_count = _count_command_rows(images_text)
    chars_per_page = len(plain) / max(pages, 1)
    lower_name = f"{pdf_path.name}".lower()
    return {
        "pdf_sha256": sha256_file(pdf_path),
        "pdf_bytes": pdf_path.stat().st_size,
        "page_count": pages,
        "encrypted": encrypted,
        "pdf_version": info.get("pdf version", ""),
        "pdf_text_chars": len(plain),
        "pdf_text_words": len(plain.split()),
        "text_chars_per_page": round(chars_per_page, 3),
        "font_count": font_count,
        "image_count": image_count,
        "likely_scanned": chars_per_page < 150 or (font_count == 0 and image_count >= max(pages, 1)),
        "supplement_name_cue": bool(re.search(r"supp|support|appendix|esm|additional", lower_name)),
    }


def _record_excerpt(record_path: Path) -> tuple[dict[str, Any], str]:
    with gzip.open(record_path, "rt", encoding="utf-8") as handle:
        record = json.load(handle)
    parts = [str(record.get("title") or ""), str(record.get("abstract") or "")]
    for section in (record.get("sections") or [])[:8]:
        parts.append(str(section.get("heading") or ""))
        parts.append(str(section.get("text") or "")[:1500])
    return record, normalize_text(html.unescape(" ".join(parts))).lower()


def _keyword_hits(text: str, keywords: set[str]) -> int:
    return sum(keyword in text for keyword in keywords)


def add_selection_scores(candidate: dict[str, Any], searchable_text: str) -> None:
    year = int(candidate.get("year") or 0)
    pages = max(int(candidate.get("page_count") or 0), 1)
    density = float(candidate.get("text_chars_per_page") or 0)
    formula_hits = _keyword_hits(searchable_text, FORMULA_KEYWORDS)
    table_hits = _keyword_hits(searchable_text, TABLE_KEYWORDS)
    figure_hits = _keyword_hits(searchable_text, FIGURE_KEYWORDS)
    candidate["keyword_hits"] = {
        "formula_fe_topology": formula_hits,
        "table_review_clinical": table_hits,
        "figure_am_biomaterial": figure_hits,
    }
    candidate["scores"] = {
        "layout_stress": round(
            (120 if candidate.get("likely_scanned") else 0)
            + max(0.0, 900.0 - density) / 12.0
            + (25 if int(candidate.get("font_count") or 0) <= 1 else 0)
            + min(float(candidate.get("image_count") or 0) / pages, 5.0) * 3.0
            + max(0, 2018 - year) * 0.7,
            4,
        ),
        "formula_fe_topology": round(float(candidate.get("jats_formula_count") or 0) * 3.0 + formula_hits * 25.0, 4),
        "table_review_clinical": round(float(candidate.get("jats_table_count") or 0) * 4.0 + table_hits * 20.0, 4),
        "figure_am_biomaterial": round(float(candidate.get("jats_figure_count") or 0) * 2.0 + figure_hits * 18.0, 4),
        "standard_born_digital": round(
            min(density, 3000.0) / 70.0
            + min(int(candidate.get("font_count") or 0), 12)
            - (35 if candidate.get("likely_scanned") else 0)
            - abs(pages - 12) * 0.15,
            4,
        ),
    }
    candidate["qualifies"] = {
        "layout_stress": bool(
            candidate.get("likely_scanned")
            or density < 3500
            or pages >= 20
            or int(candidate.get("font_count") or 0) >= 15
            or float(candidate.get("image_count") or 0) / pages >= 15
            or (year and year < 2012)
        ),
        "formula_fe_topology": bool(int(candidate.get("jats_formula_count") or 0) >= 4 or formula_hits),
        "table_review_clinical": bool(int(candidate.get("jats_table_count") or 0) >= 3 or table_hits),
        "figure_am_biomaterial": bool(int(candidate.get("jats_figure_count") or 0) >= 4 or figure_hits),
        "standard_born_digital": not bool(candidate.get("likely_scanned")),
    }


def _choose_best_attachment(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        rows,
        key=lambda row: (
            bool(row.get("supplement_name_cue")),
            -int(row.get("page_count") or 0),
            -int(row.get("pdf_text_chars") or 0),
            -int(row.get("pdf_bytes") or 0),
            str(row.get("pdf_path") or "").lower(),
        ),
    )[0]


def _rank(candidates: list[dict[str, Any]], stratum: str) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda row: (
            not bool(row["qualifies"][stratum]),
            -float(row["scores"][stratum]),
            str(row["doi"]),
            str(row["pdf_sha256"]),
        ),
    )


def select_stratified(
    candidates: list[dict[str, Any]], strata: list[str], per_stratum: int, reserve_per_stratum: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    reserves: list[dict[str, Any]] = []
    used: set[str] = set()
    for stratum in strata:
        available = [row for row in candidates if row["doi"] not in used]
        ranked = _rank(available, stratum)
        if len(ranked) < per_stratum + reserve_per_stratum:
            raise RuntimeError(f"Not enough unique candidates for {stratum}: {len(ranked)}")
        chosen = sorted(ranked[:per_stratum], key=lambda row: (str(row["doi"]), str(row["pdf_sha256"])))
        for row in chosen:
            copy = dict(row)
            copy["benchmark_stratum"] = stratum
            copy["selection_basis"] = "qualified" if row["qualifies"][stratum] else "ranked_fill"
            selected.append(copy)
            used.add(row["doi"])
        reserve_ranked = _rank([row for row in candidates if row["doi"] not in used], stratum)
        reserve_chosen = sorted(
            reserve_ranked[:reserve_per_stratum],
            key=lambda row: (str(row["doi"]), str(row["pdf_sha256"])),
        )
        for row in reserve_chosen:
            copy = dict(row)
            copy["benchmark_stratum"] = stratum
            copy["selection_basis"] = "reserve_qualified" if row["qualifies"][stratum] else "reserve_ranked_fill"
            reserves.append(copy)
            used.add(row["doi"])
    return selected, reserves


def _copy_verified(source: Path, target: Path, expected_hash: str | None = None) -> str:
    source_hash = expected_hash or sha256_file(source)
    if target.exists():
        if sha256_file(target) != source_hash:
            raise RuntimeError(f"Refusing to overwrite hash-mismatched staged file: {target}")
        return source_hash
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target_hash = sha256_file(target)
    if target_hash != source_hash:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"Hash mismatch after copying {source} to {target}")
    return source_hash


def _stage_row(config: dict[str, Any], row: dict[str, Any], sample_id: str) -> dict[str, Any]:
    root = Path(config["_project_root"])
    pmcid = str(row["pmcid"])
    pdf_source = Path(row["pdf_path"])
    record_source = Path(row["record_path"])
    jats_source = Path(row["jats_path"])
    pdf_target = project_path(config, "inputs", "pdfs", f"{sample_id}_{pmcid}.pdf")
    record_target = project_path(config, "ground-truth", "records", f"{sample_id}_{pmcid}.json.gz")
    jats_target = project_path(config, "ground-truth", "jats", f"{sample_id}_{pmcid}.xml.gz")
    _copy_verified(pdf_source, pdf_target, row["pdf_sha256"])
    record_hash = _copy_verified(record_source, record_target)
    jats_hash = _copy_verified(jats_source, jats_target)
    staged = dict(row)
    staged.update(
        {
            "sample_id": sample_id,
            "source_pdf_path": str(pdf_source.resolve()),
            "staged_pdf_relpath": relative_posix(pdf_target, root),
            "record_relpath": relative_posix(record_target, root),
            "jats_relpath": relative_posix(jats_target, root),
            "record_sha256": record_hash,
            "jats_sha256": jats_hash,
        }
    )
    for key in ("pdf_path", "record_path", "jats_path", "scores", "qualifies"):
        staged.pop(key, None)
    return staged


def _prune_staged_derivatives(config: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    root = Path(config["_project_root"]).resolve()
    allowed = {
        str(row[key])
        for row in rows
        for key in ("staged_pdf_relpath", "record_relpath", "jats_relpath")
    }
    removed: list[str] = []
    for relative_root in ("inputs/pdfs", "ground-truth/records", "ground-truth/jats"):
        directory = (root / relative_root).resolve()
        if not directory.is_dir() or root not in directory.parents:
            continue
        for path in directory.iterdir():
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative not in allowed:
                path.unlink()
                removed.append(relative)
    return sorted(removed)


def build_selection(config: dict[str, Any], overwrite: bool = False) -> dict[str, Any]:
    root = Path(config["_project_root"])
    manifest_path = project_path(config, "data", "manifest.jsonl")
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(f"Selection exists; use --overwrite-selection to rebuild: {manifest_path}")
    corpus_root = Path(config["_config_path"]).parent.joinpath(config["corpus_root"]).resolve()
    zotero_db = Path(config["zotero_db"]).resolve()
    zotero_storage = Path(config["zotero_storage"]).resolve()
    linked_base = Path(config["zotero_linked_base"]).resolve() if config.get("zotero_linked_base") else None
    tools = {key: str(Path(value).resolve()) for key, value in config["pdf_tools"].items()}

    corpus_by_doi = load_corpus_by_doi(corpus_root)
    zotero_rows = load_zotero_pdf_rows(zotero_db, zotero_storage, linked_base)
    existing_rows = [row for row in zotero_rows if row["pdf_exists"]]
    metadata_match_count = sum(normalize_doi(row.get("doi")) in corpus_by_doi for row in existing_rows)
    doi_scan_by_path: dict[str, list[str]] = {}
    doi_scan_errors: list[dict[str, str]] = []
    unique_paths = sorted({str(Path(row["pdf_path"]).resolve()) for row in existing_rows})
    with ThreadPoolExecutor(max_workers=min(8, max(len(unique_paths), 1))) as executor:
        futures = {
            executor.submit(extract_pdf_dois_first_pages, Path(path), tools["pdftotext"]): path
            for path in unique_paths
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                doi_scan_by_path[path.lower()] = future.result()
            except Exception as exc:
                doi_scan_by_path[path.lower()] = []
                doi_scan_errors.append({"doi": "", "pdf_path": path, "error": f"PDF DOI scan: {exc}"})

    matched: list[dict[str, Any]] = []
    ambiguous_doi_matches: list[dict[str, str]] = []
    for source in existing_rows:
        copy = dict(source)
        path_key = str(Path(copy["pdf_path"]).resolve()).lower()
        embedded = doi_scan_by_path.get(path_key, [])
        corpus_hits = [doi for doi in embedded if doi in corpus_by_doi]
        metadata_doi = normalize_doi(copy.get("doi"))
        if metadata_doi in corpus_hits:
            chosen_doi = metadata_doi
            match_source = "zotero_and_pdf_exact"
        elif metadata_doi not in corpus_by_doi and len(corpus_hits) == 1:
            chosen_doi = corpus_hits[0]
            match_source = "pdf_exact"
        elif len(corpus_hits) > 1:
            ambiguous_doi_matches.append(
                {
                    "doi": metadata_doi,
                    "pdf_path": str(copy["pdf_path"]),
                    "error": f"ambiguous PDF DOI matches: {corpus_hits}",
                }
            )
            continue
        else:
            continue
        copy["zotero_doi"] = metadata_doi
        copy["doi"] = chosen_doi
        copy["pdf_dois_first3"] = embedded
        copy["pdf_doi_verified"] = True
        copy["doi_match_source"] = match_source
        matched.append(copy)
    analyzed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = [*doi_scan_errors, *ambiguous_doi_matches]
    pdf_cache: dict[str, dict[str, Any]] = {}
    for index, attachment in enumerate(matched, 1):
        doi = attachment["doi"]
        corpus = corpus_by_doi[doi]
        pmcid = str(corpus["pmcid"])
        record_path = corpus_root / "records" / f"{pmcid}.json.gz"
        jats_path = corpus_root / "raw" / "jats" / f"{pmcid}.xml.gz"
        pdf_path = Path(attachment["pdf_path"])
        try:
            if not record_path.is_file() or not jats_path.is_file():
                raise FileNotFoundError("matched DOI lacks record or JATS source")
            cache_key = str(pdf_path.resolve()).lower()
            pdf_features = pdf_cache.get(cache_key)
            if pdf_features is None:
                pdf_features = analyze_pdf(pdf_path, tools)
                pdf_cache[cache_key] = pdf_features
            jats_features = analyze_jats(jats_path)
            record, searchable_text = _record_excerpt(record_path)
            row = {
                **attachment,
                **pdf_features,
                **jats_features,
                "paper_id": corpus.get("paper_id") or f"doi:{doi}",
                "pmcid": pmcid,
                "pmid": str(corpus.get("pmid") or ""),
                "title": record.get("title") or attachment.get("title") or corpus.get("title") or "",
                "journal": record.get("journal") or corpus.get("journal") or "",
                "year": int(record.get("year") or corpus.get("year") or 0),
                "publication_type": record.get("publication_type") or corpus.get("publication_type") or "",
                "discovery_strata": record.get("discovery_strata") or corpus.get("discovery_strata") or [],
                "record_path": str(record_path.resolve()),
                "jats_path": str(jats_path.resolve()),
            }
            add_selection_scores(row, searchable_text)
            row["eligible"] = bool(
                row.get("pdf_doi_verified")
                and
                not row["encrypted"]
                and int(row["page_count"]) >= int(config["sampling"]["minimum_pages"])
                and int(row["jats_body_chars"]) > 0
                and int(row["jats_section_count"]) > 0
            )
            analyzed.append(row)
        except Exception as exc:
            errors.append({"doi": doi, "pdf_path": str(pdf_path), "error": str(exc)})

    best_by_doi: dict[str, dict[str, Any]] = {}
    by_doi: dict[str, list[dict[str, Any]]] = {}
    for row in analyzed:
        if row.get("eligible"):
            by_doi.setdefault(row["doi"], []).append(row)
    for doi, rows in by_doi.items():
        best_by_doi[doi] = _choose_best_attachment(rows)
    hash_seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for row in sorted(best_by_doi.values(), key=lambda item: (item["doi"], item["pdf_sha256"])):
        if row["pdf_sha256"] in hash_seen:
            continue
        hash_seen.add(row["pdf_sha256"])
        candidates.append(row)

    strata = list(config["sampling"]["strata"])
    selected, reserves = select_stratified(
        candidates,
        strata,
        int(config["sampling"]["papers_per_stratum"]),
        int(config["sampling"]["reserve_per_stratum"]),
    )
    selected_staged: list[dict[str, Any]] = []
    reserve_staged: list[dict[str, Any]] = []
    per_stratum_index: Counter[str] = Counter()
    for global_index, row in enumerate(selected, 1):
        stratum = row["benchmark_stratum"]
        per_stratum_index[stratum] += 1
        sample_id = f"P{global_index:03d}"
        staged = _stage_row(config, row, sample_id)
        position = per_stratum_index[stratum]
        staged["stratum_index"] = position
        staged["pilot"] = position == 1
        staged["manual_review"] = position in {1, 5, 10}
        selected_staged.append(staged)
    for global_index, row in enumerate(reserves, 1):
        sample_id = f"R{global_index:03d}"
        staged = _stage_row(config, row, sample_id)
        staged["smoke"] = global_index == 1
        reserve_staged.append(staged)

    write_jsonl(project_path(config, "data", "candidates.jsonl"), candidates)
    write_jsonl(manifest_path, selected_staged)
    write_jsonl(project_path(config, "data", "reserve-manifest.jsonl"), reserve_staged)
    pruned_staged_files = _prune_staged_derivatives(config, [*selected_staged, *reserve_staged])
    write_json(project_path(config, "data", "selection-errors.json"), errors)
    csv_rows = []
    for row in selected_staged:
        csv_rows.append(
            {
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            }
        )
    write_csv(project_path(config, "data", "manifest.csv"), csv_rows)
    summary = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "zotero_pdf_rows": len(zotero_rows),
        "existing_zotero_pdfs": sum(bool(row["pdf_exists"]) for row in zotero_rows),
        "zotero_metadata_doi_corpus_matches": metadata_match_count,
        "exact_pdf_doi_attachment_matches": len(matched),
        "eligible_unique_doi_pdf_candidates": len(candidates),
        "selected": len(selected_staged),
        "reserve": len(reserve_staged),
        "analysis_errors": len(errors),
        "pruned_staged_files": pruned_staged_files,
        "strata": dict(Counter(row["benchmark_stratum"] for row in selected_staged)),
        "manifest_sha256": sha256_file(manifest_path),
    }
    write_json(project_path(config, "data", "selection-summary.json"), summary)
    return summary


def validate_manifest(config: dict[str, Any]) -> dict[str, Any]:
    root = Path(config["_project_root"])
    manifest_path = project_path(config, "data", "manifest.jsonl")
    rows = list(read_jsonl(manifest_path))
    reserve_path = project_path(config, "data", "reserve-manifest.jsonl")
    reserve_rows = list(read_jsonl(reserve_path))
    all_rows = [*rows, *reserve_rows]
    issues: list[str] = []
    strata = list(config["sampling"]["strata"])
    expected_per = int(config["sampling"]["papers_per_stratum"])
    counts = Counter(str(row.get("benchmark_stratum")) for row in rows)
    if len(rows) != expected_per * len(strata):
        issues.append(f"expected {expected_per * len(strata)} rows, found {len(rows)}")
    if len({row.get("doi") for row in rows}) != len(rows):
        issues.append("duplicate DOI in benchmark manifest")
    if len({row.get("pdf_sha256") for row in rows}) != len(rows):
        issues.append("duplicate PDF hash in benchmark manifest")
    if len(reserve_rows) != len(strata) * int(config["sampling"]["reserve_per_stratum"]):
        issues.append(f"unexpected reserve count: {len(reserve_rows)}")
    if len({row.get("doi") for row in all_rows}) != len(all_rows):
        issues.append("duplicate DOI across benchmark and reserve manifests")
    if len({row.get("pdf_sha256") for row in all_rows}) != len(all_rows):
        issues.append("duplicate PDF hash across benchmark and reserve manifests")
    for stratum in strata:
        if counts[stratum] != expected_per:
            issues.append(f"{stratum}: expected {expected_per}, found {counts[stratum]}")
    observed_order = [(str(row.get("benchmark_stratum")), str(row.get("doi")), str(row.get("pdf_sha256"))) for row in rows]
    expected_order = []
    for stratum in strata:
        expected_order.extend(
            sorted(
                [item for item in observed_order if item[0] == stratum],
                key=lambda item: (item[1], item[2]),
            )
        )
    if observed_order != expected_order:
        issues.append("manifest order is not stratum order followed by DOI/PDF hash")
    for row in all_rows:
        if not row.get("pdf_doi_verified") or normalize_doi(row.get("doi")) not in {
            normalize_doi(value) for value in row.get("pdf_dois_first3", [])
        }:
            issues.append(f"{row.get('sample_id')}: PDF DOI is not verified against the corpus DOI")
        for rel_key, hash_key in (
            ("staged_pdf_relpath", "pdf_sha256"),
            ("record_relpath", "record_sha256"),
            ("jats_relpath", "jats_sha256"),
        ):
            path = root / str(row.get(rel_key) or "")
            if not path.is_file():
                issues.append(f"{row.get('sample_id')}: missing {rel_key}: {path}")
            elif sha256_file(path) != row.get(hash_key):
                issues.append(f"{row.get('sample_id')}: hash mismatch for {rel_key}")
    allowed = {
        str(row[key])
        for row in all_rows
        for key in ("staged_pdf_relpath", "record_relpath", "jats_relpath")
    }
    for relative_root in ("inputs/pdfs", "ground-truth/records", "ground-truth/jats"):
        directory = root / relative_root
        for path in directory.glob("*") if directory.is_dir() else []:
            if path.is_file() and path.relative_to(root).as_posix() not in allowed:
                issues.append(f"unreferenced staged file: {path.relative_to(root).as_posix()}")
    result = {
        "status": "PASS" if not issues else "FAIL",
        "rows": len(rows),
        "reserve_rows": len(reserve_rows),
        "strata": dict(counts),
        "issues": issues,
        "manifest_sha256": sha256_file(manifest_path),
    }
    write_json(project_path(config, "data", "manifest-validation.json"), result)
    return result

#!/usr/bin/env python3
"""Extract page-preserving text from a PDF into JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import sha256_file, utc_now, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Input PDF")
    parser.add_argument("--out", type=Path, help="Output JSONL; defaults beside the PDF")
    parser.add_argument("--min-chars", type=int, default=40, help="Minimum non-space characters for a text page")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file")
    return parser.parse_args()


def extract_with_pdfplumber(path: Path) -> tuple[str, list[str]]:
    import pdfplumber  # type: ignore

    with pdfplumber.open(path) as pdf:
        return "pdfplumber", [(page.extract_text(x_tolerance=2, y_tolerance=3) or "") for page in pdf.pages]


def extract_with_pypdf(path: Path) -> tuple[str, list[str]]:
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(str(path))
    return "pypdf", [(page.extract_text() or "") for page in reader.pages]


def main() -> int:
    args = parse_args()
    source = args.pdf.resolve()
    output = (args.out or source.with_suffix(".pages.jsonl")).resolve()

    if not source.is_file():
        print(f"ERROR: PDF not found: {source}", file=sys.stderr)
        return 1
    if output.exists() and not args.overwrite:
        print(f"ERROR: Output exists; pass --overwrite to replace it: {output}", file=sys.stderr)
        return 1

    errors: list[str] = []
    extractor = ""
    pages: list[str] = []
    for function in (extract_with_pdfplumber, extract_with_pypdf):
        try:
            extractor, pages = function(source)
            break
        except Exception as exc:
            errors.append(f"{function.__name__}: {exc}")
    if not pages and errors:
        print("ERROR: No PDF extractor succeeded: " + " | ".join(errors), file=sys.stderr)
        return 1

    digest = sha256_file(source)
    extracted_at = utc_now()
    rows = []
    status_counts = {"text": 0, "sparse": 0, "ocr_required": 0}
    for index, raw_text in enumerate(pages, 1):
        text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        non_space = sum(not char.isspace() for char in text)
        if non_space >= args.min_chars:
            status = "text"
        elif non_space:
            status = "sparse"
        else:
            status = "ocr_required"
        status_counts[status] += 1
        rows.append(
            {
                "schema_version": "1.0",
                "source_path": str(source),
                "source_sha256": digest,
                "page_number": index,
                "extractor": extractor,
                "extracted_at": extracted_at,
                "char_count": len(text),
                "non_space_char_count": non_space,
                "extraction_status": status,
                "text": text,
            }
        )

    write_jsonl(output, rows)
    print(
        json.dumps(
            {
                "source": str(source),
                "output": str(output),
                "extractor": extractor,
                "pages": len(rows),
                "status_counts": status_counts,
                "source_sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

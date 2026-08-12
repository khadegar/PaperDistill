from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild JATS-surrogate provenance from the immutable transfer manifest.")
    parser.add_argument("--corpus-root", type=Path, default=Path("corpus-10000"))
    parser.add_argument("--transfer-manifest", type=Path, default=Path("corpus-10000/transfer-manifest.jsonl"))
    parser.add_argument("--source-manifest", type=Path, default=Path("../biomechanics-corpus/manifest.jsonl"))
    parser.add_argument("--jats-root", type=Path, default=Path("../biomechanics-corpus/raw/jats"))
    parser.add_argument("--overrides", type=Path, default=Path("corpus-10000/source-overrides.jsonl"))
    args = parser.parse_args()

    corpus_root = args.corpus_root.resolve()
    transfer_rows = read_jsonl(args.transfer_manifest)
    source_rows = {str(row.get("pmcid") or ""): row for row in read_jsonl(args.source_manifest)}
    existing = {str(row.get("pmcid") or ""): row for row in read_jsonl(args.overrides)}

    rebuilt: list[str] = []
    skipped: list[dict[str, str]] = []
    for transfer in transfer_rows:
        if transfer.get("input_kind") != "jats_rendered_pdf_surrogate":
            continue
        pmcid = str(transfer.get("pmcid") or "")
        if not pmcid:
            continue
        pdf = corpus_root / "pdfs" / f"{pmcid}.pdf"
        jats = args.jats_root.resolve() / f"{pmcid}.xml.gz"
        if not pdf.is_file() or not jats.is_file():
            skipped.append({"pmcid": pmcid, "reason": "pdf_or_jats_missing"})
            continue
        source = source_rows.get(pmcid, {})
        previous = existing.get(pmcid, {})
        existing[pmcid] = {
            "pmcid": pmcid,
            "doi": str(source.get("doi") or previous.get("doi") or ""),
            "status": "withdrawn_pdf_unavailable",
            "input_kind": "jats_rendered_pdf_surrogate",
            "reason": "The public PDF returned HTTP 404; retained corpus JATS was rendered for parser coverage and is not the publisher PDF.",
            "source_relpath": f"../biomechanics-corpus/raw/jats/{pmcid}.xml.gz",
            "source_sha256": sha256_file(jats),
            "pdf_relpath": f"corpus-10000/pdfs/{pmcid}.pdf",
            "pdf_sha256": sha256_file(pdf),
            "renderer": str(previous.get("renderer") or "pandoc jats->xelatex"),
            "figures_embedded": False,
        }
        rebuilt.append(pmcid)

    atomic_write_jsonl(args.overrides, [existing[key] for key in sorted(existing)])
    print(json.dumps({"rebuilt": rebuilt, "skipped": skipped, "overrides": len(existing)}, ensure_ascii=False, indent=2))
    return 0 if not skipped else 2


if __name__ == "__main__":
    raise SystemExit(main())

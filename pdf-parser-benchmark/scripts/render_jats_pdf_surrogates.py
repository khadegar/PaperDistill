from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
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


def looks_like_pdf(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 10240:
        return False
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def strip_tex_math_blocks(xml_text: str) -> str:
    """Remove raw JATS tex-math alternatives before Pandoc PDF rendering.

    PMC JATS often stores a full LaTeX preamble inside ``<tex-math>`` while
    the sibling ``<mml:math>`` contains the same equation in MathML.  Pandoc
    can render the MathML alternative, but may copy the preamble into the
    document body and make XeLaTeX fail.  The source JATS remains untouched;
    this is only a deterministic rendering workaround.
    """
    return re.sub(r"<tex-math\b[^>]*>.*?</tex-math>", "", xml_text, flags=re.DOTALL)


def flatten_jats_lists(xml_text: str) -> str:
    """Flatten malformed nested JATS lists into readable cell text.

    A small number of PMC table cells contain list markup that Pandoc turns
    into an invalid nested LaTeX enumerate.  Keeping the list-item text while
    replacing the structural tags makes the surrogate renderable and leaves
    the original JATS available through ``source_relpath``.
    """
    xml_text = re.sub(r"<list\b[^>]*>", "", xml_text)
    xml_text = xml_text.replace("</list>", "")
    xml_text = re.sub(r"<list-item\b[^>]*>", "• ", xml_text)
    return xml_text.replace("</list-item>", "; ")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
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
    parser = argparse.ArgumentParser(
        description="Render traceable JATS-to-PDF surrogates only for corpus PDFs withdrawn from public download."
    )
    parser.add_argument("--source-manifest", type=Path, default=Path("../biomechanics-corpus/manifest.jsonl"))
    parser.add_argument("--download-state", type=Path, default=Path("corpus-10000/download-state.jsonl"))
    parser.add_argument("--jats-root", type=Path, default=Path("../biomechanics-corpus/raw/jats"))
    parser.add_argument("--corpus-root", type=Path, default=Path("corpus-10000"))
    parser.add_argument("--overrides", type=Path, default=Path("corpus-10000/source-overrides.jsonl"))
    parser.add_argument("--pandoc", default="pandoc")
    parser.add_argument("--pdf-engine", default="xelatex")
    parser.add_argument("--pmcid", action="append", default=[])
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--override-status", default="withdrawn_pdf_unavailable")
    parser.add_argument(
        "--override-reason",
        default="The public PDF returned HTTP 404; retained corpus JATS was rendered for parser coverage and is not the publisher PDF.",
    )
    parser.add_argument(
        "--strip-tex-math",
        action="store_true",
        help="Remove raw <tex-math> alternatives before Pandoc rendering; retain sibling MathML.",
    )
    parser.add_argument(
        "--flatten-jats-lists",
        action="store_true",
        help="Flatten JATS list tags when malformed nested lists break LaTeX table rendering.",
    )
    args = parser.parse_args()

    manifest = {str(row.get("pmcid") or ""): row for row in read_jsonl(args.source_manifest)}
    state = {str(row.get("pmcid") or ""): row for row in read_jsonl(args.download_state)}
    existing = {str(row.get("pmcid") or ""): row for row in read_jsonl(args.overrides)}
    requested = set(args.pmcid)
    if requested:
        targets = sorted(requested)
    else:
        targets = sorted(
            pmcid
            for pmcid, row in state.items()
            if row.get("status") == "error"
            and (
                "HTTP Error 404" in str(row.get("error") or "")
                or "package_contains_no_pdf" in str(row.get("error") or "")
            )
            and (args.overwrite or pmcid not in existing)
        )

    pdf_dir = args.corpus_root.resolve() / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    work_root = args.corpus_root.resolve() / "surrogate-jats"
    work_root.mkdir(parents=True, exist_ok=True)
    # Do not retain provenance records for an earlier failed render (for
    # example, Pandoc writing HTML because of the old temporary suffix).
    for pmcid, record in list(existing.items()):
        if record.get("input_kind") == "jats_rendered_pdf_surrogate":
            relpath = str(record.get("pdf_relpath") or "")
            prefix = f"{args.corpus_root.as_posix().rstrip('/')}/"
            if relpath.startswith(prefix):
                relpath = relpath[len(prefix) :]
            candidate = args.corpus_root.resolve() / relpath
            if not looks_like_pdf(candidate):
                existing.pop(pmcid, None)
    rendered: list[str] = []
    skipped: list[dict[str, str]] = []
    for pmcid in targets:
        source = manifest.get(pmcid, {})
        jats_gz = args.jats_root.resolve() / f"{pmcid}.xml.gz"
        output = pdf_dir / f"{pmcid}.pdf"
        if not jats_gz.is_file():
            skipped.append({"pmcid": pmcid, "reason": "jats_missing"})
            continue
        if output.is_file() and not args.overwrite:
            skipped.append({"pmcid": pmcid, "reason": "pdf_exists"})
            continue
        xml_fd, xml_name = tempfile.mkstemp(prefix=f".{pmcid}.", suffix=".xml.tmp", dir=work_root)
        os.close(xml_fd)
        # Keep the .pdf suffix: Pandoc infers the output writer from the
        # extension, and a trailing .tmp causes it to emit HTML despite
        # --pdf-engine being present.
        pdf_fd, pdf_name = tempfile.mkstemp(prefix=f".{pmcid}.", suffix=".pdf", dir=work_root)
        os.close(pdf_fd)
        xml = Path(xml_name)
        temporary_pdf = Path(pdf_name)
        try:
            with gzip.open(jats_gz, "rb") as input_handle, xml.open("wb") as output_handle:
                if args.strip_tex_math:
                    source_text = input_handle.read().decode("utf-8", errors="replace")
                    source_text = strip_tex_math_blocks(source_text)
                    if args.flatten_jats_lists:
                        source_text = flatten_jats_lists(source_text)
                    output_handle.write(source_text.encode("utf-8"))
                elif args.flatten_jats_lists:
                    source_text = input_handle.read().decode("utf-8", errors="replace")
                    output_handle.write(flatten_jats_lists(source_text).encode("utf-8"))
                else:
                    for block in iter(lambda: input_handle.read(1024 * 1024), b""):
                        output_handle.write(block)
            completed = subprocess.run(
                [
                    args.pandoc,
                    str(xml),
                    "--from=jats",
                    f"--pdf-engine={args.pdf_engine}",
                    "-V",
                    "geometry:margin=20mm",
                    "-V",
                    "papersize:a4",
                    "-o",
                    str(temporary_pdf),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
            if completed.returncode != 0 or not looks_like_pdf(temporary_pdf):
                skipped.append({"pmcid": pmcid, "reason": f"pandoc_failed_or_non_pdf:{completed.returncode}"})
                continue
            fd, staged_name = tempfile.mkstemp(prefix=f".{pmcid}.", suffix=".pdf.tmp", dir=pdf_dir)
            os.close(fd)
            staged = Path(staged_name)
            try:
                shutil.copyfile(temporary_pdf, staged)
                os.replace(staged, output)
            except Exception:
                staged.unlink(missing_ok=True)
                raise
        finally:
            xml.unlink(missing_ok=True)
            temporary_pdf.unlink(missing_ok=True)
        existing[pmcid] = {
            "pmcid": pmcid,
            "doi": str(source.get("doi") or state.get(pmcid, {}).get("doi") or ""),
            "status": args.override_status,
            "input_kind": "jats_rendered_pdf_surrogate",
            "reason": args.override_reason,
            "source_relpath": f"../biomechanics-corpus/raw/jats/{pmcid}.xml.gz",
            "source_sha256": sha256_file(jats_gz),
            "pdf_relpath": f"corpus-10000/pdfs/{pmcid}.pdf",
            "pdf_sha256": sha256_file(output),
            "renderer": (
                f"pandoc jats->{args.pdf_engine} ("
                + ", ".join(
                    option
                    for option, enabled in (
                        ("strip-tex-math", args.strip_tex_math),
                        ("flatten-jats-lists", args.flatten_jats_lists),
                    )
                    if enabled
                )
                + ")"
                if args.strip_tex_math or args.flatten_jats_lists
                else f"pandoc jats->{args.pdf_engine}"
            ),
            "figures_embedded": False,
        }
        rendered.append(pmcid)

    write_jsonl(args.overrides, [existing[key] for key in sorted(existing)])
    result = {"targets": len(targets), "rendered": rendered, "skipped": skipped, "overrides": len(existing)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not skipped else 2


if __name__ == "__main__":
    raise SystemExit(main())

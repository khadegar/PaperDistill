#!/usr/bin/env python3
"""Validate and export a canonical LaTeX manuscript to PDF, DOCX, or Markdown."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SUPPORTED_FORMATS = {"pdf", "docx", "markdown", "md"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--formats", default="pdf,docx", help="Comma-separated: pdf,docx,markdown")
    parser.add_argument("--reference-doc", type=Path, help="Optional Pandoc reference DOCX")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing export files")
    return parser.parse_args()


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def fail_process(label: str, result: subprocess.CompletedProcess[str]) -> RuntimeError:
    detail = (result.stderr or result.stdout or "no diagnostic output").strip()
    return RuntimeError(f"{label} failed with exit code {result.returncode}: {detail[-4000:]}")


def validate(project: Path) -> dict:
    validator = Path(__file__).with_name("validate_project.py")
    result = run([sys.executable, str(validator), "--project", str(project), "--json"], project)
    try:
        report = json.loads(result.stdout)
    except Exception as exc:
        raise RuntimeError(f"Project validator did not return JSON: {exc}; {result.stderr}") from exc
    if result.returncode != 0:
        raise RuntimeError("Project validation failed; resolve blocking findings before export")
    return report


def ensure_available_output(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Export exists; pass --overwrite to replace it: {path}")


def export_pdf(manuscript_dir: Path, tex: Path, output: Path) -> dict:
    engine = shutil.which("tectonic")
    with tempfile.TemporaryDirectory(prefix="biomech-tex-") as temporary:
        temp_dir = Path(temporary)
        if engine:
            result = run([engine, "--keep-logs", "--outdir", str(temp_dir), tex.name], manuscript_dir)
            if result.returncode != 0:
                raise fail_process("Tectonic", result)
            used = "tectonic"
        else:
            engine = shutil.which("xelatex") or shutil.which("pdflatex")
            if not engine:
                raise RuntimeError("No LaTeX engine found (tectonic, xelatex, or pdflatex)")
            command = [engine, "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={temp_dir}", tex.name]
            for _ in range(2):
                result = run(command, manuscript_dir)
                if result.returncode != 0:
                    raise fail_process(Path(engine).name, result)
            used = Path(engine).stem
        generated = temp_dir / f"{tex.stem}.pdf"
        if not generated.is_file() or generated.stat().st_size == 0:
            raise RuntimeError("LaTeX engine completed without a non-empty PDF")
        shutil.copy2(generated, output)
    return {"format": "pdf", "path": str(output), "engine": used, "bytes": output.stat().st_size}


def export_with_pandoc(
    manuscript_dir: Path,
    tex: Path,
    bib: Path,
    output: Path,
    output_format: str,
    reference_doc: Path | None,
) -> dict:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise RuntimeError("Pandoc was not found")
    command = [pandoc, tex.name, "--from=latex", "--citeproc", "--bibliography", bib.name, "--output", str(output)]
    if output_format == "docx" and reference_doc:
        command.extend(["--reference-doc", str(reference_doc.resolve())])
    result = run(command, manuscript_dir)
    if result.returncode != 0:
        raise fail_process("Pandoc", result)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"Pandoc completed without a non-empty {output_format} file")
    value = {"format": output_format, "path": str(output), "engine": "pandoc", "bytes": output.stat().st_size}
    if output_format == "docx":
        value["visual_qa_required"] = True
    return value


def main() -> int:
    args = parse_args()
    project = args.project.resolve()
    requested = {part.strip().casefold() for part in args.formats.split(",") if part.strip()}
    requested = {"markdown" if value == "md" else value for value in requested}
    unknown = requested - SUPPORTED_FORMATS
    if unknown:
        print(f"ERROR: Unsupported formats: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1
    if not requested:
        print("ERROR: No export formats requested", file=sys.stderr)
        return 1

    manuscript_dir = project / "manuscript"
    tex = manuscript_dir / "main.tex"
    bib = manuscript_dir / "references.bib"
    exports = project / "exports"
    if not tex.is_file() or not bib.is_file():
        print(f"ERROR: Canonical manuscript files are missing under {manuscript_dir}", file=sys.stderr)
        return 1

    output_paths = {
        "pdf": exports / "manuscript.pdf",
        "docx": exports / "manuscript.docx",
        "markdown": exports / "manuscript.md",
    }
    try:
        validation_report = validate(project)
        exports.mkdir(parents=True, exist_ok=True)
        for value in requested:
            ensure_available_output(output_paths[value], args.overwrite)

        outputs = []
        if "pdf" in requested:
            outputs.append(export_pdf(manuscript_dir, tex, output_paths["pdf"]))
        if "docx" in requested:
            outputs.append(
                export_with_pandoc(
                    manuscript_dir,
                    tex,
                    bib,
                    output_paths["docx"],
                    "docx",
                    args.reference_doc,
                )
            )
        if "markdown" in requested:
            outputs.append(
                export_with_pandoc(
                    manuscript_dir,
                    tex,
                    bib,
                    output_paths["markdown"],
                    "markdown",
                    None,
                )
            )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "project": str(project),
                "validation": validation_report["verdict"],
                "outputs": outputs,
                "next_gate": "Render and inspect every DOCX page before delivery" if "docx" in requested else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

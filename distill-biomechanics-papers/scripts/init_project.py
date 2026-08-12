#!/usr/bin/env python3
"""Initialize an auditable biomechanics literature-to-manuscript project."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from _common import latex_escape, utc_now, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path, help="New or empty project directory")
    parser.add_argument("--title", help="Project title; defaults to the target directory name")
    return parser.parse_args()


def ensure_empty_target(target: Path) -> None:
    if target.exists() and not target.is_dir():
        raise ValueError(f"Target exists and is not a directory: {target}")
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"Target directory is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)


def main() -> int:
    args = parse_args()
    target = args.target.resolve()
    title = args.title or target.name
    skill_root = Path(__file__).resolve().parents[1]
    assets = skill_root / "assets"

    try:
        ensure_empty_target(target)
        for relative in (
            "search",
            "corpus/records",
            "corpus/extracts",
            "evidence",
            "manuscript/sections",
            "audit",
            "exports",
            "templates",
        ):
            (target / relative).mkdir(parents=True, exist_ok=True)

        project_template = json.loads((assets / "project-template.json").read_text(encoding="utf-8"))
        timestamp = utc_now()
        project_template["title"] = title
        project_template["created_at"] = timestamp
        project_template["updated_at"] = timestamp
        write_json(target / "project.json", project_template)

        (target / "search/search-log.csv").write_text(
            "search_id,run_at,database,query,date_range,filters,result_count,export_file,notes\n",
            encoding="utf-8",
            newline="\n",
        )
        (target / "corpus/manifest.jsonl").write_text("", encoding="utf-8")
        (target / "evidence/claims.jsonl").write_text("", encoding="utf-8")
        (target / "manuscript/references.bib").write_text("", encoding="utf-8")

        tex = (assets / "manuscript-template.tex").read_text(encoding="utf-8")
        tex = tex.replace("{{TITLE_TEX}}", latex_escape(title))
        (target / "manuscript/main.tex").write_text(tex, encoding="utf-8", newline="\n")

        shutil.copy2(assets / "paper-record-template.json", target / "templates/paper-record.json")
        shutil.copy2(assets / "claim-template.json", target / "templates/claim.json")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"project": str(target), "title": title, "created_at": timestamp}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


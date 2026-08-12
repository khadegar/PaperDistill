#!/usr/bin/env python3
"""Count normalized journal titles in a Zotero BibTeX export."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from _common import normalize_doi, normalize_title


DOMAIN_PATTERN = re.compile(
    r"biomech|bone|implant|scaffold|porous|topolog|additive|3d\s*print|femur|femoral|"
    r"spine|spinal|vertebr|interbody|cage|orthop|fracture|trabecular|titanium|lattice|"
    r"tpms|osseointegr|mechanobiolog|finite\s+element|prosthe|hip|joint|fatigue|"
    r"stress\s+shielding|musculoskeletal|osteogen|fixation|pelvi|mandib",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bib", required=True, type=Path, help="Zotero BibTeX export")
    parser.add_argument("--top", type=int, default=50, help="Number of journals to report")
    parser.add_argument(
        "--domain",
        choices=("all", "biomechanics"),
        default="all",
        help="Optional title/keyword domain filter",
    )
    parser.add_argument("--contains", help="Additional case-insensitive regex applied to title and keywords")
    parser.add_argument("--aliases", type=Path, help="JSON map of journal aliases to canonical titles")
    parser.add_argument("--all-types", action="store_true", help="Include non-article entries that carry a journal field")
    parser.add_argument("--format", choices=("table", "json", "csv"), default="table")
    parser.add_argument("--out", type=Path, help="Optional output file")
    return parser.parse_args()


def unwrap_bib_value(value: str) -> str:
    value = value.strip().rstrip(",").strip()
    if len(value) >= 2 and ((value[0] == "{" and value[-1] == "}") or (value[0] == '"' and value[-1] == '"')):
        value = value[1:-1]
    return value.strip()


def clean_tex(value: str) -> str:
    value = value.replace(r"\&", "&").replace("~", " ")
    value = re.sub(r"\\(?:textit|emph|textbf)\{([^{}]*)\}", r"\1", value)
    value = value.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", value).strip()


def parse_bibtex(path: Path) -> list[dict[str, str]]:
    start = re.compile(r"^@(\w+)\{([^,]+),\s*$")
    field = re.compile(r"^\s*(title|journal|journaltitle|doi|year|keywords)\s*=\s*(.*)\s*$", re.IGNORECASE)
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.rstrip("\r\n")
            match = start.match(line)
            if match:
                if current is not None:
                    entries.append(current)
                current = {"entry_type": match.group(1).casefold(), "key": match.group(2).strip()}
                continue
            if current is None:
                continue
            match = field.match(line)
            if match:
                name = match.group(1).casefold()
                if name == "journaltitle":
                    name = "journal"
                current[name] = unwrap_bib_value(match.group(2))
        if current is not None:
            entries.append(current)
    return entries


def journal_key(value: str) -> str:
    value = clean_tex(value).casefold().replace("&", " and ")
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def load_aliases(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {journal_key(str(key)): str(value) for key, value in raw.items()}


def default_display(value: str) -> str:
    value = clean_tex(value)
    replacements = {
        "Ieee ": "IEEE ",
        "Bmc ": "BMC ",
        "Acs ": "ACS ",
        "Plos ": "PLOS ",
        "Cirp ": "CIRP ",
    }
    for old, new in replacements.items():
        if value.startswith(old):
            value = new + value[len(old) :]
    return value


def entry_identity(entry: dict[str, str]) -> str:
    doi = normalize_doi(clean_tex(entry.get("doi", "")))
    if doi:
        return "doi:" + doi
    title = normalize_title(clean_tex(entry.get("title", "")))
    year = clean_tex(entry.get("year", ""))
    return f"title:{title}:{year}"


def render_table(report: dict[str, Any]) -> str:
    rows = report["journals"]
    width = max([len("Journal")] + [len(row["journal"]) for row in rows])
    lines = [
        f"entries={report['total_entries']} with_journal={report['with_journal']} "
        f"unique={report['unique_with_journal']} duplicates_removed={report['duplicates_removed']}",
        f"{'Count':>5}  {'Journal':<{width}}",
        f"{'-' * 5}  {'-' * width}",
    ]
    lines.extend(f"{row['count']:>5}  {row['journal']:<{width}}" for row in rows)
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    bib = args.bib.resolve()
    if not bib.is_file():
        print(f"ERROR: BibTeX file not found: {bib}", file=sys.stderr)
        return 1

    default_aliases = Path(__file__).resolve().parents[1] / "assets/journal-aliases.json"
    aliases = load_aliases((args.aliases or default_aliases).resolve())
    custom_pattern = re.compile(args.contains, re.IGNORECASE) if args.contains else None

    entries = parse_bibtex(bib)
    candidates: list[dict[str, str]] = []
    for entry in entries:
        if not entry.get("journal"):
            continue
        if not args.all_types and entry.get("entry_type") != "article":
            continue
        searchable = " ".join((clean_tex(entry.get("title", "")), clean_tex(entry.get("keywords", ""))))
        if args.domain == "biomechanics" and not DOMAIN_PATTERN.search(searchable):
            continue
        if custom_pattern and not custom_pattern.search(searchable):
            continue
        candidates.append(entry)

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for entry in candidates:
        identity = entry_identity(entry)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        unique.append(entry)

    counts: Counter[str] = Counter()
    variants: dict[str, Counter[str]] = defaultdict(Counter)
    for entry in unique:
        raw = default_display(entry["journal"])
        key = journal_key(raw)
        canonical = aliases.get(key)
        group_key = journal_key(canonical) if canonical else key
        counts[group_key] += 1
        variants[group_key][canonical or raw] += 1

    journals = []
    for key, count in counts.most_common(max(args.top, 0)):
        display = variants[key].most_common(1)[0][0]
        journals.append({"journal": display, "count": count})

    report = {
        "source": str(bib),
        "domain": args.domain,
        "contains": args.contains,
        "total_entries": len(entries),
        "with_journal": len(candidates),
        "unique_with_journal": len(unique),
        "duplicates_removed": len(candidates) - len(unique),
        "journals": journals,
    }

    if args.format == "json":
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
    elif args.format == "csv":
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("count", "journal"))
                writer.writeheader()
                for row in journals:
                    writer.writerow({"count": row["count"], "journal": row["journal"]})
            print(json.dumps({"output": str(args.out.resolve()), "journals": len(journals)}, ensure_ascii=False))
            return 0
        from io import StringIO

        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=("count", "journal"))
        writer.writeheader()
        for row in journals:
            writer.writerow({"count": row["count"], "journal": row["journal"]})
        rendered = buffer.getvalue().rstrip()
    else:
        rendered = render_table(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({"output": str(args.out.resolve()), "journals": len(journals)}, ensure_ascii=False))
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


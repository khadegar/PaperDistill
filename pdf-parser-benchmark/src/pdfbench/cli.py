from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .common import load_config
from .deployment import build_bundle_manifest, verify_bundle_manifest
from .evaluation import assess_repeatability, build_blind_package, evaluate_runs
from .reporting import render_report
from .runner import gpu_preflight, run_benchmark
from .selection import build_selection, validate_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auditable A100 PDF-to-Markdown benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_config(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config", type=Path, required=True)

    select = subparsers.add_parser("select", help="Build and stage the deterministic 50-paper sample")
    add_config(select)
    select.add_argument("--overwrite-selection", action="store_true")

    validate = subparsers.add_parser("validate-manifest", help="Verify counts, uniqueness, and staged hashes")
    add_config(validate)

    preflight = subparsers.add_parser("preflight", help="Sample the GPU and enforce the idle gate")
    add_config(preflight)
    preflight.add_argument("--duration", type=int)

    run = subparsers.add_parser("run", help="Run one or all parsers sequentially")
    add_config(run)
    run.add_argument("--tool", choices=["all", "mineru", "marker", "docling"], required=True)
    run.add_argument(
        "--sample-set",
        choices=["smoke", "pilot", "full", "repeat", "corpus10000"],
        default="full",
    )
    run.add_argument("--sample-id", action="append", default=[])
    run.add_argument("--run-label", default="main")
    run.add_argument("--no-resume", action="store_true")

    evaluate = subparsers.add_parser("evaluate", help="Compare primary Markdown outputs with JATS")
    add_config(evaluate)
    evaluate.add_argument("--run-label", default="main")
    evaluate.add_argument("--sample-set", choices=["full", "pilot", "repeat"], default="full")

    blind = subparsers.add_parser("blind-package", help="Create the 15-paper blinded manual-review package")
    add_config(blind)
    blind.add_argument("--run-label", default="main")
    blind.add_argument("--overwrite-blind-package", action="store_true")

    repeatability = subparsers.add_parser(
        "repeatability", help="Compare the fixed five-paper rerun with the main run"
    )
    add_config(repeatability)
    repeatability.add_argument("--baseline-label", default="main")
    repeatability.add_argument("--repeat-label", default="repeat")

    report = subparsers.add_parser("report", help="Aggregate scores and render the final report")
    add_config(report)
    report.add_argument("--run-label", default="main")

    bundle = subparsers.add_parser("bundle-manifest", help="Hash source, wheels, models, PDFs, and JATS")
    add_config(bundle)

    verify_bundle = subparsers.add_parser("verify-bundle", help="Verify the offline transfer manifest")
    add_config(verify_bundle)
    return parser


def _print(value: Any) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "select":
            result = build_selection(config, overwrite=args.overwrite_selection)
        elif args.command == "validate-manifest":
            result = validate_manifest(config)
        elif args.command == "preflight":
            result = gpu_preflight(config, duration=args.duration)
        elif args.command == "run":
            tools = list(config["tools"]) if args.tool == "all" else [args.tool]
            result = run_benchmark(
                config,
                tools=tools,
                sample_set=args.sample_set,
                sample_ids=args.sample_id,
                run_label=args.run_label,
                resume=not args.no_resume,
            )
        elif args.command == "evaluate":
            result = evaluate_runs(config, run_label=args.run_label, sample_set=args.sample_set)
        elif args.command == "blind-package":
            result = build_blind_package(
                config,
                run_label=args.run_label,
                overwrite=args.overwrite_blind_package,
            )
        elif args.command == "repeatability":
            result = assess_repeatability(
                config,
                baseline_label=args.baseline_label,
                repeat_label=args.repeat_label,
            )
        elif args.command == "report":
            result = render_report(config, run_label=args.run_label)
        elif args.command == "bundle-manifest":
            result = build_bundle_manifest(config)
        elif args.command == "verify-bundle":
            result = verify_bundle_manifest(config)
        else:  # pragma: no cover
            raise RuntimeError(args.command)
        _print(result)
        return 0 if result.get("status") not in {"FAIL", "BLOCKED"} else 2
    except Exception as exc:
        _print({"status": "ERROR", "command": args.command, "error": str(exc)})
        return 1

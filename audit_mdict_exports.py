#!/usr/bin/env python3
"""Audit Apple Dictionary XML exports before packing them as MDict files."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from convert_to_mdict import (
    ConversionError,
    audit_dictionary,
    load_manifest,
    source_resources,
)


def audit_exports(export_directory: Path) -> list[dict[str, object]]:
    results = []
    for item in load_manifest(export_directory):
        xml_path = export_directory / item["output"]
        if not xml_path.is_file():
            raise ConversionError(f"exported XML does not exist: {xml_path}")
        audit = audit_dictionary(xml_path, source_resources(item))
        result = {"dictionary": item["dictionary"], "file": item["output"]}
        result.update(asdict(audit))
        results.append(result)
    return results


def print_report(results: Sequence[dict[str, object]]) -> None:
    for result in results:
        stylesheet_names = result["stylesheets"] or ("none",)
        stylesheets = ", ".join(stylesheet_names)
        print(result["dictionary"])
        print(
            f"  {result['entry_count']} entries; "
            f"{result['lookup_links']} lookups; "
            f"{result['reference_links']} references"
        )
        print(
            f"  {result['unresolved_reference_links']} unresolved references; "
            f"{result['missing_anchors']} missing anchors; CSS: {stylesheets}"
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate exported dictionary XML, links, anchors, and CSS."
    )
    parser.add_argument("export_directory", type=Path, help="XML export directory")
    parser.add_argument(
        "--json", action="store_true", help="write the audit report as JSON"
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    export_directory = arguments.export_directory.expanduser()
    if not export_directory.is_dir():
        print(
            f"error: export directory does not exist: {export_directory}",
            file=sys.stderr,
        )
        return 2

    try:
        results = audit_exports(export_directory)
    except (ConversionError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if arguments.json:
        json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

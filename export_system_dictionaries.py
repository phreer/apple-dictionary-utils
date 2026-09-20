#!/usr/bin/env python3
"""Export all regular macOS dictionary assets to an output directory."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from extract_dictionary import DictionaryFormatError, extract
from list_dictionary_assets import (
    DEFAULT_ASSET_DIRECTORY,
    DictionaryAsset,
    iter_assets,
)


MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class ExportJob:
    asset: DictionaryAsset
    package_path: Path
    body_path: Path
    output_name: str


@dataclass(frozen=True)
class ExportResult:
    asset: str
    dictionary: str
    dictionary_identifier: str | None
    package: str
    source: str
    output: str
    entry_count: int
    size_bytes: int


def build_export_jobs(asset_directory: Path) -> list[ExportJob]:
    jobs = []
    output_names: dict[str, Path] = {}

    for asset in iter_assets(asset_directory):
        for package in asset.packages:
            package_path = Path(package["path"])
            body_path = package_path / "Contents" / "Resources" / "Body.data"

            # Apple Dictionary stores only localized .lproj/Body.data files.
            # Requiring a root Body.data selects the regular dictionaries.
            if not body_path.is_file():
                continue

            output_name = f"{package_path.stem}.xml"
            normalized_name = output_name.casefold()
            previous_path = output_names.get(normalized_name)
            if previous_path is not None:
                raise ValueError(
                    f"output name collision: {previous_path} and {package_path} "
                    f"both map to {output_name}"
                )
            output_names[normalized_name] = package_path
            jobs.append(
                ExportJob(
                    asset=asset,
                    package_path=package_path,
                    body_path=body_path,
                    output_name=output_name,
                )
            )

    return jobs


def temporary_path(directory: Path, output_name: str) -> Path:
    with tempfile.NamedTemporaryFile(
        dir=directory,
        prefix=f".{output_name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        return Path(temporary_file.name)


def export_job(job: ExportJob, output_directory: Path) -> ExportResult:
    destination = output_directory / job.output_name
    temporary_output = temporary_path(output_directory, job.output_name)
    try:
        entry_count = extract(job.body_path, temporary_output)
        os.replace(temporary_output, destination)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise

    return ExportResult(
        asset=job.asset.asset,
        dictionary=job.asset.dictionary,
        dictionary_identifier=job.asset.dictionary_identifier,
        package=job.package_path.name,
        source=str(job.body_path),
        output=job.output_name,
        entry_count=entry_count,
        size_bytes=destination.stat().st_size,
    )


def write_manifest(output_directory: Path, results: Sequence[ExportResult]) -> None:
    destination = output_directory / MANIFEST_NAME
    temporary_output = temporary_path(output_directory, MANIFEST_NAME)
    try:
        with temporary_output.open("w", encoding="utf-8") as manifest_file:
            json.dump(
                [asdict(result) for result in results],
                manifest_file,
                ensure_ascii=False,
                indent=2,
            )
            manifest_file.write("\n")
        os.replace(temporary_output, destination)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise


def check_existing_outputs(
    jobs: Sequence[ExportJob], output_directory: Path
) -> list[Path]:
    paths = [output_directory / job.output_name for job in jobs]
    paths.append(output_directory / MANIFEST_NAME)
    return [path for path in paths if path.exists()]


def export_all(
    asset_directory: Path, output_directory: Path, force: bool = False
) -> list[ExportResult]:
    jobs = build_export_jobs(asset_directory)
    if not jobs:
        raise ValueError(f"no regular dictionaries found in {asset_directory}")

    output_directory.mkdir(parents=True, exist_ok=True)
    existing_outputs = check_existing_outputs(jobs, output_directory)
    if existing_outputs and not force:
        formatted_paths = "\n".join(f"  {path}" for path in existing_outputs)
        raise FileExistsError(
            "refusing to overwrite existing output files; use --force:\n"
            f"{formatted_paths}"
        )

    results = []
    for index, job in enumerate(jobs, start=1):
        print(
            f"[{index}/{len(jobs)}] {job.asset.dictionary} -> {job.output_name}",
            file=sys.stderr,
        )
        results.append(export_job(job, output_directory))

    write_manifest(output_directory, results)
    return results


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export all regular macOS dictionary assets as one-entry-per-line "
            "XML files. Apple Dictionary localization files are excluded."
        )
    )
    parser.add_argument("output_directory", type=Path, help="export destination")
    parser.add_argument(
        "--asset-directory",
        type=Path,
        default=DEFAULT_ASSET_DIRECTORY,
        help=f"dictionary asset directory (default: {DEFAULT_ASSET_DIRECTORY})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite dictionary XML files and manifest.json",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if not arguments.asset_directory.is_dir():
        print(
            f"error: asset directory does not exist: {arguments.asset_directory}",
            file=sys.stderr,
        )
        return 2

    try:
        results = export_all(
            arguments.asset_directory,
            arguments.output_directory.expanduser(),
            force=arguments.force,
        )
    except (DictionaryFormatError, FileExistsError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    total_entries = sum(result.entry_count for result in results)
    print(
        f"exported {len(results)} dictionaries and {total_entries} entries to "
        f"{arguments.output_directory.expanduser()}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

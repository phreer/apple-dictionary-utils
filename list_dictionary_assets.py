#!/usr/bin/env python3
"""List dictionaries installed as macOS MobileAsset bundles."""

from __future__ import annotations

import argparse
import csv
import json
import os
import plistlib
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


DEFAULT_ASSET_DIRECTORY = Path(
    "/System/Library/AssetsV2/"
    "com_apple_MobileAsset_DictionaryServices_dictionaryOSX"
)


@dataclass
class DictionaryAsset:
    asset: str
    asset_path: str
    dictionary: str
    dictionary_identifier: str | None
    package_name: str | None
    dictionary_type: str | None
    language: str | None
    index_languages: list[str]
    countries: list[str]
    format_version: int | None
    content_version: int | None
    compatibility_version: int | None
    bundle_version: str | None
    asset_size_bytes: int
    asset_file_count: int
    packages: list[dict[str, Any]]

    @property
    def dictionary_size_bytes(self) -> int:
        return sum(package["size_bytes"] for package in self.packages)


def directory_stats(path: Path) -> tuple[int, int]:
    """Return the logical byte size and regular-file count below *path*."""
    total_size = 0
    file_count = 0

    for current_directory, directory_names, file_names in os.walk(path):
        directory_names.sort()
        file_names.sort()
        current_path = Path(current_directory)
        for file_name in file_names:
            file_path = current_path / file_name
            try:
                stat_result = file_path.lstat()
            except OSError as error:
                print(f"warning: cannot stat {file_path}: {error}", file=sys.stderr)
                continue
            total_size += stat_result.st_size
            file_count += 1

    return total_size, file_count


def dictionary_packages(asset_path: Path) -> list[Path]:
    asset_data_path = asset_path / "AssetData"
    try:
        return sorted(
            path for path in asset_data_path.iterdir() if path.suffix == ".dictionary"
        )
    except OSError:
        return []


def read_asset(asset_path: Path) -> DictionaryAsset:
    info_path = asset_path / "Info.plist"
    with info_path.open("rb") as info_file:
        info = plistlib.load(info_file)

    properties = info.get("MobileAssetProperties", {})
    packages = dictionary_packages(asset_path)
    asset_size, asset_file_count = directory_stats(asset_path)

    package_details = []
    for package_path in packages:
        package_size, package_file_count = directory_stats(package_path)
        package_details.append(
            {
                "name": package_path.name,
                "path": str(package_path),
                "size_bytes": package_size,
                "file_count": package_file_count,
            }
        )

    configured_package_name = properties.get("DictionaryPackageName")
    display_name = (
        properties.get("DictionaryPackageDisplayName")
        or info.get("CFBundleName")
        or configured_package_name
        or asset_path.stem
    )

    return DictionaryAsset(
        asset=asset_path.name,
        asset_path=str(asset_path),
        dictionary=display_name,
        dictionary_identifier=properties.get("DictionaryIdentifier"),
        package_name=configured_package_name,
        dictionary_type=properties.get("DictionaryType"),
        language=properties.get("Language"),
        index_languages=properties.get("IndexLanguages", []),
        countries=properties.get("Countries", []),
        format_version=properties.get("FormatVersion"),
        content_version=properties.get("_ContentVersion"),
        compatibility_version=properties.get("_CompatibilityVersion"),
        bundle_version=info.get("CFBundleVersion"),
        asset_size_bytes=asset_size,
        asset_file_count=asset_file_count,
        packages=package_details,
    )


def iter_assets(root: Path) -> Iterator[DictionaryAsset]:
    try:
        asset_paths = sorted(root.glob("*.asset"))
    except OSError as error:
        raise RuntimeError(f"cannot scan {root}: {error}") from error

    for asset_path in asset_paths:
        try:
            yield read_asset(asset_path)
        except (OSError, plistlib.InvalidFileException) as error:
            print(f"warning: cannot parse {asset_path}: {error}", file=sys.stderr)


def human_size(byte_count: int) -> str:
    value = float(byte_count)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{byte_count} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def joined(values: Iterable[Any]) -> str:
    return ", ".join(str(value) for value in values) or "-"


def print_table(assets: Sequence[DictionaryAsset]) -> None:
    for index, asset in enumerate(assets):
        if index:
            print()
        print(f"Asset:      {asset.asset}")
        print(f"Dictionary: {asset.dictionary}")
        print(f"Identifier: {asset.dictionary_identifier or '-'}")
        print(f"Package:    {asset.package_name or '-'}")
        print(f"Type:       {asset.dictionary_type or '-'}")
        print(f"Languages:  {joined(asset.index_languages)}")
        print(
            f"Size:       {human_size(asset.dictionary_size_bytes)} (dictionary), "
            f"{human_size(asset.asset_size_bytes)} (asset)"
        )
        print(f"Files:      {asset.asset_file_count}")
        print(f"Path:       {asset.asset_path}")


def print_csv(assets: Sequence[DictionaryAsset]) -> None:
    field_names = (
        "asset",
        "dictionary",
        "dictionary_identifier",
        "package_name",
        "dictionary_type",
        "language",
        "index_languages",
        "countries",
        "content_version",
        "dictionary_size_bytes",
        "asset_size_bytes",
        "asset_file_count",
        "asset_path",
    )
    writer = csv.DictWriter(sys.stdout, fieldnames=field_names)
    writer.writeheader()
    for asset in assets:
        row = asdict(asset)
        writer.writerow(
            {
                **{field: row.get(field) for field in field_names},
                "index_languages": joined(asset.index_languages),
                "countries": joined(asset.countries),
                "dictionary_size_bytes": asset.dictionary_size_bytes,
            }
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show metadata and sizes for installed macOS dictionary assets."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=DEFAULT_ASSET_DIRECTORY,
        help=f"asset directory (default: {DEFAULT_ASSET_DIRECTORY})",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json", "csv"),
        default="table",
        help="output format (default: table)",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    root = arguments.directory.expanduser()
    if not root.is_dir():
        print(f"error: asset directory does not exist: {root}", file=sys.stderr)
        return 2

    try:
        assets = list(iter_assets(root))
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not assets:
        print(f"warning: no .asset directories found in {root}", file=sys.stderr)

    if arguments.format == "json":
        json.dump(
            [asdict(asset) for asset in assets],
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        print()
    elif arguments.format == "csv":
        print_csv(assets)
    else:
        print_table(assets)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

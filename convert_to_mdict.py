#!/usr/bin/env python3
"""Convert exported Apple Dictionary XML files to MDict MDX/MDD files."""

from __future__ import annotations

import argparse
import html
import json
import os
import plistlib
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, BinaryIO, Iterator, Sequence
from xml.etree import ElementTree


DICTIONARY_NAMESPACE = "http://www.apple.com/DTDs/DictionaryService-1.0.rng"
TITLE_ATTRIBUTE = f"{{{DICTIONARY_NAMESPACE}}}title"
PRONUNCIATION_ATTRIBUTE = f"{{{DICTIONARY_NAMESPACE}}}prn"
XML_LANG_ATTRIBUTE = "{http://www.w3.org/XML/1998/namespace}lang"
KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
X_DICTIONARY_LINK = re.compile(
    r'href\s*=\s*(?:"(?P<double>x-dictionary:[^"]+)"|'
    r"'(?P<single>x-dictionary:[^']+)')"
)
XPOINTER = re.compile(
    r"^xpointer\(//\*\[@id=(?P<quote>['\"])(?P<anchor>.+?)(?P=quote)\]\)$"
)
HTML_ID = re.compile(r"\bid=(?:\"(?P<double>[^\"]+)\"|'(?P<single>[^']+)')")
DISPLAY_RESOURCE_EXTENSIONS = {
    ".css",
    ".gif",
    ".htm",
    ".html",
    ".jpeg",
    ".jpg",
    ".js",
    ".pdf",
    ".png",
    ".svg",
    ".xsl",
}


class ConversionError(Exception):
    """Raised when exported dictionary data cannot be converted."""


@dataclass(frozen=True)
class ReferenceLink:
    """The components of an Apple Dictionary record-reference URL."""

    entry_id: str
    dictionary_identifier: str | None = None
    fallback_title: str | None = None
    anchor: str | None = None


@dataclass(frozen=True)
class ParsedEntry:
    """Metadata needed to turn one Apple Dictionary entry into MDict records."""

    title: str
    entry_id: str | None
    used_fallback_title: bool
    reading_aliases: tuple[str, ...]


@dataclass(frozen=True)
class DictionaryAudit:
    """Structural checks and link counts for one exported dictionary."""

    entry_count: int
    lookup_links: int
    reference_links: int
    unresolved_reference_links: int
    anchor_links: int
    missing_anchors: int
    stylesheets: tuple[str, ...]


def load_manifest(export_directory: Path) -> list[dict[str, Any]]:
    manifest_path = export_directory / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, json.JSONDecodeError) as error:
        raise ConversionError(f"cannot read {manifest_path}: {error}") from error

    if not isinstance(manifest, list) or not manifest:
        raise ConversionError(f"invalid or empty manifest: {manifest_path}")
    required_fields = {"dictionary", "dictionary_identifier", "source", "output"}
    for index, item in enumerate(manifest):
        if not isinstance(item, dict) or not required_fields.issubset(item):
            raise ConversionError(f"invalid manifest entry at index {index}")
    return manifest


def element_text_with_image_alts(element: ElementTree.Element) -> str:
    parts = []

    def visit(current: ElementTree.Element) -> None:
        if current.text:
            parts.append(current.text)
        for child in current:
            if child.tag.rsplit("}", 1)[-1] == "img" and child.get("alt"):
                parts.append(child.get("alt", ""))
            else:
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return " ".join("".join(parts).split())


def readable_entry_id(entry_id: str) -> str:
    value = re.sub(r"^(?:fbm|ref)_", "", entry_id)
    value = value.replace("_", " ")
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return value or entry_id


def entry_title(root: ElementTree.Element) -> tuple[str, bool]:
    title = root.get(TITLE_ATTRIBUTE, "").strip()
    if title:
        return title, False

    entry_id = root.get("id", "").strip()
    if entry_id.startswith(("fbm_", "ref_")):
        return readable_entry_id(entry_id), True

    for element in root.iter():
        classes = element.get("class", "").split()
        if "hw" not in classes:
            continue
        headword = element_text_with_image_alts(element)
        if headword:
            return headword, True

    if entry_id:
        return entry_id, True

    raise ConversionError("entry has neither a title nor an id")


def entry_reading_aliases(
    root: ElementTree.Element, title: str
) -> tuple[str, ...]:
    language = root.get(XML_LANG_ATTRIBUTE) or root.get("lang", "")
    if not language.lower().startswith("ja"):
        return ()

    aliases = []
    seen = set()
    for element in root.iter():
        if "hw" not in element.get("class", "").split():
            continue
        if PRONUNCIATION_ATTRIBUTE not in element.attrib:
            continue
        alias = element_text_with_image_alts(element)
        if not alias or alias == title or not KANA.search(alias) or alias in seen:
            continue
        if "\n" in alias or "\r" in alias:
            raise ConversionError(f"multiline reading alias: {alias!r}")
        aliases.append(alias)
        seen.add(alias)
    return tuple(aliases)


def parse_entry_data(xml: str, source: Path, line_number: int) -> ParsedEntry:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise ConversionError(
            f"invalid XML in {source} at line {line_number}: {error}"
        ) from error

    try:
        title, used_fallback = entry_title(root)
    except ConversionError as error:
        raise ConversionError(f"{error} in {source} at line {line_number}") from error
    if "\n" in title or "\r" in title:
        raise ConversionError(f"multiline title in {source} at line {line_number}")
    return ParsedEntry(
        title=title,
        entry_id=root.get("id"),
        used_fallback_title=used_fallback,
        reading_aliases=entry_reading_aliases(root, title),
    )


def parse_entry(
    xml: str, source: Path, line_number: int
) -> tuple[str, str | None, bool]:
    entry = parse_entry_data(xml, source, line_number)
    return entry.title, entry.entry_id, entry.used_fallback_title


def build_entry_id_map(xml_path: Path) -> dict[str, str]:
    entry_ids = {}
    with xml_path.open("r", encoding="utf-8") as xml_file:
        for line_number, line in enumerate(xml_file, start=1):
            xml = line.strip()
            if not xml:
                continue
            entry = parse_entry_data(xml, xml_path, line_number)
            if not entry.entry_id:
                raise ConversionError(
                    f"entry has no id in {xml_path} at line {line_number}"
                )
            if entry.entry_id in entry_ids:
                raise ConversionError(
                    f"duplicate entry id {entry.entry_id!r} in {xml_path} "
                    f"at line {line_number}"
                )
            entry_ids[entry.entry_id] = entry.title
    return entry_ids


def parse_reference_link(url: str) -> ReferenceLink:
    decoded_url = html.unescape(url)
    if not decoded_url.startswith("x-dictionary:r:"):
        raise ConversionError(f"not a record-reference URL: {url}")
    payload = decoded_url.removeprefix("x-dictionary:r:")
    entry_id, separator, remainder = payload.partition(":")
    if not entry_id:
        raise ConversionError(f"invalid empty record reference: {url}")

    dictionary_identifier = None
    fallback_title = None
    anchor = None
    if separator:
        dictionary_reference, title_separator, fallback_title = remainder.partition(":")
        dictionary_identifier, anchor_separator, fragment = dictionary_reference.partition("#")
        dictionary_identifier = dictionary_identifier or None
        fallback_title = fallback_title.strip() if title_separator else None
        fallback_title = fallback_title or None
        if anchor_separator:
            match = XPOINTER.fullmatch(fragment)
            if match is None:
                raise ConversionError(f"unsupported record-reference fragment: {url}")
            anchor = match.group("anchor")

    return ReferenceLink(
        entry_id=entry_id,
        dictionary_identifier=dictionary_identifier,
        fallback_title=fallback_title,
        anchor=anchor,
    )


def convert_link(url: str, entry_ids: dict[str, str]) -> str:
    url = html.unescape(url)
    if url.startswith("x-dictionary:d:"):
        lookup_term = url.removeprefix("x-dictionary:d:")
        if not lookup_term:
            raise ConversionError(f"invalid empty dictionary lookup: {url}")
        return f"entry://{lookup_term}"

    if url.startswith("x-dictionary:r:"):
        reference = parse_reference_link(url)
        target = entry_ids.get(reference.entry_id)
        if target is None:
            target = reference.fallback_title or reference.entry_id
        if reference.anchor:
            target = f"{target}#{reference.anchor}"
        return f"entry://{target}"

    return url


def convert_links(xml: str, entry_ids: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        quote = '"' if match.group("double") is not None else "'"
        url = match.group("double") or match.group("single")
        target = html.escape(convert_link(url, entry_ids), quote=True)
        return f"href={quote}{target}{quote}"

    return X_DICTIONARY_LINK.sub(replace, xml)


def iter_dictionary_links(xml: str) -> Iterator[str]:
    for match in X_DICTIONARY_LINK.finditer(xml):
        yield match.group("double") or match.group("single")


def load_dictionary_info(resources_directory: Path) -> dict[str, Any]:
    info_path = resources_directory.parent / "Info.plist"
    try:
        with info_path.open("rb") as info_file:
            info = plistlib.load(info_file)
    except FileNotFoundError:
        return {}
    except (OSError, plistlib.InvalidFileException) as error:
        raise ConversionError(f"cannot read {info_path}: {error}") from error
    if not isinstance(info, dict):
        raise ConversionError(f"invalid dictionary property list: {info_path}")
    return info


def configured_stylesheets(resources_directory: Path) -> tuple[str, ...]:
    info = load_dictionary_info(resources_directory)
    configured = info.get("DCSDictionaryCSS")
    if configured is None or configured == "":
        return ()
    if isinstance(configured, str):
        names = (configured,)
    elif isinstance(configured, list) and all(
        isinstance(name, str) and name for name in configured
    ):
        names = tuple(configured)
    else:
        raise ConversionError("DCSDictionaryCSS must be a string or list of strings")

    for name in names:
        path = resources_directory / name
        if not path.is_file():
            raise ConversionError(f"configured stylesheet does not exist: {path}")
    return names


def stylesheet_links(resources_directory: Path) -> str:
    stylesheets = configured_stylesheets(resources_directory)
    return "".join(
        f'<link rel="stylesheet" href="{html.escape(stylesheet, quote=True)}">'
        for stylesheet in stylesheets
    )


def audit_dictionary(xml_path: Path, resources_directory: Path) -> DictionaryAudit:
    stylesheets = configured_stylesheets(resources_directory)
    entry_ids = build_entry_id_map(xml_path)
    lookup_links = 0
    reference_links = 0
    unresolved_reference_links = 0
    anchor_links = 0
    requested_anchors: set[str] = set()

    with xml_path.open("r", encoding="utf-8") as xml_file:
        for line in xml_file:
            for url in iter_dictionary_links(line):
                decoded_url = html.unescape(url)
                if decoded_url.startswith("x-dictionary:d:"):
                    convert_link(decoded_url, entry_ids)
                    lookup_links += 1
                    continue
                if decoded_url.startswith("x-dictionary:r:"):
                    reference = parse_reference_link(decoded_url)
                    reference_links += 1
                    unresolved_reference_links += reference.entry_id not in entry_ids
                    if reference.anchor:
                        anchor_links += 1
                        requested_anchors.add(reference.anchor)
                    continue
                raise ConversionError(f"unsupported Apple Dictionary link: {decoded_url}")

    found_anchors: set[str] = set()
    if requested_anchors:
        with xml_path.open("r", encoding="utf-8") as xml_file:
            for line in xml_file:
                for match in HTML_ID.finditer(line):
                    anchor = match.group("double") or match.group("single")
                    if anchor in requested_anchors:
                        found_anchors.add(anchor)

    return DictionaryAudit(
        entry_count=len(entry_ids),
        lookup_links=lookup_links,
        reference_links=reference_links,
        unresolved_reference_links=unresolved_reference_links,
        anchor_links=anchor_links,
        missing_anchors=len(requested_anchors - found_anchors),
        stylesheets=stylesheets,
    )


def write_mdict_source(
    xml_path: Path, source_path: Path, resources_directory: Path
) -> tuple[int, int, int]:
    entry_ids = build_entry_id_map(xml_path)
    primary_titles = set(entry_ids.values())
    stylesheets = stylesheet_links(resources_directory)
    entry_count = 0
    fallback_title_count = 0
    reading_aliases: set[tuple[str, str]] = set()

    def write_record(mdict_source: BinaryIO, key: str, record: str) -> None:
        mdict_source.write(key.encode("utf-8"))
        mdict_source.write(b"\r\n")
        mdict_source.write(record.encode("utf-8"))
        mdict_source.write(b"\r\n</>\r\n")

    with xml_path.open("r", encoding="utf-8") as xml_file, source_path.open(
        "wb"
    ) as mdict_source:
        for line_number, line in enumerate(xml_file, start=1):
            xml = line.strip()
            if not xml:
                continue
            entry = parse_entry_data(xml, xml_path, line_number)
            record = stylesheets + convert_links(xml, entry_ids)
            write_record(mdict_source, entry.title, record)
            for alias in entry.reading_aliases:
                if alias not in primary_titles:
                    reading_aliases.add((alias, entry.title))
            entry_count += 1
            fallback_title_count += entry.used_fallback_title

        for alias, target in sorted(reading_aliases):
            write_record(mdict_source, alias, f"@@@LINK={target}")

    return entry_count, fallback_title_count, len(reading_aliases)


def source_resources(manifest_entry: dict[str, Any]) -> Path:
    return Path(manifest_entry["source"]).parent


def copy_display_resources(source: Path, destination: Path) -> int:
    if not source.is_dir():
        return 0

    copied = 0
    for resource in sorted(source.rglob("*")):
        if not resource.is_file():
            continue
        relative_path = resource.relative_to(source)
        if any(part.endswith(".lproj") for part in relative_path.parts):
            continue
        if resource.suffix.lower() not in DISPLAY_RESOURCE_EXTENSIONS:
            continue

        output_path = destination / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resource, output_path)
        copied += 1
    return copied


def dictionary_description(manifest_entry: dict[str, Any]) -> str:
    resources = source_resources(manifest_entry)
    info = load_dictionary_info(resources)

    copyright_text = info.get("DCSDictionaryCopyright", "")
    identifier = manifest_entry.get("dictionary_identifier") or "unknown"
    description = f"Converted from macOS Dictionary ({identifier})."
    if copyright_text:
        description = f"{description}<br>{copyright_text}"
    return description


def temporary_output(directory: Path, stem: str, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(
        dir=directory,
        prefix=f".{stem}.",
        suffix=suffix,
        delete=False,
    ) as temporary_file:
        return Path(temporary_file.name)


def load_mdict_utils() -> tuple[ModuleType, ModuleType]:
    try:
        from mdict_utils import reader, writer
    except ImportError as error:
        raise ConversionError(
            "cannot import mdict-utils; install it with: "
            "python3 -m pip install mdict-utils"
        ) from error
    return reader, writer


def verify_mdict(reader: ModuleType, path: Path) -> None:
    try:
        metadata = reader.meta(str(path))
    except Exception as error:
        raise ConversionError(f"cannot verify {path}: {error}") from error
    if not metadata:
        raise ConversionError(f"cannot verify {path}: empty metadata")


def pack_file(
    reader: ModuleType,
    writer: ModuleType,
    source: Path,
    target: Path,
    title: str,
    description: str,
    is_mdd: bool,
) -> None:
    temporary_target = temporary_output(target.parent, target.stem, target.suffix)
    try:
        if is_mdd:
            dictionary = writer.pack_mdd_file(str(source))
        else:
            dictionary = writer.pack_mdx_txt(str(source), encoding="utf-8")
        writer.pack(
            str(temporary_target),
            dictionary,
            title=title,
            description=description,
            encoding="utf-8",
            is_mdd=is_mdd,
        )
        verify_mdict(reader, temporary_target)
        os.replace(temporary_target, target)
    except Exception:
        temporary_target.unlink(missing_ok=True)
        raise


def convert_dictionary(
    reader: ModuleType,
    writer: ModuleType,
    export_directory: Path,
    output_directory: Path,
    manifest_entry: dict[str, Any],
) -> tuple[int, int, int, int]:
    xml_path = export_directory / manifest_entry["output"]
    if not xml_path.is_file():
        raise ConversionError(f"exported XML does not exist: {xml_path}")

    output_stem = xml_path.stem
    mdx_path = output_directory / f"{output_stem}.mdx"
    mdd_path = output_directory / f"{output_stem}.mdd"
    resources = source_resources(manifest_entry)

    with tempfile.TemporaryDirectory(prefix="apple-dictionary-mdict-") as directory:
        working_directory = Path(directory)
        source_path = working_directory / f"{output_stem}.txt"
        resource_directory = working_directory / "resources"

        title = manifest_entry["dictionary"]
        description = dictionary_description(manifest_entry)
        entry_count, fallback_title_count, reading_alias_count = write_mdict_source(
            xml_path, source_path, resources
        )
        resource_count = copy_display_resources(resources, resource_directory)

        pack_file(
            reader,
            writer,
            source_path,
            mdx_path,
            title,
            description,
            is_mdd=False,
        )
        if resource_count:
            pack_file(
                reader,
                writer,
                resource_directory,
                mdd_path,
                title,
                description,
                is_mdd=True,
            )

    return entry_count, resource_count, fallback_title_count, reading_alias_count


def expected_outputs(
    export_directory: Path,
    output_directory: Path,
    manifest: Sequence[dict[str, Any]],
) -> list[Path]:
    paths = []
    for item in manifest:
        stem = (export_directory / item["output"]).stem
        paths.extend((output_directory / f"{stem}.mdx", output_directory / f"{stem}.mdd"))
    return paths


def convert_all(
    reader: ModuleType,
    writer: ModuleType,
    export_directory: Path,
    output_directory: Path,
    force: bool = False,
) -> None:
    manifest = load_manifest(export_directory)
    output_directory.mkdir(parents=True, exist_ok=True)

    existing = [
        path
        for path in expected_outputs(export_directory, output_directory, manifest)
        if path.exists()
    ]
    if existing and not force:
        formatted_paths = "\n".join(f"  {path}" for path in existing)
        raise FileExistsError(
            "refusing to overwrite existing MDict files; use --force:\n"
            f"{formatted_paths}"
        )

    for index, item in enumerate(manifest, start=1):
        print(
            f"[{index}/{len(manifest)}] {item['dictionary']}",
            file=sys.stderr,
        )
        (
            entry_count,
            resource_count,
            fallback_title_count,
            reading_alias_count,
        ) = convert_dictionary(reader, writer, export_directory, output_directory, item)
        print(
            f"  packed {entry_count} entries and {resource_count} resources; "
            f"added {reading_alias_count} Japanese reading aliases; "
            f"recovered {fallback_title_count} empty titles",
            file=sys.stderr,
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert XML produced by export_system_dictionaries.py into "
            "MDict MDX/MDD files using mdict-utils."
        )
    )
    parser.add_argument("export_directory", type=Path, help="XML export directory")
    parser.add_argument("output_directory", type=Path, help="MDX/MDD destination")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing MDX/MDD files",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    export_directory = arguments.export_directory.expanduser()
    output_directory = arguments.output_directory.expanduser()
    if not export_directory.is_dir():
        print(f"error: export directory does not exist: {export_directory}", file=sys.stderr)
        return 2

    try:
        reader, writer = load_mdict_utils()
        convert_all(
            reader,
            writer,
            export_directory,
            output_directory,
            force=arguments.force,
        )
    except (ConversionError, FileExistsError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

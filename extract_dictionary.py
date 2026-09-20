#!/usr/bin/env python3
"""Extract XML entries from an Apple Dictionary Body.data file."""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import BinaryIO, ContextManager


DATA_LENGTH_OFFSET = 0x40
FIRST_BLOCK_OFFSET = 0x60
UINT32 = struct.Struct("<I")
INTERNAL_LENGTH_SIZE = UINT32.size
COMPRESSED_HEADER_SIZE = UINT32.size


class DictionaryFormatError(Exception):
    """Raised when Body.data does not have the expected structure."""


def read_exact(source: BinaryIO, size: int, description: str) -> bytes:
    data = source.read(size)
    if len(data) != size:
        raise DictionaryFormatError(
            f"incomplete {description}: expected {size} bytes, got {len(data)}"
        )
    return data


def read_uint32(source: BinaryIO, description: str) -> int:
    return UINT32.unpack(read_exact(source, UINT32.size, description))[0]


def iter_blocks(source: BinaryIO) -> Iterator[tuple[bytes, bool]]:
    source.seek(DATA_LENGTH_OFFSET)
    data_length = read_uint32(source, "data section length")
    data_end = DATA_LENGTH_OFFSET + data_length
    position = FIRST_BLOCK_OFFSET

    if data_end < position:
        raise DictionaryFormatError(
            f"invalid data section end 0x{data_end:x} before first block"
        )

    while position < data_end:
        source.seek(position)
        block_length = read_uint32(source, f"block length at 0x{position:x}")
        block_end = position + UINT32.size + block_length

        if block_length < INTERNAL_LENGTH_SIZE:
            raise DictionaryFormatError(
                f"invalid block length {block_length} at 0x{position:x}"
            )
        if block_end > data_end:
            raise DictionaryFormatError(
                f"block at 0x{position:x} ends beyond data section: "
                f"0x{block_end:x} > 0x{data_end:x}"
            )

        block = read_exact(
            source, block_length, f"block payload at 0x{position + 4:x}"
        )
        internal_length = UINT32.unpack_from(block)[0]

        if internal_length != block_length - UINT32.size:
            raise DictionaryFormatError(
                f"invalid internal block length {internal_length} "
                f"at 0x{position + 4:x}"
            )

        payload = block[INTERNAL_LENGTH_SIZE:]
        if payload.lstrip().startswith(b"<d:entry"):
            yield payload, False
        else:
            if len(payload) < COMPRESSED_HEADER_SIZE:
                raise DictionaryFormatError(
                    f"missing compressed block header at 0x{position + 8:x}"
                )
            expected_size = UINT32.unpack_from(payload)[0]
            compressed = payload[COMPRESSED_HEADER_SIZE:]
            try:
                uncompressed = zlib.decompress(compressed)
            except zlib.error as error:
                raise DictionaryFormatError(
                    f"cannot decompress block at 0x{position:x}: {error}"
                ) from error

            if len(uncompressed) != expected_size:
                raise DictionaryFormatError(
                    f"incorrect uncompressed size at 0x{position:x}: "
                    f"expected {expected_size}, got {len(uncompressed)}"
                )

            yield uncompressed, True
        position = block_end


def validate_entry(entry: bytes, location: str) -> None:
    stripped_entry = entry.strip()
    if not stripped_entry.startswith(b"<d:entry"):
        raise DictionaryFormatError(f"entry {location} does not start with <d:entry")
    if not stripped_entry.endswith(b"</d:entry>"):
        raise DictionaryFormatError(f"entry {location} does not end with </d:entry>")


def iter_entries(source: BinaryIO) -> Iterator[bytes]:
    for block_number, (block, is_framed) in enumerate(iter_blocks(source), start=1):
        if not is_framed:
            validate_entry(block, f"in block {block_number}")
            yield block
            continue

        position = 0
        while position < len(block):
            remaining = len(block) - position
            if remaining < UINT32.size:
                raise DictionaryFormatError(
                    f"incomplete entry length in block {block_number} "
                    f"at offset 0x{position:x}"
                )

            entry_length = UINT32.unpack_from(block, position)[0]
            entry_start = position + UINT32.size
            entry_end = entry_start + entry_length
            if entry_end > len(block):
                raise DictionaryFormatError(
                    f"entry in block {block_number} at offset 0x{position:x} "
                    f"ends beyond the block"
                )

            entry = block[entry_start:entry_end]
            validate_entry(
                entry, f"in block {block_number} at offset 0x{position:x}"
            )

            yield entry
            position = entry_end


def one_line(entry: bytes) -> bytes:
    return b"".join(line.rstrip() for line in entry.splitlines())


def output_context(path: Path | None) -> ContextManager[BinaryIO]:
    if path is None:
        return nullcontext(sys.stdout.buffer)
    return path.open("wb")


def extract(source_path: Path, output_path: Path | None = None) -> int:
    entry_count = 0
    with source_path.open("rb") as source, output_context(output_path) as output:
        for entry in iter_entries(source):
            output.write(one_line(entry))
            output.write(b"\n")
            entry_count += 1
    return entry_count


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract Body.data into one XML dictionary entry per output line, "
            "replacing the dedict, strip, and checkxml.py pipeline."
        )
    )
    parser.add_argument("body_data", type=Path, help="path to Body.data")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output file (default: standard output)",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if not arguments.body_data.is_file():
        print(f"error: input file does not exist: {arguments.body_data}", file=sys.stderr)
        return 2

    try:
        extract(arguments.body_data, arguments.output)
    except (DictionaryFormatError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

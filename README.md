# Apple Dictionary Tools

Python tools for extracting, exporting, and converting macOS Apple Dictionary assets.

## Python Usage

Extract XML from `Body.data`:

```bash
./extract_dictionary.py path/to/Body.data > dictionary.xml
```

Import directly into SQLite:

```bash
./extract_dictionary.py path/to/Body.data | ./appledict2sqlite3.py
```

Export the regular system dictionaries:

```bash
./export_system_dictionaries.py path/to/output-directory
```

Convert the export to MDict (install
[`mdict-utils`](https://github.com/liuyug/mdict-utils) first):

```bash
python3 -m pip install mdict-utils
./convert_to_mdict.py path/to/export-directory path/to/mdict-directory
```

The converter preserves styles and display resources, rewrites Apple Dictionary
links, and adds kana aliases for Japanese entries. Use `--force` to replace
existing output files.

Audit an export:

```bash
./audit_mdict_exports.py path/to/export-directory
```

## Legacy C Pipeline

The old C tools are kept in [`legacy/`](legacy/) for compatibility:

```bash
make -C legacy
./legacy/dedict path/to/Body.data | ./legacy/strip | ./legacy/checkxml.py > dictionary.xml
```

The Python extractor supersedes this pipeline.

## Documentation

See [`docs/mdict-repair-plan.md`](docs/mdict-repair-plan.md) for MDict display
compatibility findings and the repair plan.

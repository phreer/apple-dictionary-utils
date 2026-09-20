# Apple Dictionary Tools
Tools for extracting data from Apple dictionary files (used by the Dictionary application).

# Usage
Extract a `Body.data` file into one XML entry per line with the Python tool:

`./extract_dictionary.py path/to/Body.data > dictionary.xml`

To convert the extracted entries directly to an SQLite database:

`./extract_dictionary.py path/to/Body.data | ./appledict2sqlite3.py`

Export every regular system dictionary to a directory (excluding the localized
Apple Dictionary glossary):

`./export_system_dictionaries.py path/to/output-directory`

The exporter writes one XML file per dictionary and a `manifest.json` describing
the source asset, dictionary identifier, entry count, and output size. It refuses
to replace existing exports unless `--force` is supplied.

Convert an export directory to MDict `.mdx`/`.mdd` files with
[`mdict-utils`](https://github.com/liuyug/mdict-utils):

`python3 -m pip install mdict-utils`

`./convert_to_mdict.py path/to/export-directory path/to/mdict-directory`

The converter rewrites Apple Dictionary cross-references as MDict `entry://`
links and packages stylesheets, images, and other display resources into a
companion `.mdd`. Existing output files require `--force` to replace.

Audit exported XML, configured stylesheets, record references, and anchors
without packing any files:

`./audit_mdict_exports.py path/to/export-directory`

Use `--json` to produce a machine-readable report. The display-compatibility
findings and prioritized roadmap are documented in
[`docs/mdict-repair-plan.md`](docs/mdict-repair-plan.md).

The older C-based pipeline is still available. First compile it with `make`,
then run

`./dedict path/to/Body.data | ./strip | ./checkxml.py > dictionary.xml`

or if you want to convert it to sqlite3 database directly

`./dedict path/to/Body.data | ./strip | ./checkxml.py | ./appledict2sqlite3.py`

which will create a file called `dictionary.db`. The sizes can vary, but on my
machine I got ~192MB for dictionary.xml and ~250MB dictionary.db. These tools have
been tested only with the New Oxford American Dictionary but they should work without
any problems with other dictionaries.

# Code
Some of the code has been taken from https://gist.github.com/josephg/5e134adf70760ee7e49d
and modified to fix errors and make it more useful.

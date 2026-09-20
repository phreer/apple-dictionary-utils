from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path

from convert_to_mdict import (
    COMPATIBILITY_STYLESHEET,
    ConversionError,
    audit_dictionary,
    configured_stylesheets,
    copy_display_resources,
    convert_link,
    convert_links,
    parse_entry_data,
    parse_reference_link,
    stylesheet_links,
    write_mdict_source,
)


class LinkConversionTests(unittest.TestCase):
    def test_lookup_keeps_complete_term(self) -> None:
        self.assertEqual(
            convert_link("x-dictionary:d:word:with:colons", {}),
            "entry://word:with:colons",
        )

    def test_reference_uses_resolved_title_without_bogus_fragment(self) -> None:
        url = "x-dictionary:r:entry-1:com.apple.dictionary.test:fallback"
        self.assertEqual(convert_link(url, {"entry-1": "Headword"}), "entry://Headword")

    def test_unresolved_reference_uses_fallback_title(self) -> None:
        url = "x-dictionary:r:missing:com.apple.dictionary.test:Fallback"
        self.assertEqual(convert_link(url, {}), "entry://Fallback")

    def test_xpointer_is_converted_to_html_fragment(self) -> None:
        url = (
            "x-dictionary:r:entry-1:com.apple.dictionary.test"
            "#xpointer(//*[@id='entry-1.007']):Headword"
        )
        reference = parse_reference_link(url)
        self.assertEqual(reference.anchor, "entry-1.007")
        self.assertEqual(
            convert_link(url, {"entry-1": "Headword"}),
            "entry://Headword#entry-1.007",
        )

    def test_xpointer_with_inner_single_quotes_is_matched(self) -> None:
        xml = (
            '<a href="x-dictionary:r:entry-1:com.apple.dictionary.test'
            "#xpointer(//*[@id='entry-1.007']):Headword\">link</a>"
        )
        self.assertEqual(
            convert_links(xml, {"entry-1": "Headword"}),
            '<a href="entry://Headword#entry-1.007">link</a>',
        )


class StylesheetTests(unittest.TestCase):
    def test_only_configured_stylesheet_is_linked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "Test.dictionary" / "Contents"
            resources = contents / "Resources"
            resources.mkdir(parents=True)
            (resources / "DefaultStyle.css").write_text(".entry {}")
            (resources / "VerticalStyle.css").write_text("body { writing-mode: vertical-rl }")
            with (contents / "Info.plist").open("wb") as info_file:
                plistlib.dump({"DCSDictionaryCSS": "DefaultStyle.css"}, info_file)

            self.assertEqual(configured_stylesheets(resources), ("DefaultStyle.css",))
            self.assertEqual(
                stylesheet_links(resources),
                '<link rel="stylesheet" href="DefaultStyle.css">'
                f'<link rel="stylesheet" href="{COMPATIBILITY_STYLESHEET}">',
            )

    def test_resources_include_portable_apple_styles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "source"
            destination = root / "destination"
            resources.mkdir()
            (resources / "DefaultStyle.css").write_text(
                "span { color: -apple-system-secondary-label; }",
                encoding="utf-8",
            )

            self.assertEqual(copy_display_resources(resources, destination), 2)
            converted = (destination / "DefaultStyle.css").read_text(encoding="utf-8")
            compatibility = (destination / COMPATIBILITY_STYLESHEET).read_text(
                encoding="utf-8"
            )
            self.assertIn("var(--apple-dictionary-secondary-label", converted)
            self.assertNotIn("color: -apple-system-secondary-label", converted)
            self.assertIn("span.oup_label", compatibility)
            self.assertIn("border: 1px solid currentColor", compatibility)

    def test_missing_configured_stylesheet_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "Test.dictionary" / "Contents"
            resources = contents / "Resources"
            resources.mkdir(parents=True)
            with (contents / "Info.plist").open("wb") as info_file:
                plistlib.dump({"DCSDictionaryCSS": "Missing.css"}, info_file)

            with self.assertRaisesRegex(ConversionError, "does not exist"):
                configured_stylesheets(resources)


class ReadingAliasTests(unittest.TestCase):
    def test_extracts_kana_from_japanese_pronunciation_headword(self) -> None:
        xml = (
            '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
            'id="entry-1" d:title="私" lang="ja">'
            '<span d:prn="1" class="hw">わたし <d:prn/></span>'
            "</d:entry>"
        )
        entry = parse_entry_data(xml, Path("Test.xml"), 1)
        self.assertEqual(entry.reading_aliases, ("わたし",))

    def test_ignores_non_japanese_and_non_kana_headwords(self) -> None:
        chinese = (
            '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
            'id="entry-1" d:title="私" lang="zh">'
            '<span d:prn="1" class="hw">sī</span></d:entry>'
        )
        english = (
            '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
            'id="entry-2" d:title="I" lang="ja">'
            '<span d:prn="1" class="hw">I</span></d:entry>'
        )
        self.assertEqual(
            parse_entry_data(chinese, Path("Test.xml"), 1).reading_aliases,
            (),
        )
        self.assertEqual(
            parse_entry_data(english, Path("Test.xml"), 2).reading_aliases,
            (),
        )

    def test_writes_redirect_records_for_each_reading_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "Test.dictionary" / "Contents" / "Resources"
            resources.mkdir(parents=True)
            xml_path = root / "Test.xml"
            source_path = root / "Test.txt"
            xml_path.write_text(
                '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
                'id="entry-1" d:title="私" lang="ja">'
                '<span d:prn="1" class="hw">わたし <d:prn/></span></d:entry>\n'
                '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
                'id="entry-2" d:title="渡し" lang="ja">'
                '<span d:prn="1" class="hw">わたし <d:prn/></span></d:entry>\n',
                encoding="utf-8",
            )

            counts = write_mdict_source(xml_path, source_path, resources)
            source = source_path.read_text(encoding="utf-8")
            self.assertEqual(counts, (2, 0, 2))
            self.assertIn("わたし\n@@@LINK=私", source)
            self.assertIn("わたし\n@@@LINK=渡し", source)

    def test_skips_alias_that_is_already_a_primary_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "Test.dictionary" / "Contents" / "Resources"
            resources.mkdir(parents=True)
            xml_path = root / "Test.xml"
            source_path = root / "Test.txt"
            xml_path.write_text(
                '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
                'id="entry-1" d:title="私" lang="ja">'
                '<span d:prn="1" class="hw">わたし <d:prn/></span></d:entry>\n'
                '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
                'id="entry-2" d:title="わたし" lang="ja">わたし</d:entry>\n',
                encoding="utf-8",
            )

            counts = write_mdict_source(xml_path, source_path, resources)
            self.assertEqual(counts, (2, 0, 0))


class AuditTests(unittest.TestCase):
    def test_audit_counts_links_and_validates_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents = root / "Test.dictionary" / "Contents"
            resources = contents / "Resources"
            resources.mkdir(parents=True)
            (resources / "DefaultStyle.css").write_text(".entry {}")
            with (contents / "Info.plist").open("wb") as info_file:
                plistlib.dump({"DCSDictionaryCSS": "DefaultStyle.css"}, info_file)

            xml_path = root / "Test.xml"
            xml_path.write_text(
                '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
                'id="entry-1" d:title="one"><span id="entry-1.007">one</span></d:entry>\n'
                '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
                'id="entry-2" d:title="two">'
                '<a href="x-dictionary:d:one">one</a>'
                '<a href="x-dictionary:r:entry-1:com.apple.dictionary.test">one</a>'
                '<a href="x-dictionary:r:entry-1:com.apple.dictionary.test'
                "#xpointer(//*[@id='entry-1.007']):one\">sense</a>"
                '<a href="x-dictionary:r:missing:com.apple.dictionary.test:missing">missing</a>'
                "</d:entry>\n",
                encoding="utf-8",
            )

            audit = audit_dictionary(xml_path, resources)
            self.assertEqual(audit.entry_count, 2)
            self.assertEqual(audit.lookup_links, 1)
            self.assertEqual(audit.reference_links, 3)
            self.assertEqual(audit.unresolved_reference_links, 1)
            self.assertEqual(audit.anchor_links, 1)
            self.assertEqual(audit.missing_anchors, 0)
            self.assertEqual(audit.stylesheets, ("DefaultStyle.css",))

    def test_audit_rejects_duplicate_entry_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents = root / "Test.dictionary" / "Contents"
            resources = contents / "Resources"
            resources.mkdir(parents=True)
            xml_path = root / "Test.xml"
            entry = (
                '<d:entry xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng" '
                'id="duplicate" d:title="word">word</d:entry>\n'
            )
            xml_path.write_text(entry + entry, encoding="utf-8")

            with self.assertRaisesRegex(ConversionError, "duplicate entry id"):
                audit_dictionary(xml_path, resources)


if __name__ == "__main__":
    unittest.main()

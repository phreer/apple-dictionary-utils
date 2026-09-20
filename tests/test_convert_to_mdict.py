from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path

from convert_to_mdict import (
    ConversionError,
    audit_dictionary,
    configured_stylesheets,
    convert_link,
    convert_links,
    parse_reference_link,
    stylesheet_links,
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
                '<link rel="stylesheet" href="DefaultStyle.css">',
            )

    def test_missing_configured_stylesheet_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "Test.dictionary" / "Contents"
            resources = contents / "Resources"
            resources.mkdir(parents=True)
            with (contents / "Info.plist").open("wb") as info_file:
                plistlib.dump({"DCSDictionaryCSS": "Missing.css"}, info_file)

            with self.assertRaisesRegex(ConversionError, "does not exist"):
                configured_stylesheets(resources)


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

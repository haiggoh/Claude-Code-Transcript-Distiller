"""Regression tests for the four defects fixed in 0.6.2.

Each test pins the specific misbehaviour that was observed on a real 7.85 MB transcript,
so a regression fails here rather than silently degrading an artifact a human is unlikely
to re-measure.
"""
from __future__ import annotations
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("compact_session_bundle_defects_062", ROOT / "cc_transcript.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class HoistedConflictEncodingTests(unittest.TestCase):
    """B1: the header re-encoded one entry per occurrence (95 KB for 11 distinct values)."""

    def test_consecutive_equal_values_collapse_to_one_span(self):
        conflicts = [(n, "/a") for n in range(2, 12)]
        runs = MODULE.collapse_conflict_runs(conflicts)
        self.assertEqual(1, len(runs))
        self.assertEqual({"from_line": 2, "to_line": 11, "occurrences": 10, "value": "/a"}, runs[0])

    def test_every_transition_is_preserved_in_order(self):
        conflicts = [(2, "/a"), (3, "/a"), (4, "/b"), (5, "/a")]
        runs = MODULE.collapse_conflict_runs(conflicts)
        self.assertEqual(["/a", "/b", "/a"], [run["value"] for run in runs],
                         "a value returning after a change must not merge with its earlier span")
        self.assertEqual([(2, 3), (4, 4), (5, 5)], [(r["from_line"], r["to_line"]) for r in runs])

    def test_occurrence_count_is_conserved(self):
        conflicts = [(n, "/a" if n % 7 else "/b") for n in range(2, 200)]
        runs = MODULE.collapse_conflict_runs(conflicts)
        self.assertEqual(len(conflicts), sum(run["occurrences"] for run in runs),
                         "a span must account for exactly the occurrences it replaced")

    def test_encoding_is_sublinear_in_occurrences(self):
        # The defect: size grew with record count rather than with distinct-value changes.
        conflicts = [(n, "/a") for n in range(2, 1000)]
        runs = MODULE.collapse_conflict_runs(conflicts)
        self.assertLess(len(json.dumps(runs)), 200,
                        "998 identical occurrences must not serialize proportionally to their count")


class CapsuleColumnLabelTests(unittest.TestCase):
    """B2: a column headed 'JSONL Line' carried the compact line number."""

    def test_table_header_names_the_compact_line(self):
        header = MODULE.table([]).splitlines()[0]
        self.assertIn("Compact Line", header)
        self.assertNotIn("JSONL Line", header,
                         "the column is compact-relative; labelling it JSONL sends readers "
                         "to the wrong record in the original transcript")


class PreviewElisionTests(unittest.TestCase):
    """B3: previews spliced non-adjacent lines into sentences the source never contained."""

    def test_non_adjacent_signal_line_is_marked_as_elided(self):
        text = "Base directory for this skill: /x/y\n\n# Heading\n\nsome intervening prose\nERROR: it failed"
        out = MODULE.excerpt(text, 400)
        self.assertIn(MODULE.ELISION_MARK, out,
                      "dropped lines must be visible, or the preview asserts false adjacency")
        self.assertIn("Base directory", out)
        self.assertIn("ERROR: it failed", out)
        self.assertNotIn("intervening prose", out)

    def test_adjacent_signal_line_is_not_marked_as_elided(self):
        out = MODULE.excerpt("first line here\nERROR: it failed", 400)
        self.assertNotIn(MODULE.ELISION_MARK, out,
                         "contiguous lines must not be flagged as a gap")

    def test_fragment_starting_mid_sentence_is_prefixed(self):
        text = "Heading line\nfiller\nfiller two\nunfinished. ERROR: truncated mid-thought"
        out = MODULE.excerpt(text, 400)
        self.assertIn("…unfinished.", out,
                      "a lowercase continuation must not read as the start of a statement")

    def test_empty_text_yields_the_sentinel(self):
        self.assertEqual(MODULE.EMPTY_PREVIEW, MODULE.excerpt("   \n\n  ", 400))


class ContentlessRowTests(unittest.TestCase):
    """B4: 409 of 1,399 chronology rows were content-free (empty thinking blocks)."""

    def row(self, kind, preview, raw_text="", tool_name=""):
        return MODULE.Row(1, 1, "00:00:00", kind, preview, raw_text=raw_text, tool_name=tool_name)

    def test_assistant_row_with_only_an_empty_thinking_block_is_contentless(self):
        self.assertTrue(MODULE.is_contentless_row(self.row("ASSISTANT", MODULE.EMPTY_PREVIEW)))

    def test_rows_carrying_any_information_are_kept(self):
        kept = [
            self.row("ASSISTANT", "actual visible text"),
            self.row("ASSISTANT", MODULE.EMPTY_PREVIEW, raw_text="thinking text present"),
            self.row("ASSISTANT/TOOL-CALL", MODULE.EMPTY_PREVIEW, tool_name="TaskList"),
            self.row("TOOL/RESULT", MODULE.EMPTY_PREVIEW, tool_name="Read"),
            self.row("QUEUED-USER", MODULE.EMPTY_PREVIEW),
        ]
        for row in kept:
            with self.subTest(kind=row.kind, tool=row.tool_name):
                self.assertFalse(MODULE.is_contentless_row(row))

    def test_queued_user_evidence_is_never_dropped(self):
        # The preservation contract lists queued instructions as never omitted; an empty
        # queued record is still evidence that something was queued.
        self.assertFalse(MODULE.is_contentless_row(self.row("QUEUED-USER", MODULE.EMPTY_PREVIEW)))

    def test_no_argument_call_detail_is_falsy_not_the_sentinel(self):
        name, detail, _ = MODULE.describe_call({"name": "TaskList", "input": {}})
        self.assertEqual("TaskList", name)
        self.assertEqual("", detail,
                         "a truthy '[empty]' defeated `if detail` and rendered 'TaskList: [empty]'")

    def test_call_with_arguments_still_reports_detail(self):
        _, detail, _ = MODULE.describe_call({"name": "Bash", "input": {"command": "ls -la"}})
        self.assertIn("ls -la", detail)


class ReleaseVersionSourceTests(unittest.TestCase):
    """B6: the release builder had its own version literal and mis-named the archive."""

    def test_builder_carries_no_hardcoded_version_literal(self):
        # The end-to-end behaviour is covered by test_release_builder.py, which runs a real
        # build; this only guards the specific mechanism that failed — a version literal
        # kept apart from its source of truth.
        source = (ROOT / "scripts" / "build-release.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r'else\s*"\d+\.\d+\.\d+"',
                            "a hardcoded version fallback silently mis-names release artifacts")
        self.assertIn("def source_version", source)


    def test_packaged_version_matches_the_latest_release_entry(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        headings = [
            line.strip()
            for line in changelog.splitlines()
            if line.startswith("## ")
        ]
        self.assertTrue(
            headings,
            "the changelog must contain at least one version heading",
        )
        if headings[0].casefold() == "## unreleased":
            headings = headings[1:]
        self.assertTrue(
            headings,
            "an Unreleased section must be followed by a released version",
        )
        self.assertEqual(
            f"## {MODULE.VERSION}",
            headings[0],
            "the newest released changelog heading must describe "
            "the version being shipped",
        )


if __name__ == "__main__":
    unittest.main()

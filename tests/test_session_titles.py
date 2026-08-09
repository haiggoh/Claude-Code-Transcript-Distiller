from __future__ import annotations
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("compact_session_bundle", ROOT / "compact_session_bundle.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

class SessionTitleTests(unittest.TestCase):
    def make_session(self, records, session_id="11111111-1111-1111-1111-111111111111"):
        directory = Path(tempfile.mkdtemp()) / "project-name"
        directory.mkdir()
        path = directory / f"{session_id}.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(directory.parent))
        return path

    @staticmethod
    def user(text, **extra):
        return {"type": "user", "message": {"role": "user", "content": text}, **extra}

    def test_custom_title_has_priority_and_latest_wins(self):
        path = self.make_session([
            self.user("First prompt"),
            {"type": "ai-title", "aiTitle": "AI title"},
            {"type": "custom-title", "customTitle": "Old name"},
            {"type": "custom-title", "customTitle": "New name"},
        ])
        self.assertEqual(MODULE.resolve_session_label(path), MODULE.SessionLabel("New name", "custom title"))

    def test_ai_title_precedes_summary_and_prompt(self):
        path = self.make_session([
            self.user("First prompt"),
            {"type": "summary", "summary": "Summary"},
            {"type": "ai-title", "aiTitle": "Generated title"},
        ])
        self.assertEqual(MODULE.resolve_session_label(path), MODULE.SessionLabel("Generated title", "AI title"))

    def test_summary_precedes_prompt(self):
        path = self.make_session([self.user("First prompt"), {"type": "summary", "summary": "Summary"}])
        self.assertEqual(MODULE.resolve_session_label(path), MODULE.SessionLabel("Summary", "summary"))

    def test_first_prompt_skips_meta_and_tool_results(self):
        path = self.make_session([
            self.user("Internal", isMeta=True),
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "result"}]}},
            self.user("Meaningful prompt"),
        ])
        self.assertEqual(MODULE.resolve_session_label(path), MODULE.SessionLabel("Meaningful prompt", "first prompt"))

    def test_first_prompt_skips_command_only_placeholder(self):
        placeholder = (
            "<command-name>/clear</command-name> "
            "<command-message>clear</command-message> "
            "<command-args></command-args>"
        )
        path = self.make_session([
            self.user(placeholder),
            self.user("Actual session objective"),
        ])
        self.assertEqual(
            MODULE.resolve_session_label(path),
            MODULE.SessionLabel("Actual session objective", "first prompt"),
        )

    def test_command_envelope_is_removed_from_mixed_prompt(self):
        path = self.make_session([
            self.user(
                "<command-name>/plan</command-name> "
                "<command-message>plan</command-message> "
                "Please review the deployment plan"
            ),
        ])
        self.assertEqual(
            MODULE.resolve_session_label(path),
            MODULE.SessionLabel("Please review the deployment plan", "first prompt"),
        )

    def test_redacts_normalizes_and_truncates(self):
        path = self.make_session([{"type": "ai-title", "aiTitle": "token=secretvalue\nSecond line"}])
        label = MODULE.resolve_session_label(path, 18)
        self.assertEqual(label.source, "AI title")
        self.assertNotIn("secretvalue", label.text)
        self.assertNotIn("\n", label.text)
        self.assertLessEqual(len(label.text), 18)

    def test_wrong_session_metadata_is_ignored(self):
        path = self.make_session([
            {"type": "custom-title", "customTitle": "Wrong", "sessionId": "other"},
            self.user("Correct fallback"),
        ])
        self.assertEqual(MODULE.resolve_session_label(path), MODULE.SessionLabel("Correct fallback", "first prompt"))

    def test_malformed_and_empty_session_use_fallback(self):
        path = self.make_session([])
        path.write_text("not json\n", encoding="utf-8")
        self.assertEqual(MODULE.resolve_session_label(path), MODULE.SessionLabel("Untitled session", "fallback"))

    def test_duplicate_labels_keep_distinct_paths(self):
        one = self.make_session([{"type": "ai-title", "aiTitle": "Same"}], "11111111-1111-1111-1111-111111111111")
        two = self.make_session([{"type": "ai-title", "aiTitle": "Same"}], "22222222-2222-2222-2222-222222222222")
        self.assertEqual(MODULE.resolve_session_label(one).text, MODULE.resolve_session_label(two).text)
        self.assertNotEqual(one.stem, two.stem)

if __name__ == "__main__":
    unittest.main()

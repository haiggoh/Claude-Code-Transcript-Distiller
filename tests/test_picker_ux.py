from __future__ import annotations
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("distiller_picker_ux", ROOT / "cc_transcript.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

class PickerInputTests(unittest.TestCase):
    def test_hidden_session_numbers_are_parseable(self):
        self.assertEqual(MODULE.parse_selection("7", 20), [7])
        self.assertEqual(MODULE.parse_selection("18-20", 20), [18, 19, 20])

    def test_more_count_forms_are_equivalent(self):
        self.assertEqual(MODULE.parse_more_command("m"), 1)
        self.assertEqual(MODULE.parse_more_command("mmmmm"), 5)
        self.assertEqual(MODULE.parse_more_command("m5"), 5)
        self.assertIsNone(MODULE.parse_more_command("m0"))

    def test_arrow_escape_sequences_are_removed(self):
        cleaned, changed = MODULE.strip_terminal_escape_sequences("\x1b[A")
        self.assertEqual(cleaned, "")
        self.assertTrue(changed)
        cleaned, changed = MODULE.strip_terminal_escape_sequences("7\x1b[B")
        self.assertEqual(cleaned, "7")
        self.assertTrue(changed)

    def test_declining_more_exports_is_normal(self):
        with mock.patch("builtins.input", return_value="n"):
            self.assertFalse(MODULE.prompt_export_more())
        with mock.patch("builtins.input", return_value=""):
            self.assertFalse(MODULE.prompt_export_more())

    def test_main_decline_after_success_returns_zero(self):
        with tempfile.TemporaryDirectory(
            prefix="distiller-main-exit-test-"
        ) as temporary:
            source = Path(temporary) / "session.jsonl"
            source.write_text(
                '{"type":"user"}\n',
                encoding="utf-8",
            )
            args = SimpleNamespace(
                preview_chars=160,
                current=False,
                input=None,
                keep_base64=False,
                truncate_base64=False,
                output_dir=Path(temporary) / "out",
                base_name=None,
            )

            with (
                mock.patch.object(
                    MODULE,
                    "parse_args",
                    return_value=args,
                ),
                mock.patch.object(
                    MODULE,
                    "picker",
                    return_value=[source],
                ),
                mock.patch.object(
                    MODULE,
                    "export_session",
                    return_value=("created", [], False),
                ),
                mock.patch.object(
                    MODULE,
                    "prompt_export_more",
                    return_value=False,
                ) as prompt,
                mock.patch.object(
                    MODULE,
                    "print_batch_summary",
                ),
            ):
                self.assertEqual(MODULE.main(), 0)
                prompt.assert_called_once_with()

class PickerBundleStatusTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.source = self.root / "session.jsonl"
        self.source.write_text('{"type":"user"}\n', encoding="utf-8")
        self.output = self.root / "out"
        self.output.mkdir()

    def write_pair(self, snapshot_size=None, source_sha=None):
        header = {"__bundle_format__": 3}
        if snapshot_size is not None:
            header["__snapshot_size__"] = snapshot_size
        if source_sha is not None:
            header["__source_sha256__"] = source_sha
        (self.output / "session.compact.jsonl.txt").write_text(
            json.dumps({"__compact_session_header__": header}) + "\n",
            encoding="utf-8",
        )
        (self.output / "session.indexed_capsule.md").write_text("capsule\n", encoding="utf-8")

    def test_grown_source_is_marked_as_new_tail_to_verify(self):
        self.write_pair(snapshot_size=self.source.stat().st_size)
        with self.source.open("a", encoding="utf-8") as handle:
            handle.write('{"type":"assistant"}\n')
        self.assertEqual(
            MODULE.picker_bundle_status(self.source, self.output),
            ("new tail to verify", "yellow"),
        )

    def test_matching_checksum_is_current(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.write_pair(self.source.stat().st_size, digest)
        self.assertEqual(
            MODULE.picker_bundle_status(self.source, self.output),
            ("current", "green"),
        )

    def test_partial_pair_is_red(self):
        (self.output / "session.compact.jsonl.txt").write_text("{}\n", encoding="utf-8")
        self.assertEqual(
            MODULE.picker_bundle_status(self.source, self.output),
            ("partial artifacts", "red"),
        )

class ExistingBundlePromptTests(unittest.TestCase):
    def test_extension_defaults_to_continuation(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(MODULE.prompt_existing_mode("extension"), "continuation")

    def test_migration_still_defaults_to_amend(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(MODULE.prompt_existing_mode("format2-migration"), "amend")

if __name__ == "__main__":
    unittest.main()

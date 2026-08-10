from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("compact_session_bundle_migration_v3", ROOT / "compact_session_bundle.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

class MigrationV3Tests(unittest.TestCase):
    def root(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        return root

    def args(self, output, keep=False):
        return argparse.Namespace(
            output_dir=output, base_name=None, no_snapshot=False,
            permissive=False, keep_base64=False, omit_thinking=False,
            keep_legacy_artifacts=keep, existing="amend", force=False,
            preview_chars=MODULE.PREVIEW_CHARS, verify=True,
        )

    def prepare_format2(self, root, keep=False):
        source = root / "session.jsonl"
        source.write_text(json.dumps({"type":"user","sessionId":"s","message":{"role":"user","content":"Migrate me"}}) + "\n", encoding="utf-8")
        output = root / "out"
        output.mkdir()
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        header = {"__compact_session_header__": {
            "sessionId":"s", "__bundle_format__":2,
            "__generator__":"compact_session_bundle.py 0.5.0",
            "__source_name__":"session.jsonl", "__source_sha256__":source_sha,
        }}
        compact = output / "session.compact.jsonl.txt"
        old_body = {
            "type":"user",
            "message":{"role":"user","content":"Migrate me"},
        }
        compact.write_text(
            json.dumps(header) + "\n" + json.dumps(old_body) + "\n",
            encoding="utf-8",
        )
        (output / "session.indexed_capsule.md").write_text("old indexed\n", encoding="utf-8")
        (output / "session.compact_index.md").write_text("old index\n", encoding="utf-8")
        (output / "session.capsule.md").write_text("old capsule\n", encoding="utf-8")
        return source, output

    def test_exact_source_format2_migrates_and_retires_sidecars(self):
        root = self.root()
        source, output = self.prepare_format2(root)
        summary, artifacts, _ = MODULE.export_session(self.args(output), source, False)
        self.assertIn("legacy bundle migrated", summary)
        self.assertEqual({path.name for path in artifacts}, {"session.compact.jsonl.txt", "session.indexed_capsule.md"})
        self.assertFalse((output / "session.compact_index.md").exists())
        self.assertFalse((output / "session.capsule.md").exists())
        records = MODULE.read_jsonl(output / "session.compact.jsonl.txt")
        self.assertEqual(records[0]["__compact_session_header__"]["__bundle_format__"], 3)

    def test_keep_legacy_artifacts_retains_sidecars(self):
        root = self.root()
        source, output = self.prepare_format2(root)
        MODULE.export_session(self.args(output, keep=True), source, False)
        self.assertTrue((output / "session.compact_index.md").exists())
        self.assertTrue((output / "session.capsule.md").exists())

    def test_post_commit_verification_failure_restores_every_file(self):
        root = self.root()
        current = root / "current.txt"
        retired = root / "retired.txt"
        current.write_text("old current", encoding="utf-8")
        retired.write_text("old retired", encoding="utf-8")
        calls = 0
        def verifier(paths):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected post-commit failure")
        with self.assertRaisesRegex(RuntimeError, "injected"):
            MODULE.transactional_write(
                {current:"new current"}, {current}, {retired}, verifier
            )
        self.assertEqual(current.read_text(encoding="utf-8"), "old current")
        self.assertEqual(retired.read_text(encoding="utf-8"), "old retired")
        self.assertEqual(calls, 2)

    def test_source_hash_mismatch_is_not_accepted_as_format2_migration(self):
        existing = [{"__compact_session_header__": {
            "sessionId":"s", "__source_name__":"session.jsonl",
            "__source_sha256__":"old", "__bundle_format__":2,
        }}, {"old":True}]
        new = [{"__compact_session_header__": {
            "sessionId":"s", "__source_name__":"session.jsonl",
            "__source_sha256__":"new", "__bundle_format__":3,
        }}, {"new":True}]
        relationship, _ = MODULE.compare_existing(existing, new)
        self.assertEqual(relationship, "different")


    def test_format2_projected_prefix_is_verified_extension(self):
        existing = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"old-source", "__bundle_format__":2,
            }},
            {"type":"user","message":{"role":"user","content":"first"}},
        ]
        new = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"grown-source", "__bundle_format__":3,
            }},
            {"type":"user","message":{"role":"user","content":"first"}},
            {"type":"assistant","message":{"role":"assistant","content":"second"}},
        ]
        relationship, prior = MODULE.compare_existing(existing, new)
        self.assertEqual(relationship, "format2-extension")
        self.assertEqual(prior, 1)

    def test_format2_projected_changed_prefix_is_refused(self):
        existing = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"old-source", "__bundle_format__":2,
            }},
            {"type":"user","message":{"role":"user","content":"old"}},
        ]
        new = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"new-source", "__bundle_format__":3,
            }},
            {"type":"user","message":{"role":"user","content":"changed"}},
            {"type":"assistant","message":{"role":"assistant","content":"tail"}},
        ]
        relationship, _ = MODULE.compare_existing(existing, new)
        self.assertEqual(relationship, "different")


    def test_format2_extension_amends_end_to_end(self):
        root = self.root()
        source = root / "session.jsonl"
        first = {"type":"user","sessionId":"s","message":{"role":"user","content":"first"}}
        second = {"type":"assistant","sessionId":"s","message":{"role":"assistant","content":"second"}}
        source.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
        output = root / "out"
        output.mkdir()
        old_header = {"__compact_session_header__": {
            "sessionId":"s", "__bundle_format__":2,
            "__generator__":"compact_session_bundle.py 0.5.0",
            "__source_name__":"session.jsonl", "__source_sha256__":"older-source",
        }}
        old_first = {"type":"user","message":{"role":"user","content":"first"}}
        (output / "session.compact.jsonl.txt").write_text(
            json.dumps(old_header) + "\n" + json.dumps(old_first) + "\n",
            encoding="utf-8",
        )
        (output / "session.indexed_capsule.md").write_text("old indexed\n", encoding="utf-8")
        (output / "session.compact_index.md").write_text("old index\n", encoding="utf-8")
        (output / "session.capsule.md").write_text("old capsule\n", encoding="utf-8")
        summary, artifacts, _ = MODULE.export_session(self.args(output), source, False)
        self.assertIn("legacy bundle migrated", summary)
        self.assertEqual({path.name for path in artifacts}, {
            "session.compact.jsonl.txt", "session.indexed_capsule.md",
        })
        self.assertFalse((output / "session.compact_index.md").exists())
        self.assertFalse((output / "session.capsule.md").exists())
        records = MODULE.read_jsonl(output / "session.compact.jsonl.txt")
        self.assertEqual(len(records), 3)


    def test_matching_source_hash_does_not_bypass_body_validation(self):
        existing = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"same", "__bundle_format__":2,
            }},
            {"type":"user","message":{"role":"user","content":"corrupt"}},
        ]
        new = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"same", "__bundle_format__":3,
            }},
            {"type":"user","message":{"role":"user","content":"canonical"}},
        ]
        relationship, _ = MODULE.compare_existing(existing, new)
        self.assertEqual(relationship, "different")

    def test_format2_extension_projects_exact_v05_mirror_marker(self):
        payload = "tool output"
        old = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"old", "__bundle_format__":2,
            }},
            {
                "type":"user",
                "message":{"role":"user","content":[{
                    "type":"tool_result", "tool_use_id":"t",
                    "content":payload,
                }]},
                "toolUseResult":{
                    "stdout":MODULE.FORMAT2_DUPLICATE_PAYLOAD_MARKER,
                },
            },
        ]
        projected = MODULE.project_format2_body(old)
        new = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"grown", "__bundle_format__":3,
            }},
            projected[0],
            {"type":"assistant","message":{
                "role":"assistant", "content":"new tail",
            }},
        ]
        relationship, prior = MODULE.compare_existing(old, new)
        self.assertEqual(relationship, "format2-extension")
        self.assertEqual(prior, 1)
        self.assertIn(
            "__duplicate_payload_mirror__",
            projected[0]["toolUseResult"]["stdout"],
        )

    def test_no_verify_still_verifies_before_retiring_sidecars(self):
        root = self.root()
        source, output = self.prepare_format2(root)
        args = self.args(output)
        args.verify = False
        calls = []
        original_verify = MODULE.verify

        def tracked_verify(*verify_args, **verify_kwargs):
            calls.append(verify_args)
            return original_verify(*verify_args, **verify_kwargs)

        MODULE.verify = tracked_verify
        try:
            MODULE.export_session(args, source, False)
        finally:
            MODULE.verify = original_verify

        self.assertEqual(len(calls), 2)
        self.assertFalse((output / "session.compact_index.md").exists())
        self.assertFalse((output / "session.capsule.md").exists())


    def test_format2_projection_collapses_hoisted_state_repeats(self):
        existing = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"old", "__bundle_format__":2,
            }},
            {"type":"mode", "mode":"normal"},
            {"type":"permission-mode", "permissionMode":"auto"},
            {"type":"mode", "mode":"normal"},
            {"type":"permission-mode", "permissionMode":"auto"},
            {"type":"mode", "mode":"plan"},
            {"type":"mode", "mode":"plan"},
            {"type":"user","message":{
                "role":"user", "content":"instruction",
            }},
        ]
        projected = MODULE.project_format2_body(existing)
        self.assertEqual(
            [record for record in projected if record.get("type") == "mode"],
            [
                {"type":"mode", "mode":"normal"},
                {"type":"mode", "mode":"plan"},
            ],
        )
        self.assertEqual(
            [
                record for record in projected
                if record.get("type") == "permission-mode"
            ],
            [{"type":"permission-mode", "permissionMode":"auto"}],
        )
        self.assertEqual(projected[-1]["type"], "user")


    def test_format2_projection_preserves_empty_stdout(self):
        payload = "tool output"
        existing = [
            {"__compact_session_header__": {
                "sessionId":"s", "__source_name__":"session.jsonl",
                "__source_sha256__":"old", "__bundle_format__":2,
            }},
            {
                "type":"user",
                "message":{"role":"user","content":[{
                    "type":"tool_result", "tool_use_id":"t",
                    "content":payload,
                }]},
                "toolUseResult":{
                    "stdout":"", "stderr":"",
                    "interrupted":False,
                    "isImage":False,
                    "noOutputExpected":False,
                },
            },
        ]
        projected = MODULE.project_format2_body(existing)
        self.assertEqual(projected[0]["toolUseResult"]["stdout"], "")
        self.assertEqual(projected[0]["toolUseResult"]["stderr"], "")
        self.assertNotIn(
            "__duplicate_payload_mirror__",
            MODULE.serialize_jsonl(projected),
        )

if __name__ == "__main__":
    unittest.main()

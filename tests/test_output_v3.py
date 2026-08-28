from __future__ import annotations
import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("compact_session_bundle_output_v3", ROOT / "cc_transcript.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

class OutputV3Tests(unittest.TestCase):
    def make_source(self, records):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        source = root / "session.jsonl"
        source.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return root, source

    def args(self, output):
        return argparse.Namespace(
            output_dir=output, base_name=None, no_snapshot=False,
            permissive=False, keep_base64=False, omit_thinking=False,
            existing=None, force=False, preview_chars=MODULE.PREVIEW_CHARS,
            verify=True,
        )

    def test_default_export_creates_exactly_two_artifacts(self):
        root, source = self.make_source([
            {"type":"user","sessionId":"s","message":{"role":"user","content":"Please keep this instruction"}},
            {"type":"assistant","sessionId":"s","message":{"role":"assistant","content":"We should verify the result"}},
        ])
        output = root / "out"
        summary, artifacts, stop = MODULE.export_session(self.args(output), source, False)
        self.assertFalse(stop)
        self.assertEqual({path.name for path in artifacts}, {
            "session.compact.jsonl.txt", "session.indexed_capsule.md",
        })
        self.assertEqual({path.name for path in output.iterdir()}, {
            "session.compact.jsonl.txt", "session.indexed_capsule.md",
        })
        self.assertIn("bundle created", summary)

    def test_evidence_ids_are_one_per_line_and_shared_by_roles(self):
        records = [
            {"__compact_session_header__":{"__bundle_format__":3}},
            {"type":"user","message":{"role":"user","content":"Correction: do not finish; remaining work must be tested"}},
        ]
        rows = MODULE.build_rows(records, MODULE.PREVIEW_CHARS)
        audit = MODULE.Audit(physical_lines=1, parsed_records=1, retained_source_records=1)
        rendered = MODULE.make_indexed_capsule("s.jsonl", "s.compact.jsonl.txt", "abc123", rows, audit)
        self.assertEqual(rendered.count("### E1 — compact line 2 —"), 1)
        self.assertGreaterEqual(rendered.count("See E1."), 3)
        self.assertIn("correction, decision, open_work", rendered)
        self.assertIn("Treat this indexed capsule as the primary working context", rendered)


    def test_continuation_materializes_cross_boundary_duplicate(self):
        root, source = self.make_source([{
            "type":"assistant", "sessionId":"s",
            "message":{"role":"assistant","content":[{
                "type":"tool_use", "id":"t1", "name":"Write",
                "input":{"content":"x" * MODULE.PAYLOAD_INTERN_THRESHOLD},
            }]},
        }])
        output = root / "out"
        MODULE.export_session(self.args(output), source, False)
        with source.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type":"user", "sessionId":"s",
                "message":{"role":"user","content":[]},
                "toolUseResult":{"content":"x" * MODULE.PAYLOAD_INTERN_THRESHOLD},
            }) + "\n")
        args = self.args(output)
        args.existing = "continuation"
        summary, artifacts, _ = MODULE.export_session(args, source, False)
        self.assertIn("continuation created", summary)
        self.assertEqual({path.name for path in artifacts}, {
            "session.part2.compact.jsonl.txt",
            "session.part2.indexed_capsule.md",
        })
        continuation = MODULE.read_jsonl(output / "session.part2.compact.jsonl.txt")
        self.assertEqual(
            continuation[1]["toolUseResult"]["content"],
            "x" * MODULE.PAYLOAD_INTERN_THRESHOLD,
        )
        self.assertNotIn("__duplicate_payload__", MODULE.serialize_jsonl(continuation))
        header = continuation[0]["__compact_session_header__"]["__continuation__"]
        self.assertEqual(header["part"], 2)
        self.assertEqual(header["first_global_line"], 3)


    def test_continuation_materializes_cross_boundary_binary_mirror(self):
        payload = "cmVwZWF0LWJpbmFyeQ=="
        first = {
            "type":"user", "sessionId":"s",
            "message":{"role":"user","content":[{
                "type":"image",
                "source":{
                    "type":"base64", "media_type":"image/png",
                    "data":payload,
                },
            }]},
        }
        root, source = self.make_source([first])
        output = root / "out"
        MODULE.export_session(self.args(output), source, False)
        with source.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(first) + "\n")

        args = self.args(output)
        args.existing = "continuation"
        _, artifacts, _ = MODULE.export_session(args, source, False)
        self.assertEqual({path.name for path in artifacts}, {
            "session.part2.compact.jsonl.txt",
            "session.part2.indexed_capsule.md",
        })
        continuation = MODULE.read_jsonl(
            output / "session.part2.compact.jsonl.txt"
        )
        binary_value = continuation[1]["message"]["content"][0][
            "source"
        ]["data"]
        self.assertIn("__omitted_binary__", binary_value)
        self.assertNotIn(
            "__omitted_binary_mirror__",
            MODULE.serialize_jsonl(continuation),
        )
        MODULE.verify_binary_policy(continuation)

if __name__ == "__main__":
    unittest.main()

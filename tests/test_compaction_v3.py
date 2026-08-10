from __future__ import annotations
import base64, importlib.util, json, shutil, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("compact_session_bundle_v3", ROOT / "compact_session_bundle.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

class CompactionV3Tests(unittest.TestCase):
    def compact(self, records, **options):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        path = directory / "session.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return MODULE.compact_records(path, False, **options)

    def test_format_three_metadata_cleanup(self):
        records, audit = self.compact([
            {"type":"ai-title","sessionId":"s","aiTitle":"Old"},
            {"type":"ai-title","sessionId":"s","aiTitle":"Final"},
            {"type":"last-prompt","sessionId":"s","leafUuid":"x","lastPrompt":"cached"},
            {"type":"user","sessionId":"s","message":{"role":"user","content":"Real"}},
        ])
        header = records[0]["__compact_session_header__"]
        self.assertEqual(header["__bundle_format__"], 3)
        self.assertEqual(header["__session_title__"], {"value":"Final","source":"ai-title"})
        self.assertEqual(audit.transformations["repeated_ai_titles_removed"], 2)
        self.assertEqual(audit.transformations["last_prompt_records_removed"], 1)
        self.assertEqual(records[1]["message"]["content"], "Real")

    def test_binary_mirror_and_keep_base64(self):
        payload = base64.b64encode(b"binary-image").decode("ascii")
        source = {"type":"user","sessionId":"s",
            "message":{"role":"user","content":[{"type":"image","source":{"type":"base64","media_type":"image/png","data":payload}}]},
            "toolUseResult":{"file":{"type":"image","base64":payload,"originalSize":12,"dimensions":{"width":2,"height":3}}}}
        records, audit = self.compact([source])
        descriptor = records[1]["message"]["content"][0]["source"]["data"]["__omitted_binary__"]
        self.assertEqual(descriptor["sha256_of"], "decoded-bytes")
        self.assertEqual(descriptor["dimensions"], {"width":2,"height":3})
        self.assertIn("__omitted_binary_mirror__", records[1]["toolUseResult"]["file"]["base64"])
        self.assertNotIn(payload, MODULE.serialize_jsonl(records))
        self.assertEqual(audit.transformations["base64_occurrences_omitted"], 2)
        kept, _ = self.compact([source], keep_base64=True)
        self.assertEqual(kept[1]["message"]["content"][0]["source"]["data"], payload)

    def test_invalid_base64_fallback(self):
        records, _ = self.compact([{"type":"user","sessionId":"s","message":{"role":"user","content":[{"type":"image","source":{"type":"base64","data":"not valid!"}}]}}])
        marker = records[1]["message"]["content"][0]["source"]["data"]["__omitted_binary__"]
        self.assertEqual(marker["sha256_of"], "encoded-text")
        self.assertEqual(marker["decode_status"], "invalid-base64")

    def test_state_transitions_and_unknown_shape(self):
        records, audit = self.compact([
            {"type":"mode","sessionId":"s","mode":"normal"},
            {"type":"mode","sessionId":"s","mode":"normal"},
            {"type":"mode","sessionId":"s","mode":"plan"},
            {"type":"mode","sessionId":"s","mode":"plan"},
            {"type":"mode","sessionId":"s","mode":"normal"},
            {"type":"permission-mode","sessionId":"s","permissionMode":"auto"},
            {"type":"permission-mode","sessionId":"s","permissionMode":"auto"},
            {"type":"permission-mode","sessionId":"s","permissionMode":"edit-decision"},
            {"type":"mode","sessionId":"s","mode":"normal","future":True},
            {"type":"mode","sessionId":"s","mode":"normal","future":True},
        ])
        body = records[1:]
        self.assertEqual([r["mode"] for r in body if r["type"] == "mode" and "future" not in r], ["normal","plan","normal"])
        self.assertEqual(audit.transformations["unchanged_mode_records_removed"], 2)
        self.assertEqual(audit.transformations["unchanged_permission_mode_records_removed"], 1)
        self.assertEqual(sum("future" in r for r in body), 2)

    def test_thinking_policy_and_file_history(self):
        thinking = {"type":"assistant","sessionId":"s","message":{"role":"assistant","content":[{"type":"thinking","thinking":"reasoning","signature":"secret"}]}}
        preserved, _ = self.compact([thinking])
        self.assertEqual(preserved[1]["message"]["content"][0]["thinking"], "reasoning")
        self.assertNotIn("signature", preserved[1]["message"]["content"][0])
        omitted, audit = self.compact([thinking], omit_thinking=True)
        self.assertEqual(omitted[1]["message"]["content"][0]["type"], "thinking-omitted")
        self.assertEqual(audit.transformations["thinking_blocks_omitted"], 1)
        delta = {"type":"file-history-delta","backup":"opaque","messageId":"m","snapshotMessageId":"s","timestamp":"t","trackingPath":"p"}
        records, _ = self.compact([delta])
        self.assertEqual(records[1], delta)


    def test_exact_result_payload_references_earlier_tool_input(self):
        payload = "x" * MODULE.PAYLOAD_INTERN_THRESHOLD
        records, audit = self.compact([
            {"type":"assistant","sessionId":"s","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Write","input":{"content":payload}}]}},
            {"type":"user","sessionId":"s","message":{"role":"user","content":[]},"toolUseResult":{"content":payload}},
        ])
        self.assertEqual(records[1]["message"]["content"][0]["input"]["content"], payload)
        reference = records[2]["toolUseResult"]["content"]["__duplicate_payload__"]
        self.assertEqual(reference["first_compact_line"], 2)
        self.assertEqual(reference["characters"], len(payload))
        self.assertEqual(audit.transformations["duplicate_payloads_referenced"], 1)

    def test_small_and_conversational_payloads_are_not_interned(self):
        large = "visible " * 200
        small = "s" * (MODULE.PAYLOAD_INTERN_THRESHOLD - 1)
        records, audit = self.compact([
            {"type":"user","sessionId":"s","message":{"role":"user","content":large}},
            {"type":"user","sessionId":"s","message":{"role":"user","content":large},"toolUseResult":{"content":small}},
            {"type":"user","sessionId":"s","message":{"role":"user","content":large},"toolUseResult":{"content":small}},
        ])
        self.assertEqual(records[1]["message"]["content"], large)
        self.assertEqual(records[2]["message"]["content"], large)
        self.assertEqual(records[3]["toolUseResult"]["content"], small)
        self.assertEqual(audit.transformations["duplicate_payloads_referenced"], 0)

    def test_sparse_navigation_contains_lines_not_previews(self):
        records, audit = self.compact([
            {"type":"user","sessionId":"s","message":{"role":"user","content":"Initial instruction"}},
            {"type":"mode","sessionId":"s","mode":"plan"},
            {"type":"user","sessionId":"s","message":{"role":"user","content":"Remaining open work"}},
        ])
        rows = MODULE.build_rows(records, MODULE.PREVIEW_CHARS)
        navigation = MODULE.make_navigation(rows)
        self.assertEqual(navigation["initial_user"], 2)
        self.assertEqual(navigation["latest_user"], 4)
        self.assertEqual(navigation["human_turns"], [2, 4])
        self.assertEqual(navigation["state_changes"], [3])
        self.assertEqual(navigation["open_work"], [4])
        self.assertNotIn("Initial instruction", json.dumps(navigation))


    def test_duplicate_reference_verification_requires_earlier_payload(self):
        payload = "z" * MODULE.PAYLOAD_INTERN_THRESHOLD
        digest = MODULE.payload_digest(payload)
        records = [
            {"__compact_session_header__":{"__bundle_format__":3}},
            {"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t","name":"Write","input":{"content":payload}}]}},
            {"type":"user","message":{"role":"user","content":[]},"toolUseResult":{"content":{"__duplicate_payload__":{"first_compact_line":2,"characters":len(payload),"sha256":digest}}}},
        ]
        MODULE.verify_duplicate_payload_references(records)
        records[2]["toolUseResult"]["content"]["__duplicate_payload__"]["first_compact_line"] = 3
        with self.assertRaisesRegex(RuntimeError, "Invalid duplicate payload"):
            MODULE.verify_duplicate_payload_references(records)

    def test_file_history_accounting_matches_preserved_serialization(self):
        delta = {"type":"file-history-delta","backup":"opaque","messageId":"m","snapshotMessageId":"s","timestamp":"t","trackingPath":"p"}
        records, audit = self.compact([delta])
        expected_bytes = len((json.dumps(delta, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        self.assertEqual(audit.transformations["file_history_delta_records_preserved"], 1)
        self.assertEqual(audit.transformations["file_history_delta_bytes_preserved"], expected_bytes)


    def test_same_line_duplicate_uses_same_record_marker_not_backward_reference(self):
        payload = "q" * MODULE.PAYLOAD_INTERN_THRESHOLD
        records, audit = self.compact([{
            "type":"user", "sessionId":"s",
            "message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t","content":payload}]},
            "toolUseResult":{"content":payload},
        }])
        self.assertEqual(records[1]["message"]["content"][0]["content"], payload)
        marker = records[1]["toolUseResult"]["content"][
            "__duplicate_payload_mirror__"
        ]
        self.assertEqual(marker["characters"], len(payload))
        self.assertEqual(marker["sha256"], MODULE.payload_digest(payload))
        self.assertEqual(
            audit.transformations["same_record_payload_mirrors_referenced"],
            1,
        )
        self.assertEqual(audit.transformations["duplicate_payloads_referenced"], 0)
        self.assertNotIn(
            "__duplicate_payload__",
            MODULE.serialize_jsonl(records),
        )
        MODULE.verify_same_record_mirrors(records)
        MODULE.verify_duplicate_payload_references(records)


    def test_header_title_uses_first_meaningful_prompt_fallback(self):
        records, _ = self.compact([
            {"type":"user","sessionId":"s","isMeta":True,"message":{"role":"user","content":"Internal metadata"}},
            {"type":"user","sessionId":"s","message":{"role":"user","content":"Actual objective"}},
        ])
        self.assertEqual(
            records[0]["__compact_session_header__"]["__session_title__"],
            {"value":"Actual objective", "source":"first-prompt"},
        )

    def test_same_record_tool_result_mirror_uses_typed_marker(self):
        payload = "mirror payload"
        records, audit = self.compact([{
            "type":"user", "sessionId":"s",
            "message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t","content":payload}]},
            "toolUseResult":{"stdout":payload},
        }])
        marker = records[1]["toolUseResult"]["stdout"]["__duplicate_payload_mirror__"]
        self.assertEqual(marker["characters"], len(payload))
        self.assertEqual(marker["sha256"], MODULE.payload_digest(payload))
        self.assertEqual(audit.transformations["same_record_payload_mirrors_referenced"], 1)
        MODULE.verify_same_record_mirrors(records)


    def test_binary_policy_rejects_raw_base64_and_unresolved_mirror(self):
        payload = base64.b64encode(b"raw").decode("ascii")
        raw_records = [
            {"__compact_session_header__": {
                "__bundle_format__":3,
                "__omission_policy__":{"base64_omitted":True},
            }},
            {"type":"user","message":{"role":"user","content":[{
                "type":"image",
                "source":{"type":"base64","data":payload},
            }]}},
        ]
        with self.assertRaisesRegex(RuntimeError, "raw base64"):
            MODULE.verify_binary_policy(raw_records)

        mirror_records = [
            {"__compact_session_header__": {
                "__bundle_format__":3,
                "__omission_policy__":{"base64_omitted":True},
            }},
            {"value":{"__omitted_binary_mirror__":{
                "sha256_of":"decoded-bytes",
                "sha256":"deadbeef",
            }}},
        ]
        with self.assertRaisesRegex(RuntimeError, "Unresolved binary mirror"):
            MODULE.verify_binary_policy(mirror_records)

if __name__ == "__main__":
    unittest.main()

"""Resolve a session by its window NAME (custom-title), report its resumed forks and liveness.

User request 2026-11-27: four "dead" sessions were named by window title ("📡 <model> · 55").
The title lives only in custom-title records (JSON-escaped, so a text grep misses it); resumed
sessions are FORKS (new id, copied history = shared message uuids), and a collision-suffixed live
fork ("· 55-abundant-gadget") must never be mistaken for the exact name ("· 55").
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("cc_transcript_find", ROOT / "cc_transcript.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

EXACT = "📡 Z.ai GLM 5.3 Flash · 55"


def rec(uuid, ts, role="user", text="hi"):
    return {"type": role, "uuid": uuid, "timestamp": ts,
            "message": {"role": role, "content": text}}


class FindByNameTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.projects = self.root / "projects"
        self.project = self.projects / "proj"
        self.project.mkdir(parents=True)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()

    def write(self, sid, title, records):
        lines = [json.dumps(r) for r in records]  # ensure_ascii default: '·' becomes ·
        if title is not None:
            lines.append(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": sid}))
        path = self.project / f"{sid}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def history(self, n=30):
        return [rec(f"u{i}", f"2026-11-26T20:{i:02d}:00.000Z") for i in range(n)]

    # --- name resolution ------------------------------------------------------------------

    def test_title_is_read_even_though_json_escapes_it(self):
        path = self.write("aaaa", EXACT, self.history(2))
        self.assertIn("\\u00b7", path.read_text())  # the trap: a text grep for '·' misses it
        self.assertEqual(MODULE.session_custom_title(path), EXACT)

    def test_exact_match_beats_collision_suffixed_prefix_match(self):
        dead = self.write("dead", EXACT, self.history(2))
        self.write("fork", EXACT + "-abundant-gadget", self.history(2))
        kind, matches = MODULE.find_sessions_by_name(EXACT, self.projects)
        self.assertEqual(kind, "exact")
        self.assertEqual(matches, [dead])

    def test_prefix_match_when_no_exact_title_exists(self):
        fork = self.write("fork", EXACT + "-abundant-gadget", self.history(2))
        kind, matches = MODULE.find_sessions_by_name(EXACT, self.projects)
        self.assertEqual(kind, "prefix")
        self.assertEqual(matches, [fork])

    def test_two_exact_matches_are_both_returned_never_guessed(self):
        a = self.write("aaaa", EXACT, self.history(2))
        b = self.write("bbbb", EXACT, self.history(2))
        kind, matches = MODULE.find_sessions_by_name(EXACT, self.projects)
        self.assertEqual(kind, "exact")
        self.assertEqual(sorted(matches), sorted([a, b]))

    def test_no_match(self):
        self.write("aaaa", "something else", self.history(2))
        self.assertEqual(MODULE.find_sessions_by_name(EXACT, self.projects), ("none", []))

    # --- forks ----------------------------------------------------------------------------

    def test_fork_detected_with_fork_point_and_each_sides_unique_tail(self):
        shared = self.history(30)
        dead = self.write("dead", EXACT, shared + [rec("d1", "2026-11-27T12:18:00.000Z"),
                                                   rec("d2", "2026-11-27T12:21:00.000Z", "assistant")])
        self.write("fork", EXACT + "-abundant-gadget",
                   shared + [rec(f"f{i}", f"2026-11-27T13:{i:02d}:00.000Z") for i in range(5)])
        self.write("other", "unrelated", [rec(f"x{i}", "2026-11-27T10:00:00.000Z") for i in range(40)])
        forks = MODULE.find_forks(dead)
        self.assertEqual([f.path.stem for f in forks], ["fork"])
        fork = forks[0]
        self.assertEqual(fork.title, EXACT + "-abundant-gadget")
        self.assertEqual(fork.fork_point, "2026-11-26T20:29:00.000Z")
        self.assertEqual(fork.target_only, 2)
        self.assertEqual(fork.other_only, 5)

    def test_a_few_shared_uuids_do_not_make_a_fork(self):
        dead = self.write("dead", EXACT, self.history(30))
        self.write("near", "near", self.history(3) + [rec("n1", "2026-11-27T01:00:00.000Z")])
        self.assertEqual(MODULE.find_forks(dead), [])

    # --- liveness -------------------------------------------------------------------------

    def _register(self, sid, pid, proc_start):
        (self.sessions / f"{pid}.json").write_text(json.dumps(
            {"pid": pid, "sessionId": sid, "procStart": proc_start}))

    def _own_proc_start(self):
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getpid())], capture_output=True,
                             text=True, env={**os.environ, "LC_ALL": "C", "TZ": "UTC"}).stdout
        return " ".join(out.split())

    def test_live_when_registered_pid_runs_with_matching_start(self):
        self._register("sess", os.getpid(), self._own_proc_start())
        self.assertEqual(MODULE.session_liveness("sess", self.sessions), "live")

    def test_recycled_pid_with_other_start_time_is_not_live(self):
        self._register("sess", os.getpid(), "Mon Jan  1 00:00:00 2001")
        self.assertEqual(MODULE.session_liveness("sess", self.sessions), "exited")

    def test_unregistered_session_is_exited(self):
        self.assertEqual(MODULE.session_liveness("nope", self.sessions), "exited")

    # --- CLI ------------------------------------------------------------------------------

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        old = (sys.argv, MODULE.CLAUDE_PROJECTS_DIR, MODULE.CLAUDE_SESSIONS_DIR)
        sys.argv = ["cc_transcript.py", *argv]
        MODULE.CLAUDE_PROJECTS_DIR, MODULE.CLAUDE_SESSIONS_DIR = self.projects, self.sessions
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = MODULE.main()
        finally:
            sys.argv, MODULE.CLAUDE_PROJECTS_DIR, MODULE.CLAUDE_SESSIONS_DIR = old
        return code, out.getvalue(), err.getvalue()

    def test_find_prints_match_and_forks_without_writing(self):
        shared = self.history(30)
        self.write("dead", EXACT, shared + [rec("d1", "2026-11-27T12:18:00.000Z")])
        self.write("fork", EXACT + "-abundant-gadget", shared + [rec("f1", "2026-11-27T13:00:00.000Z")])
        outdir = self.root / "out"
        code, out, _ = self.run_main(["--find", EXACT, "-o", str(outdir)])
        self.assertEqual(code, 0)
        self.assertIn("exact", out)
        self.assertIn("dead", out)
        self.assertIn("fork", out)
        self.assertIn("2026-11-26T20:29", out)
        self.assertFalse(outdir.exists())

    def test_find_with_no_match_exits_1(self):
        code, _, err = self.run_main(["--find", EXACT])
        self.assertEqual(code, 1)
        self.assertIn(EXACT, err)

    def test_positional_name_that_is_ambiguous_refuses_and_lists(self):
        self.write("aaaa", EXACT, self.history(2))
        self.write("bbbb", EXACT, self.history(2))
        code, _, err = self.run_main([EXACT, "-o", str(self.root / "out")])
        self.assertEqual(code, 2)
        self.assertIn("aaaa", err)
        self.assertIn("bbbb", err)

    def test_positional_unknown_name_fails_naming_both_readings(self):
        code, _, err = self.run_main(["no-such-file-or-name", "-o", str(self.root / "out")])
        self.assertEqual(code, 1)
        self.assertIn("not a file", err)

    def test_positional_name_resolves_and_distills_the_exact_session(self):
        self.write("dead", EXACT, self.history(3))
        self.write("fork", EXACT + "-abundant-gadget", self.history(3))
        outdir = self.root / "out"
        code, out, _ = self.run_main([EXACT, "-o", str(outdir)])
        self.assertEqual(code, 0, out)
        self.assertIn("dead.jsonl", out)
        self.assertTrue(any(p.name.startswith("dead") for p in outdir.iterdir()))
        self.assertFalse(any(p.name.startswith("fork") for p in outdir.iterdir()))


if __name__ == "__main__":
    unittest.main()

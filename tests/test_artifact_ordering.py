"""Regression tests for artifact write order and mtime staggering (0.6.3).

A batch export wrote every artifact inside the same second, and filesystem timestamps carry no
sub-second component that Finder sorts on — so "sort by date" gave an arbitrary order that no
longer matched the sessions' chronology. Two halves are pinned here: the batch must be PROCESSED
oldest-source-first, and the artifacts must then land on distinct, increasing, non-future mtimes.
"""
from __future__ import annotations
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("compact_session_bundle_artifact_ordering",
                                              ROOT / "cc_transcript.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class SourceChronologyOrderTests(unittest.TestCase):
    """The picker lists newest-first; export order must be the reverse of that."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        now = time.time()
        self.sources = {}
        for index, name in enumerate(("oldest", "middle", "newest")):
            path = self.dir / f"{name}.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            stamp = now - (3 - index) * 3600
            os.utime(path, (stamp, stamp))
            self.sources[name] = path

    def test_oldest_source_is_processed_first(self) -> None:
        picker_order = [self.sources["newest"], self.sources["middle"], self.sources["oldest"]]
        ordered = sorted(picker_order, key=MODULE.source_chronology_key)
        self.assertEqual([p.stem for p in ordered], ["oldest", "middle", "newest"])

    def test_unreadable_source_sorts_last_without_raising(self) -> None:
        """An unreadable mtime is not a reason to refuse to export the readable sources."""
        candidates = list(self.sources.values()) + [self.dir / "does-not-exist.jsonl"]
        ordered = sorted(candidates, key=MODULE.source_chronology_key)
        self.assertEqual(ordered[-1].stem, "does-not-exist")


class ArtifactTimeStaggerTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def _artifacts(self, sessions: int) -> list[Path]:
        made = []
        for name in [f"session{n}" for n in range(sessions)]:
            for kind in ("compact.jsonl.txt", "indexed_capsule.md"):
                path = self.dir / f"{name}.{kind}"
                path.write_text("x", encoding="utf-8")
                made.append(path)
        return made

    def test_batch_artifacts_get_distinct_increasing_seconds(self) -> None:
        """Three sessions, six artifacts: date order must equal write order."""
        artifacts = self._artifacts(3)
        self.assertEqual(MODULE.stagger_artifact_times(artifacts), 6)
        stamps = [os.stat(a).st_mtime for a in artifacts]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len({int(s) for s in stamps}), len(stamps),
                         "artifacts shared a whole second, which is exactly the Finder-sort bug")
        for earlier, later in zip(stamps, stamps[1:]):
            self.assertAlmostEqual(later - earlier, 1.0, places=6)

    def test_nothing_is_stamped_in_the_future(self) -> None:
        """Future mtimes confuse Finder, backup tools and staleness checks."""
        artifacts = self._artifacts(4)
        MODULE.stagger_artifact_times(artifacts)
        self.assertLessEqual(max(os.stat(a).st_mtime for a in artifacts), time.time() + 0.001)

    def test_single_and_empty_runs_are_noops(self) -> None:
        artifacts = self._artifacts(1)
        self.assertEqual(MODULE.stagger_artifact_times([]), 0)
        self.assertEqual(MODULE.stagger_artifact_times(artifacts[:1]), 0)

    def test_missing_artifact_is_skipped_not_fatal(self) -> None:
        """Cosmetic ordering must never fail an export whose real output already succeeded."""
        artifacts = self._artifacts(1)
        mixed = [artifacts[0], self.dir / "vanished.md", artifacts[1]]
        self.assertEqual(MODULE.stagger_artifact_times(mixed), 2)


class BatchWiringTests(unittest.TestCase):
    """The helpers above are only useful if main() actually calls them.

    Both fixes live inside main()'s batch loop, which needs the interactive picker to produce a
    multi-session batch and so is not reachable from a unit test. This inspects the parsed AST
    instead of the text, so it pins the WIRING (the calls exist in main) without being sensitive
    to formatting. It is a structural check, not a behavioural one — the behaviour is covered by
    the tests above.
    """

    def _main_function(self):
        import ast
        tree = ast.parse((ROOT / "cc_transcript.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return node
        self.fail("main() not found")

    def _called_names(self, node):
        import ast
        names = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
            # a bare function reference passed as key= is not a Call
            if isinstance(sub, ast.Name):
                names.add(sub.id)
        return names

    def test_main_orders_the_batch_and_staggers_the_artifacts(self) -> None:
        called = self._called_names(self._main_function())
        self.assertIn("source_chronology_key", called,
                      "main() no longer orders the batch by source chronology")
        self.assertIn("stagger_artifact_times", called,
                      "main() no longer staggers artifact mtimes")


if __name__ == "__main__":
    unittest.main()

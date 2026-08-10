from __future__ import annotations
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

class ReleaseBuilderTests(unittest.TestCase):
    def test_custom_archive_is_runtime_only_and_defaults_to_061(self):
        workspace = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workspace)
        for name in ("compact_session_bundle.py", "README.md", "LICENSE", "CHANGELOG.md"):
            shutil.copy2(ROOT / name, workspace / name)
        (workspace / "docs").mkdir()
        shutil.copy2(
            ROOT / "docs/compact-format-3.md",
            workspace / "docs/compact-format-3.md",
        )
        (workspace / "scripts").mkdir()
        shutil.copy2(
            ROOT / "scripts/build-release.py",
            workspace / "scripts/build-release.py",
        )
        subprocess.run([sys.executable, "scripts/build-release.py"], cwd=workspace, check=True, capture_output=True, text=True)
        archive = workspace / "dist/claude-code-session-bundle-0.6.1.tar.gz"
        self.assertTrue(archive.is_file())
        with tarfile.open(archive, "r:gz") as handle:
            names = set(handle.getnames())
        self.assertIn(
            "claude-code-session-bundle-0.6.1/compact_session_bundle.py",
            names,
        )
        self.assertIn(
            "claude-code-session-bundle-0.6.1/docs/compact-format-3.md",
            names,
        )
        self.assertFalse(any("/tests/" in name for name in names))


    def test_readme_documents_format_three_policy_and_two_artifacts(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("### Base64 and thinking policy", readme)
        self.assertIn("SESSION.compact.jsonl.txt", readme)
        self.assertIn("SESSION.indexed_capsule.md", readme)
        self.assertIn("--keep-base64", readme)
        self.assertIn("--omit-thinking", readme)
        self.assertIn("--keep-legacy-artifacts", readme)
        self.assertNotIn("SESSION.compact_index.md", readme)
        self.assertNotIn("SESSION.capsule.md", readme)

if __name__ == "__main__":
    unittest.main()

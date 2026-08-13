from __future__ import annotations
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def packaged_version() -> str:
    text = (ROOT / "compact_session_bundle.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "compact_session_bundle.py must declare VERSION"
    return match.group(1)


class ReleaseBuilderTests(unittest.TestCase):
    def build(self, *args):
        """Run the builder against a copy of the repo and return its dist directory."""
        workspace = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workspace)
        for name in ("compact_session_bundle.py", "README.md", "LICENSE", "CHANGELOG.md"):
            shutil.copy2(ROOT / name, workspace / name)
        (workspace / "docs").mkdir()
        shutil.copy2(ROOT / "docs/compact-format-3.md", workspace / "docs/compact-format-3.md")
        (workspace / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/build-release.py", workspace / "scripts/build-release.py")
        subprocess.run(
            [sys.executable, "scripts/build-release.py", *args],
            cwd=workspace, check=True, capture_output=True, text=True,
        )
        return workspace / "dist"

    def test_archive_version_tracks_the_packaged_script(self):
        # Asserted against VERSION rather than a literal: this test previously pinned
        # "0.6.1" in its name and three assertions, so the builder's own stale default
        # went unnoticed and it produced a 0.6.1-named archive from a 0.6.2 source.
        version = packaged_version()
        dist = self.build()
        archive = dist / f"claude-code-session-bundle-{version}.tar.gz"
        self.assertTrue(archive.is_file(), f"expected an archive named for version {version}")
        self.assertTrue((dist / f"claude-code-session-bundle-installer-v{version}.zsh").is_file())
        with tarfile.open(archive, "r:gz") as handle:
            names = set(handle.getnames())
        self.assertIn(f"claude-code-session-bundle-{version}/compact_session_bundle.py", names)
        self.assertIn(f"claude-code-session-bundle-{version}/docs/compact-format-3.md", names)
        self.assertFalse(any("/tests/" in name for name in names))

    def test_explicit_version_argument_overrides_the_source_version(self):
        dist = self.build("9.9.9")
        self.assertTrue((dist / "claude-code-session-bundle-9.9.9.tar.gz").is_file())
        self.assertFalse(
            (dist / f"claude-code-session-bundle-{packaged_version()}.tar.gz").is_file(),
            "an explicit version must not also emit a source-versioned archive",
        )

    def test_checksums_cover_every_built_artifact(self):
        dist = self.build()
        sums = (dist / "SHA256SUMS").read_text(encoding="utf-8")
        built = {p.name for p in dist.iterdir() if p.name != "SHA256SUMS"}
        for name in built:
            with self.subTest(artifact=name):
                self.assertIn(name, sums)


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

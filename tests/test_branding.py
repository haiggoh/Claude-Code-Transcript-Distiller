from __future__ import annotations
"""Branding (rollout Task 16): the About mark ⚗️ leads the export report header,
the SKILL.md invocation section, and the README h1.

Usage: python3 tests/test_branding.py [-v]
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("cc_transcript_branding", ROOT / "cc_transcript.py")  # noqa: E402
MODULE = importlib.util.module_from_spec(SPEC)  # noqa: E402
sys.modules[SPEC.name] = MODULE  # noqa: E402
assert SPEC.loader is not None  # noqa: E402
SPEC.loader.exec_module(MODULE)


class BrandingTests(unittest.TestCase):
    def test_export_report_header_exact(self):
        # exact pin — never loosened to a substring
        self.assertEqual(MODULE.EXPORT_REPORT_HEADER,
                         "⚗️ Successfully exported the following artifacts:")

    def test_skill_branding_section(self):
        text = (ROOT / "skills" / "cc-transcript" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Branding (⚗️)", text)

    def test_readme_h1_exact(self):
        first = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first, "# ⚗️ Claude Code Transcript Distiller")


if __name__ == "__main__":
    unittest.main()

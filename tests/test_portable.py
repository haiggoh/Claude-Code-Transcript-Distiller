"""Portable package for the nested cc-transcript plugin: the nested skills/cc-transcript/
package IS the portable root (manifest at root per build.md); PORTABLE-BUILD.json records
the external CLI dependency and its version mapping (plugin version == CLI VERSION)."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "scripts" / "build-portable.py"
PORTABLE = ROOT / "portable"
NESTED = ROOT / "skills" / "cc-transcript"


def run_build(*args):
    return subprocess.run([sys.executable, str(BUILD), *args], capture_output=True,
                          text=True, cwd=ROOT, timeout=60)


def test_portable_is_the_nested_package_at_root():
    assert (PORTABLE / ".claude-plugin" / "plugin.json").is_file()
    assert (PORTABLE / "SKILL.md").is_file()
    for rel in (".claude-plugin/plugin.json", "SKILL.md"):
        assert (PORTABLE / rel).read_bytes() == (NESTED / rel).read_bytes(), rel
    m = json.loads((PORTABLE / ".claude-plugin" / "plugin.json").read_text())
    assert m["name"] == "cc-transcript"


def test_portable_fresh():
    r = run_build("--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_build_json_records_cli_version_mapping():
    d = json.loads((PORTABLE / "PORTABLE-BUILD.json").read_text())
    assert d["schema"] == "haiggoh-portable-build/1"
    deps = d["external_dependencies"]
    assert len(deps) == 1 and deps[0]["name"].startswith("cc_transcript")
    import re
    cli_ver = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                        (ROOT / "cc_transcript.py").read_text(), re.M).group(1)
    plugin_ver = json.loads((NESTED / ".claude-plugin" / "plugin.json").read_text())["version"]
    assert deps[0]["version"] == cli_ver == plugin_ver, (deps[0]["version"], cli_ver, plugin_ver)
    assert deps[0]["surfaces"]["chat"] is False and deps[0]["surfaces"]["code"] is True


def test_skill_states_host_cli_plainly():
    text = (PORTABLE / "SKILL.md").read_text()
    assert "host-only" in text and "guidance-only" in text


def test_zip_manifest_at_root():
    import zipfile, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r = run_build("--zip", "--out", tmp)
        assert r.returncode == 0, r.stdout + r.stderr
        z = Path(tmp) / "cc-transcript-portable.zip"
        names = zipfile.ZipFile(z).namelist()
        assert ".claude-plugin/plugin.json" in names and "SKILL.md" in names, names
        assert not any(n.startswith("skills/") for n in names)

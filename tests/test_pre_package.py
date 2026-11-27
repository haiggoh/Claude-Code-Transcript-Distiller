"""Tests for .rtc/pre-package.sh — the rtc-ship asset builder hook.

rtc-ship.sh runs the hook from the worktree root BEFORE resolving the asset globs
in rtc-ship.json, so dist/ must hold exactly THIS version's assets when it exits:
stale tarballs from older builds are wiped first, otherwise the glob
dist/claude-code-transcript-distiller-*.tar.gz matches them too and the ship
fails (or ships a stale asset). CCT_REPO_ROOT lets tests point the hook at a
temp copy of the repo; set-but-invalid must abort, never fall through.
"""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".rtc" / "pre-package.sh"

# files build-release.py packs into the tarball (it reads each one's bytes)
PACKED_FILES = [
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "docs/compact-format-3.md",
    "skills/cc-transcript/SKILL.md",
    "skills/cc-transcript/.claude-plugin/plugin.json",
]


def run_pre_package(root, *args, cwd=None):
    env = dict(os.environ)
    env["CCT_REPO_ROOT"] = str(root)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True, env=env, cwd=str(cwd if cwd is not None else root),
    )


def make_repo(tmp_path, version="9.9.9", builder=None):
    """A minimal fake repo root: cc_transcript.py + scripts/build-release.py + packed files."""
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "cc_transcript.py").write_text(f'VERSION = "{version}"\n')
    if builder is None:
        shutil.copy(ROOT / "scripts" / "build-release.py", tmp_path / "scripts" / "build-release.py")
    else:
        (tmp_path / "scripts" / "build-release.py").write_text(builder)
    for name in PACKED_FILES:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("placeholder\n")
    return tmp_path


def test_help_exits_zero_and_builds_nothing(tmp_path):
    r = run_pre_package(tmp_path, "--help")
    assert r.returncode == 0, r.stderr
    assert "Usage" in r.stdout
    assert "set -euo" not in r.stdout, "--help must not print the shell directive line"
    assert not (tmp_path / "dist").exists(), "--help must not build anything"


def test_hook_is_executable():
    # rtc-ship.sh only runs the hook when [ -x .rtc/pre-package.sh ]; git must record mode 100755
    assert os.access(SCRIPT, os.X_OK), ".rtc/pre-package.sh must be executable (mode 100755)"


def test_unknown_argument_exits_2_and_builds_nothing(tmp_path):
    r = run_pre_package(tmp_path, "--frobnicate")
    assert r.returncode == 2
    assert not (tmp_path / "dist").exists()


def test_builds_exactly_this_version_assets(tmp_path):
    root = make_repo(tmp_path, version="9.9.9")
    r = run_pre_package(root)
    assert r.returncode == 0, r.stderr
    names = sorted(p.name for p in (root / "dist").iterdir())
    assert names == [
        "SHA256SUMS",
        "claude-code-transcript-distiller-9.9.9.tar.gz",
        "claude-code-transcript-distiller-installer-v9.9.9.zsh",
    ]


def test_stale_artifacts_are_wiped_before_build(tmp_path):
    root = make_repo(tmp_path, version="9.9.9")
    dist = root / "dist"
    dist.mkdir()
    (dist / "claude-code-transcript-distiller-0.0.1.tar.gz").write_bytes(b"stale")
    (dist / "claude-code-transcript-distiller-installer-v0.0.1.zsh").write_text("#!/bin/zsh\n")
    r = run_pre_package(root)
    assert r.returncode == 0, r.stderr
    names = sorted(p.name for p in dist.iterdir())
    assert all("0.0.1" not in n for n in names), names
    assert "claude-code-transcript-distiller-9.9.9.tar.gz" in names


def test_missing_asset_fails_loudly_naming_the_version(tmp_path):
    # a builder that writes a WRONG-version tarball: the hook must fail, not pass
    stub = (
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "d = Path(__file__).resolve().parent.parent / 'dist'\n"
        "d.mkdir(exist_ok=True)\n"
        "(d / 'claude-code-transcript-distiller-0.0.1.tar.gz').write_bytes(b'x')\n"
    )
    root = make_repo(tmp_path, version="9.9.9", builder=stub)
    r = run_pre_package(root)
    assert r.returncode != 0
    assert "9.9.9" in r.stderr, "the failure must name the expected version"


def test_invalid_repo_root_override_fails_loudly(tmp_path):
    # cwd stays valid so the hook actually launches; the override is what is invalid
    r = run_pre_package(tmp_path / "does-not-exist", cwd=tmp_path)
    assert r.returncode != 0
    assert "CCT_REPO_ROOT" in r.stderr

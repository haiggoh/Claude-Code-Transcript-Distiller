#!/usr/bin/env python3
"""Build the portable package for the nested cc-transcript plugin (COORDINATION C1/C2).

The publishable plugin IS the nested ``skills/cc-transcript/`` directory (its own manifest),
so the portable package root is that directory's contents — manifest at the zip/dir root,
per build.md ("manifest at zip root or inside one top-level folder"). The skill invokes the
HOST-installed cc_transcript.py CLI (external dependency, recorded in PORTABLE-BUILD.json's
version mapping: CLI VERSION vs plugin version).

Usage:
  build-portable.py            build portable/ in place (temp build + swap)
  build-portable.py --check    rebuild in a temp dir (stamped with the committed
                               source_commit/source_repo) and diff; exit 1 on drift
  build-portable.py --zip [--out DIR]   emit dist/cc-transcript-portable.zip (portable/ untouched)
  build-portable.py --commit SHA  record this source_commit

Env: none. Stdlib only. Requires git only when --commit is not given.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

SCHEMA = "haiggoh-portable-build/1"
TRANSFORM_VERSION = "1"
BUILDER = "scripts/build-portable.py"
BUILDER_VERSION = "1.0.0"
FALLBACK_SOURCE_REPO = "haiggoh/claude-code-transcript-distiller"
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
IGNORE_DIRS = {"__pycache__", ".pytest_cache", ".git"}
IGNORE_SUFFIXES = (".pyc", ".pyo")

NESTED = "skills/cc-transcript"   # the publishable package (own manifest)
# host-relative files of the NESTED package, copied to the portable ROOT
COPY_FILES = [NESTED + "/.claude-plugin/plugin.json", NESTED + "/SKILL.md"]


def _cli_version(root):
    """The external CLI's version literal (cc_transcript.py VERSION) — the version mapping
    the plan requires recorded: plugin version == CLI version."""
    src = os.path.join(root, "cc_transcript.py")
    try:
        with open(src, encoding="utf-8") as f:
            m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
            return m.group(1) if m else None
    except OSError:
        return None


COMPONENTS = [
    {"path": ".claude-plugin/plugin.json", "kind": "manifest",
     "adaptation_class": "mechanical", "evidence_level": "source-inspected",
     "surfaces": {"chat": True, "cowork": True, "code": True},
     "notes": "same name+version as the nested host manifest; an installed marketplace plugin "
              "wins over a synced copy in Code, so no double-run"},
    {"path": "SKILL.md", "kind": "skill",
     "adaptation_class": "native-unchanged", "evidence_level": "source-inspected",
     "surfaces": {"chat": "guidance-only (no host CLI)", "cowork": "guidance-only (no host CLI)", "code": True},
     "notes": "invokes the HOST-installed cc_transcript.py CLI (external dependency); in "
              "Chat/Cowork there is no Homebrew — the skill says so instead of faking a distill"},
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git(args, cwd):
    try:
        return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                              check=True).stdout.strip()
    except FileNotFoundError:
        sys.exit("build-portable.py: git binary not found — pass --commit <sha> explicitly")
    except subprocess.CalledProcessError as e:
        sys.exit(f"build-portable.py: git {' '.join(args)} failed: {e.stderr.strip()}")


def source_identity(root, commit_override):
    commit = commit_override or git(["rev-parse", "HEAD"], root)
    remote = ""
    try:
        remote = git(["remote", "get-url", "origin"], root)
    except SystemExit:
        remote = ""
    m = re.match(r"(?:git@[^:]+:|https://[^/]+/)([^/]+/[^/]+?)(?:\.git)?$", remote or "")
    return (m.group(1) if m else FALLBACK_SOURCE_REPO), commit


def walk_files(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in sorted(filenames):
            if fn.endswith(IGNORE_SUFFIXES):
                continue
            p = os.path.join(dirpath, fn)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = p
    return out


def build_tree(root, dest):
    for rel in COPY_FILES:
        src = os.path.join(root, rel)
        dst = os.path.join(dest, os.path.relpath(rel, NESTED))  # nested package -> portable root
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb") as i, open(dst, "wb") as o:
            shutil.copyfileobj(i, o)
        os.chmod(dst, 0o644)


def write_build_json(dest, root, commit_override, repo_override=None):
    source_repo, commit = source_identity(root, commit_override)
    if repo_override:
        source_repo = repo_override
    with open(os.path.join(dest, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        plugin_version = json.load(f)["version"]
    files = {rel: sha256_file(os.path.join(dest, rel)) for rel in walk_files(dest)}
    assert plugin_version == _cli_version(root), \
        f"plugin {plugin_version} != CLI {_cli_version(root)} — align them before shipping"
    doc = {
        "schema": SCHEMA, "source_repo": source_repo, "source_commit": commit,
        "builder": BUILDER, "builder_version": BUILDER_VERSION,
        "transform_version": TRANSFORM_VERSION, "files": files, "components": COMPONENTS,
        "external_dependencies": [{
            "name": "cc_transcript.py CLI", "version": _cli_version(root),
            "install": "host-only (Homebrew haiggoh/tap/claude-code-transcript-distiller); "
                       "plugin version tracks it (both 0.8.4)",
            "surfaces": {"chat": False, "cowork": False, "code": True},
        }],
    }
    with open(os.path.join(dest, "PORTABLE-BUILD.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def diff_trees(a, b):
    fa, fb = walk_files(a), walk_files(b)
    return [rel for rel in sorted(set(fa) | set(fb))
            if fa.get(rel) is None or fb.get(rel) is None
            or sha256_file(fa[rel]) != sha256_file(fb[rel])]


def write_zip(build_dir, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    zpath = os.path.join(out_dir, f"{name}-portable.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(walk_files(build_dir)):
            zi = zipfile.ZipInfo(rel, date_time=ZIP_EPOCH)
            zi.external_attr = (0o644 & 0xFFFF) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(os.path.join(build_dir, rel), "rb") as f:
                z.writestr(zi, f.read())
    return zpath


def main(argv=None):
    ap = argparse.ArgumentParser(prog="build-portable.py",
                                 description="Generate the committed portable/ package for the nested cc-transcript plugin. Never hand-edit portable/.")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--out", metavar="DIR", default=None)
    ap.add_argument("--commit", metavar="SHA", default=None)
    args = ap.parse_args(argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    portable = os.path.join(root, "portable")
    check_commit, check_repo = args.commit, None
    if args.check and check_commit is None and os.path.isfile(os.path.join(portable, "PORTABLE-BUILD.json")):
        with open(os.path.join(portable, "PORTABLE-BUILD.json"), encoding="utf-8") as f:
            committed = json.load(f)
        check_commit, check_repo = committed.get("source_commit"), committed.get("source_repo")
    with tempfile.TemporaryDirectory() as tmp:
        fresh = os.path.join(tmp, "portable")
        os.makedirs(fresh)
        build_tree(root, fresh)
        write_build_json(fresh, root, check_commit or args.commit, check_repo)
        if args.check:
            if not os.path.isdir(portable):
                print("✗ portable/ does not exist — run scripts/build-portable.py first")
                return 1
            drift = diff_trees(fresh, portable)
            if drift:
                print("✗ portable/ drifted:", *("  " + r for r in drift), sep="\n")
                return 1
            print("✓ portable/ matches a fresh build")
            return 0
        if args.zip:
            with open(os.path.join(root, NESTED, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
                name = json.load(f)["name"]
            print(f"✓ wrote {write_zip(fresh, args.out or os.path.join(root, 'dist'), name)} (temp build; portable/ untouched)")
            return 0
        if os.path.isdir(portable):
            shutil.rmtree(portable)
        try:
            os.rename(fresh, portable)
        except OSError:
            shutil.copytree(fresh, portable)
            shutil.rmtree(fresh)
    print(f"✓ built portable/ ({len(walk_files(portable))} files) + PORTABLE-BUILD.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

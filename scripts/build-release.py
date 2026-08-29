#!/usr/bin/env python3
from __future__ import annotations
import gzip, hashlib, io, re, sys, tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def source_version() -> str:
    """Read VERSION from the script being packaged.

    A hardcoded default here silently produced a 0.6.1-named archive from a 0.6.2 source:
    a version literal kept apart from its source of truth goes stale the moment the source
    moves, and the artifact name is not something a release check tends to re-read. The
    same failure shipped a Homebrew formula pinned to a superseded release.
    """
    text = (ROOT / "cc_transcript.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("FAIL: could not read VERSION from cc_transcript.py")
    return match.group(1)


SOURCE_VERSION = source_version()
if len(sys.argv) > 2:
    raise SystemExit("FAIL: usage: build-release.py [VERSION]")
if len(sys.argv) == 2 and sys.argv[1] != SOURCE_VERSION:
    raise SystemExit(
        f"FAIL: requested version {sys.argv[1]} does not match "
        f"cc_transcript.py VERSION {SOURCE_VERSION}"
    )
VERSION = SOURCE_VERSION
DIST = ROOT / "dist"
PREFIX = f"claude-code-transcript-distiller-{VERSION}"
FILES = [
    "cc_transcript.py",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "docs/compact-format-3.md",
]
DIST.mkdir(exist_ok=True)
archive = DIST / f"{PREFIX}.tar.gz"
raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
    for name in FILES:
        data = (ROOT / name).read_bytes()
        info = tarfile.TarInfo(f"{PREFIX}/{name}")
        info.size = len(data)
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mode = 0o755 if name == "cc_transcript.py" else 0o644
        tf.addfile(info, io.BytesIO(data))
archive.write_bytes(gzip.compress(raw.getvalue(), compresslevel=9, mtime=0))
archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()

installer = DIST / f"claude-code-transcript-distiller-installer-v{VERSION}.zsh"
installer.write_text(f'''#!/bin/zsh
set -e
version="{VERSION}"
expected_sha="{archive_sha}"
url="https://github.com/haiggoh/claude-code-transcript-distiller/releases/download/v{VERSION}/claude-code-transcript-distiller-{VERSION}.tar.gz"
tmp="$(mktemp -d "${{TMPDIR:-/tmp}}/cc-transcript.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
archive="$tmp/bundle.tar.gz"
if [[ -n "${{CC_TRANSSCRIPT_ARCHIVE:-}}" ]]; then
  cp "$CC_TRANSSCRIPT_ARCHIVE" "$archive"
  print "PASS: using local release archive."
elif curl -fL --retry 3 -o "$archive" "$url"; then
  print "PASS: downloaded Claude Code Transcript Distiller v$version."
else
  exit_status=$?
  print -u2 "FAIL: download exited with status $exit_status."
  exit "$exit_status"
fi
actual_sha="$(shasum -a 256 "$archive" | awk '{{print $1}}')"
[[ "$actual_sha" == "$expected_sha" ]] || {{ print -u2 "FAIL: archive checksum mismatch."; exit 1; }}
print "PASS: archive checksum verified."
tar -xzf "$archive" -C "$tmp"
source_dir="$tmp/claude-code-transcript-distiller-$version"
root="$HOME/.local/share/claude-code-transcript-distiller"
destination="$root/current"
staging="$root/.staging-$$"
backup="$root/.previous-$(date '+%Y%m%d-%H%M%S')"
mkdir -p "$root" "$HOME/.local/bin"
rm -rf "$staging"
cp -R "$source_dir" "$staging"
if [[ -e "$destination" ]]; then mv "$destination" "$backup"; print "PASS: previous installation backed up to $backup"; fi
mv "$staging" "$destination"
chmod 755 "$destination/cc_transcript.py"
ln -sfn "$destination/cc_transcript.py" "$HOME/.local/bin/cc-transcript"
python3 -m py_compile "$destination/cc_transcript.py"
rm -rf "$destination/__pycache__"
print "PASS: installed Claude Code Transcript Distiller v$version."
print "Command: $HOME/.local/bin/cc-transcript"
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
  print 'INFO: add this to ~/.zshrc: export PATH="$HOME/.local/bin:$PATH"'
fi
''', encoding="utf-8")
installer.chmod(0o755)
installer_sha = hashlib.sha256(installer.read_bytes()).hexdigest()
(DIST / "SHA256SUMS").write_text(
    f"{archive_sha}  {archive.name}\n{installer_sha}  {installer.name}\n",
    encoding="utf-8",
)
print(f"PASS: built {archive.name}")
print(f"PASS: built {installer.name}")
print("PASS: wrote SHA256SUMS")
print(f"ARCHIVE_SHA256={archive_sha}")

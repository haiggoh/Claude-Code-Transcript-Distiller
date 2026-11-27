#!/usr/bin/env bash
# .rtc/pre-package.sh — build this repo's release assets into dist/ (called by rtc-ship.sh).
#
# rtc-ship.sh runs this from the worktree root BEFORE resolving the asset globs in
# rtc-ship.json, so dist/ must hold exactly THIS release's assets when it exits:
# stale artifacts from older builds are wiped first, otherwise the glob
# dist/claude-code-transcript-distiller-*.tar.gz matches them too and the ship
# fails on a stale asset (or ships one).
#
# Usage: ./.rtc/pre-package.sh           (run by rtc-ship.sh from the repo root)
#        ./.rtc/pre-package.sh --help    show this text; never builds
# Env:   CCT_REPO_ROOT  override the repo root (tests point it at a temp copy);
#        set but invalid -> abort, never fall through to the cwd.
set -euo pipefail

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set -euo/{/^set -euo/!p;}' "$0" | sed 's/^# \{0,1\}//'
    exit 0 ;;
  "") ;;
  *) printf 'Usage: %s [--help]  (unknown argument: %s)\n' "$0" "$1" >&2; exit 2 ;;
esac

ROOT="${CCT_REPO_ROOT:-$PWD}"
if [ -n "${CCT_REPO_ROOT:-}" ]; then
  [ -d "$ROOT" ] || { echo "✖ pre-package: CCT_REPO_ROOT=$ROOT is not a directory" >&2; exit 1; }
  [ -f "$ROOT/scripts/build-release.py" ] || { echo "✖ pre-package: $ROOT/scripts/build-release.py does not exist" >&2; exit 1; }
fi
cd "$ROOT"

rm -f dist/claude-code-transcript-distiller-*.tar.gz \
      dist/claude-code-transcript-distiller-installer-*.zsh \
      dist/SHA256SUMS
python3 scripts/build-release.py

V=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' cc_transcript.py)
[ -n "$V" ] || { echo "✖ pre-package: could not read VERSION from cc_transcript.py" >&2; exit 1; }
need=(
  "dist/claude-code-transcript-distiller-$V.tar.gz"
  "dist/claude-code-transcript-distiller-installer-v$V.zsh"
  "dist/SHA256SUMS"
)
for f in "${need[@]}"; do
  [ -f "$f" ] || { echo "✖ pre-package: $f was not built (cc_transcript.py VERSION says $V)" >&2; exit 1; }
done
echo "packaged $V: ${#need[@]} assets in dist/"

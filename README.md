# Claude Code Session Bundle

Convert Claude Code session transcripts into smaller, portable artifacts that are easier to review or pass to another language model without spending context on repetitive metadata, accounting fields, duplicated tool output, and other structural noise.

The tool preserves a compact, line-addressable transcript as its evidence layer and generates complementary Markdown views for navigation and working context. It is useful when continuing Claude Code work in ChatGPT, Gemini, another assistant, a code-review workflow, or an archival system with limited context capacity.

> [!IMPORTANT]
> Compaction is semantic and structural, not byte-for-byte lossless. Keep the original Claude Code transcript when exact raw provenance matters.

## Features

- Produces four complementary artifacts from each Claude Code session.
- Removes selected structural noise and usage accounting.
- Hoists repeated metadata only when it remains invariant.
- Removes thinking signatures while retaining transcript content.
- Deduplicates byte-identical tool-result payloads.
- Optionally replaces embedded base64 images with length markers.
- Generates deterministic semantic working context.
- Supports interactive selection of one or several sessions.
- Accepts comma-separated lists and human-friendly ranges.
- Detects multiple open or likely active session transcripts on macOS.
- Safely amends an older bundle only when it is a verified prefix.
- Can write appended records as a numbered continuation instead.
- Migrates recognized legacy bundles without treating unrelated content as safe.
- Refuses unexplained replacement unless `--force` is explicit.
- Snapshots live transcripts before reading them.
- Uses staged writes, rollback protection, checksums, and post-write verification.
- Uses only the Python standard library. No package installation is required.

## Output files

For a session named `SESSION.jsonl`, the default output directory receives:

| Artifact | Purpose |
| --- | --- |
| `SESSION.compact.jsonl.txt` | Normalized, line-addressable evidence and primary compact source |
| `SESSION.compact_index.md` | Small navigation map for use alongside the compact JSONL |
| `SESSION.capsule.md` | Deterministic semantic working context organized by instructions, decisions, actions, tests, errors, and open work |
| `SESSION.indexed_capsule.md` | Portable hybrid combining a dense chronology with the capsule's semantic sections |

### Which artifacts should I share?

- **Most compact portable option:** start with the indexed capsule.
- **Strongest compact evidence:** share the compact JSONL, optionally with its small index.
- **Balanced review package:** share the compact JSONL and indexed capsule.
- **Exact forensic source:** retain and consult the original raw Claude Code JSONL.

The index and capsules point back to compact JSONL line numbers. They are aids, not substitutes for the evidence layer.

## Requirements

- Python 3.10 or newer is recommended.
- Claude Code session files under `~/.claude/projects/` for automatic discovery.
- macOS provides the strongest active-session detection through `lsof`; other platforms can still use metadata and environment-ID fallbacks.

No third-party Python dependencies are used.

## Installation

Clone the repository and place the script somewhere stable:

```bash
git clone https://github.com/haiggoh/claude-code-session-bundle.git
cd claude-code-session-bundle
mkdir -p "$HOME/.claude/scripts"
install -m 700 compact_session_bundle.py "$HOME/.claude/scripts/compact_session_bundle.py"
python3 -m py_compile "$HOME/.claude/scripts/compact_session_bundle.py"
```

Add an alias to `~/.zshrc`:

```zsh
alias cc-transcript="python3 $HOME/.claude/scripts/compact_session_bundle.py"
```

Reload the shell configuration:

```zsh
source "$HOME/.zshrc"
```

Check the installation:

```zsh
cc-transcript --version
```

## Quick start

Launch the interactive selector:

```zsh
cc-transcript
```

A typical selector marks transcripts that are confirmed open or likely active:

```text
Recent Claude Code sessions:

  [1] SESSION-A (...)  <-- current/open (open-file)
  [2] SESSION-B (...)  <-- current/open (open-file)
  [3] SESSION-C (...)

Select one or more sessions [default: 1,2, current candidates]:
```

Supported selections include:

```text
1
1, 2, 3
1 2 3
1,2,3
1-3
1 - 3
1 to 3
session 1-3
```

After processing a batch, the tool lists every artifact written and asks whether to export more sessions.

## Existing bundles

The script compares a newly generated compact transcript with the existing compact file before replacing anything.

| Relationship | Behavior |
| --- | --- |
| New | Writes the bundle without asking an existing-bundle question |
| Identical | Leaves the files untouched |
| Verified extension | Offers to amend the complete bundle or write a numbered continuation |
| Recognized legacy bundle | Offers a safe migration to the current format |
| Truncated or unexplained difference | Refuses replacement unless `--force` is supplied |

### Amend the complete bundle

Amendment regenerates the complete bundle from the current raw transcript. It is allowed automatically only when the old compact body is an exact prefix of the new body, or when a narrowly recognized legacy transformation explains the difference.

```zsh
cc-transcript --existing amend
```

### Write a continuation

Continuation mode writes only newly appended compact records:

```zsh
cc-transcript --existing continuation
```

It produces files such as:

```text
SESSION.part2.compact.jsonl.txt
SESSION.part2.compact_index.md
SESSION.part2.capsule.md
SESSION.part2.indexed_capsule.md
```

Continuation metadata records the prior checksum, prior line count, and first new global compact line.

### Refuse replacement

```zsh
cc-transcript --existing refuse
```

### Force replacement

```zsh
cc-transcript --force
```

`--force` bypasses the prefix safety rule. Use it only after independently confirming that the existing outputs may be replaced. It should not be the normal migration mechanism.

## Non-interactive usage

Export an explicit transcript:

```zsh
cc-transcript "$HOME/.claude/projects/PROJECT/SESSION.jsonl"
```

Attempt to resolve the current session non-interactively:

```zsh
cc-transcript --current
```

Choose another output directory:

```zsh
cc-transcript -o "$HOME/Documents/claude-session-bundles"
```

Set an output stem for a single explicit input:

```zsh
cc-transcript /path/to/session.jsonl --base-name project-handover
```

Allow malformed JSON lines to be preserved as marked records:

```zsh
cc-transcript /path/to/session.jsonl --permissive
```

Replace image base64 with length markers:

```zsh
cc-transcript /path/to/session.jsonl --truncate-base64
```

Disable post-write verification:

```zsh
cc-transcript /path/to/session.jsonl --no-verify
```

Disabling verification is not recommended for routine use.

## Active-session detection

The interactive picker can mark more than one transcript:

- `current/open (open-file)` — detected through macOS `lsof`;
- `current/open (environment-id)` — matched through a Claude session environment variable;
- `likely current (likely-recent-activity)` — a recent, substantial transcript identified through metadata;
- `likely current (likely-recent-size)` — a conservative fallback when stronger signals are absent.

Only open-file and matching environment-ID signals are direct indicators. Metadata labels are heuristics and may include a recently closed session or miss a live transcript whose writes are buffered.

Claude Code orchestration IDs do not always match transcript filenames. A `/clear` operation can also create a newer but tiny stub transcript. The fallback logic therefore considers recency and size rather than blindly selecting the newest filename.

## What compaction changes

The compact evidence file may:

- omit designated noise records;
- remove `userType`;
- remove message usage accounting;
- remove signatures from thinking blocks;
- hoist invariant session fields into the compact header;
- retain changing values in their original records;
- replace duplicated tool-result payloads with an explicit marker;
- truncate embedded base64 only when `--truncate-base64` is requested;
- preserve malformed lines as marked records only in permissive mode.

The compact header records source identity, source checksum, source line count, transformation counts, and hoisting conflicts.

## Security and privacy

Claude Code transcripts can contain:

- source code and proprietary text;
- prompts and model responses;
- file paths and project names;
- shell commands and their output;
- credentials, cookies, tokens, URLs, or environment values;
- personal or customer information.

The Markdown views apply best-effort pattern-based redaction to displayed excerpts. The compact JSONL is an evidence artifact and is **not generally redacted**. Regex-based redaction is not a security boundary.

Before uploading any artifact to an external service:

1. inspect it locally;
2. remove secrets and material you are not authorized to disclose;
3. follow your organization's data-handling policy;
4. confirm the destination model and account have the required privacy controls;
5. retain the original transcript only in an appropriately protected location.

Generated files should be treated as sensitive by default.

## Reliability notes

### Live transcripts

The tool takes a stable temporary snapshot before compaction. It retries if file size or modification time changes during copying. A live harness may still buffer records before writing them to disk, so a successful snapshot proves consistency of the visible file—not that every in-memory event has already been flushed.

For the most complete transcript, close Claude Code gracefully when practical before creating a final archival bundle.

### Transactional output

All artifacts are staged before commit. Existing outputs are moved to rollback files during replacement and restored if commit fails. Verification checks JSONL parsing, line counts, checksums, nonempty Markdown outputs, and compact-line references.

### Determinism

The generated capsule avoids embedding the wall-clock generation time. Given the same compact records and options, semantic output is intended to be reproducible.

## Command reference

```text
compact_session_bundle.py [INPUT]
  --current
  -o, --output-dir DIRECTORY
  --base-name NAME
  --existing {amend,continuation,refuse}
  --force
  --permissive
  --truncate-base64
  --preview-chars NUMBER
  --no-snapshot
  --verify / --no-verify
  --version
```

Run the built-in help for the authoritative current options:

```zsh
cc-transcript --help
```

## Suggested external-LLM workflow

1. Generate the bundle.
2. Inspect the indexed capsule for a compact overview and chronology.
3. Attach the compact JSONL when the receiving model needs evidence beyond capsule excerpts.
4. Tell the receiving model that compact JSONL line numbers are authoritative.
5. Ask it to distinguish direct transcript evidence from capsule heuristics.
6. Return to the original raw transcript if exact omitted metadata or byte-level provenance is required.

Example instruction:

```text
Use the indexed capsule as working context and the compact JSONL as the evidence source.
Cite compact JSONL line numbers for consequential claims. Treat detected decisions,
open work, tests, and errors as rule-based candidates until the referenced evidence
confirms them.
```

## Development

Run a syntax check:

```zsh
python3 -m py_compile compact_session_bundle.py
```

Useful regression cases include:

- a new transcript with no bundle;
- an identical rerun;
- a strict extension amended in place;
- a strict extension written as a continuation;
- an older-format bundle migrated safely;
- a divergent bundle refused;
- changing `cwd` or `gitBranch` values;
- several tool calls or results in one record;
- malformed input in strict and permissive modes;
- Markdown control characters and secret-like excerpts;
- several selected sessions with one per-session failure;
- multiple open Claude Code transcripts.

## Limitations

- Claude Code's transcript schema can change without notice.
- Classification and capsule sections use deterministic heuristics, not semantic guarantees.
- Active-session metadata fallbacks are approximate.
- Buffered transcript writes may not yet exist on disk.
- Compact output is not a complete substitute for the original raw transcript.
- Best-effort redaction cannot guarantee removal of every secret.

## License

This project is intended to be released under the MIT License. See [`LICENSE`](LICENSE).

## Project status

This is an independent utility and is not affiliated with or endorsed by Anthropic. Claude and Claude Code are trademarks of their respective owner.

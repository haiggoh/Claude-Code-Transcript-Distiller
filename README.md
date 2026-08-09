# Claude Code Session Bundle

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Standard library](https://img.shields.io/badge/Python%20packages-standard%20library%20only-brightgreen.svg)](#requirements)

**Compact and index Claude Code JSONL transcripts for review, archival, and context handoff to other LLMs.**

Claude Code Session Bundle is a Python CLI for exporting Claude Code session transcripts as smaller, portable, line-addressable artifacts. It is intended for carrying established context into ChatGPT, Gemini, another LLM, a code-review workflow, or an archival system without including as much repetitive metadata, usage accounting, duplicated tool output, and other structural noise.

Unlike a plain generated summary, the bundle retains a compact transcript as its evidence layer and generates complementary navigation and semantic views.

> [!IMPORTANT]
> Compaction is semantic and structural, not byte-for-byte lossless. The original Claude Code JSONL remains the authoritative raw source and should be retained whenever exact provenance matters.

## Contents

- [Quick start](#quick-start)
- [Use cases](#use-cases)
- [Output files](#output-files)
- [How this differs from Claude Code `/compact`](#how-this-differs-from-claude-code-compact)
- [Features](#features)
- [Requirements and platform support](#requirements-and-platform-support)
- [Installation](#installation)
- [Interactive selection](#interactive-selection)
- [Existing bundles](#existing-bundles)
- [Non-interactive usage](#non-interactive-usage)
- [Active-session detection](#active-session-detection)
- [What compaction changes](#what-compaction-changes)
- [Security and privacy](#security-and-privacy)
- [Reliability notes](#reliability-notes)
- [Command reference](#command-reference)
- [External-LLM workflow](#external-llm-workflow)
- [FAQ](#faq)
- [Development and compatibility](#development-and-compatibility)
- [Limitations](#limitations)
- [License](#license)

## Quick start

Run the interactive selector:

```zsh
python3 compact_session_bundle.py
```

Select one or more sessions using a number, list, or range:

```text
Recent Claude Code sessions:

  [1] SESSION-A (...)  <-- current/open (open-file)
  [2] SESSION-B (...)  <-- current/open (open-file)
  [3] SESSION-C (...)

Select one or more sessions [default: 1,2, current candidates]: 1-3
```

Each selected session can produce:

```text
SESSION.compact.jsonl.txt
SESSION.compact_index.md
SESSION.capsule.md
SESSION.indexed_capsule.md
```

Start with the indexed capsule when you want a small standalone handoff. Add the compact JSONL when the receiving model needs more complete, line-addressable evidence.

## Use cases

- Transfer established context from Claude Code to ChatGPT, Gemini, or another LLM.
- Create a context handoff without relying only on an AI-generated summary.
- Export and inspect Claude Code JSONL session history.
- Continue a coding task in another assistant or interface.
- Archive transcripts in a smaller, navigable format.
- Review tool calls, test results, errors, decisions, and unfinished work.
- Preserve compact line references while reducing unnecessary context-window usage.
- Export several active or recent Claude Code sessions in one run.
- Update a bundle when its source transcript later grows.

No specific token reduction is guaranteed. The amount saved depends on the source transcript, enabled options, and artifacts supplied to the receiving model.

## Output files

For a source named `SESSION.jsonl`, the default output directory receives four complementary artifacts.

| Artifact | Purpose |
| --- | --- |
| `SESSION.compact.jsonl.txt` | Normalized, line-addressable compact evidence |
| `SESSION.compact_index.md` | Small navigation map designed to accompany the compact JSONL |
| `SESSION.capsule.md` | Rule-based semantic working context organized by instructions, decisions, actions, tests, errors, and open work |
| `SESSION.indexed_capsule.md` | Portable hybrid combining a dense chronology with the capsule's semantic sections |

### Which output should I use?

| Goal | Recommended artifact |
| --- | --- |
| Small standalone context handoff | Indexed capsule |
| Evidence-oriented LLM transfer | Compact JSONL + indexed capsule |
| Fast navigation through compact evidence | Compact JSONL + compact index |
| Human-readable semantic review | Capsule or indexed capsule |
| Exact raw provenance | Original Claude Code JSONL |

Within the generated bundle, the compact JSONL is the primary line-addressable evidence layer. It is still transformed and intentionally omits some raw data. The original Claude Code transcript remains the authoritative raw source.

## How this differs from Claude Code `/compact`

Claude Code's `/compact` command summarizes context inside an active Claude Code session.

This tool instead processes the session's on-disk JSONL transcript and creates portable artifacts for:

- cross-tool context transfer;
- external LLM handoff;
- transcript review and navigation;
- evidence-oriented compaction;
- repeatable session export;
- archival.

It does not replace Claude Code's internal context management, resume sessions, or inject generated bundles back into Claude Code automatically.

## Features

- Produces four complementary artifacts from each selected session.
- Removes designated structural noise and usage accounting.
- Hoists repeated metadata only when it remains invariant.
- Retains changing metadata values in their original records.
- Removes thinking signatures while retaining transcript content.
- Deduplicates byte-identical tool-result payloads.
- Optionally replaces embedded base64 images with length markers.
- Generates rule-based semantic working context.
- Supports interactive selection of one or several sessions.
- Displays human-readable session labels while retaining the complete UUID.
- Accepts comma-separated lists and human-friendly ranges.
- Detects multiple writing, identity-matched, or recently active session transcripts on macOS.
- Leaves identical bundles untouched.
- Safely amends an older bundle when it is a verified prefix.
- Can write appended records as a numbered continuation.
- Migrates narrowly recognized legacy bundles.
- Refuses unexplained replacement unless `--force` is explicit.
- Snapshots live transcripts before reading them.
- Uses staged writes and commit-time rollback protection.
- Performs checksum and compact-line-reference verification.
- Uses only the Python standard library.

## Requirements and platform support

- Python 3.10 or newer.
- Claude Code session files under `~/.claude/projects/` for automatic discovery.
- No third-party Python packages.

The project is currently developed and tested on macOS. Most compaction logic uses portable Python standard-library APIs and may also work on Linux or Windows, but that has not yet been established through a published cross-platform test suite.

On macOS, the strongest active-session detection optionally invokes the system `lsof` command.

## Install from a release asset

Download the checksummed standalone installer from the latest release:

```zsh
curl -fLO https://github.com/haiggoh/claude-code-session-bundle/releases/download/v0.5.0/claude-code-session-bundle-installer-v0.5.0.zsh
zsh -n claude-code-session-bundle-installer-v0.5.0.zsh
zsh claude-code-session-bundle-installer-v0.5.0.zsh
```

It verifies the release archive, installs source under `~/.local/share/claude-code-session-bundle/current`, and creates `~/.local/bin/cc-transcript`. Release assets also include a reproducible source archive and `SHA256SUMS`.

## Installation

Choose an installation layout based on whether you intend to use, maintain, or fork the project.

### Standard user installation

This keeps application source under `~/.local/share` and exposes a short command through `~/.local/bin`.

```zsh
mkdir -p "$HOME/.local/share" "$HOME/.local/bin"

git clone \
  https://github.com/haiggoh/claude-code-session-bundle.git \
  "$HOME/.local/share/claude-code-session-bundle"

chmod 755 \
  "$HOME/.local/share/claude-code-session-bundle/compact_session_bundle.py"

ln -s \
  "$HOME/.local/share/claude-code-session-bundle/compact_session_bundle.py" \
  "$HOME/.local/bin/cc-transcript"
```

If `~/.local/bin` is not already on `PATH`, add this to `~/.zshrc`:

```zsh
export PATH="$HOME/.local/bin:$PATH"
```

Reload the shell and verify:

```zsh
source "$HOME/.zshrc"
cc-transcript --version
```

Update later with:

```zsh
git -C "$HOME/.local/share/claude-code-session-bundle" pull --ff-only
```

### Development or fork checkout

If you want Claude Code to maintain the repository itself, a dedicated workspace such as `~/ClaudeWorkspace` is appropriate:

```zsh
mkdir -p "$HOME/ClaudeWorkspace" "$HOME/.local/bin"

git clone \
  https://github.com/haiggoh/claude-code-session-bundle.git \
  "$HOME/ClaudeWorkspace/claude-code-session-bundle"

chmod 755 \
  "$HOME/ClaudeWorkspace/claude-code-session-bundle/compact_session_bundle.py"

ln -s \
  "$HOME/ClaudeWorkspace/claude-code-session-bundle/compact_session_bundle.py" \
  "$HOME/.local/bin/cc-transcript"
```

This keeps the executable linked directly to the working tree. Changes made and tested in that checkout become the version invoked by `cc-transcript`.

### Claude-local standalone copy

If you prefer to keep Claude-related utilities under `~/.claude`:

```zsh
mkdir -p "$HOME/.claude/scripts"

install -m 700 \
  compact_session_bundle.py \
  "$HOME/.claude/scripts/compact_session_bundle.py"
```

Add an alias to `~/.zshrc`:

```zsh
alias cc-transcript='python3 "$HOME/.claude/scripts/compact_session_bundle.py"'
```

Then reload:

```zsh
source "$HOME/.zshrc"
```

This layout creates a standalone copy. Updating the cloned repository will not automatically update the installed script.

### Run without installation

From a cloned repository:

```zsh
python3 compact_session_bundle.py
```

## Interactive selection

Launch the selector:

```zsh
cc-transcript
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
sessions 3 to 1
```

Ranges include both endpoints. Duplicate selections are removed while preserving order.

After processing a batch, the tool:

1. reports each selected session's result;
2. lists artifacts written;
3. identifies sessions that were already current or skipped;
4. asks whether to export more sessions.

### Session labels

Picker labels follow Claude Code's display precedence: the latest custom title, latest AI-generated title, latest summary, first meaningful non-meta user prompt, then a project fallback. Labels are read defensively from the internal JSONL format, redacted, normalized to one line, bounded in length, and cached for the current invocation. The complete session UUID remains visible beneath every title. Sessions without usable title or prompt text are shown as `Untitled session`.

Activity markers state the observed signal: `writing now`, `session ID match`, `recently active`, or `recent fallback`. These signals are merged, so one transcript being open for writing does not hide other idle but recently active sessions.


## Existing bundles

Before replacing an existing compact transcript, the script compares it with the newly generated compact records.

| Relationship | Behavior |
| --- | --- |
| New | Writes the bundle without asking an existing-bundle question |
| Identical | Leaves existing files untouched |
| Verified extension | Offers to amend the complete bundle or write a continuation |
| Recognized legacy bundle | Offers migration to the current format |
| Truncated or unexplained difference | Refuses replacement unless `--force` is supplied |

The existing-bundle prompt appears only after a bundle has actually been detected and classified.

### Amend the complete bundle

```zsh
cc-transcript --existing amend
```

Amendment regenerates the complete bundle from the current raw transcript. It is approved automatically only when:

- the old compact body is an exact prefix of the new body; or
- a narrowly recognized legacy transformation explains the difference.

### Write a continuation

```zsh
cc-transcript --existing continuation
```

Continuation mode creates files such as:

```text
SESSION.part2.compact.jsonl.txt
SESSION.part2.compact_index.md
SESSION.part2.capsule.md
SESSION.part2.indexed_capsule.md
```

Continuation metadata records:

- the part number;
- the previous canonical compact checksum;
- the previous canonical compact line count;
- the first new global compact line.

> [!CAUTION]
> Continuations are currently calculated relative to the canonical complete bundle, not cumulatively against earlier continuation files. If the canonical bundle is not amended between continuation exports, a later part may overlap records already present in an earlier part.
>
> To maintain non-overlapping continuation boundaries with the current release, amend the canonical bundle after incorporating each continuation, or inspect the recorded boundaries before combining parts.

A legacy bundle must first be migrated before a continuation can be generated.

### Refuse replacement

```zsh
cc-transcript --existing refuse
```

### Force replacement

```zsh
cc-transcript --force
```

`--force` bypasses the prefix safety rule. Use it only after independently confirming that the existing output may be replaced.

The parser accepts `--overwrite` for compatibility, but in version 0.4.3 it does not authorize or alter replacement behavior. Use `--existing` or `--force` according to the intended operation.

## Non-interactive usage

### Export an explicit transcript

```zsh
cc-transcript \
  "$HOME/.claude/projects/PROJECT/SESSION.jsonl"
```

### Resolve the current session

```zsh
cc-transcript --current
```

`--current` attempts an environment-ID match and then uses a bounded recency-and-size fallback within the current project.

### Choose another output directory

```zsh
cc-transcript \
  -o "$HOME/Documents/claude-session-bundles"
```

### Set an output name

`--base-name` is supported for one explicit input:

```zsh
cc-transcript \
  /path/to/session.jsonl \
  --base-name project-handover
```

It cannot be used with several interactively selected sessions.

### Preserve malformed input

Strict mode rejects malformed JSON and invalid UTF-8. Permissive mode preserves malformed input as marked records:

```zsh
cc-transcript \
  /path/to/session.jsonl \
  --permissive
```

### Truncate embedded image data

```zsh
cc-transcript \
  /path/to/session.jsonl \
  --truncate-base64
```

This intentionally replaces image base64 with length markers and makes the output more lossy.

### Disable the source snapshot

```zsh
cc-transcript \
  /path/to/session.jsonl \
  --no-snapshot
```

Reading a live transcript directly is less robust than using the default stable snapshot.

### Disable post-write verification

```zsh
cc-transcript \
  /path/to/session.jsonl \
  --no-verify
```

Disabling verification is not recommended for routine use.

## Active-session detection

The interactive picker can mark more than one transcript.

| Marker | Meaning |
| --- | --- |
| `current/open (open-file)` | File detected as open through macOS `lsof` |
| `current/open (environment-id)` | Filename matched a Claude session environment value |
| `likely current (likely-recent-activity)` | Recent, substantial transcript identified through metadata |
| `likely current (likely-recent-size)` | Conservative metadata fallback |

`open-file` is the strongest current-session signal.

An environment-ID match is useful but not conclusive. Some Claude Code harness variants expose an orchestration or task ID rather than the transcript's own ID, and matching is based on filename containment.

Metadata-based labels are heuristics. They may:

- include a recently closed session;
- miss a live transcript whose writes are buffered;
- misidentify an unusually small active transcript;
- lag behind process state.

A `/clear` operation can create a newer but tiny stub transcript. Fallback logic therefore considers recency and size instead of blindly selecting the newest file.

## What compaction changes

The compact evidence file may:

- omit designated noise records;
- remove `userType`;
- remove message usage accounting;
- remove signatures from thinking blocks;
- hoist invariant session fields into the compact header;
- retain changing values in their original records;
- replace duplicated tool-result payloads with explicit markers;
- truncate embedded base64 only when requested;
- preserve malformed lines as marked records only in permissive mode.

The compact header records:

- source identity;
- source checksum;
- source physical line count;
- generator and bundle format;
- transformation counts;
- hoisting conflicts;
- snapshot metadata.

The source JSONL is never intentionally written to. By default, the tool copies its visible bytes to a temporary snapshot and generates artifacts in a separate output directory.

## Security and privacy

Claude Code transcripts can contain:

- source code and proprietary text;
- prompts and model responses;
- file paths and project names;
- shell commands and output;
- credentials, cookies, tokens, URLs, or environment values;
- personal or customer information.

Markdown views apply best-effort pattern-based redaction to displayed excerpts. The compact JSONL is **not generally redacted**.

Regex-based redaction is not a security boundary.

Before uploading any artifact to an external service:

1. inspect it locally;
2. remove secrets and material you are not authorized to disclose;
3. follow your organization's data-handling policy;
4. confirm the destination account has the required privacy controls;
5. retain raw transcripts only in appropriately protected storage.

Treat generated files as sensitive by default.

## Reliability notes

### Live transcripts

The tool takes a stable temporary snapshot before compaction and retries if file size or modification time changes during copying.

A live Claude Code process may still buffer records before writing them to disk. A successful snapshot proves consistency of the visible file, not that every in-memory event has been flushed.

For a final archive, close Claude Code gracefully when practical before generating the bundle.

### Transactional writes

Artifacts are staged before commit.

During an approved replacement:

1. existing outputs are moved to temporary rollback files;
2. staged outputs are committed;
3. rollback files are removed after the commit succeeds;
4. previous outputs are restored if the commit operation itself fails.

Post-write verification currently runs after commit. If verification fails, the command reports an error but does not automatically restore the previous bundle.

### Verification

Default verification checks:

- compact JSONL parsing;
- expected compact line count;
- compact SHA-256;
- nonempty Markdown outputs;
- checksum references in capsule artifacts;
- compact-line references within the valid range.

### Reproducibility

For identical input bytes, script version, and options, capsule generation is intended to be reproducible. The capsule does not embed the wall-clock generation time.

### Legacy migration

A legacy bundle is migrated only when:

- it has a recognized older format;
- session identity or source identity agrees; and
- the difference is explained by the documented legacy field-hoisting behavior.

Unexplained differences remain blocked.

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

The parser also accepts the compatibility flag `--overwrite`, but version 0.4.3 does not use it to authorize replacement.

Run the built-in help for the current parser options:

```zsh
cc-transcript --help
```

## External-LLM workflow

1. Generate the bundle.
2. Inspect the indexed capsule for a compact overview and chronology.
3. Attach the compact JSONL when the receiving model needs evidence beyond capsule excerpts.
4. Tell the receiving model that compact JSONL line numbers identify the compact evidence records.
5. Ask it to distinguish direct transcript evidence from rule-based capsule candidates.
6. Return to the original raw transcript when omitted metadata or exact provenance is required.

Example instruction:

```text
Use the indexed capsule as working context and the compact JSONL as the primary
compact evidence source. Cite compact JSONL line numbers for consequential claims.
Treat detected decisions, open work, tests, and errors as rule-based candidates
until the referenced evidence confirms them. The original raw JSONL remains the
authoritative source if the compact artifacts are insufficient.
```

## FAQ

### Can this transfer a Claude Code session to ChatGPT or Gemini?

Yes. It creates local text artifacts suitable for manual upload or transfer. It does not upload them automatically.

### Does it modify the original Claude Code transcript?

No. The source transcript is read or copied into a temporary snapshot. Generated artifacts are written separately.

### Is this a Claude Code plugin?

No. The current release is a standalone Python CLI related to Claude Code. It is not packaged as a Claude Code plugin.

### Is the output lossless?

No. The process intentionally removes selected structural noise, accounting fields, signatures, and duplicated payloads.

### Is the compact transcript fully redacted?

No. Inspect all artifacts before sharing them.

### Can several sessions be exported at once?

Yes. Interactive selection accepts lists and ranges such as:

```text
1,2,3
1 2 3
1-3
1 to 3
```

### Can an existing bundle be updated later?

Yes. A verified extension can be amended into the complete bundle or exported as a numbered continuation. See the continuation overlap warning before relying on several continuation parts.

### Why was replacement refused?

The existing compact transcript was not identical, a verified prefix, or a narrowly recognized legacy representation of the selected source.

## Development and compatibility

Run a syntax check:

```zsh
python3 -m py_compile compact_session_bundle.py
```

Check the version:

```zsh
python3 compact_session_bundle.py --version
```

The current release includes a standard-library regression suite and has also been validated against real Claude Code transcripts.

Useful regression cases include:

- new, identical, extended, truncated, and divergent transcripts;
- safe legacy migration;
- amendment and continuation behavior;
- changing `cwd` or `gitBranch` values;
- multiple tool calls and results in one record;
- malformed JSON and invalid UTF-8;
- Markdown control characters and secret-like excerpts;
- multiple selected sessions with a per-session failure;
- multiple open transcripts;
- `/clear` stub transcripts;
- failures during staged output commit;
- reproducibility from identical input.

### Reporting compatibility problems

Claude Code's transcript format is not a stable public interchange format. If a Claude Code update introduces unrecognized records or incorrect output, open an issue with:

- the Claude Code version;
- the operating system and Python version;
- the command used;
- a minimized, redacted sample;
- the expected and actual behavior.

Do not attach raw transcripts containing secrets, proprietary source code, customer data, or personal information.

## Limitations

- Claude Code's transcript schema can change without notice.
- Capsule classification uses deterministic heuristics, not semantic guarantees.
- Active-session fallbacks are approximate.
- Buffered transcript events may not yet exist on disk.
- Compact output is not a complete substitute for the raw transcript.
- Best-effort redaction cannot guarantee removal of every secret.
- Shell mutation detection cannot understand every possible command.
- Later continuation parts may overlap earlier ones unless the canonical bundle is amended between exports.
- Post-write verification failure does not currently trigger automatic rollback.
- Cross-platform behavior has not yet been covered by a published automated test suite.
- The tool does not upload, synchronize, or resume sessions automatically.

## License

Licensed under the [MIT License](LICENSE).

Copyright © 2026 Heiko Brantsch.

## Project status

This is an independent utility and is not affiliated with or endorsed by Anthropic.

Claude and Claude Code are trademarks of their respective owner.
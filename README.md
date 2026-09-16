# Claude Code Transcript Distiller

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Standard library](https://img.shields.io/badge/Python%20packages-standard%20library%20only-brightgreen.svg)](#requirements)

**Compact evidence and indexed handoff for native Claude Code transcripts.**

Claude Code Transcript Distiller is a Python CLI for distilling native Claude Code session transcripts into compact, portable, line-addressable artifacts. It is intended for carrying established context into ChatGPT, Gemini, another LLM, a code-review workflow, or an archival system without including as much repetitive metadata, usage accounting, duplicated tool output, and other structural noise.

Unlike a plain generated summary, the distiller retains a compact transcript as its evidence layer and generates an indexed capsule as the primary standalone handoff.

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
- [Alternative installation layouts](#alternative-installation-layouts)
- [Development and compatibility](#development-and-compatibility)
- [Limitations](#limitations)
- [Current development](#current-development)
- [License](#license)

## Quick start

Run the interactive selector:

```zsh
python3 cc_transcript.py
```

Select one or more sessions using a number, list, or range:

```text
Recent Claude Code sessions:

Sizes: transcript [compact JSONL | indexed capsule]

  [1] Review authentication refactor  <-- recently active
      ID: SESSION-A · 742 KB [410 KB | 38 KB] · 2026-08-09 14:32

  [2] Update deployment checks  <-- recently active
      ID: SESSION-B · 1.2 MB [680 KB | 52 KB] · 2026-08-09 13:42 · project: example-project

  [3] Untitled session
      ID: SESSION-C · 32 KB · 2026-08-08 00:45

Select one or more sessions [default: 1,2, active candidates]: 1-3
```

Each selected session produces exactly:

```text
SESSION.compact.jsonl.txt
SESSION.indexed_capsule.md
```

Start with the indexed capsule as the normal handoff. Read or upload the compact JSONL only when consequential detail requires stronger line-addressable evidence.

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
For a source named `SESSION.jsonl`, the default output directory receives two artifacts.

| Artifact | Purpose |
| --- | --- |
| `SESSION.compact.jsonl.txt` | Format-3, line-addressable transformed evidence with sparse header navigation |
| `SESSION.indexed_capsule.md` | Primary standalone chronology and semantic handoff with stable evidence IDs |

The former `.compact_index.md` and `.capsule.md` sidecars are retired. During verified migration they are removed only after the new canonical pair passes committed verification; use `--keep-legacy-artifacts` to retain them.

### Which output should I use?

| Goal | Recommended artifact |
| --- | --- |
| Normal cross-LLM context handoff | Indexed capsule |
| Consequential evidence lookup | Indexed capsule, then referenced compact JSONL lines |
| Archival transformed evidence | Compact JSONL + indexed capsule |
| Exact raw provenance | Original Claude Code JSONL |

The compact JSONL remains transformed evidence. The original Claude Code transcript remains the authoritative raw source. See [`docs/compact-format-3.md`](docs/compact-format-3.md) for the durable format rules and migration invariants.

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
- Produces exactly two canonical artifacts per session.
- Uses compact bundle format 3 with sparse, preview-free header navigation.
- Omits recognized base64 by default using deterministic binary metadata; `--keep-base64` preserves it exactly.
- Resolves the final session title once and removes repeated `ai-title` and `last-prompt` metadata.
- Preserves complete user text, visible assistant text, tool inputs, and unique textual tool results by default.
- Replaces later exact allowlisted large result payloads with verified backward references.
- Preserves thinking by default; `--omit-thinking` records deterministic size/hash metadata.
- Preserves `file-history-delta` records unchanged and accounts for their records and bytes.
- Collapses only unchanged recognized mode/permission repeats while preserving initial state and every transition.
- Builds an indexed capsule with one detailed evidence object per compact line and deterministic multi-role evidence IDs.
- Supports interactive multi-session selection and human-readable session labels.
- Leaves identical current-format bundles untouched and blocks unexplained replacement unless `--force` is explicit.
- Migrates verified format-2 bundles transactionally and can retain retired sidecars with `--keep-legacy-artifacts`.
- Keeps canonical-relative continuation boundaries and the existing overlap warning.
- Snapshots live transcripts, verifies staged and committed output, and restores prior artifacts on failure.
- Uses only the Python standard library.

## Requirements and platform support

- Python 3.10 or newer.
- Claude Code session files under `~/.claude/projects/` for automatic discovery.
- No third-party Python packages.

The project is currently developed and tested on macOS. Most compaction logic uses portable Python standard-library APIs and may also work on Linux or Windows, but that has not yet been established through a published cross-platform test suite.

On macOS, the strongest active-session detection optionally invokes the system `lsof` command.

## Installation

### Homebrew — recommended

```zsh
brew install haiggoh/tap/claude-code-transcript-distiller
cc-transcript --version
```

### Standalone release installer

```zsh
curl -fLO https://github.com/haiggoh/Claude-Code-Transcript-Distiller/releases/download/v0.8.1/claude-code-transcript-distiller-installer-v0.8.1.zsh
zsh -n claude-code-transcript-distiller-installer-v0.8.1.zsh
zsh claude-code-transcript-distiller-installer-v0.8.1.zsh
```

The installer verifies the release archive, installs source under `~/.local/share/claude-code-transcript-distiller/current`, and creates `~/.local/bin/cc-transcript`. If `~/.local/bin` is not on `PATH`, it prints the line to add to `~/.zshrc`.

Release assets also include a reproducible source archive and `SHA256SUMS`.

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

Ranges include both endpoints. Duplicate selections are removed while preserving order. Session numbers refer to the complete stable list, so an existing session can be selected before its row is displayed. Use `m`, repeated `m`, or `m5` to reveal more rows. Navigation escape sequences are ignored.

After processing a batch, the tool:

1. reports each selected session's result;
2. lists artifacts written;
3. identifies sessions that were already current or skipped;
4. asks whether to export more sessions.

### Session labels

Picker labels follow Claude Code's display precedence: the latest custom title, latest AI-generated title, latest summary, first meaningful non-meta user prompt, then a project fallback. Labels are read defensively from the internal JSONL format, redacted, normalized to one line, bounded in length, and cached for the current invocation. The complete session UUID remains visible beneath every title. Sessions without usable title or prompt text are shown as `Untitled session`.

The picker displays sizes as `transcript [compact JSONL | indexed capsule]`. Brackets appear only when at least one canonical artifact exists; `—` marks one missing artifact in a partial pair. Continuation files are not included. Bold traffic-light status marks current, source-grown, partial, unreadable, shrunk, or changed bundles; a yellow `new tail to verify` marker is prioritization, not replacement authorization. The project field is omitted for home-directory sessions; other sessions show only the CWD basename.

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

Continuation mode defaults to the verified new tail. The first continuation is `part2`; later continuations use the next `partN`:

```text
SESSION.partN.compact.jsonl.txt
SESSION.partN.indexed_capsule.md
```

`--existing amend` rewrites the canonical pair from the full raw transcript; it does not rewrite or delete numbered parts.

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

The parser accepts `--overwrite` for compatibility, but it does not authorize or alter replacement behavior. Use `--existing` or `--force` according to the intended operation.

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

### Base64 and thinking policy

Recognized base64 is omitted by default and replaced with structured metadata containing available media details, encoded length, decoded length when valid, and a deterministic SHA-256. To preserve the exact encoded text deliberately:

```zsh
cc-transcript \
  /path/to/session.jsonl \
  --keep-base64
```

The former `--truncate-base64` flag remains accepted as a deprecated compatibility no-op confirming the default omission policy. It conflicts with `--keep-base64`.

Thinking remains preserved by default. Explicit omission is available with:

```zsh
cc-transcript /path/to/session.jsonl --omit-thinking
```

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
| `writing now` | Transcript file is open at the instant of detection |
| `session ID match` | Filename matches an available Claude session environment value |
| `recently active` | Substantial transcript modified within the recent-activity window |
| `recent fallback` | Last-resort recency-and-size selection when stronger signals are absent |

`writing now` is the strongest instantaneous signal, but idle Claude sessions may close their transcript between writes.

An environment-ID match is useful but not conclusive. Some Claude Code harness variants expose an orchestration or task ID rather than the transcript's own ID, and matching is based on filename containment.

Recent-activity markers are heuristics. They may:

- include a recently closed session;
- miss a live transcript whose writes are buffered;
- misidentify an unusually small active transcript;
- lag behind process state.

A `/clear` operation can create a newer but tiny stub transcript. Fallback logic therefore considers recency and size instead of blindly selecting the newest file.

## What compaction changes
The format-3 compact evidence file may:

- omit designated structural-noise records and usage accounting;
- remove `userType` and thinking signatures;
- preserve thinking text unless `--omit-thinking` is explicit;
- hoist only invariant metadata and retain changing values chronologically;
- omit recognized base64 by default using structured metadata;
- preserve exact base64 with `--keep-base64`;
- keep one resolved session title in the header while removing repeated `ai-title` and `last-prompt` records;
- collapse unchanged recognized mode and permission announcements;
- replace later byte-identical allowlisted result payloads of at least 1,000 characters with typed backward references;
- preserve malformed input as marked records only in permissive mode;
- preserve `file-history-delta` records unchanged.

It never semantically deduplicates text and does not truncate unique user, visible assistant, tool-input, or textual tool-result evidence by default.

The compact header records source identity/checksum, format and generator, snapshot metadata, omission policy, transformation accounting, conflicts, and sparse navigation line numbers.

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
Artifacts are staged before any existing generated output is moved. The staged pair is verified, prior artifacts are moved to rollback paths, the new pair is committed and verified again, and rollback copies are removed only after success. Any staging, commit, or committed-verification failure restores the complete prior generated state.

During verified migration, only the same bundle's `.compact_index.md` and `.capsule.md` sidecars are eligible for retirement. Unrelated files are never deleted. Retirement always forces staged and committed verification even when `--no-verify` was requested.

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
cc_transcript.py [INPUT]

  --current
  -o, --output-dir DIRECTORY
  --base-name NAME
  --existing {amend,continuation,refuse}
  --force
  --permissive
  --keep-base64
  --truncate-base64        deprecated compatibility flag
  --omit-thinking
  --keep-legacy-artifacts
  --preview-chars NUMBER
  --no-snapshot
  --verify / --no-verify
  --version
```

`--overwrite` remains accepted for compatibility but never bypasses identity checks. Use `--force` only for an independently verified unrelated replacement. Run `cc-transcript --help` for parser details.

## External-LLM workflow
1. Generate the canonical pair.
2. Use the indexed capsule as the primary working context.
3. Establish chronology, then reconcile its objectives, instructions, corrections, decisions, actions, tests, failures, and open work.
4. Follow stable evidence IDs instead of treating repeated references as separate evidence.
5. Inspect only referenced compact JSONL lines when the capsule lacks consequential detail.
6. Return to the original raw transcript when transformed evidence is insufficient.

Example instruction:

```text
Treat the indexed capsule as primary working context. Escalate to referenced
compact JSONL lines only when needed for a consequential claim. Prefer direct
tool evidence and later evidence-backed findings, and state uncertainty rather
than inventing detail. The original raw JSONL remains authoritative.
```

## FAQ

### Can this transfer a Claude Code session to ChatGPT or Gemini?

Yes. It creates local text artifacts suitable for manual upload or transfer. It does not upload them automatically.

### Does it modify the original Claude Code transcript?

No. The source transcript is read or copied into a temporary snapshot. Generated artifacts are written separately.

### Is this a Claude Code plugin?

No. The core remains a standalone Python CLI. An optional Claude Code companion skill is planned as a separate integration and is not included in v0.8.1.

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

## Alternative installation layouts

<details>
<summary>Development checkout, Claude-local copy, and direct execution</summary>

### Development or fork checkout

Use this when you want Claude Code or another development tool to maintain the repository itself:

```zsh
mkdir -p "$HOME/ClaudeWorkspace"
git clone https://github.com/haiggoh/Claude-Code-Transcript-Distiller.git "$HOME/ClaudeWorkspace/claude-code-transcript-distiller"
python3 "$HOME/ClaudeWorkspace/claude-code-transcript-distiller/cc_transcript.py"
```

### Claude-local standalone copy

```zsh
mkdir -p "$HOME/.claude/scripts"
install -m 700 cc_transcript.py "$HOME/.claude/scripts/cc_transcript.py"
```

### Run directly from any clone

```zsh
python3 cc_transcript.py
```

These layouts are useful for development or custom setups. Homebrew or the standalone release installer is preferred for routine use.

</details>

## Development and compatibility

Run a syntax check:

```zsh
python3 -m py_compile cc_transcript.py
```

Check the version:

```zsh
python3 cc_transcript.py --version
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
- Cross-platform behavior has not yet been covered by a published automated test suite.
- The tool does not upload, synchronize, or resume sessions automatically.

## Current development

Version 0.8.1 fixes an ordering defect between cross-record payload interning and same-record result-mirror deduplication. Compact bundle format 3 is unchanged. The next approved workstream is an optional Claude Code companion skill that invokes the installed `cc-transcript` CLI without bundling another implementation. The companion skill remains planned, not released.

## License

Licensed under the [MIT License](LICENSE).

Copyright © 2026 Heiko Brantsch.

## Project status

This is an independent utility and is not affiliated with or endorsed by Anthropic.

Claude and Claude Code are trademarks of their respective owner.
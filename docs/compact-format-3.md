# Compact Bundle Format 3

Compact bundle format 3 is the evidence format introduced by Claude Code Transcript Distiller v0.6.0. It reduces duplication and binary payload size while preserving chronological, line-addressable evidence and making every omission or reference explicit.

This document describes durable format rules and compatibility invariants. The original Claude Code JSONL transcript remains the authoritative raw source.

## Canonical artifact set

A normal export produces exactly two artifacts:

```text
SESSION.compact.jsonl.txt
SESSION.indexed_capsule.md
```

### Compact JSONL

The compact JSONL is transformed archival and escalation evidence. Each physical line is one JSON object, and references use its one-based physical line numbers.

### Indexed capsule

The indexed capsule is the primary day-to-day handoff. It combines a concise chronology, canonical human instructions, unique evidence excerpts, semantic role references, an evidence map, and a compaction audit.

The receiving model should read the indexed capsule first and inspect referenced compact JSONL lines only when consequential detail is missing or contradictory.

### Retired sidecars

Format 3 no longer generates these standalone files:

```text
SESSION.compact_index.md
SESSION.capsule.md
```

Their useful navigation and semantic roles are covered by sparse compact-header navigation and the indexed capsule.

## Evidence and preservation model

The evidence hierarchy is:

1. Original Claude Code JSONL — authoritative raw source.
2. Format-3 compact JSONL — transformed, line-addressable evidence.
3. Indexed capsule — rule-based working context and navigation.

Format 3 does not claim byte-for-byte losslessness, sanitization, or guaranteed token reduction.

By default, compaction does not truncate or omit:

- user text;
- visible assistant text;
- tool inputs;
- unique textual tool results;
- timestamps and record order;
- tool names, IDs, associations, and error state;
- file paths and changing working state;
- interactive answers and queued instructions;
- necessary message and parent relationships;
- thinking text;
- unique file-history metadata.

Format 3 may omit, collapse, or reference:

- recognized base64 payloads;
- usage and cache accounting;
- thinking signatures;
- repeated title metadata;
- `last-prompt` metadata;
- unchanged recognized mode and permission announcements;
- exact repeated allowlisted large result payloads;
- established structural-noise records.

## Compact header

The first compact JSONL line contains:

```json
{
  "__compact_session_header__": {
    "__bundle_format__": 3,
    "__generator__": "cc_transcript.py 0.8.0"
  }
}
```

The generator string carries the producing version, so it changes on every release. It also
changed *filename* at 0.7.0 — the entry script was renamed `compact_session_bundle.py` →
`cc_transcript.py` as part of the project rename to Claude Code Transcript Distiller. Neither
the version bump nor the rename affects existing-bundle classification: comparison operates on
record bodies and never on the header line, so a bundle produced by an earlier release still
classifies as `identical`/`extension` after the rename.

The full header also records available source and session identity, source filename and SHA-256, source physical-line count, stable-snapshot metadata, invariant hoisted fields, conflicts, resolved title, omission policy, transformation accounting, and sparse navigation.

### Hoisted-field conflicts

A field whose value is not invariant is not hoisted; its conflicting occurrences are recorded as
**range-encoded spans**, since 0.6.2:

```json
{
  "__hoisted_conflicts__": {
    "cwd": [
      { "from_line": 48, "to_line": 218, "occurrences": 121, "value": "/path/one" },
      { "from_line": 219, "to_line": 604, "occurrences": 380, "value": "/path/two" }
    ]
  }
}
```

Consecutive occurrences sharing a value collapse into one span; every value transition is
preserved in order. `occurrences` states how many records inside the span carried the value, so a
span never implies that every line within it carried the field. Before 0.6.2 this was one
`[line, value]` pair per occurrence, which re-encoded the same value once per record and made the
header dominate the artifact (95 KB of a 94 KB header line for 11 distinct values).

Output is deterministic for identical source bytes, generator version, and options. The format excludes wall-clock generation time, temporary paths, and nondeterministic ordering.

## Sparse navigation

The header's `__navigation__` object contains compact line numbers, not transcript previews or a second chronology.

Available groups include:

```text
initial_user
latest_user
human_turns
state_changes
tests
errors
corrections
answers
open_work
```

Only nonempty groups are emitted. Every navigation value must identify a valid physical line in the compact JSONL.

## Structured binary omission

Recognized base64 is omitted by default. Raw encoded content is replaced with structured metadata rather than an empty string or an untyped truncation marker.

A valid payload is represented conceptually as:

```json
{
  "__omitted_binary__": {
    "encoding": "base64",
    "media_type": "image/png",
    "encoded_chars": 184233,
    "decoded_bytes": 138174,
    "sha256": "...",
    "sha256_of": "decoded-bytes"
  }
}
```

When available, the descriptor may also preserve filename, source type, dimensions, original size, and file type.

Canonical binary identity is SHA-256 of strictly decoded bytes. If strict decoding fails in permissive processing, the descriptor hashes the original encoded text and records:

```json
{
  "sha256_of": "encoded-text",
  "decode_status": "invalid-base64"
}
```

When the same binary is stored more than once, one occurrence retains the full descriptor and later or mirrored occurrences use a typed identity marker:

```json
{
  "__omitted_binary_mirror__": {
    "sha256": "...",
    "sha256_of": "decoded-bytes"
  }
}
```

Binary recognition and omission occur before generic textual payload interning.

Use `--keep-base64` to preserve recognized encoded text exactly. The deprecated `--truncate-base64` option remains accepted as a compatibility flag for the default omission policy and conflicts with `--keep-base64`.

## Redundant metadata

Format 3 resolves one final session title using the established title precedence and stores its value and source in the header.

Repeated `ai-title` body records are removed. `last-prompt` records are also removed because canonical user messages remain in chronological evidence. Both removals are counted in the transformation ledger.

## Exact payload references

Format 3 can replace a later exact copy of a known non-conversational result payload with:

```json
{
  "__duplicate_payload__": {
    "first_compact_line": 182,
    "characters": 48211,
    "sha256": "..."
  }
}
```

The rules are deliberately narrow:

- minimum payload size is 1,000 characters;
- equality is exact, not semantic or fuzzy;
- only explicitly recognized result and file-content fields qualify;
- user text, visible assistant text, and tool inputs are never replaced;
- the first full payload remains in chronological position;
- a reference must resolve uniquely to an earlier compact line with matching length and SHA-256;
- exact mirrors within one JSONL record use `__duplicate_payload_mirror__` metadata that identifies the retained same-record payload by length and SHA-256;
- unknown fields remain untouched.

Each event still preserves its chronology, tool identity, associations, and error state even when its repeated payload becomes a reference.

Continuation tails materialize cross-boundary text references as full payloads and binary mirrors as full omission descriptors, so each continuation remains independently verifiable without restoring raw binary data. This does not change canonical-relative continuation boundaries.

## Mode and permission state

Recognized `mode` and `permission-mode` records are independent state streams keyed by session identity.

For each stream, format 3:

1. retains the first observed value;
2. retains every later value change;
3. removes only an exact consecutive repeat;
4. preserves unknown, malformed, or ambiguous record variants unchanged;
5. never hard-codes a closed list of possible mode values;
6. never synthesizes a timestamp that the source record does not contain.

This preserves switches into and out of planning, editing, decision, automatic, normal, or future modes.

## Thinking and file history

Thinking text remains present by default, while thinking signatures continue to be removed.

With `--omit-thinking`, thinking text is replaced by deterministic metadata containing its character count and SHA-256.

`file-history-delta` records are preserved unchanged in format 3. Their record count and serialized byte count are included in the transformation audit. Schema-specific file-history compaction is intentionally outside this format.

## Omission policy and transformation ledger

The header exposes a machine-readable omission policy, including whether base64 or thinking was omitted and confirming that conversational text, tool inputs, unique tool results, and file-history deltas follow their preservation rules.

The transformation ledger accounts for applicable operations such as:

- structural-noise omission;
- usage-object removal;
- thinking-signature removal;
- repeated title and `last-prompt` removal;
- binary occurrences and unique binaries omitted;
- mirrored binary occurrences;
- encoded characters and decoded bytes represented by omission metadata;
- exact payload references and avoided repeated characters;
- repeated mode and permission announcements;
- preserved file-history records and bytes;
- malformed or non-object input;
- invariant-field hoisting and conflicts.

The ledger describes transformations; it does not claim token savings.

## Indexed-capsule evidence IDs

The indexed capsule assigns at most one evidence object to each qualifying compact line. IDs are assigned in ascending compact-line order:

```text
E1
E2
E3
```

One evidence object may have several roles. Role order is deterministic:

```text
interactive_answer
correction
error
test
decision
open_work
state_change
diagnostic
```

Semantic sections refer to the same evidence ID instead of repeating the full excerpt. Evidence IDs are navigation aids into compact evidence, not proof that heuristic classification is semantically correct.

## Chronology and preview integrity

The chronology table's line column is headed **`Compact Line`** and addresses the compact JSONL,
never the original transcript. Until 0.6.2 it was headed `JSONL Line`, which invited a reader
following the capsule's own escalation instruction to look up that number in the raw transcript and
land on an unrelated record.

A preview may combine the record's first line with the first line matching a signal pattern. Those
two lines are frequently **not adjacent** in the source, so any skipped text is marked with `[…]`,
and a surviving fragment that begins mid-sentence is prefixed with an ellipsis. Without those
marks a preview can read as one continuous statement the source never contained — a fabrication
rather than a truncation, which this format's purpose does not tolerate. A preview is always a
lossy display artifact: the compact record remains the evidence.

Chronology rows carrying no information at all are omitted — specifically plain assistant records
whose only content block is a zero-length thinking block, which the source transcript emits and
which accounted for 29% of the table on a real session. Rows retaining any evidence value are kept
even when their preview is empty: a tool call or result still identifies its tool, and a queued
user instruction is itself evidence.

## Existing bundles and migration

Format-3 replacement decisions require stable identity evidence. Filenames and recency alone are insufficient.

The implementation classifies existing bundles as current, verified extension, recognized migration, truncation, identity mismatch, or unexplained difference.

### Format-2 migration

A format-2 bundle can migrate automatically only when session/source identity is compatible and, after applying documented format-3 transformations, its projected body is an exact equivalent or prefix of the fresh format-3 body.

A matching source SHA-256 strengthens identity evidence but never substitutes for projected-body equivalence.

The finite projection covers format-3 changes such as title and `last-prompt` removal, recognized state-repeat collapse, structured binary omission, established tool-result mirror restoration, and exact allowlisted payload references.

Changed prefixes, truncation, identity mismatch, and unexplained divergence remain blocked unless `--force` is explicit. The compatibility flag `--overwrite` never bypasses identity checks.

### Retired-sidecar handling

After a verified migration, the same bundle's `.compact_index.md` and `.capsule.md` files are retired by default. `--keep-legacy-artifacts` retains them.

No retired sidecar is removed before the new canonical pair passes staged and committed verification. This verification is mandatory for retirement even when `--no-verify` was requested. Unrelated files are never eligible for deletion.

## Transaction and verification guarantees

An approved write follows this order:

1. generate the canonical pair in staging files;
2. verify staged compact and indexed-capsule content;
3. move prior generated artifacts to rollback paths;
4. commit the new pair;
5. verify committed content;
6. remove rollback copies only after success.

A staging, commit, or committed-verification failure restores the complete previous generated state, including sidecars awaiting verified retirement.

Verification checks include:

- parseable compact JSONL;
- expected format, line count, and checksum;
- valid sparse-navigation references;
- uniquely resolvable backward payload references;
- indexed-capsule checksum linkage;
- unique evidence IDs and valid compact-line references;
- nonempty canonical artifacts.

## Continuations

A format-3 continuation produces only:

```text
SESSION.partN.compact.jsonl.txt
SESSION.partN.indexed_capsule.md
```

It records the part number, prior canonical compact checksum and line count, and the first new global compact line.

Format 3 intentionally retains canonical-relative continuation boundaries. If the canonical bundle is not amended between continuation exports, a later part can overlap an earlier continuation. Cumulative continuation-chain redesign is outside format 3.

## Explicit non-goals

Format 3 does not introduce:

- semantic or approximate deduplication;
- lossy summarization of unique tool output;
- default thinking omission;
- schema-specific `file-history-delta` transformation;
- generalized state encoding that makes every record depend on previous state;
- broad removal of IDs or relationships;
- automatic upload, synchronization, or session resume;
- a guarantee that compact output is sanitized or safe to disclose.

## Compatibility principle

Future changes must preserve explainability and deterministic verification. A transformation is eligible for automatic migration only when it belongs to a finite documented rule set and the resulting identity or prefix relationship can be proven. Unexplained differences must fail closed.

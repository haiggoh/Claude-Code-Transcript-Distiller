# Changelog

## 0.6.2

Defect fixes only; no naming or format-contract changes. All four issues were found by running
0.6.1 against a real 7.85 MB / 2,554-record transcript and measuring its artifacts.

- Range-encode `__hoisted_conflicts__` as `from_line`/`to_line`/`occurrences`/`value` spans instead of one entry per occurrence. On the measured session the header line drops from 94,190 to 4,806 bytes (the conflict ledger from 95,379 to 2,659; 1,760 pairs to 23 spans) with every occurrence still accounted for. Header-only, so existing-bundle classification is unaffected.
- Rename the chronology column `JSONL Line` to `Compact Line`. It always held the compact line number, so a reader following the capsule's escalation instruction into the raw transcript landed on an unrelated record — a result at source line 48 was labelled 34.
- Mark elided text in previews with `[…]`, and prefix a fragment that starts mid-sentence with an ellipsis. A preview may join the first line to the first signal-matching line, which is often not adjacent; joined silently, the result read as a continuous statement the source never contained. 73 previews in the measured session were affected.
- Omit chronology rows carrying no information — plain assistant records whose only block is a zero-length thinking block. Rows with any evidence value are kept, including tool calls, tool results, and queued user instructions with empty previews. Rows containing the empty sentinel drop from 409 to 20 of the table.
- Render a no-argument tool call as its bare name. `describe_call` returned the truthy `"[empty]"` sentinel, which defeated the caller's `if detail` test and produced rows reading `TaskList: [empty]`.
- Remove a duplicated `__pycache__`/`*.py[cod]` stanza from `.gitignore`; correct the stale generator-version example in `docs/compact-format-3.md` and document the header, chronology, and preview rules above.
- Fix the release builder producing artifacts named for a stale hardcoded version: it emitted a `0.6.1` archive from a `0.6.2` source. The default now reads `VERSION` from the packaged script, and its test asserts that relationship instead of pinning a literal — the test previously encoded `0.6.1` in its own name, which is why the builder's drift went unnoticed.
- Add 18 regression tests (64 total). Each new assertion was mutation-tested against the pre-fix behaviour to confirm it detects the regression it guards.

Note on existing bundles: because comparison is body-only and unchanged, re-running against a bundle produced by an earlier 0.6.x correctly reports `identical` and writes nothing. The smaller header and the corrected capsule therefore apply to newly created bundles; regenerate with `--overwrite` to adopt them for an existing one.

## 0.6.1

- Show the original transcript size alongside existing canonical compact JSONL and indexed-capsule sizes in the interactive picker.
- Keep generated artifact sizes visually grouped as `[compact JSONL | indexed capsule]`, including explicit partial-pair markers.
- Omit the repetitive project field for home-directory sessions and show only the latest CWD basename for actual projects.
- Resolve and cache picker title and CWD metadata in one transcript scan.

## 0.6.0

- Make compact JSONL plus indexed capsule the exact canonical output pair; retire standalone index and capsule sidecars after verified migration.
- Introduce compact format 3 with sparse header navigation and an expanded omission/transformation ledger.
- Omit recognized base64 by default using structured metadata and decoded-byte SHA-256 identity; add `--keep-base64`.
- Remove repeated `ai-title` and `last-prompt` metadata while retaining one resolved title in the header.
- Add exact allowlisted large-payload references while preserving user text, visible assistant text, tool inputs, and unique results.
- Collapse unchanged recognized mode/permission announcements while preserving initial values and transitions.
- Preserve thinking by default and add `--omit-thinking`; preserve `file-history-delta` unchanged.
- Redesign the indexed capsule around deterministic evidence IDs and capsule-first receiving-LLM instructions.
- Add format-2 migration/extension projection, committed-output rollback, retired-sidecar handling, and `--keep-legacy-artifacts`.
- Add focused public format-3, output, migration, rollback, and release-builder regression tests.

## 0.5.0

- Display Claude Code custom titles, AI-generated titles, summaries, or first-prompt fallbacks in the interactive picker.
- Keep complete session UUIDs visible beneath human-readable labels.
- Redact, normalize, truncate, and cache labels per invocation.
- Merge open-file, environment-ID, and recent-activity signals.
- Replace ambiguous “likely” wording with factual activity labels.
- Add standard-library regression tests for title resolution.
- Add visual spacing between picker entries and skip command-only placeholder prompts.
- Hide title-source annotations and use `Untitled session` as the final fallback.

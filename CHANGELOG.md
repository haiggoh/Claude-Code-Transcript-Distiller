# Changelog

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

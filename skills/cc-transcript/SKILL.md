---
name: cc-transcript
description: Distill a native Claude Code session transcript into a compact, line-addressable capsule for handoff to another model, for code review, or for archival. Use when a session is too long to re-read or to resume, when context must be carried into a fresh session or an external LLM, or when the user asks to compact, capsule, distill, or hand off a transcript. Invokes the installed cc-transcript CLI.
---

# cc-transcript — transcript distillation for handoff and review

## When to use

- A session is long enough that resuming it costs real money, and what is actually needed is
  its *conclusions* plus addressable evidence — not its full text.
- Context must move somewhere else: a fresh session, another model, or a review workflow.
- An interrupted session needs a durable record of what it established.

## What it produces

Two artifacts per session, written to `~/.claude/compacted-sessions` unless `-o` says otherwise:

- `<base>.indexed_capsule.md` — the primary handoff: chronology, context, and decisions, with
  line addresses pointing back into the source JSONL.
- `<base>.compact.jsonl.txt` — the evidence layer: structurally transformed, line-addressable.

Compaction is **semantic and structural, not byte-for-byte lossless**. The original JSONL stays
the authoritative raw evidence — never delete it on the strength of a capsule.

## Invocation

```sh
cc-transcript                 # interactive picker (needs a TTY)
cc-transcript --current       # the CURRENTLY RUNNING session, no TTY required
cc-transcript <path.jsonl>    # an explicit transcript
cc-transcript --current -o /tmp/out
```

Run `cc-transcript --help` for the full flag set before reaching for anything not listed here.

Two flags matter more than the rest:

- `--current` resolves the running session without a TTY. **Prefer it whenever the choice of
  session is already known** — the bare picker needs a terminal and will not work piped.
- `--omit-thinking` drops thinking blocks explicitly, when the capsule is going somewhere the
  reasoning should not.

`--overwrite` is checked against identity before it replaces anything; `--force` bypasses that and
can replace an unrelated or truncated bundle, so treat it as the destructive option it says it is.

## Two things that mislead

- **The picker selects the LARGEST recent transcript, not the newest.** That is deliberate — the
  newest is often a one-exchange session — but it means the obvious pick is not always the one you
  wanted. Confirm the session id in the output.
- **The newest turn may not be on disk yet.** Claude Code flushes with a lag, so a capsule taken
  mid-session can legitimately miss the last exchange. If the final turn matters, say so rather
  than presenting the capsule as complete.

## Prerequisites

Installed via Homebrew (`brew install haiggoh/tap/claude-code-transcript-distiller`) or the
standalone release installer, either of which puts `cc-transcript` on `PATH`. From a repo checkout
`python3 cc_transcript.py` works and is equivalent.

## Privacy

Runs entirely locally with no network calls. Output is **not sanitized** — compaction removes
redundancy, not secrets — so inspect a capsule before sharing it anywhere.

## Related

- `audit-loose-ends` — its transcript scan answers "what did this session change?" cheaply; reach
  for the distiller instead when the whole context must travel.
- `resume-interrupted` — recovers a cut-off session; a capsule preserves what it recovered.

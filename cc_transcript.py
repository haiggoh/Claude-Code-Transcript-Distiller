#!/usr/bin/env python3
"""Distill native Claude Code transcripts into compact, line-addressable evidence.

Each selected session produces two canonical artifacts:
  .compact.jsonl.txt   transformed, line-addressable format-3 evidence
  .indexed_capsule.md  primary chronology-and-context handoff

Format 3 omits recognized base64 by default while preserving deterministic
metadata, removes redundant title and last-prompt records, collapses unchanged
mode announcements, references exact repeated large result payloads, and embeds
sparse navigation in the compact header. Use --keep-base64 for exact binary text
preservation and --omit-thinking for explicit thinking omission.

Existing bundles are classified before replacement. Exact or safely projected
format-2 bundles can migrate transactionally; retired index/capsule sidecars are
removed only after the new pair verifies unless --keep-legacy-artifacts is used.
Unexplained differences remain blocked unless --force is explicit.

Compaction is semantic and structural, not byte-for-byte losslessness. Preserve
the original Claude Code JSONL as authoritative raw evidence. Generated output
may still contain sensitive transcript content and is not sanitized.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

VERSION = "0.8.3"
BUNDLE_FORMAT = 3
PAYLOAD_INTERN_THRESHOLD = 1_000
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_OUTPUT_DIR = Path.home() / ".claude" / "compacted-sessions"
HOISTED_FIELDS = ["sessionId", "version", "gitBranch", "cwd", "entrypoint"]
NOISE_TYPE_SUBTYPE = {
    ("file-history-snapshot", None),
    ("system", "mode"),
    ("system", "permission-mode"),
    ("system", "turn_duration"),
    ("system", "stop_hook_summary"),
}
NOISE_ATTACHMENT_TYPES = {"output_style", "task_reminder"}
PREVIEW_CHARS = 220
# Sentinel for a preview with nothing renderable. It is a display string, so any
# truthiness test on a preview must compare against it explicitly (`if detail` was
# always true, which is how "Tool: [empty]" reached the chronology).
EMPTY_PREVIEW = "[empty]"
# Marks text dropped between two non-adjacent preview lines.
ELISION_MARK = "[…]"
CAPSULE_EXCERPT = 700
CAPSULE_LARGE_RECORD = 12_000
SESSION_TITLE_CHARS = 100

SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b((?:api[_-]?key|token|secret|password|cookie)\s*[=:]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*=\s*)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "sk-[REDACTED]"),
    (re.compile(r"(?i)([?&](?:token|key|secret|signature|sig|code)=)[^&#\s]+"), r"\1[REDACTED]"),
]
SIGNAL_RE = re.compile(
    r"(?i)\b(error|fail|warning|success|pass|verif|creat|updat|wrote|edit|delet|"
    r"complete|finish|remain|pending|unfinished|ledger|fallback|authoritative|"
    r"date[- ]?filter|status|unchanged|preserve|memory|waypoint|permission|auth|"
    r"answer|decision|selected|expected|actual|test)"
)
MUTATING_TOOLS = {
    "write", "edit", "multiedit", "notebookedit", "update_memory", "add_memory",
    "replace_memory_content", "delete_memory", "write_note", "replace_note_content",
    "create_task", "update_task",
}
SHELL_TOOLS = {"bash", "shell", "execute", "execute_code"}
MUTATING_SHELL_RE = re.compile(
    r"(?i)(?:^|[;&|]\s*|\bsudo\s+)"
    r"(?:rm|mv|cp|mkdir|rmdir|touch|chmod|chown|install|launchctl|systemctl|"
    r"defaults\s+write|git\s+(?:commit|checkout|switch|reset|clean)|"
    r"cat\s+.*(?:>|>>)|printf\s+.*(?:>|>>)|sed\s+-i|perl\s+-i)\b"
)
DECISIVE_RESULT_RE = re.compile(
    r"(?i)(?:^|\n)\s*(?:PASS|PASSED|FAIL|FAILED|ERROR)\b|"
    r"\b(?:all tests? passed|tests? failed|exit code\s*[:=]\s*\d+|"
    r"authoritative.*(?:expected|actual|pass)|"
    r"fallback.*(?:expected|actual|pass)|"
    r"date[- ]?filter.*(?:expected|actual|pass))\b"
)
TEST_RE = re.compile(
    r"(?i)\b(pytest|unittest|syntax|compile|assert|exit code|health check|"
    r"smoke check|verification|verify|authoritative|fallback|date[- ]?filter)\b"
)
DECISION_RE = re.compile(r"(?i)\b(must|should|do not|don't|never|keep|preserve|use |architecture|decision|source of truth|unchanged|instead)\b")
OPEN_RE = re.compile(r"(?i)\b(todo|remaining|remain|unfinished|pending|next|defer|blocked|open question|follow[- ]?up)\b")
CORRECTION_RE = re.compile(r"(?i)\b(correction|actually|instead|not |no longer|retired|supersed|unchanged|do not)\b")


@dataclass
class Audit:
    physical_lines: int = 0
    blank_lines: int = 0
    parsed_records: int = 0
    non_object_lines: int = 0
    malformed_lines: int = 0
    decoding_failures: int = 0
    retained_source_records: int = 0
    omitted_records: int = 0
    transformed_records: int = 0
    generated_records: int = 1
    # Range-encoded spans (see collapse_conflict_runs), not one entry per occurrence.
    hoisted_conflicts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    transformations: Counter[str] = field(default_factory=Counter)
    malformed_details: list[tuple[int, str]] = field(default_factory=list)

    @property
    def retained_records(self) -> int:
        return self.retained_source_records + self.generated_records


@dataclass
class Row:
    record: int
    line: int
    time: str
    kind: str
    preview: str
    canonical: str = ""
    tool_id: str = ""
    tool_name: str = ""
    raw_text: str = ""
    command: str = ""


@dataclass(frozen=True)
class SessionLabel:
    text: str
    source: str


@dataclass(frozen=True)
class PickerMetadata:
    label: SessionLabel
    cwd: str = ""


def redact(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(filter(None, map(flatten, value)))
    if isinstance(value, dict):
        parts = []
        for key in ("text", "content", "message", "answer", "answers", "prompt",
                    "output", "stdout", "stderr", "result", "summary", "title",
                    "question", "label", "description", "value"):
            if key in value:
                part = flatten(value[key])
                if part:
                    parts.append(part)
        return "\n".join(parts)
    return ""


def normalize(text: str, limit: int = PREVIEW_CHARS) -> str:
    """Normalize display text without applying context-specific Markdown escaping."""
    text = redact(text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit < 2:
        raise ValueError("text limit must be at least 2")
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text or EMPTY_PREVIEW


def collapse_conflict_runs(
    conflicts: Sequence[tuple[int, Any]],
) -> list[dict[str, Any]]:
    """Range-encode consecutive equal hoisted-field values instead of one entry per line.

    A flat [line, value] pair per occurrence re-encodes the same value once per record,
    which made the header dominate the whole artifact: 1,760 pairs covering 11 distinct
    values serialized to 95 KB, i.e. >99% of a 94 KB header line, in the one record a
    consumer reads first.

    Consecutive occurrences sharing a value collapse into a single span. `occurrences` is
    retained so the span stays honest: it says "these N records between from_line and
    to_line carried this value", never "every line in the span carried the field". Order
    and every value transition are preserved, which is the information this ledger exists
    to convey.
    """
    runs: list[dict[str, Any]] = []
    for line_no, value in conflicts:
        if runs and runs[-1]["value"] == value and line_no >= runs[-1]["to_line"]:
            runs[-1]["to_line"] = line_no
            runs[-1]["occurrences"] += 1
        else:
            runs.append({
                "from_line": line_no,
                "to_line": line_no,
                "occurrences": 1,
                "value": value,
            })
    return runs


def markdown_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def fenced(text: str) -> str:
    """Render untrusted transcript text without allowing it to reshape the document."""
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}text\n{text}\n{fence}"


def excerpt(text: str, limit: int = PREVIEW_CHARS) -> str:
    """Preview the first line plus the first 'signal' line, marking any skipped text.

    The signal line can sit anywhere later in the record, so joining it to the first line
    with only a separator asserts adjacency that does not exist: dropped paragraphs
    disappear silently and the surviving fragment can begin mid-sentence, so the preview
    reads as one continuous statement the source never contained. For an evidence-handoff
    artifact that is a fabrication, not merely a cosmetic issue — so a gap is always
    marked with ELISION_MARK, and a fragment that clearly starts mid-sentence is prefixed
    with an ellipsis.
    """
    lines = [re.sub(r"\s+", " ", line).strip() for line in redact(text).splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return EMPTY_PREVIEW
    head = lines[0]
    signal_index = next(
        (index for index, line in enumerate(lines) if SIGNAL_RE.search(line)), None
    )
    if signal_index is None or signal_index == 0:
        return normalize(head, limit)
    signal = lines[signal_index]
    # Adjacent lines are contiguous in the source; anything further skipped content.
    joiner = " ⏐ " if signal_index == 1 else f" ⏐ {ELISION_MARK} "
    if signal[:1].islower() or signal[:1] in ",;:)":
        signal = f"…{signal}"
    return normalize(f"{head}{joiner}{signal}", limit)


def message_role(data: dict[str, Any]) -> str:
    if isinstance(data.get("role"), str):
        return data["role"].lower()
    message = data.get("message")
    return message.get("role", "").lower() if isinstance(message, dict) and isinstance(message.get("role"), str) else ""


def outer_type(data: dict[str, Any]) -> str:
    for key in ("type", "record_type", "event", "kind"):
        if isinstance(data.get(key), str) and data[key]:
            return data[key].lower().replace("_", "-")
    return "unknown"


def content_blocks(data: dict[str, Any]) -> list[Any]:
    message = data.get("message")
    if isinstance(message, dict) and "content" in message:
        value = message["content"]
        return value if isinstance(value, list) else [value]
    if "content" in data:
        value = data["content"]
        return value if isinstance(value, list) else [value]
    return []


def block_type(block: Any) -> str:
    if isinstance(block, dict) and isinstance(block.get("type"), str):
        return block["type"].lower().replace("_", "-")
    return "text" if isinstance(block, str) else ""


def text_blocks(data: dict[str, Any]) -> str:
    parts = []
    for block in content_blocks(data):
        kind = block_type(block)
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and kind in {"text", "input-text", "output-text"}:
            part = flatten(block.get("text") or block.get("content"))
            if part:
                parts.append(part)
    return "\n".join(parts)


def call_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in content_blocks(data) if isinstance(b, dict) and block_type(b) in {"tool-use", "tool-call"}]


def result_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in content_blocks(data) if isinstance(b, dict) and
            (block_type(b) == "tool-result" or "tool_use_id" in b or "tool_call_id" in b)]


def timestamp(data: dict[str, Any]) -> str:
    stamp = data.get("timestamp") or data.get("created_at")
    if stamp is None:
        return "--:--:--"
    try:
        if isinstance(stamp, (int, float)):
            if stamp > 10_000_000_000:
                stamp /= 1000
            return datetime.fromtimestamp(stamp, timezone.utc).strftime("%H:%M:%S")
        match = re.search(r"(?:T|\s)(\d{2}:\d{2}:\d{2})", str(stamp))
        return match.group(1) if match else "--:--:--"
    except (ValueError, TypeError, OverflowError, OSError):
        return "--:--:--"


# Stage A: established compactor scaffold

def strip_thinking_signatures(record: dict[str, Any]) -> None:
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "thinking":
                block.pop("signature", None)


def strip_usage_accounting(record: dict[str, Any]) -> None:
    message = record.get("message")
    if isinstance(message, dict):
        message.pop("usage", None)


def compact_session_title(
    source_records: Sequence[tuple[int, dict[str, Any]]],
) -> dict[str, str] | None:
    """Resolve one final title using the established picker precedence."""
    custom_title = ""
    ai_title = ""
    summary = ""
    first_prompt = ""
    for _, record in source_records:
        kind = outer_type(record)
        if kind == "custom-title":
            custom_title = _title_text(record, "customTitle", "custom_title") or custom_title
        elif kind == "ai-title":
            ai_title = _title_text(record, "aiTitle", "ai_title") or ai_title
        elif kind == "summary" or "summary" in record:
            summary = _title_text(record, "summary") or summary
        if (
            not first_prompt
            and message_role(record) == "user"
            and not record.get("isMeta")
            and not result_blocks(record)
        ):
            candidate = _meaningful_prompt_text(text_blocks(record))
            if candidate:
                first_prompt = candidate
    for source, value in (
        ("custom-title", custom_title),
        ("ai-title", ai_title),
        ("summary", summary),
        ("first-prompt", first_prompt),
    ):
        if value.strip():
            return {"value": normalize(value, SESSION_TITLE_CHARS), "source": source}
    return None


def state_stream_key(
    record: dict[str, Any],
) -> tuple[tuple[str, str], str] | None:
    """Recognize only the empirically established state-record schemas."""
    kind = record.get("type")
    if kind == "mode":
        expected = {"type", "sessionId", "mode"}
        value = record.get("mode")
    elif kind == "permission-mode":
        expected = {"type", "sessionId", "permissionMode"}
        value = record.get("permissionMode")
    else:
        return None
    session_id = record.get("sessionId")
    if set(record) != expected:
        return None
    if not isinstance(session_id, str) or not isinstance(value, str):
        return None
    return (kind, session_id), value


def binary_descriptor(encoded: str, metadata: dict[str, Any]) -> dict[str, Any]:
    compact_encoded = re.sub(r"\s+", "", encoded)
    descriptor: dict[str, Any] = {
        "encoding": "base64",
        "encoded_chars": len(encoded),
    }
    try:
        decoded = base64.b64decode(compact_encoded, validate=True)
    except (binascii.Error, ValueError):
        descriptor.update({
            "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "sha256_of": "encoded-text",
            "decode_status": "invalid-base64",
        })
    else:
        descriptor.update({
            "decoded_bytes": len(decoded),
            "sha256": hashlib.sha256(decoded).hexdigest(),
            "sha256_of": "decoded-bytes",
        })
    for key in (
        "media_type", "filename", "source_type", "dimensions",
        "original_size", "file_type",
    ):
        value = metadata.get(key)
        if value not in (None, "", {}, []):
            descriptor[key] = copy.deepcopy(value)
    return descriptor


def binary_occurrences(
    record: dict[str, Any],
) -> list[tuple[dict[str, Any], str, str, dict[str, Any]]]:
    found: list[tuple[dict[str, Any], str, str, dict[str, Any]]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            source = value.get("source")
            if (
                isinstance(source, dict)
                and isinstance(source.get("data"), str)
                and (value.get("type") == "image" or source.get("type") == "base64")
            ):
                found.append((source, "data", source["data"], {
                    "media_type": source.get("media_type"),
                    "source_type": value.get("type"),
                }))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(record.get("message"))
    result = record.get("toolUseResult")
    file_info = result.get("file") if isinstance(result, dict) else None
    if isinstance(file_info, dict) and isinstance(file_info.get("base64"), str):
        found.append((file_info, "base64", file_info["base64"], {
            "dimensions": file_info.get("dimensions"),
            "original_size": file_info.get("originalSize"),
            "file_type": file_info.get("type"),
            "filename": file_info.get("filename") or file_info.get("name"),
        }))
    return found


def omit_binary_payloads(
    record: dict[str, Any],
    audit: Audit,
    seen: set[tuple[str, str]],
) -> None:
    grouped: dict[str, list[tuple[dict[str, Any], str, str, dict[str, Any]]]] = {}
    for occurrence in binary_occurrences(record):
        grouped.setdefault(occurrence[2], []).append(occurrence)
    for encoded, group in grouped.items():
        metadata: dict[str, Any] = {}
        for _, _, _, candidate in group:
            for key, value in candidate.items():
                if value not in (None, "", {}, []):
                    metadata.setdefault(key, value)
        descriptor = binary_descriptor(encoded, metadata)
        identity = (descriptor["sha256_of"], descriptor["sha256"])
        new_identity = identity not in seen
        if new_identity:
            seen.add(identity)
            audit.transformations["unique_binary_payloads_omitted"] += 1
            audit.transformations["unique_decoded_binary_bytes_omitted"] += descriptor.get("decoded_bytes", 0)
        for index, (container, key, payload, _) in enumerate(group):
            audit.transformations["base64_occurrences_omitted"] += 1
            audit.transformations["base64_chars_omitted"] += len(payload)
            if new_identity and index == 0:
                container[key] = {"__omitted_binary__": copy.deepcopy(descriptor)}
            else:
                container[key] = {"__omitted_binary_mirror__": {
                    "sha256": descriptor["sha256"],
                    "sha256_of": descriptor["sha256_of"],
                }}
                audit.transformations["mirrored_binary_occurrences_deduplicated"] += 1


def omit_thinking_blocks(record: dict[str, Any], audit: Audit) -> None:
    message = record.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return
    for index, block in enumerate(message["content"]):
        if not isinstance(block, dict) or block.get("type") != "thinking":
            continue
        thinking = block.get("thinking")
        if not isinstance(thinking, str):
            thinking = block.get("text")
        if not isinstance(thinking, str):
            continue
        message["content"][index] = {
            "type": "thinking-omitted",
            "characters": len(thinking),
            "sha256": hashlib.sha256(thinking.encode("utf-8")).hexdigest(),
        }
        audit.transformations["thinking_blocks_omitted"] += 1
        audit.transformations["thinking_characters_omitted"] += len(thinking)


def payload_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def protected_tool_input_payloads(record: dict[str, Any]) -> list[str]:
    """Return large allowlisted tool-input strings without ever replacing them."""
    found: list[str] = []
    for block in call_blocks(record):
        value = block.get("input")
        if not isinstance(value, dict):
            continue
        for key in ("content", "text"):
            candidate = value.get(key)
            if isinstance(candidate, str) and len(candidate) >= PAYLOAD_INTERN_THRESHOLD:
                found.append(candidate)
    return found


def result_payload_slots(record: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Return only explicitly recognized non-conversational result fields."""
    slots: list[tuple[dict[str, Any], str]] = []
    for block in result_blocks(record):
        if isinstance(block.get("content"), str):
            slots.append((block, "content"))
    result = record.get("toolUseResult")
    if isinstance(result, dict):
        for key in ("content", "stdout", "stderr", "output", "result"):
            if isinstance(result.get(key), str):
                slots.append((result, key))
        file_info = result.get("file")
        if isinstance(file_info, dict) and isinstance(file_info.get("content"), str):
            slots.append((file_info, "content"))
    return slots


def intern_exact_payloads(
    record: dict[str, Any],
    compact_line: int,
    seen: dict[tuple[int, str], tuple[int, str]],
    audit: Audit,
) -> None:
    """Replace later exact allowlisted result payloads with backward references."""
    for value in protected_tool_input_payloads(record):
        identity = (len(value), payload_digest(value))
        seen.setdefault(identity, (compact_line, value))

    for container, key in result_payload_slots(record):
        value = container.get(key)
        if not isinstance(value, str) or len(value) < PAYLOAD_INTERN_THRESHOLD:
            continue
        digest = payload_digest(value)
        identity = (len(value), digest)
        prior = seen.get(identity)
        if prior is None:
            seen[identity] = (compact_line, value)
            continue
        first_line, first_value = prior
        if first_value != value or first_line >= compact_line:
            # A line-only reference cannot identify an earlier occurrence within
            # the same JSON record. Keep same-line duplicates intact.
            continue
        container[key] = {
            "__duplicate_payload__": {
                "first_compact_line": first_line,
                "characters": len(value),
                "sha256": digest,
            }
        }
        audit.transformations["duplicate_payloads_referenced"] += 1
        audit.transformations["duplicate_payload_characters_avoided"] += len(value)


def make_navigation(rows: Sequence[Row]) -> dict[str, Any]:
    """Build sparse preview-free line arrays for the compact header."""
    users = [row.line for row in rows if row.kind in {"USER", "QUEUED-USER"}]
    navigation: dict[str, Any] = {
        "human_turns": users,
        "state_changes": [row.line for row in rows if row.kind in {"MODE", "PERMISSION-MODE"}],
        "tests": [row.line for row in rows if is_test_evidence(row)],
        "errors": [row.line for row in rows if is_error_evidence(row)],
        "corrections": [row.line for row in rows if row.kind in {"USER", "QUEUED-USER"} and CORRECTION_RE.search(row.raw_text)],
        "answers": [row.line for row in rows if is_interactive_answer(row)],
        "open_work": [row.line for row in rows if row.kind in {"USER", "QUEUED-USER", "ASSISTANT"} and OPEN_RE.search(row.raw_text)],
    }
    if users:
        navigation["initial_user"] = users[0]
        navigation["latest_user"] = users[-1]
    return {key: value for key, value in navigation.items() if value not in ([], None)}


def dedupe_tool_result(record: dict[str, Any], audit: Audit | None = None) -> None:
    """Replace exact same-record result mirrors with typed metadata."""
    result = record.get("toolUseResult")
    if not isinstance(result, dict):
        return
    candidates: list[tuple[dict[str, Any], str]] = []
    for key in ("content", "stdout"):
        if isinstance(result.get(key), str):
            candidates.append((result, key))
    file_info = result.get("file")
    if isinstance(file_info, dict) and isinstance(file_info.get("content"), str):
        candidates.append((file_info, "content"))

    for block in result_blocks(record):
        content = block.get("content")
        block_text: str | None = content if isinstance(content, str) else None
        if isinstance(content, list):
            texts = [
                item.get("text") for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ]
            if len(texts) == 1:
                block_text = texts[0]
        if block_text is None:
            continue
        for container, key in candidates:
            if container.get(key) != block_text:
                continue
            container[key] = {
                "__duplicate_payload_mirror__": {
                    "characters": len(block_text),
                    "sha256": payload_digest(block_text),
                    "retained_in": "message.tool_result.content",
                }
            }
            if audit is not None:
                audit.transformations["same_record_payload_mirrors_referenced"] += 1
                audit.transformations["same_record_payload_characters_avoided"] += len(block_text)


def is_noise(record: dict[str, Any]) -> bool:
    if (record.get("type"), record.get("subtype")) in NOISE_TYPE_SUBTYPE:
        return True
    attachment = record.get("attachment")
    return record.get("type") == "attachment" and isinstance(attachment, dict) and attachment.get("type") in NOISE_ATTACHMENT_TYPES


def compact_records(
    input_path: Path,
    permissive: bool,
    keep_base64: bool = False,
    omit_thinking: bool = False,
) -> tuple[list[dict[str, Any]], Audit]:
    audit = Audit()
    source_records: list[tuple[int, dict[str, Any]]] = []
    malformed_records: list[tuple[int, dict[str, Any]]] = []
    errors = "replace" if permissive else "strict"
    try:
        handle = input_path.open("r", encoding="utf-8", errors=errors)
        with handle:
            for line_no, raw in enumerate(handle, 1):
                audit.physical_lines += 1
                if "�" in raw and permissive:
                    audit.decoding_failures += raw.count("�")
                if not raw.strip():
                    audit.blank_lines += 1
                    continue
                try:
                    source = json.loads(raw)
                except json.JSONDecodeError as error:
                    audit.malformed_lines += 1
                    audit.malformed_details.append((line_no, str(error)))
                    if permissive:
                        malformed_records.append((line_no, {"__unparsed_line__": raw.rstrip("\n"), "__source_line__": line_no}))
                        continue
                    raise ValueError(f"Malformed JSON at source line {line_no}: {error}") from error
                if not isinstance(source, dict):
                    audit.non_object_lines += 1
                    if permissive:
                        malformed_records.append((line_no, {"__non_object_json__": source, "__source_line__": line_no}))
                        continue
                    raise ValueError(f"Non-object JSON at source line {line_no}")
                audit.parsed_records += 1
                source_records.append((line_no, source))
    except UnicodeDecodeError as error:
        raise ValueError(f"Input is not valid UTF-8 near byte {error.start}; use --permissive to preserve replacement text") from error

    session_title = compact_session_title(source_records)

    # Hoist only fields whose values remain invariant throughout the transcript.
    occurrences: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    for line_no, record in source_records:
        for name in HOISTED_FIELDS:
            if name in record:
                occurrences[name].append((line_no, record[name]))
    header: dict[str, Any] = {}
    invariant_fields: set[str] = set()
    for name, values in occurrences.items():
        first = values[0][1]
        conflicts = [(line_no, value) for line_no, value in values[1:] if value != first]
        if conflicts:
            audit.hoisted_conflicts[name] = collapse_conflict_runs(conflicts)
        else:
            header[name] = first
            invariant_fields.add(name)

    records: list[dict[str, Any]] = []
    malformed_by_line = dict(malformed_records)
    source_by_line = dict(source_records)
    state_values: dict[tuple[str, str], str] = {}
    binary_seen: set[tuple[str, str]] = set()
    payload_seen: dict[tuple[int, str], tuple[int, str]] = {}
    for line_no in range(1, audit.physical_lines + 1):
        if line_no in malformed_by_line:
            records.append(malformed_by_line[line_no])
            continue
        source = source_by_line.get(line_no)
        if source is None:
            continue
        source_kind = outer_type(source)
        if source_kind == "ai-title":
            audit.omitted_records += 1
            audit.transformations["repeated_ai_titles_removed"] += 1
            continue
        if source_kind == "last-prompt":
            audit.omitted_records += 1
            audit.transformations["last_prompt_records_removed"] += 1
            continue
        state = state_stream_key(source)
        if state is not None:
            stream, value = state
            if state_values.get(stream) == value:
                audit.omitted_records += 1
                counter = (
                    "unchanged_mode_records_removed"
                    if stream[0] == "mode"
                    else "unchanged_permission_mode_records_removed"
                )
                audit.transformations[counter] += 1
                continue
            state_values[stream] = value
        record = copy.deepcopy(source)
        if is_noise(record):
            audit.omitted_records += 1
            audit.transformations["noise_records_omitted"] += 1
            continue
        before = json.dumps(record, sort_keys=True, ensure_ascii=False)
        for name in invariant_fields:
            record.pop(name, None)
        if record.pop("userType", None) is not None:
            audit.transformations["user_type_removed"] += 1
        before_signature = json.dumps(record, sort_keys=True, ensure_ascii=False)
        strip_thinking_signatures(record)
        if json.dumps(record, sort_keys=True, ensure_ascii=False) != before_signature:
            audit.transformations["thinking_signatures_removed"] += 1
        had_usage = isinstance(record.get("message"), dict) and "usage" in record["message"]
        strip_usage_accounting(record)
        if had_usage:
            audit.transformations["usage_objects_removed"] += 1
        if not keep_base64:
            omit_binary_payloads(record, audit, binary_seen)
        if omit_thinking:
            omit_thinking_blocks(record, audit)
        compact_line = len(records) + 2
        intern_exact_payloads(record, compact_line, payload_seen, audit)
        dedupe_tool_result(record, audit)
        if outer_type(record) == "file-history-delta":
            audit.transformations["file_history_delta_records_preserved"] += 1
            audit.transformations["file_history_delta_bytes_preserved"] += len(
                (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            )
        after = json.dumps(record, sort_keys=True, ensure_ascii=False)
        audit.transformed_records += before != after
        records.append(record)

    audit.retained_source_records = len(records)
    compact_header = {
        **header,
        "__bundle_format__": BUNDLE_FORMAT,
        "__generator__": f"cc_transcript.py {VERSION}",
        "__session_title__": session_title,
        "__omission_policy__": {
            "text_turns_truncated": False,
            "tool_inputs_truncated": False,
            "unique_tool_results_truncated": False,
            "base64_omitted": not keep_base64,
            "thinking_omitted": omit_thinking,
            "file_history_deltas_preserved": True,
        },
        "__source_name__": input_path.name,
        "__source_sha256__": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "__source_physical_lines__": audit.physical_lines,
        "__hoisted_conflicts__": audit.hoisted_conflicts,
        "__transformations__": dict(audit.transformations),
    }
    return [{"__compact_session_header__": compact_header}] + records, audit

def serialize_jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)


def source_chronology_key(path: Path) -> float:
    """Sort key putting the OLDEST source transcript first.

    Uses the source's mtime, the same signal list_sessions() orders the picker by, so "oldest
    first" here means the same thing the user saw when choosing. A source that cannot be stat'ed
    sorts last rather than aborting the batch — an unreadable mtime is not a reason to refuse to
    export.
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return float("inf")


def stagger_artifact_times(artifacts: list[Path]) -> int:
    """Give each artifact its own mtime, one second apart, in the order written.

    Artifacts written in one run land in the same second, and a filesystem timestamp has no
    sub-second component that Finder sorts on — so a whole batch shares one modification time and
    "sort by date" produces an arbitrary order that no longer matches the sessions' chronology.
    Since the batch is processed oldest-source-first, stamping successive artifacts one second
    apart makes date order equal transcript order.

    Times run BACKWARD from now (last artifact ~now, earlier ones progressively older) so nothing
    is stamped in the future, which would confuse Finder, backup tools, and make-style staleness
    checks. A file that vanished or is not writable is skipped: cosmetic ordering must never fail
    an export whose real output already succeeded. Returns the number of files stamped.
    """
    if len(artifacts) < 2:
        return 0
    base = time.time() - (len(artifacts) - 1)
    stamped = 0
    for offset, artifact in enumerate(artifacts):
        when = base + offset
        try:
            os.utime(artifact, (when, when))
            stamped += 1
        except OSError:
            continue
    return stamped


def atomic_write(path: Path, content: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temp_name).replace(path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


# Shared normalization for Stages B and C

def classify(data: dict[str, Any]) -> str:
    otype, msg_role = outer_type(data), message_role(data)
    kinds = {block_type(block) for block in content_blocks(data)}
    if "__compact_session_header__" in data:
        return "COMPACT-SESSION-HEADER"
    if "__unparsed_line__" in data:
        return "MALFORMED-SOURCE"
    if "queue" in otype:
        return "QUEUED-USER"
    if kinds & {"tool-use", "tool-call"}:
        return "ASSISTANT/TOOL-CALL"
    if result_blocks(data) or msg_role == "tool" or otype in {"tool", "tool-result"}:
        return "TOOL/RESULT"
    if msg_role == "user":
        return "USER"
    if msg_role == "assistant":
        return "ASSISTANT"
    if otype != "unknown":
        return otype.upper()
    return "UNCLASSIFIED-OBJECT"


def describe_call(block: dict[str, Any]) -> tuple[str, str, str]:
    name = str(block.get("name") or block.get("tool_name") or "Tool")
    tool_id = str(block.get("id") or block.get("tool_use_id") or block.get("tool_call_id") or "")
    value = block.get("input")
    if value is None:
        value = block.get("parameters") or block.get("arguments")
    detail = ""
    if isinstance(value, dict):
        if name.casefold() == "askuserquestion" and isinstance(value.get("questions"), list):
            questions = []
            for item in value["questions"][:2]:
                if isinstance(item, dict):
                    labels = [flatten(x.get("label")) for x in item.get("options", [])[:4] if isinstance(x, dict)]
                    questions.append(flatten(item.get("question")) + (f" [{', '.join(filter(None, labels))}]" if labels else ""))
            detail = "; ".join(questions)
        else:
            for key in ("command", "file_path", "path", "query", "pattern", "url", "prompt", "title"):
                if value.get(key) not in (None, "", [], {}):
                    detail = flatten(value[key])
                    break
    else:
        detail = flatten(value)
    # Return "" rather than the EMPTY_PREVIEW sentinel for a no-argument call: callers
    # test `if detail`, and a truthy "[empty]" rendered rows reading "TaskList: [empty]".
    rendered = normalize(detail, 150)
    return name, "" if rendered == EMPTY_PREVIEW else rendered, tool_id


def build_rows(records: list[dict[str, Any]], preview_chars: int) -> list[Row]:
    rows: list[Row] = []
    tools: dict[str, tuple[str, str]] = {}

    for line_no, data in enumerate(records, 1):
        kind = classify(data)
        raw_text = ""
        tool_id = ""
        tool_name = ""
        command = ""

        if kind == "COMPACT-SESSION-HEADER":
            header = data["__compact_session_header__"]
            text = "; ".join(f"{key}={value}" for key, value in header.items())

        elif kind == "ASSISTANT/TOOL-CALL":
            parts: list[str] = []
            raw_parts: list[str] = []
            names: list[str] = []
            commands: list[str] = []

            assistant_text = text_blocks(data)
            if assistant_text:
                raw_parts.append(assistant_text)
                parts.append(excerpt(assistant_text, 90))

            calls = call_blocks(data)
            for block in calls:
                name, detail, call_id = describe_call(block)
                names.append(name)
                if detail:
                    commands.append(detail)
                    raw_parts.append(f"{name}: {detail}")
                if call_id:
                    tools[call_id] = (name, detail)
                    if not tool_id:
                        tool_id = call_id

            for block in calls[:2]:
                name, detail, _ = describe_call(block)
                parts.append(f"{name}: {detail}" if detail else name)

            if len(calls) > 2:
                parts.append(f"+{len(calls)-2} more call(s)")

            tool_name = ", ".join(dict.fromkeys(names))
            command = " ⏐ ".join(commands)
            raw_text = "\n".join(raw_parts)
            text = " ⏐ ".join(parts)

        elif kind == "TOOL/RESULT":
            parts: list[str] = []
            raw_parts: list[str] = []
            names: list[str] = []
            commands: list[str] = []
            results = result_blocks(data)

            for result_index, block in enumerate(results):
                result_id = str(
                    block.get("tool_use_id") or block.get("tool_call_id") or ""
                )
                paired_name, paired_command = tools.get(result_id, ("", ""))
                name = paired_name or str(
                    block.get("tool_name") or block.get("name") or "Tool"
                )
                payload = block.get("content")
                if payload is None:
                    payload = (
                        block.get("error")
                        or block.get("result")
                        or block.get("output")
                    )
                result_text = flatten(payload)
                # Preserve every result for classification/evidence; bound only the preview.
                raw_parts.append(result_text)
                names.append(name)
                if paired_command:
                    commands.append(paired_command)
                status = (
                    "ERROR"
                    if block.get("is_error") or block.get("error")
                    else "Result"
                )
                if result_index < 2:
                    parts.append(
                        f"{status} {name}: {excerpt(result_text, 155)}"
                    )
                if not tool_id:
                    tool_id = result_id

            if len(results) > 2:
                parts.append(f"+{len(results)-2} more result(s)")

            tool_name = ", ".join(dict.fromkeys(names))
            command = " ⏐ ".join(commands)
            raw_text = "\n".join(raw_parts)
            text = " ⏐ ".join(parts) or "[tool result]"

        else:
            raw_text = text_blocks(data) or flatten(data)
            text = excerpt(raw_text, preview_chars)

        canonical = re.sub(
            r"\s+", " ", redact(raw_text)
        ).strip().casefold()

        rows.append(
            Row(
                len(rows) + 1,
                line_no,
                timestamp(data),
                kind,
                normalize(text, preview_chars),
                canonical,
                tool_id,
                tool_name,
                raw_text,
                command,
            )
        )

    return rows


def annotate_queues(rows: list[Row]) -> None:
    users = {row.canonical: row.line for row in rows if row.kind == "USER" and row.canonical}
    seen: Counter[str] = Counter()
    for row in rows:
        if row.kind != "QUEUED-USER" or not row.canonical:
            continue
        seen[row.canonical] += 1
        link = f"canonical USER line {users[row.canonical]}" if row.canonical in users else "only preserved instruction"
        row.preview = normalize(f"{row.preview} [queued copy #{seen[row.canonical]}; {link}]")


# Stage B: intentionally compact V4-style index

def is_contentless_row(row: Row) -> bool:
    """True when a chronology row would carry no information at all.

    Plain assistant records whose only block is a zero-length thinking block (the source
    transcript emits these) preview as the empty sentinel and have no text, tool name, or
    result to contribute. They accounted for 409 of 1,399 chronology rows — 29% of the
    table — on a real session. Only the plain ASSISTANT kind qualifies: a tool call or a
    result still carries its tool identity even with an empty preview, and an empty USER
    record is itself evidence.
    """
    return (
        row.kind == "ASSISTANT"
        and row.preview == EMPTY_PREVIEW
        and not row.raw_text.strip()
        and not row.tool_name
    )


def is_landmark(row: Row) -> bool:
    return (row.kind in {"USER", "QUEUED-USER", "ASSISTANT/TOOL-CALL"}
            or (row.kind == "ASSISTANT" and row.preview != EMPTY_PREVIEW)
            or (row.kind == "TOOL/RESULT" and bool(SIGNAL_RE.search(row.preview)))
            or row.kind in {"MODE", "PERMISSION-MODE", "MALFORMED-SOURCE"})


def table(rows: Iterable[Row]) -> str:
    # "JSONL Line" claimed to address the raw transcript but carried the COMPACT line
    # number (a result at source line 48 rendered as 34), while the capsule instructs the
    # receiving model to escalate to referenced lines — so it sent readers to the wrong
    # record in the original file. Both columns are compact-relative in the current
    # pipeline; they are labelled separately rather than merged because Row.record counts
    # emitted rows and Row.line is the compact line, and nothing guarantees they cannot
    # diverge for some input shape.
    out = ["| Record | Compact Line | Time | Actor / Type | Semantic Preview |",
           "| ---: | ---: | :---: | :--- | :--- |"]
    out.extend(f"| {r.record} | {r.line} | {r.time} | {r.kind} | {markdown_cell(r.preview)} |" for r in rows)
    return "\n".join(out) + "\n"


def make_index(compact_name: str, rows: list[Row], audit: Audit) -> str:
    counts = Counter(row.kind for row in rows)
    landmarks = [row for row in rows if is_landmark(row)]
    parts = [
        f"# Transcript Navigation Index: `{compact_name}`\n\n",
        "`Compact Line` addresses the physical line in the compact transcript, not in the original Claude Code JSONL. This small index is a navigation map, not a replacement for exact evidence or the semantic capsule.\n\n",
        "## Semantic Landmarks\n\n", table(landmarks),
        "\n## Full Structural Map\n\n", table(rows),
        "\n## Parse Audit\n\n",
        f"- Compact physical lines: {len(rows)}\n",
        f"- Raw physical lines: {audit.physical_lines}\n",
        f"- Raw parsed objects: {audit.parsed_records}\n",
        f"- Raw malformed lines: {audit.malformed_lines}\n",
        f"- Records omitted by compaction policy: {audit.omitted_records}\n",
        f"- Records transformed: {audit.transformed_records}\n",
        "\n### Classification counts\n\n",
    ]
    parts.extend(f"- {kind}: {counts[kind]}\n" for kind in sorted(counts))
    return "".join(parts)


# Stage C: compact deterministic capsule, using V3's useful evidence distinction

def row_tool_names(row: Row) -> set[str]:
    return {
        name.strip().casefold()
        for name in row.tool_name.split(",")
        if name.strip()
    }


def is_mutating_call(row: Row) -> bool:
    names = row_tool_names(row)
    if names & MUTATING_TOOLS:
        return True
    return bool(names & SHELL_TOOLS and MUTATING_SHELL_RE.search(row.command))


def is_test_evidence(row: Row) -> bool:
    if row.kind != "TOOL/RESULT":
        return False
    if not (row_tool_names(row) & SHELL_TOOLS):
        return False
    return bool(
        TEST_RE.search(row.command)
        or DECISIVE_RESULT_RE.search((row.raw_text or "")[:3000])
    )


def is_interactive_answer(row: Row) -> bool:
    return (
        row.kind == "TOOL/RESULT"
        and "askuserquestion" in row_tool_names(row)
    )


def is_error_evidence(row: Row) -> bool:
    if row.kind != "TOOL/RESULT":
        return False
    if row.preview.startswith("ERROR "):
        return True
    return bool(re.search(
        r"(?i)(?:^|\n)\s*(?:error|failed|exception|traceback|denied|blocked)\b",
        (row.raw_text or "")[:2000],
    ))


def bounded(text: str, limit: int) -> str:
    text = redact(text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n[Excerpt truncated; source has {len(text)} characters.]"


def make_capsule(source_name: str, compact_name: str, checksum: str, rows: list[Row]) -> str:
    users = [r for r in rows if r.kind in {"USER", "QUEUED-USER"} and r.raw_text]
    assistant = [r for r in rows if r.kind == "ASSISTANT" and r.raw_text]
    calls = [r for r in rows if r.kind == "ASSISTANT/TOOL-CALL"]
    results = [r for r in rows if r.kind == "TOOL/RESULT"]
    decisions = [r for r in users + assistant if DECISION_RE.search(r.raw_text)]
    corrections = [r for r in users if CORRECTION_RE.search(r.raw_text)]
    open_items = [r for r in users + assistant if OPEN_RE.search(r.raw_text)]
    mutations = [r for r in calls if is_mutating_call(r)]
    diagnostics = [r for r in calls if not is_mutating_call(r)]
    diagnostic_landmarks = [
        r for r in diagnostics
        if SIGNAL_RE.search(r.preview) or TEST_RE.search(r.command)
    ]
    tests = [r for r in results if is_test_evidence(r)]
    errors = [r for r in results if is_error_evidence(r)]
    interactive_answers = [r for r in results if is_interactive_answer(r)]

    out = [
        f"# Session Capsule: `{source_name}`\n\n",
        "> Working context only. The compact JSONL is the evidentiary source of truth; use the index to navigate to exact lines.\n\n",
        "## Session identity and audit\n\n",
        f"- Source session: `{source_name}`\n- Compact source: `{compact_name}`\n",
        f"- Compact SHA-256: `{checksum}`\n- Compact lines: {len(rows)}\n",
        f"- Generator: cc_transcript.py {VERSION}\n",
        "- Generation is reproducible: no wall-clock timestamp is embedded.\n\n",
        "## Executive session summary\n\n",
    ]
    if users:
        out.append(f"- Initial objective [line {users[0].line}]: {normalize(users[0].raw_text, CAPSULE_EXCERPT)}\n")
        if len(users) > 1:
            out.append(f"- Latest human direction [line {users[-1].line}]: {normalize(users[-1].raw_text, CAPSULE_EXCERPT)}\n")
    out.append(
        f"- Observed actions: {len(mutations)} state-changing call candidates; "
        f"{len(diagnostics)} diagnostic/inspection calls; "
        f"{len(results)} tool-result records.\n"
    )
    out.append(f"- Review signals: {len(corrections)} possible corrections; {len(open_items)} possible open-state statements; {len(errors)} error-bearing results.\n\n")

    out.append("## Canonical human instructions and corrections\n\n")
    seen: set[str] = set()
    for row in users:
        key = row.canonical
        if not key or key in seen:
            continue
        seen.add(key)
        tag = "correction" if CORRECTION_RE.search(row.raw_text) else "instruction"
        out.append(f"### Line {row.line} — {tag}\n\n{fenced(bounded(row.raw_text, CAPSULE_LARGE_RECORD))}\n\n")

    out.append("## Interactive answers\n\n")
    for row in interactive_answers:
        out.append(
            f"- [AskUserQuestion answer; line {row.line}] "
            f"{normalize(row.raw_text or row.preview, CAPSULE_EXCERPT)}\n"
        )
    if not interactive_answers:
        out.append("- No AskUserQuestion answers detected.\n")

    out.append("\n## Decisions and architecture constraints\n\n")
    for row in decisions:
        out.append(f"- [{row.kind}; line {row.line}] {normalize(row.raw_text, CAPSULE_EXCERPT)}\n")
    if not decisions:
        out.append("- No rule-based decision candidates detected.\n")

    out.append("\n## State-changing actions\n\n")
    for row in mutations:
        out.append(f"- [tool intent; line {row.line}] {row.preview}\n")
    if not mutations:
        out.append("- No state-changing tool call was detected.\n")

    out.append("\n## Diagnostic and inspection actions\n\n")
    out.append(
        f"- {len(diagnostics)} diagnostic/inspection call(s) detected; "
        "routine reads and status checks are collapsed by default.\n"
    )
    for row in diagnostic_landmarks:
        out.append(
            f"- [diagnostic intent; line {row.line}] {row.preview}\n"
        )

    out.append("\n## Tests and empirical evidence\n\n")
    for row in tests:
        out.append(f"- [direct tool result; line {row.line}] {normalize(row.raw_text or row.preview, CAPSULE_EXCERPT)}\n")
    if not tests:
        out.append("- No result matched the deterministic test/verification patterns. Inspect the index before inferring completion.\n")

    out.append("\n## Errors, failures, and unresolved anomalies\n\n")
    for row in errors:
        out.append(f"- [direct tool result; line {row.line}] {normalize(row.raw_text or row.preview, CAPSULE_EXCERPT)}\n")
    if not errors:
        out.append("- No error-bearing tool result detected by the rule-based scan.\n")

    out.append("\n## Open work and next actions\n\n")
    for row in open_items:
        out.append(f"- [{row.kind}; line {row.line}] {normalize(row.raw_text, CAPSULE_EXCERPT)}\n")
    if not open_items:
        out.append("- No explicit open-state phrase detected; this does not prove the session is complete.\n")

    out.append("\n## Superseded claims and corrections\n\n")
    for row in corrections:
        out.append(f"- [user correction candidate; line {row.line}] {normalize(row.raw_text, CAPSULE_EXCERPT)}\n")
    if not corrections:
        out.append("- No user correction candidate detected.\n")

    out.append("\n## Evidence locator\n\n| Compact line | Type | Topic / preview |\n| ---: | :--- | :--- |\n")
    important = sorted(
        {
            r.line: r
            for r in users + calls + tests + errors + corrections + interactive_answers
        }.values(),
        key=lambda r: r.line,
    )
    for row in important:
        out.append(f"| {row.line} | {row.kind} | {markdown_cell(row.preview)} |\n")
    return "".join(out)


def evidence_roles(row: Row) -> list[str]:
    """Return deterministic semantic roles for one compact line."""
    roles: list[str] = []
    if is_interactive_answer(row):
        roles.append("interactive_answer")
    if row.kind in {"USER", "QUEUED-USER"} and CORRECTION_RE.search(row.raw_text):
        roles.append("correction")
    if is_error_evidence(row):
        roles.append("error")
    if is_test_evidence(row):
        roles.append("test")
    if row.kind in {"USER", "QUEUED-USER", "ASSISTANT"} and DECISION_RE.search(row.raw_text):
        roles.append("decision")
    if row.kind in {"USER", "QUEUED-USER", "ASSISTANT"} and OPEN_RE.search(row.raw_text):
        roles.append("open_work")
    if row.kind in {"MODE", "PERMISSION-MODE"} or (
        row.kind == "ASSISTANT/TOOL-CALL" and is_mutating_call(row)
    ):
        roles.append("state_change")
    if row.kind == "ASSISTANT/TOOL-CALL" and not is_mutating_call(row) and (
        SIGNAL_RE.search(row.preview) or TEST_RE.search(row.command)
    ):
        roles.append("diagnostic")
    return roles


def make_indexed_capsule(
    source_name: str,
    compact_name: str,
    checksum: str,
    rows: list[Row],
    audit: Audit,
    continuation: dict[str, Any] | None = None,
) -> str:
    """Create the primary handoff with one detailed evidence object per line."""
    chronology = [row for row in rows if row.kind in {
        "USER", "QUEUED-USER", "ASSISTANT", "ASSISTANT/TOOL-CALL", "TOOL/RESULT",
    } and not is_contentless_row(row)]
    evidence_rows = [row for row in rows if (
        row.kind in {"USER", "QUEUED-USER"}
        or evidence_roles(row)
    )]
    evidence_rows = sorted({row.line: row for row in evidence_rows}.values(), key=lambda row: row.line)
    evidence_ids = {row.line: f"E{index}" for index, row in enumerate(evidence_rows, 1)}
    roles_by_line = {row.line: evidence_roles(row) for row in evidence_rows}

    out = [
        f"# Indexed Session Capsule: `{source_name}`\n\n",
        "> Primary working context. The accompanying compact JSONL is archival/escalation evidence.\n\n",
        "## Instructions for the receiving LLM\n\n",
        "Treat this indexed capsule as the primary working context. Do not request or read the accompanying compact JSONL unless this file lacks enough detail to reconstruct a consequential event.\n\n",
        "1. Read the indexed chronology to establish event order.\n",
        "2. Reconstruct objectives, instructions, corrections, decisions, actions, tests, failures, and open work from the semantic sections.\n",
        "3. Reconcile repeated references to one evidence ID instead of treating them as independent evidence.\n",
        "4. Prefer direct tool-result evidence and later evidence-backed findings over earlier plans or claims.\n",
        "5. Treat rule-detected roles as candidates rather than guarantees.\n",
        "6. Do not infer completion merely because no error or open-work phrase was found.\n",
        "7. Escalate to referenced compact JSONL lines only when this capsule is insufficient.\n",
        "8. If compact evidence is unavailable, state the remaining uncertainty rather than inventing detail.\n\n",
        "## Identity and provenance\n\n",
        f"- Source session: `{source_name}`\n",
        f"- Compact evidence: `{compact_name}`\n",
        f"- Compact SHA-256: `{checksum}`\n",
        f"- Compact lines: {len(rows)}\n",
        f"- Generator: cc_transcript.py {VERSION}\n\n",
    ]
    if continuation:
        out.extend([
            "## Continuation identity\n\n",
            f"- Part: {continuation['part']}\n",
            f"- Prior compact lines: {continuation['prior_lines']}\n",
            f"- First new global compact line: {continuation['first_global_line']}\n",
            f"- Prior compact SHA-256: `{continuation['prior_sha256']}`\n\n",
        ])

    out.extend(["## Indexed chronology\n\n", table(chronology), "\n"])
    out.append("## Canonical human instructions\n\n")
    human_refs = [evidence_ids[row.line] for row in evidence_rows if row.kind in {"USER", "QUEUED-USER"}]
    out.extend(f"- {evidence_id}\n" for evidence_id in human_refs)
    if not human_refs:
        out.append("- No human instruction detected.\n")

    out.append("\n## Unique evidence excerpts\n\n")
    for row in evidence_rows:
        evidence_id = evidence_ids[row.line]
        roles = roles_by_line[row.line]
        role_text = ", ".join(roles) if roles else "human_instruction"
        out.append(f"### {evidence_id} — compact line {row.line} — {role_text}\n\n")
        payload = row.raw_text or row.preview
        out.append(fenced(bounded(payload, CAPSULE_LARGE_RECORD)) + "\n\n")

    sections = [
        ("Decisions and constraints", "decision"),
        ("State-changing actions", "state_change"),
        ("Tests and empirical results", "test"),
        ("Errors and anomalies", "error"),
        ("Open work", "open_work"),
        ("Corrections and superseded claims", "correction"),
        ("Interactive answers", "interactive_answer"),
        ("Diagnostic landmarks", "diagnostic"),
    ]
    for heading, role in sections:
        out.append(f"## {heading}\n\n")
        references = [evidence_ids[line] for line in sorted(evidence_ids) if role in roles_by_line[line]]
        if references:
            out.extend(f"- See {evidence_id}.\n" for evidence_id in references)
        else:
            out.append("- No rule-based candidate detected; absence does not prove none exists.\n")
        out.append("\n")

    out.append("## Compact evidence map\n\n")
    out.append("| Evidence | Compact line | Type | Roles |\n| :--- | ---: | :--- | :--- |\n")
    for row in evidence_rows:
        roles = ", ".join(roles_by_line[row.line]) or "human_instruction"
        out.append(f"| {evidence_ids[row.line]} | {row.line} | {row.kind} | {roles} |\n")

    out.extend([
        "\n## Compaction audit\n\n",
        f"- Raw physical lines: {audit.physical_lines}\n",
        f"- Retained source records: {audit.retained_source_records}\n",
        f"- Omitted records: {audit.omitted_records}\n",
        f"- Malformed lines: {audit.malformed_lines}\n",
        f"- Non-object JSON values: {audit.non_object_lines}\n",
        f"- Hoisted-field conflicts: {len(audit.hoisted_conflicts)}\n",
    ])
    for name, count in sorted(audit.transformations.items()):
        out.append(f"- Transformation `{name}`: {count}\n")
    return "".join(out)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read existing compact transcript {path}: {error}") from error
    if not records or not isinstance(records[0], dict) or "__compact_session_header__" not in records[0]:
        raise ValueError(f"Existing file is not a recognized compact transcript: {path}")
    return records


def record_bytes(record: dict[str, Any]) -> bytes:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def header_identity(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not records or not isinstance(records[0], dict):
        return {}
    header = records[0].get("__compact_session_header__")
    if not isinstance(header, dict):
        return {}
    return {name: header.get(name) for name in ("sessionId", "__source_name__") if header.get(name)}


def is_legacy_bundle(records: Sequence[dict[str, Any]]) -> bool:
    if not records or not isinstance(records[0], dict):
        return False
    header = records[0].get("__compact_session_header__")
    if not isinstance(header, dict):
        return False
    generator = str(header.get("__generator__", ""))
    bundle_format = header.get("__bundle_format__")
    # v0.3 and earlier had no explicit format marker. Do not apply legacy
    # normalization to a bundle that declares the current format.
    return bundle_format in (None, 1) and not re.search(r"\b0\.[4-9]\.\d+\b", generator)


def identities_compatible(existing: Sequence[dict[str, Any]], new: Sequence[dict[str, Any]]) -> bool:
    old_identity = header_identity(existing)
    new_identity = header_identity(new)
    old_session = old_identity.get("sessionId")
    new_session = new_identity.get("sessionId")
    if old_session and new_session and old_session != new_session:
        return False
    old_source = old_identity.get("__source_name__")
    new_source = new_identity.get("__source_name__")
    if old_source and new_source and old_source != new_source:
        return False
    # At least one stable identity must agree; a filename-only legacy header is
    # acceptable because pre-v0.4 headers normally carry sessionId.
    return bool((old_session and old_session == new_session) or
                (old_source and old_source == new_source))


def legacy_project(record: dict[str, Any]) -> dict[str, Any]:
    """Project a new record to the exact field policy used before v0.4.

    Legacy compactors removed every hoisted field from every retained body
    record, even if a value changed later. v0.4 preserves conflicting values.
    No other mismatch is normalized here.
    """
    projected = copy.deepcopy(record)
    for name in HOISTED_FIELDS:
        projected.pop(name, None)
    return projected


FORMAT2_DUPLICATE_PAYLOAD_MARKER = (
    "<DUPLICATE OF message.content - stripped by cc_transcript.py>"
)


def restore_format2_tool_mirrors(record: dict[str, Any]) -> None:
    """Restore only documented format-2 same-record mirror markers."""
    result = record.get("toolUseResult")
    if not isinstance(result, dict):
        return
    for block in result_blocks(record):
        content = block.get("content")
        block_text: str | None = content if isinstance(content, str) else None
        if isinstance(content, list):
            texts = [
                item.get("text") for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ]
            if len(texts) == 1:
                block_text = texts[0]
        if block_text is None:
            continue
        file_info = result.get("file")
        if (
            isinstance(file_info, dict)
            and file_info.get("content")
            == FORMAT2_DUPLICATE_PAYLOAD_MARKER
        ):
            file_info["content"] = block_text
        elif result.get("stdout") == FORMAT2_DUPLICATE_PAYLOAD_MARKER:
            result["stdout"] = block_text


def format2_state_stream_key(
    record: dict[str, Any],
    session_identity: str,
) -> tuple[tuple[str, str], str] | None:
    """Recognize raw or exactly hoisted format-2 state records."""
    state = state_stream_key(record)
    if state is not None:
        return state

    kind = record.get("type")
    if kind == "mode":
        expected = {"type", "mode"}
        value = record.get("mode")
    elif kind == "permission-mode":
        expected = {"type", "permissionMode"}
        value = record.get("permissionMode")
    else:
        return None

    if set(record) != expected or not isinstance(value, str):
        return None
    return (kind, session_identity), value


def project_format2_body(
    existing: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply only documented format-3 transformations to a format-2 body."""
    projected: list[dict[str, Any]] = []
    state_values: dict[tuple[str, str], str] = {}
    binary_seen: set[tuple[str, str]] = set()
    payload_seen: dict[tuple[int, str], tuple[int, str]] = {}
    audit = Audit()
    old_header = existing[0].get("__compact_session_header__", {})
    session_identity = ""
    if isinstance(old_header, dict):
        session_identity = str(
            old_header.get("sessionId")
            or old_header.get("__source_name__")
            or "format2-bundle"
        )

    for source in existing[1:]:
        record = copy.deepcopy(source)
        kind = outer_type(record)
        if kind in {"ai-title", "last-prompt"}:
            continue
        state = format2_state_stream_key(record, session_identity)
        if state is not None:
            stream, value = state
            if state_values.get(stream) == value:
                continue
            state_values[stream] = value
        restore_format2_tool_mirrors(record)
        omit_binary_payloads(record, audit, binary_seen)
        compact_line = len(projected) + 2
        intern_exact_payloads(record, compact_line, payload_seen, audit)
        dedupe_tool_result(record, audit)
        projected.append(record)
    return projected


def compare_existing(existing: Sequence[dict[str, Any]], new: Sequence[dict[str, Any]]) -> tuple[str, int]:
    """Classify existing evidence using literal or narrowly versioned checks."""
    old_header = existing[0].get("__compact_session_header__", {}) if existing else {}
    new_header = new[0].get("__compact_session_header__", {}) if new else {}
    old_format = old_header.get("__bundle_format__") if isinstance(old_header, dict) else None

    if old_format == 2 and identities_compatible(existing, new):
        projected = project_format2_body(existing)
        new_body = list(new[1:])
        common = min(len(projected), len(new_body))
        if all(record_bytes(projected[index]) == record_bytes(new_body[index]) for index in range(common)):
            if len(new_body) > len(projected):
                return "format2-extension", len(projected)
            if len(new_body) == len(projected):
                # Projected-body equivalence is required regardless of the
                # source hash claimed by the older header.
                return "format2-migration", len(projected)
            return "legacy-truncation", len(projected)
        return "different", len(projected)

    old_body = list(existing[1:])
    new_body = list(new[1:])
    common = min(len(old_body), len(new_body))
    if all(record_bytes(old_body[i]) == record_bytes(new_body[i]) for i in range(common)):
        if len(new_body) > len(old_body):
            return "extension", len(old_body)
        if len(new_body) == len(old_body):
            return "identical", len(old_body)
        return "truncation", len(old_body)

    if is_legacy_bundle(existing) and identities_compatible(existing, new):
        if len(new_body) < len(old_body):
            return "legacy-truncation", len(old_body)
        if all(record_bytes(old_body[i]) == record_bytes(legacy_project(new_body[i]))
               for i in range(len(old_body))):
            return "legacy-migration", len(old_body)
    return "different", len(old_body)


def materialize_continuation_payloads(
    full_records: Sequence[dict[str, Any]],
    tail_records: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    retained: dict[tuple[int, int, str], str] = {}
    binary_descriptors: dict[tuple[str, str], dict[str, Any]] = {}
    for record in full_records:
        for kind, metadata in iter_binary_metadata(record):
            if kind != "descriptor":
                continue
            identity = (metadata.get("sha256_of"), metadata.get("sha256"))
            if all(isinstance(item, str) and item for item in identity):
                binary_descriptors.setdefault(identity, copy.deepcopy(metadata))

    for line_number, record in enumerate(full_records, 1):
        values = list(protected_tool_input_payloads(record))
        values.extend(
            value for container, key in result_payload_slots(record)
            if isinstance((value := container.get(key)), str)
            and len(value) >= PAYLOAD_INTERN_THRESHOLD
        )
        for value in values:
            retained.setdefault(
                (line_number, len(value), payload_digest(value)), value
            )

    def resolve(value: Any) -> Any:
        if isinstance(value, dict):
            binary_mirror = value.get("__omitted_binary_mirror__")
            if isinstance(binary_mirror, dict) and len(value) == 1:
                identity = (
                    binary_mirror.get("sha256_of"),
                    binary_mirror.get("sha256"),
                )
                descriptor = binary_descriptors.get(identity)
                if descriptor is None:
                    raise RuntimeError(
                        f"Cannot materialize continuation binary mirror: {identity!r}"
                    )
                return {"__omitted_binary__": copy.deepcopy(descriptor)}

            marker = value.get("__duplicate_payload__")
            if isinstance(marker, dict) and len(value) == 1:
                identity = (
                    marker.get("first_compact_line"),
                    marker.get("characters"),
                    marker.get("sha256"),
                )
                payload = retained.get(identity)
                if payload is None:
                    raise RuntimeError(
                        f"Cannot materialize continuation payload reference: {identity!r}"
                    )
                return payload
            return {key: resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child) for child in value]
        return value

    return [resolve(copy.deepcopy(record)) for record in tail_records]


def next_part_number(output_dir: Path, stem: str) -> int:
    pattern = re.compile(rf"^{re.escape(stem)}\.part(\d+)\.compact\.jsonl\.txt$")
    numbers = [int(match.group(1)) for path in output_dir.glob(f"{stem}.part*.compact.jsonl.txt")
               if (match := pattern.match(path.name))]
    return max(numbers, default=1) + 1


def snapshot_input(source: Path, attempts: int = 3) -> tuple[Path, dict[str, Any]]:
    """Take a stable byte snapshot so a live transcript cannot change mid-run."""
    fd, temp_name = tempfile.mkstemp(prefix=".compact-session-source-", suffix=".jsonl")
    os.close(fd)
    target = Path(temp_name)
    try:
        for attempt in range(1, attempts + 1):
            before = source.stat()
            with source.open("rb") as src, target.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
                return target, {
                    "size": after.st_size,
                    "mtime_ns": after.st_mtime_ns,
                    "attempt": attempt,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
        raise RuntimeError(f"Source kept changing while being snapshotted: {source}")
    except Exception:
        target.unlink(missing_ok=True)
        raise


def transactional_write(
    files: dict[Path, str],
    replace_allowed: set[Path],
    retire_paths: set[Path] | None = None,
    verifier: Callable[[dict[Path, Path]], None] | None = None,
) -> None:
    """Stage, verify, commit, verify again, and restore every prior artifact on failure."""
    if not files:
        return
    retire_paths = retire_paths or set()
    for path in files:
        if path.exists() and path not in replace_allowed:
            raise FileExistsError(f"Refusing to replace unapproved output: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in retire_paths:
        if path in files:
            raise ValueError(f"Cannot retire a current output: {path}")

    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for path, content in files.items():
            fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            staged[path] = Path(name)

        if verifier is not None:
            verifier(staged)

        for path in list(files) + sorted(retire_paths):
            if not path.exists():
                continue
            backup = path.with_name(f".{path.name}.rollback-{os.getpid()}")
            if backup.exists():
                raise FileExistsError(f"Rollback path already exists: {backup}")
            path.replace(backup)
            backups[path] = backup

        for path in files:
            staged[path].replace(path)
            committed.append(path)

        if verifier is not None:
            verifier({path: path for path in files})

        for backup in backups.values():
            backup.unlink(missing_ok=True)
    except Exception:
        for path in committed:
            path.unlink(missing_ok=True)
        for path, backup in backups.items():
            if backup.exists():
                backup.replace(path)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def base_name(path: Path) -> str:
    name = path.name
    for suffix in (".compact.jsonl.txt", ".compact.jsonl", ".jsonl.txt", ".jsonl"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return path.stem


def list_sessions() -> list[Path]:
    files = [Path(item) for item in glob.glob(str(CLAUDE_PROJECTS_DIR / "**" / "*.jsonl"), recursive=True)]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def current_hint(path: Path) -> bool:
    return any(value and value in path.stem for value in
               (os.environ.get("CLAUDE_SESSION_ID"), os.environ.get("CLAUDE_CODE_SESSION_ID")))


def detect_active_sessions(files: Sequence[Path]) -> dict[Path, str]:
    """Merge instantaneous, identity, and recent-activity session signals.

    An open file descriptor is an instantaneous signal, not a complete inventory:
    idle Claude Code sessions can close their transcript between writes. Recent
    activity is therefore retained even when another transcript is open now.
    """
    resolved = {path.resolve(): path for path in files}
    active: dict[Path, str] = {}
    candidates = [path for path in files[:100] if "subagents" not in path.parts]
    if sys.platform == "darwin" and candidates:
        try:
            result = subprocess.run(
                ["lsof", "-Fn", *(str(path) for path in candidates)],
                text=True, capture_output=True, timeout=8, check=False,
            )
            for line in result.stdout.splitlines():
                if line.startswith("n"):
                    opened = Path(line[1:]).resolve()
                    if opened in resolved:
                        active[resolved[opened]] = "open-file"
        except (OSError, subprocess.SubprocessError):
            pass

    for path in files:
        if current_hint(path):
            active.setdefault(path, "environment-id")

    project_dir = find_current_project_dir()
    direct = (
        [path for path in files if path.parent.resolve() == project_dir.resolve()]
        if project_dir is not None else []
    )
    now = datetime.now().timestamp()
    recent = [
        path for path in direct
        if now - path.stat().st_mtime <= 2 * 60 * 60
        and path.stat().st_size >= 64 * 1024
    ]
    for path in recent:
        active.setdefault(path, "recent-activity")

    if not active and direct:
        newest = max(path.stat().st_mtime for path in direct)
        cohort = [path for path in direct if newest - path.stat().st_mtime <= 45 * 60]
        if cohort:
            largest = max(cohort, key=lambda path: path.stat().st_size)
            active[largest] = "recent-size-fallback"
    return active


def _title_text(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _record_matches_session(record: dict[str, Any], session_id: str) -> bool:
    value = record.get("sessionId")
    return value is None or value == session_id


def _meaningful_prompt_text(text: str) -> str:
    """Remove Claude Code command-envelope elements from a title fallback."""
    cleaned = re.sub(
        r"(?is)<command-(?:name|message|args)\b[^>]*>.*?</command-(?:name|message|args)>",
        " ",
        text,
    )
    cleaned = re.sub(
        r"(?is)<command-(?:name|message|args)\b[^>]*/>",
        " ",
        cleaned,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def resolve_picker_metadata(
    path: Path,
    limit: int = SESSION_TITLE_CHARS,
) -> PickerMetadata:
    """Resolve picker title and latest valid CWD in one transcript scan."""
    custom_title = ""
    ai_title = ""
    summary = ""
    first_prompt = ""
    latest_cwd = ""
    session_id = path.stem

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (
                    not isinstance(record, dict)
                    or not _record_matches_session(record, session_id)
                ):
                    continue

                cwd = record.get("cwd")
                if isinstance(cwd, str) and cwd.strip():
                    latest_cwd = cwd.strip()

                kind = outer_type(record)
                if kind == "custom-title":
                    custom_title = (
                        _title_text(record, "customTitle", "custom_title")
                        or custom_title
                    )
                elif kind == "ai-title":
                    ai_title = (
                        _title_text(record, "aiTitle", "ai_title")
                        or ai_title
                    )
                elif kind == "summary" or "summary" in record:
                    summary = _title_text(record, "summary") or summary

                if (
                    not first_prompt
                    and message_role(record) == "user"
                    and not record.get("isMeta")
                    and not result_blocks(record)
                ):
                    candidate = _meaningful_prompt_text(text_blocks(record))
                    if candidate:
                        first_prompt = candidate
    except OSError:
        pass

    label = SessionLabel("Untitled session", "fallback")
    for source, value in (
        ("custom title", custom_title),
        ("AI title", ai_title),
        ("summary", summary),
        ("first prompt", first_prompt),
    ):
        if value.strip():
            label = SessionLabel(normalize(value, limit), source)
            break

    return PickerMetadata(label=label, cwd=latest_cwd)


def resolve_session_label(
    path: Path,
    limit: int = SESSION_TITLE_CHARS,
) -> SessionLabel:
    """Resolve the human-readable label used by the interactive picker."""
    return resolve_picker_metadata(path, limit).label


def picker_project_name(cwd: str) -> str:
    """Return a concise project name, omitting the user's home directory."""
    if not cwd:
        return ""
    expanded = Path(cwd).expanduser()
    if os.path.normpath(str(expanded)) == os.path.normpath(str(Path.home())):
        return ""
    return expanded.name


def format_picker_size(size: int) -> str:
    """Format picker sizes compactly without repeating per-value labels."""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):,.1f} MB"
    if size >= 1024:
        return f"{size / 1024:,.0f} KB"
    return f"{size} B"


ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
PICKER_COLORS = {"green": "32", "yellow": "33", "red": "31"}


def strip_terminal_escape_sequences(text: str) -> tuple[str, bool]:
    """Remove terminal control sequences before parsing picker input."""
    cleaned = ANSI_ESCAPE_RE.sub("", text)
    return cleaned, cleaned != text


def parse_more_command(text: str) -> int | None:
    """Return ten-row batches requested by m, mmmmm, or m5."""
    value = text.strip().lower()
    if re.fullmatch(r"m+", value):
        return len(value)
    match = re.fullmatch(r"m(\d+)", value)
    if match and int(match.group(1)) > 0:
        return int(match.group(1))
    return None


def picker_color_enabled(stream: Any = None) -> bool:
    stream = sys.stdout if stream is None else stream
    return bool(
        os.environ.get("NO_COLOR") is None
        and hasattr(stream, "isatty")
        and stream.isatty()
    )


def picker_terminal_control_enabled(stream: Any = None) -> bool:
    stream = sys.stdout if stream is None else stream
    return bool(hasattr(stream, "isatty") and stream.isatty())


def style_picker_status(text: str, color: str, enabled: bool | None = None) -> str:
    if enabled is None:
        enabled = picker_color_enabled()
    if not enabled or color not in PICKER_COLORS:
        return text
    return f"\x1b[1;{PICKER_COLORS[color]}m{text}\x1b[0m"


def picker_bundle_status(
    transcript_path: Path,
    output_dir: Path,
) -> tuple[str, str]:
    """Return an honest artifact/source status for picker prioritization."""
    stem = base_name(transcript_path)
    output_dir = output_dir.expanduser().resolve()
    compact = output_dir / f"{stem}.compact.jsonl.txt"
    indexed = output_dir / f"{stem}.indexed_capsule.md"
    if not compact.is_file() and not indexed.is_file():
        return "", ""
    if compact.is_file() != indexed.is_file():
        return "partial artifacts", "red"
    try:
        header = read_jsonl(compact)[0]["__compact_session_header__"]
        current_size = transcript_path.stat().st_size
        snapshot_size = header.get("__snapshot_size__")
        source_sha = header.get("__source_sha256__")
        if isinstance(snapshot_size, int):
            if current_size > snapshot_size:
                return "new tail to verify", "yellow"
            if current_size < snapshot_size:
                return "source shrank", "red"
        if isinstance(source_sha, str) and source_sha:
            actual_sha = hashlib.sha256(transcript_path.read_bytes()).hexdigest()
            if actual_sha == source_sha:
                return "current", "green"
            return "source changed", "red"
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return "artifacts unreadable", "red"
    return "artifacts present", "green"


def erase_picker_controls(line_count: int) -> None:
    """Clear the prior controls and prompt so more rows insert above them."""
    if not picker_terminal_control_enabled() or line_count < 1:
        return
    for _ in range(line_count):
        sys.stdout.write("\x1b[1A\x1b[2K")
    sys.stdout.flush()



def picker_size_summary(
    transcript_path: Path,
    output_dir: Path,
) -> str:
    """Show transcript size plus existing canonical artifact sizes."""
    transcript_size = format_picker_size(transcript_path.stat().st_size)
    stem = base_name(transcript_path)
    output_dir = output_dir.expanduser().resolve()
    compact_path = output_dir / f"{stem}.compact.jsonl.txt"
    indexed_path = output_dir / f"{stem}.indexed_capsule.md"

    if not compact_path.is_file() and not indexed_path.is_file():
        return transcript_size

    compact_size = (
        format_picker_size(compact_path.stat().st_size)
        if compact_path.is_file()
        else "—"
    )
    indexed_size = (
        format_picker_size(indexed_path.stat().st_size)
        if indexed_path.is_file()
        else "—"
    )
    return f"{transcript_size} [{compact_size} | {indexed_size}]"


def parse_selection(text: str, maximum: int) -> list[int]:
    """Parse lists/ranges such as '1, 2 3', '1-3', or 'session 1 to 3'."""
    cleaned = text.strip().lower()
    cleaned = re.sub(r"\bsessions?\b", " ", cleaned)
    cleaned = re.sub(r"\bto\b", "-", cleaned)
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    cleaned = cleaned.replace(",", " ")
    tokens = cleaned.split()
    if not tokens:
        raise ValueError("empty selection")
    selected: list[int] = []
    for token in tokens:
        if re.fullmatch(r"\d+", token):
            values = [int(token)]
        else:
            match = re.fullmatch(r"(\d+)-(\d+)", token)
            if not match:
                raise ValueError(f"unrecognized selection token: {token!r}")
            first, last = map(int, match.groups())
            step = 1 if last >= first else -1
            values = list(range(first, last + step, step))
        for value in values:
            if not 1 <= value <= maximum:
                raise ValueError(f"session number {value} is outside 1-{maximum}")
            if value not in selected:
                selected.append(value)
    return selected


def picker(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[Path] | None:
    files = list_sessions()
    if not files:
        print(f"No session files found under {CLAUDE_PROJECTS_DIR}")
        return None
    active = detect_active_sessions(files)
    metadata_cache: dict[Path, PickerMetadata] = {}
    size_cache: dict[Path, str] = {}
    status_cache: dict[Path, tuple[str, str]] = {}
    shown_count = min(5, len(files))
    printed_count = 0
    active_indices = [
        index for index, session_path in enumerate(files, 1)
        if session_path in active
    ]

    print("\nRecent Claude Code sessions:\n")
    print("Sizes: transcript [compact JSONL | indexed capsule]\n")
    while True:
        for index in range(printed_count + 1, shown_count + 1):
            session_path = files[index - 1]
            method = active.get(session_path)
            stat_result = session_path.stat()
            metadata_cache.setdefault(session_path, resolve_picker_metadata(session_path))
            metadata = metadata_cache[session_path]
            activity_text = {
                "open-file": "writing now",
                "environment-id": "session ID match",
                "recent-activity": "recently active",
                "recent-size-fallback": "recent fallback",
            }.get(method, method or "")
            activity_marker = f"  <-- {activity_text}" if activity_text else ""
            size_cache.setdefault(
                session_path, picker_size_summary(session_path, output_dir)
            )
            status_cache.setdefault(
                session_path, picker_bundle_status(session_path, output_dir)
            )
            status_text, status_color = status_cache[session_path]
            status_marker = (
                "  " + style_picker_status(f"[{status_text}]", status_color)
                if status_text else ""
            )
            project = picker_project_name(metadata.cwd)
            project_text = f" · project: {project}" if project else ""
            print(f"  [{index}] {metadata.label.text}{activity_marker}{status_marker}")
            print(
                f"      ID: {session_path.stem} · {size_cache[session_path]} · "
                f"{datetime.fromtimestamp(stat_result.st_mtime):%Y-%m-%d %H:%M}"
                f"{project_text}"
            )
            print()
        printed_count = shown_count

        remaining = len(files) - shown_count
        if remaining:
            print(
                f"  [m] show {min(10, remaining)} more "
                f"([m5] five batches, [mmmmm] same)"
            )
        print(f"  Any session number 1-{len(files)} is selectable.")
        print("  [q] quit")
        default_indices = active_indices or [1]
        default_text = ",".join(map(str, default_indices))
        default_note = "active candidates" if active_indices else "most recent"
        raw_choice = input(
            f"\nSelect one or more sessions "
            f"[default: {default_text}, {default_note}]: "
        ).strip().lower()
        choice, stripped_escape = strip_terminal_escape_sequences(raw_choice)
        choice = choice.strip()
        if stripped_escape and not choice:
            print("Ignored a navigation key; enter q, m, or session numbers.")
            continue
        if not choice:
            return [files[index - 1] for index in default_indices]
        if choice in {"q", "quit", "exit"}:
            return None
        more_batches = parse_more_command(choice)
        if more_batches is not None and remaining:
            erase_picker_controls(4)
            shown_count = min(shown_count + 10 * more_batches, len(files))
            continue
        try:
            indices = parse_selection(choice, len(files))
            return [files[index - 1] for index in indices]
        except ValueError as error:
            print(f"Not a valid selection: {error}")



def prompt_existing_mode(
    relationship: str,
    existing_parts: Sequence[Path] = (),
) -> str | None:
    print(f"\nAn existing bundle was detected ({relationship}).")
    if relationship == "extension":
        print("  [c] write only the verified new tail as a numbered continuation (default)")
        print("  [a] rewrite the complete canonical bundle from the full raw transcript")
        if existing_parts:
            names = ", ".join(path.name for path in existing_parts[-3:])
            print(
                "  CAUTION: numbered parts already exist. A new part is relative "
                "to the canonical complete bundle and can overlap them."
            )
            print(f"  Existing part files include: {names}")
        print("  Amend rewrites only the canonical pair; numbered parts remain unchanged.")
    elif relationship in {"legacy-migration", "format2-migration", "format2-extension"}:
        print("  [a] migrate and replace the verified older-format bundle (default)")
    print("  [r] refuse replacement and skip this session")
    print("  [q] stop the current export batch")
    while True:
        default = "continuation" if relationship == "extension" else "amend"
        choice = input(
            f"Choose existing-bundle behavior [default: {default[0]}]: "
        ).strip().lower()
        if choice == "":
            return default
        if choice in {"a", "amend"}:
            return "amend"
        if relationship == "extension" and choice in {"c", "continuation"}:
            return "continuation"
        if choice in {"r", "refuse"}:
            return "refuse"
        if choice in {"q", "quit"}:
            return None
        print("Not a valid choice, try again.")



def iter_binary_metadata(value: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        descriptor = value.get("__omitted_binary__")
        if isinstance(descriptor, dict):
            yield "descriptor", descriptor
        mirror = value.get("__omitted_binary_mirror__")
        if isinstance(mirror, dict):
            yield "mirror", mirror
        for child in value.values():
            yield from iter_binary_metadata(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_binary_metadata(child)


def verify_binary_policy(records: Sequence[dict[str, Any]]) -> None:
    header = records[0].get("__compact_session_header__", {})
    policy = header.get("__omission_policy__", {}) if isinstance(header, dict) else {}
    if not policy.get("base64_omitted"):
        return

    for line_number, record in enumerate(records[1:], 2):
        if binary_occurrences(record):
            raise RuntimeError(
                f"Recognized raw base64 remains at compact line {line_number}"
            )

    descriptors: set[tuple[str, str]] = set()
    mirrors: list[tuple[int, tuple[str, str]]] = []
    for line_number, record in enumerate(records[1:], 2):
        for kind, metadata in iter_binary_metadata(record):
            identity = (
                metadata.get("sha256_of"),
                metadata.get("sha256"),
            )
            if not all(isinstance(item, str) and item for item in identity):
                raise RuntimeError(
                    f"Invalid binary metadata at compact line {line_number}"
                )
            if kind == "descriptor":
                descriptors.add(identity)
            else:
                mirrors.append((line_number, identity))

    for line_number, identity in mirrors:
        if identity not in descriptors:
            raise RuntimeError(
                f"Unresolved binary mirror at compact line {line_number}"
            )


def iter_same_record_mirror_markers(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        marker = value.get("__duplicate_payload_mirror__")
        if isinstance(marker, dict):
            yield marker
        for child in value.values():
            yield from iter_same_record_mirror_markers(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_same_record_mirror_markers(child)


def strings_in(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        if "__duplicate_payload_mirror__" in value or "__duplicate_payload__" in value:
            return
        for child in value.values():
            yield from strings_in(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings_in(child)


def verify_same_record_mirrors(records: Sequence[dict[str, Any]]) -> None:
    for line_number, record in enumerate(records, 1):
        identities = {
            (len(value), payload_digest(value)) for value in strings_in(record)
        }
        for marker in iter_same_record_mirror_markers(record):
            characters = marker.get("characters")
            digest = marker.get("sha256")
            if (
                not isinstance(characters, int)
                or not isinstance(digest, str)
                or (characters, digest) not in identities
            ):
                raise RuntimeError(
                    f"Unresolved same-record payload mirror at compact line {line_number}"
                )


def iter_duplicate_payload_markers(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        marker = value.get("__duplicate_payload__")
        if isinstance(marker, dict):
            yield marker
        for child in value.values():
            yield from iter_duplicate_payload_markers(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_duplicate_payload_markers(child)


def verify_duplicate_payload_references(records: Sequence[dict[str, Any]]) -> None:
    """Require every typed payload reference to resolve to one earlier retained value."""
    seen: dict[tuple[int, str], tuple[int, str]] = {}
    for line_number, record in enumerate(records, 1):
        for marker in iter_duplicate_payload_markers(record):
            first_line = marker.get("first_compact_line")
            characters = marker.get("characters")
            digest = marker.get("sha256")
            if (
                not isinstance(first_line, int)
                or not isinstance(characters, int)
                or not isinstance(digest, str)
                or first_line >= line_number
            ):
                raise RuntimeError(
                    f"Invalid duplicate payload reference at compact line {line_number}"
                )
            resolved = seen.get((characters, digest))
            if resolved is None or resolved[0] != first_line:
                raise RuntimeError(
                    f"Unresolved duplicate payload reference at compact line {line_number}"
                )

        retained: list[str] = protected_tool_input_payloads(record)
        retained.extend(
            value for container, key in result_payload_slots(record)
            if isinstance((value := container.get(key)), str)
            and len(value) >= PAYLOAD_INTERN_THRESHOLD
        )
        for value in retained:
            identity = (len(value), payload_digest(value))
            seen.setdefault(identity, (line_number, value))


def verify(
    compact_path: Path,
    indexed_capsule_path: Path,
    expected_lines: int,
    expected_checksum: str,
) -> None:
    parsed = read_jsonl(compact_path)
    if len(parsed) != expected_lines:
        raise RuntimeError(f"Compact verification failed: expected {expected_lines} lines, found {len(parsed)}")
    written_checksum = hashlib.sha256(compact_path.read_bytes()).hexdigest()
    if written_checksum != expected_checksum:
        raise RuntimeError(f"Compact checksum mismatch: expected {expected_checksum}, found {written_checksum}")
    verify_binary_policy(parsed)
    verify_same_record_mirrors(parsed)
    verify_duplicate_payload_references(parsed)
    header = parsed[0]["__compact_session_header__"]
    if header.get("__bundle_format__") != BUNDLE_FORMAT:
        raise RuntimeError("Compact verification failed: wrong bundle format")
    navigation = header.get("__navigation__", {})
    for value in navigation.values():
        references = value if isinstance(value, list) else [value]
        for referenced in references:
            if not isinstance(referenced, int) or not 1 <= referenced <= expected_lines:
                raise RuntimeError(f"Invalid navigation reference: {referenced!r}")
    if not indexed_capsule_path.is_file() or indexed_capsule_path.stat().st_size == 0:
        raise RuntimeError(f"Output verification failed: {indexed_capsule_path}")
    rendered = indexed_capsule_path.read_text(encoding="utf-8")
    if expected_checksum not in rendered:
        raise RuntimeError("Indexed capsule does not reference the compact checksum")
    evidence_ids = re.findall(r"(?m)^### (E\d+) — compact line (\d+) —", rendered)
    if len({item[0] for item in evidence_ids}) != len(evidence_ids):
        raise RuntimeError("Duplicate evidence ID in indexed capsule")
    for _, line_text in evidence_ids:
        referenced = int(line_text)
        if not 1 <= referenced <= expected_lines:
            raise RuntimeError(f"Out-of-range evidence line: {referenced}")


def find_current_project_dir() -> Path | None:
    """The project transcript dir for the CWD (path with '/' replaced by '-')."""
    candidate = CLAUDE_PROJECTS_DIR / str(Path.cwd()).replace("/", "-")
    return candidate if candidate.is_dir() else None


def is_likely_current_session(path: Path) -> bool:
    for env_name in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        val = os.environ.get(env_name)
        if val and val in path.stem:
            return True
    return False


def find_current_session_file() -> tuple[Path | None, str | None]:
    """Non-interactive resolution of "the session I'm running in right now".

    Ported from the pre-bundle compact_session.py (2026-08-05) so --current works here.

    Some harness variants set CLAUDE_CODE_SESSION_ID to an orchestration/task id that is
    NOT the transcript's own filename (confirmed 2026-07-17), so try the env-var match
    first, then fall back within the current project dir only -- deliberately
    NON-recursive, so a nested subagents/*.jsonl is never mistaken for the main session.

    Returns (path, method) with method "env" or "mtime-fallback", else (None, None).
    """
    project_dir = find_current_project_dir()
    if project_dir is None:
        return None, None
    direct_files = [p for p in project_dir.glob("*.jsonl") if p.is_file()]
    if not direct_files:
        return None, None
    for path in direct_files:
        if is_likely_current_session(path):
            return path, "env"
    # A /clear stub can have a newer mtime than its much larger parent. Compare sizes
    # only inside a bounded recent-time cohort, rather than among an arbitrary file
    # count that could include large stale sessions from earlier days.
    stats = [(path, path.stat()) for path in direct_files]
    newest_mtime = max(stat.st_mtime for _, stat in stats)
    recent_window_seconds = 45 * 60
    recent = [path for path, stat in stats
              if newest_mtime - stat.st_mtime <= recent_window_seconds]
    largest = max(recent, key=lambda path: path.stat().st_size)
    return largest, "recent-window-size-fallback"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="Raw Claude Code session JSONL")
    parser.add_argument(
        "--current",
        action="store_true",
        help="Non-interactive: auto-resolve the CURRENTLY RUNNING session's transcript "
             "(no TTY needed, unlike the bare-invocation picker). Prints which "
             "resolution method was used.",
    )
    parser.add_argument("-o", "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--base-name")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacement only after identity/extension checks; use --force for unrelated content")
    parser.add_argument("--force", action="store_true", help="DANGEROUS: replace a non-extension or truncated existing bundle")
    parser.add_argument("--existing", choices=("amend", "continuation", "refuse"), default=None,
                        help="Existing-bundle behavior. Extensions default to continuation; migrations default to amend")
    parser.add_argument("--no-snapshot", action="store_true", help="Read the source directly instead of taking a stable snapshot")
    parser.add_argument("--permissive", action="store_true", help="Preserve malformed input as marked records")
    parser.add_argument("--keep-base64", action="store_true",
                        help="Preserve recognized raw base64 instead of default omission metadata")
    parser.add_argument("--truncate-base64", action="store_true",
                        help="Deprecated compatibility flag; omission is already the default")
    parser.add_argument("--omit-thinking", action="store_true",
                        help="Replace thinking text with deterministic size/hash metadata")
    parser.add_argument("--keep-legacy-artifacts", action="store_true",
                        help="Retain verified retired .compact_index.md and .capsule.md files during migration")
    parser.add_argument("--preview-chars", type=int, default=PREVIEW_CHARS)
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args()


def export_session(
    args: argparse.Namespace,
    input_path: Path,
    interactive: bool,
) -> tuple[str, list[Path], bool]:
    """Export one session. Returns (summary, artifacts, stop_batch)."""
    if not input_path.is_file():
        raise ValueError(f"Input file not found: {input_path}")
    output_dir = args.output_dir.expanduser().resolve()
    stem = args.base_name or base_name(input_path)
    if Path(stem).name != stem or stem in {"", ".", ".."}:
        raise ValueError("--base-name must be a plain filename stem")

    snapshot_path: Path | None = None
    try:
        if args.no_snapshot:
            read_path = input_path
            snapshot_meta = {"size": input_path.stat().st_size, "attempt": 0}
        else:
            snapshot_path, snapshot_meta = snapshot_input(input_path)
            read_path = snapshot_path
        records, audit = compact_records(
            read_path, args.permissive,
            keep_base64=args.keep_base64,
            omit_thinking=args.omit_thinking,
        )
        header = records[0]["__compact_session_header__"]
        header["__source_name__"] = input_path.name
        header["__snapshot_size__"] = snapshot_meta["size"]
        header["__snapshot_attempt__"] = snapshot_meta["attempt"]

        canonical_compact = output_dir / f"{stem}.compact.jsonl.txt"
        relationship, prior_count = "new", 0
        if canonical_compact.exists():
            existing_records = read_jsonl(canonical_compact)
            relationship, prior_count = compare_existing(existing_records, records)
            print(f"Existing compact relationship for {input_path.stem}: {relationship}")
        if relationship == "identical":
            return "already current; no files changed", [], False

        unsafe = relationship in {"different", "truncation", "legacy-truncation"}
        if unsafe and not args.force:
            raise FileExistsError(
                f"Existing compact transcript is {relationship}, not a verified extension. "
                "Refusing destructive replacement."
            )

        existing_mode = args.existing or (
            "continuation" if relationship == "extension" else "amend"
        )
        if interactive and args.existing is None and relationship in {"extension", "legacy-migration", "format2-migration", "format2-extension"}:
            existing_parts = sorted(output_dir.glob(f"{stem}.part*.compact.jsonl.txt"))
            chosen = prompt_existing_mode(relationship, existing_parts)
            if chosen is None:
                return "batch stopped before export", [], True
            existing_mode = chosen
        if relationship in {"extension", "legacy-migration", "format2-migration", "format2-extension"} and existing_mode == "refuse":
            return "skipped by user; existing bundle unchanged", [], False
        if relationship in {"legacy-migration", "format2-migration", "format2-extension"} and existing_mode == "continuation":
            raise FileExistsError("A legacy bundle must first be migrated in amend mode")

        continuation: dict[str, Any] | None = None
        output_stem = stem
        output_records = records
        replace_allowed: set[Path] = set()
        if relationship == "extension" and existing_mode == "continuation":
            part = next_part_number(output_dir, stem)
            output_stem = f"{stem}.part{part}"
            tail_records = materialize_continuation_payloads(
                records, records[prior_count + 1:]
            )
            output_records = [copy.deepcopy(records[0])] + tail_records
            prior_sha = hashlib.sha256(canonical_compact.read_bytes()).hexdigest()
            continuation = {
                "part": part,
                "prior_lines": prior_count + 1,
                "first_global_line": prior_count + 2,
                "prior_sha256": prior_sha,
            }
            output_records[0]["__compact_session_header__"]["__continuation__"] = continuation
        elif relationship in {"extension", "legacy-migration", "format2-migration", "format2-extension", "different", "truncation", "legacy-truncation"}:
            replace_allowed = {
                output_dir / f"{stem}.compact.jsonl.txt",
                output_dir / f"{stem}.indexed_capsule.md",
            }

        compact_path = output_dir / f"{output_stem}.compact.jsonl.txt"
        indexed_capsule_path = output_dir / f"{output_stem}.indexed_capsule.md"
        artifacts = [compact_path, indexed_capsule_path]
        if input_path.resolve() in {path.resolve() for path in artifacts}:
            raise ValueError("Input path collides with an output path")

        rows = build_rows(output_records, args.preview_chars)
        annotate_queues(rows)
        output_records[0]["__compact_session_header__"]["__navigation__"] = make_navigation(rows)
        compact_text = serialize_jsonl(output_records)
        checksum = hashlib.sha256(compact_text.encode("utf-8")).hexdigest()
        rows = build_rows(output_records, args.preview_chars)
        annotate_queues(rows)
        indexed_text = make_indexed_capsule(
            input_path.name, compact_path.name, checksum, rows, audit, continuation
        )
        retire_paths: set[Path] = set()
        if (
            relationship in {"legacy-migration", "format2-migration", "format2-extension"}
            and not getattr(args, "keep_legacy_artifacts", False)
        ):
            retire_paths = {
                output_dir / f"{stem}.compact_index.md",
                output_dir / f"{stem}.capsule.md",
            }

        def verify_pair(paths: dict[Path, Path]) -> None:
            verify(
                paths[compact_path], paths[indexed_capsule_path],
                len(output_records), checksum,
            )

        must_verify = args.verify or bool(retire_paths)
        transactional_write(
            {
                compact_path: compact_text,
                indexed_capsule_path: indexed_text,
            },
            replace_allowed,
            retire_paths,
            verify_pair if must_verify else None,
        )
        action = (
            "continuation created" if continuation
            else "legacy bundle migrated" if relationship in {"legacy-migration", "format2-migration", "format2-extension"}
            else "bundle amended" if relationship == "extension"
            else "bundle created"
        )
        return f"{action}; {len(output_records)} compact lines; SHA-256 {checksum}", artifacts, False
    finally:
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)


def print_batch_summary(results: list[tuple[Path, str, list[Path]]]) -> None:
    exported = [item for item in results if item[2]]
    unchanged = [item for item in results if not item[2]]
    if exported:
        print("\nSuccessfully exported the following artifacts:")
        for source, summary, artifacts in exported:
            print(f"\n  {source.stem}: {summary}")
            for artifact in artifacts:
                print(f"    - {artifact}")
    if unchanged:
        print("\nSessions with no artifacts written:")
        for source, summary, _ in unchanged:
            print(f"  - {source.stem}: {summary}")


def prompt_export_more() -> bool:
    while True:
        choice = input("\nExport more sessions? [y/N]: ").strip().lower()
        if choice in {"", "n", "no", "q", "quit", "exit"}:
            return False
        if choice in {"y", "yes", "more", "m"}:
            return True
        print("Please enter y or n.")


def main() -> int:
    args = parse_args()
    if args.preview_chars < 20:
        print("Error: --preview-chars must be at least 20.", file=sys.stderr)
        return 2
    if args.current and args.input is not None:
        print("Error: --current takes no input path.", file=sys.stderr)
        return 2
    if args.keep_base64 and args.truncate_base64:
        print("Error: --keep-base64 conflicts with --truncate-base64.", file=sys.stderr)
        return 2

    interactive = not args.current and args.input is None
    if args.current:
        input_path, method = find_current_session_file()
        if input_path is None:
            print(f"Could not resolve the current transcript under {CLAUDE_PROJECTS_DIR}.", file=sys.stderr)
            return 1
        print(f"Resolved current session via: {method}  ->  {input_path.name}")
        batches = [[input_path]]
    elif args.input is not None:
        batches = [[args.input.expanduser().resolve()]]
    else:
        batches = []

    while True:
        if interactive:
            selected = picker(args.output_dir)
            if selected is None:
                if not batches:
                    print("Aborted, nothing written.")
                return 0
            if args.base_name and len(selected) > 1:
                print("Error: --base-name cannot be used with multiple selected sessions.", file=sys.stderr)
                return 2
            selected_batch = selected
        else:
            if not batches:
                return 0
            selected_batch = batches.pop(0)

        # Oldest source first: artifacts are then WRITTEN in transcript chronology, which is what
        # stagger_artifact_times() below turns into a matching date order on disk. The picker
        # deliberately lists newest-first (most recent session at the top, where you want it), so
        # without this the batch would be exported in reverse-chronological order.
        selected_batch = sorted(selected_batch, key=source_chronology_key)
        results: list[tuple[Path, str, list[Path]]] = []
        failures = 0
        stop_batch = False
        for input_path in selected_batch:
            print(f"\nProcessing: {input_path.name}")
            try:
                summary, artifacts, stop_batch = export_session(args, input_path, interactive)
                results.append((input_path, summary, artifacts))
            except (ValueError, FileExistsError, RuntimeError, OSError) as error:
                failures += 1
                print(f"Error for {input_path.name}: {error}", file=sys.stderr)
            if stop_batch:
                break
        if results:
            # Stamp across the WHOLE batch, not per session: two sessions exported in the same
            # second would otherwise each be internally ordered yet tie with each other.
            stagger_artifact_times([artifact for _, _, produced in results for artifact in produced])
            print_batch_summary(results)
        if stop_batch:
            return 1 if failures else 0
        if not interactive:
            return 1 if failures else 0
        if not prompt_export_more():
            return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
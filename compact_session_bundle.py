#!/usr/bin/env python3
"""Create portable, context-efficient bundles from Claude Code transcripts.

Each selected raw session can produce four complementary artifacts:
  <session>.compact.jsonl.txt   normalized, line-addressable evidence
  <session>.compact_index.md    small navigation map for the compact JSONL
  <session>.capsule.md          deterministic semantic working context
  <session>.indexed_capsule.md  portable chronology-and-context hybrid

Interactive runs support multiple sessions, list/range selection, repeated export
batches, human-readable session titles, and activity markers. Existing bundles
are compared before replacement: identical bundles are left untouched, verified
extensions can be amended or written as numbered continuations, recognized legacy
bundles can be migrated, and unexplained differences are refused unless --force is
explicitly supplied.

Compaction removes designated structural noise, hoists only invariant repeated
fields, strips usage accounting and thinking signatures, truncates image data only
when requested, and deduplicates byte-identical tool-result payloads. This is
semantic/structural compaction, not byte-for-byte losslessness. The compact JSONL
remains the evidentiary source; capsules and indexes are navigation/working-context
views. Outputs may still contain sensitive transcript content.
"""
from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

VERSION = "0.5.0"
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
    hoisted_conflicts: dict[str, list[tuple[int, Any]]] = field(default_factory=dict)
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
    return text or "[empty]"


def markdown_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def fenced(text: str) -> str:
    """Render untrusted transcript text without allowing it to reshape the document."""
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}text\n{text}\n{fence}"


def excerpt(text: str, limit: int = PREVIEW_CHARS) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in redact(text).splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return "[empty]"
    signal = next((line for line in lines if SIGNAL_RE.search(line)), "")
    return normalize(f"{lines[0]} ⏐ {signal}" if signal and signal != lines[0] else lines[0], limit)


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


def truncate_base64(record: dict[str, Any]) -> None:
    def truncate(block: dict[str, Any]) -> None:
        source = block.get("source")
        if isinstance(source, dict) and isinstance(source.get("data"), str) and source["data"]:
            source["data"] = f"<BASE64 TRUNCATED - {len(source['data'])} chars>"
    for block in content_blocks(record):
        if isinstance(block, dict) and block.get("type") == "image":
            truncate(block)
        elif isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), list):
            for nested in block["content"]:
                if isinstance(nested, dict) and nested.get("type") == "image":
                    truncate(nested)
    result = record.get("toolUseResult")
    file_info = result.get("file") if isinstance(result, dict) else None
    if isinstance(file_info, dict) and isinstance(file_info.get("base64"), str) and file_info["base64"]:
        file_info["base64"] = f"<BASE64 TRUNCATED - {len(file_info['base64'])} chars>"


def dedupe_tool_result(record: dict[str, Any]) -> None:
    result = record.get("toolUseResult")
    if not isinstance(result, dict):
        return
    for block in result_blocks(record):
        content = block.get("content")
        block_text = content if isinstance(content, str) else None
        if isinstance(content, list):
            texts = [x.get("text") for x in content if isinstance(x, dict) and x.get("type") == "text"]
            if len(texts) == 1:
                block_text = texts[0]
        file_info = result.get("file")
        candidate = file_info.get("content") if isinstance(file_info, dict) else result.get("stdout")
        if block_text is not None and candidate == block_text:
            if isinstance(file_info, dict):
                file_info["content"] = "<DUPLICATE OF message.content - stripped by compact_session_bundle.py>"
            elif "stdout" in result:
                result["stdout"] = "<DUPLICATE OF message.content - stripped by compact_session_bundle.py>"


def is_noise(record: dict[str, Any]) -> bool:
    if (record.get("type"), record.get("subtype")) in NOISE_TYPE_SUBTYPE:
        return True
    attachment = record.get("attachment")
    return record.get("type") == "attachment" and isinstance(attachment, dict) and attachment.get("type") in NOISE_ATTACHMENT_TYPES


def compact_records(input_path: Path, permissive: bool, truncate_images: bool) -> tuple[list[dict[str, Any]], Audit]:
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
            audit.hoisted_conflicts[name] = conflicts
        else:
            header[name] = first
            invariant_fields.add(name)

    records: list[dict[str, Any]] = []
    malformed_by_line = dict(malformed_records)
    source_by_line = dict(source_records)
    for line_no in range(1, audit.physical_lines + 1):
        if line_no in malformed_by_line:
            records.append(malformed_by_line[line_no])
            continue
        source = source_by_line.get(line_no)
        if source is None:
            continue
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
        before_dedupe = json.dumps(record, sort_keys=True, ensure_ascii=False)
        dedupe_tool_result(record)
        if json.dumps(record, sort_keys=True, ensure_ascii=False) != before_dedupe:
            audit.transformations["tool_payloads_deduplicated"] += 1
        if truncate_images:
            before_images = json.dumps(record, sort_keys=True, ensure_ascii=False)
            truncate_base64(record)
            if json.dumps(record, sort_keys=True, ensure_ascii=False) != before_images:
                audit.transformations["image_records_truncated"] += 1
        after = json.dumps(record, sort_keys=True, ensure_ascii=False)
        audit.transformed_records += before != after
        records.append(record)

    audit.retained_source_records = len(records)
    compact_header = {
        **header,
        "__bundle_format__": 2,
        "__generator__": f"compact_session_bundle.py {VERSION}",
        "__source_name__": input_path.name,
        "__source_sha256__": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "__source_physical_lines__": audit.physical_lines,
        "__hoisted_conflicts__": audit.hoisted_conflicts,
        "__transformations__": dict(audit.transformations),
    }
    return [{"__compact_session_header__": compact_header}] + records, audit

def serialize_jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)


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
    return name, normalize(detail, 150), tool_id


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

def is_landmark(row: Row) -> bool:
    return (row.kind in {"USER", "QUEUED-USER", "ASSISTANT/TOOL-CALL"}
            or (row.kind == "ASSISTANT" and row.preview != "[empty]")
            or (row.kind == "TOOL/RESULT" and bool(SIGNAL_RE.search(row.preview)))
            or row.kind in {"MODE", "PERMISSION-MODE", "MALFORMED-SOURCE"})


def table(rows: Iterable[Row]) -> str:
    out = ["| Record | JSONL Line | Time | Actor / Type | Semantic Preview |",
           "| ---: | ---: | :---: | :--- | :--- |"]
    out.extend(f"| {r.record} | {r.line} | {r.time} | {r.kind} | {markdown_cell(r.preview)} |" for r in rows)
    return "\n".join(out) + "\n"


def make_index(compact_name: str, rows: list[Row], audit: Audit) -> str:
    counts = Counter(row.kind for row in rows)
    landmarks = [row for row in rows if is_landmark(row)]
    parts = [
        f"# Transcript Navigation Index: `{compact_name}`\n\n",
        "`JSONL Line` addresses the physical line in the compact transcript. This small index is a navigation map, not a replacement for exact evidence or the semantic capsule.\n\n",
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
        f"- Generator: compact_session_bundle.py {VERSION}\n",
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


def make_indexed_capsule(
    source_name: str,
    compact_name: str,
    checksum: str,
    rows: list[Row],
    audit: Audit,
    continuation: dict[str, Any] | None = None,
) -> str:
    """Combine line-addressable structure with the capsule's semantic detail."""
    capsule = make_capsule(source_name, compact_name, checksum, rows)
    title, remainder = capsule.split("\n", 1)
    chronology = [
        row for row in rows
        if row.kind in {"USER", "QUEUED-USER", "ASSISTANT", "ASSISTANT/TOOL-CALL", "TOOL/RESULT"}
    ]
    preface = [
        title.replace("Session Capsule", "Indexed Session Capsule"), "\n",
        "\n> Portable hybrid: a compact chronology plus semantic working context. ",
        "The compact JSONL remains the exact evidentiary source.\n\n",
    ]
    if continuation:
        preface.extend([
            "## Continuation identity\n\n",
            f"- Part: {continuation['part']}\n",
            f"- Prior compact lines: {continuation['prior_lines']}\n",
            f"- First new global compact line: {continuation['first_global_line']}\n",
            f"- Prior compact SHA-256: `{continuation['prior_sha256']}`\n\n",
        ])
    preface.extend([
        "## Indexed chronology\n\n",
        "The table is deliberately denser than the standalone index: every conversational or tool event is retained, while payload detail is carried in the sections below.\n\n",
        table(chronology),
        "\n## Compaction audit\n\n",
        f"- Raw physical lines: {audit.physical_lines}\n",
        f"- Retained source records: {audit.retained_source_records}\n",
        f"- Omitted noise records: {audit.omitted_records}\n",
        f"- Malformed lines: {audit.malformed_lines}\n",
        f"- Non-object JSON values: {audit.non_object_lines}\n",
        f"- Hoisted-field conflicts: {len(audit.hoisted_conflicts)}\n",
    ])
    for name, count in sorted(audit.transformations.items()):
        preface.append(f"- Transformation `{name}`: {count}\n")
    preface.append("\n## Semantic working context\n")
    return "".join(preface) + remainder


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


def compare_existing(existing: Sequence[dict[str, Any]], new: Sequence[dict[str, Any]]) -> tuple[str, int]:
    """Classify an existing compact transcript without weakening identity checks."""
    old_body = list(existing[1:])
    new_body = list(new[1:])
    common = min(len(old_body), len(new_body))
    if all(record_bytes(old_body[i]) == record_bytes(new_body[i]) for i in range(common)):
        if len(new_body) > len(old_body):
            return "extension", len(old_body)
        if len(new_body) == len(old_body):
            return "identical", len(old_body)
        return "truncation", len(old_body)

    # Backward compatibility is deliberately narrow: require a recognized
    # pre-v0.4 bundle, matching session/source identity, and an exact prefix
    # after applying only the documented old hoisted-field policy.
    if is_legacy_bundle(existing) and identities_compatible(existing, new):
        if len(new_body) < len(old_body):
            return "legacy-truncation", len(old_body)
        if all(record_bytes(old_body[i]) == record_bytes(legacy_project(new_body[i]))
               for i in range(len(old_body))):
            return "legacy-migration", len(old_body)
    return "different", len(old_body)


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


def transactional_write(files: dict[Path, str], replace_allowed: set[Path]) -> None:
    """Stage and verify bytes, then commit the output set with rollback backups."""
    if not files:
        return
    for path in files:
        if path.exists() and path not in replace_allowed:
            raise FileExistsError(f"Refusing to replace unapproved output: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
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
        for path in files:
            if path.exists():
                backup = path.with_name(f".{path.name}.rollback-{os.getpid()}")
                path.replace(backup)
                backups[path] = backup
            staged[path].replace(path)
            committed.append(path)
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
        for temp in staged.values():
            temp.unlink(missing_ok=True)


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


def resolve_session_label(path: Path, limit: int = SESSION_TITLE_CHARS) -> SessionLabel:
    """Resolve a picker label using Claude Code's documented display precedence.

    Transcript entries are an internal Claude Code format. Unknown and malformed
    records are ignored, every metadata source is optional, and later title or
    summary entries win. The source transcript is never modified.
    """
    custom_title = ""
    ai_title = ""
    summary = ""
    first_prompt = ""
    session_id = path.stem

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or not _record_matches_session(record, session_id):
                    continue

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
    except OSError:
        pass

    for source, value in (
        ("custom title", custom_title),
        ("AI title", ai_title),
        ("summary", summary),
        ("first prompt", first_prompt),
    ):
        if value.strip():
            return SessionLabel(normalize(value, limit), source)

    return SessionLabel("Untitled session", "fallback")


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


def picker() -> list[Path] | None:
    files = list_sessions()
    if not files:
        print(f"No session files found under {CLAUDE_PROJECTS_DIR}")
        return None
    active = detect_active_sessions(files)
    title_cache: dict[Path, SessionLabel] = {}
    # Keep chronological ordering stable; markers can identify several sessions.
    shown_count = 5
    while True:
        shown = files[:shown_count]
        print("\nRecent Claude Code sessions:\n")
        active_indices: list[int] = []
        for index, path in enumerate(shown, 1):
            method = active.get(path)
            if method:
                active_indices.append(index)
            stat = path.stat()
            if path not in title_cache:
                title_cache[path] = resolve_session_label(path)
            session_label = title_cache[path]
            activity_text = {
                "open-file": "writing now",
                "environment-id": "session ID match",
                "recent-activity": "recently active",
                "recent-size-fallback": "recent fallback",
            }.get(method, method or "")
            marker = f"  <-- {activity_text}" if activity_text else ""
            print(f"  [{index}] {session_label.text}{marker}")
            print(f"      ID: {path.stem} · {stat.st_size/1024:,.0f} KB · "
                  f"{datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M} · project: {path.parent.name}")
            print()
        if len(files) > shown_count:
            print(f"  [m] show {min(10, len(files)-shown_count)} more")
        print("  [q] quit")
        default_indices = active_indices or [1]
        default_text = ",".join(map(str, default_indices))
        default_note = "active candidates" if active_indices else "most recent"
        choice = input(
            f"\nSelect one or more sessions [default: {default_text}, {default_note}]: "
        ).strip().lower()
        if not choice:
            return [shown[index - 1] for index in default_indices]
        if choice in {"q", "quit", "exit"}:
            return None
        if choice == "m" and shown_count < len(files):
            shown_count = min(shown_count + 10, len(files))
            continue
        try:
            indices = parse_selection(choice, len(shown))
            return [shown[index - 1] for index in indices]
        except ValueError as error:
            print(f"Not a valid selection: {error}")


def prompt_existing_mode(relationship: str) -> str | None:
    print(f"\nAn existing bundle was detected ({relationship}).")
    if relationship == "extension":
        print("  [a] amend the complete bundle with the verified new tail (default)")
        print("  [c] write only the new tail as a numbered continuation")
    elif relationship == "legacy-migration":
        print("  [a] migrate and replace the verified legacy bundle (default)")
    print("  [r] refuse replacement and skip this session")
    print("  [q] stop the current export batch")
    while True:
        choice = input("Choose existing-bundle behavior [default: a]: ").strip().lower()
        if choice in {"", "a", "amend"}:
            return "amend"
        if relationship == "extension" and choice in {"c", "continuation"}:
            return "continuation"
        if choice in {"r", "refuse"}:
            return "refuse"
        if choice in {"q", "quit"}:
            return None
        print("Not a valid choice, try again.")


def verify(
    compact_path: Path,
    index_path: Path,
    capsule_path: Path,
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
    for path in (index_path, capsule_path, indexed_capsule_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Output verification failed: {path}")
        text = path.read_text(encoding="utf-8")
        if path != index_path and expected_checksum not in text:
            raise RuntimeError(f"Expected compact checksum is absent from {path}")
        patterns = (
            [r"(?m)^\|\s*\d+\s*\|\s*(\d+)\s*\|"]
            if path == index_path
            else [
                r"(?m)^### Line\s+(\d+)\b",
                r"\[(?:[^\]]*;\s*)?line\s+(\d+)\]",
                r"(?m)^\|\s*(\d+)\s*\|\s*[A-Z]",
            ]
        )
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                referenced = int(match.group(1))
                if not 1 <= referenced <= expected_lines:
                    raise RuntimeError(f"Out-of-range compact line reference in {path}: {referenced}")


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
                        help="Existing-bundle behavior. In picker mode this is prompted interactively when omitted; otherwise defaults to amend")
    parser.add_argument("--no-snapshot", action="store_true", help="Read the source directly instead of taking a stable snapshot")
    parser.add_argument("--permissive", action="store_true", help="Preserve malformed input as marked records")
    parser.add_argument("--truncate-base64", action="store_true", help="LOSSY: replace image base64 with length markers")
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
        records, audit = compact_records(read_path, args.permissive, args.truncate_base64)
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

        existing_mode = args.existing or "amend"
        if interactive and args.existing is None and relationship in {"extension", "legacy-migration"}:
            chosen = prompt_existing_mode(relationship)
            if chosen is None:
                return "batch stopped before export", [], True
            existing_mode = chosen
        if relationship in {"extension", "legacy-migration"} and existing_mode == "refuse":
            return "skipped by user; existing bundle unchanged", [], False
        if relationship == "legacy-migration" and existing_mode == "continuation":
            raise FileExistsError("A legacy bundle must first be migrated in amend mode")

        continuation: dict[str, Any] | None = None
        output_stem = stem
        output_records = records
        replace_allowed: set[Path] = set()
        if relationship == "extension" and existing_mode == "continuation":
            part = next_part_number(output_dir, stem)
            output_stem = f"{stem}.part{part}"
            output_records = [copy.deepcopy(records[0])] + records[prior_count + 1:]
            prior_sha = hashlib.sha256(canonical_compact.read_bytes()).hexdigest()
            continuation = {
                "part": part,
                "prior_lines": prior_count + 1,
                "first_global_line": prior_count + 2,
                "prior_sha256": prior_sha,
            }
            output_records[0]["__compact_session_header__"]["__continuation__"] = continuation
        elif relationship in {"extension", "legacy-migration", "different", "truncation", "legacy-truncation"}:
            replace_allowed = {
                output_dir / f"{stem}.compact.jsonl.txt",
                output_dir / f"{stem}.compact_index.md",
                output_dir / f"{stem}.capsule.md",
                output_dir / f"{stem}.indexed_capsule.md",
            }

        compact_path = output_dir / f"{output_stem}.compact.jsonl.txt"
        index_path = output_dir / f"{output_stem}.compact_index.md"
        capsule_path = output_dir / f"{output_stem}.capsule.md"
        indexed_capsule_path = output_dir / f"{output_stem}.indexed_capsule.md"
        artifacts = [compact_path, index_path, capsule_path, indexed_capsule_path]
        if input_path.resolve() in {path.resolve() for path in artifacts}:
            raise ValueError("Input path collides with an output path")

        compact_text = serialize_jsonl(output_records)
        checksum = hashlib.sha256(compact_text.encode("utf-8")).hexdigest()
        rows = build_rows(output_records, args.preview_chars)
        annotate_queues(rows)
        index_text = make_index(compact_path.name, rows, audit)
        capsule_text = make_capsule(input_path.name, compact_path.name, checksum, rows)
        indexed_text = make_indexed_capsule(
            input_path.name, compact_path.name, checksum, rows, audit, continuation
        )
        transactional_write({
            compact_path: compact_text,
            index_path: index_text,
            capsule_path: capsule_text,
            indexed_capsule_path: indexed_text,
        }, replace_allowed)
        if args.verify:
            verify(
                compact_path, index_path, capsule_path, indexed_capsule_path,
                len(output_records), checksum,
            )
        action = (
            "continuation created" if continuation
            else "legacy bundle migrated" if relationship == "legacy-migration"
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
            selected = picker()
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
            print_batch_summary(results)
        if stop_batch:
            return 1 if failures else 0
        if not interactive:
            return 1 if failures else 0
        if not prompt_export_more():
            return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
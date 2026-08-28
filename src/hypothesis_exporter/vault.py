from __future__ import annotations

from collections import defaultdict
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .models import Annotation, Block, Discovery, FileEdit


ENTRY_START = re.compile(r"<!-- hypothesis-entry:start id=([^\s>]+) -->")
ENTRY_END_TEMPLATE = "<!-- hypothesis-entry:end id={key} -->"
COMMENT_START_TEMPLATE = "<!-- hypothesis-comment:start id={key} -->"
COMMENT_END_TEMPLATE = "<!-- hypothesis-comment:end id={key} -->"
SOURCE_START = re.compile(r"<!-- hypothesis-source:start key=([0-9a-f]{32}) -->")
SOURCE_END_TEMPLATE = "<!-- hypothesis-source:end key={key} -->"
ROOT_START = "<!-- hypothesis-root:start -->"
ROOT_END = "<!-- hypothesis-root:end -->"


class VaultError(RuntimeError):
    pass


def source_key(local_date: str, uri: str) -> str:
    return hashlib.sha256(f"{local_date}\0{uri}".encode()).hexdigest()[:32]


def _candidate_files(vault: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["rg", "-l", "--hidden", "-g", "*.md", "-g", "!.obsidian/**", "-g", "!.trash/**",
             "<!-- hypothesis-(entry|source|root):", str(vault)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode in (0, 1):
            return [Path(line) for line in result.stdout.splitlines() if line]
    except FileNotFoundError:
        pass
    return [
        path for path in vault.rglob("*.md")
        if ".obsidian" not in path.parts and ".trash" not in path.parts
        and "<!-- hypothesis-" in path.read_text(encoding="utf-8", errors="replace")
    ]


def _extract_block(text: str, start_match: re.Match[str], end_marker: str, path: Path,
                   errors: list[str], comment: bool = False) -> Block | None:
    key = start_match.group(1)
    end_start = text.find(end_marker, start_match.end())
    if end_start < 0:
        errors.append(f"{path}: missing closing marker for {key}")
        return None
    end = end_start + len(end_marker)
    body = text[start_match.start():end]
    parsed_comment: str | None = None
    if comment:
        comment_start_marker = COMMENT_START_TEMPLATE.format(key=key)
        comment_end_marker = COMMENT_END_TEMPLATE.format(key=key)
        cstart = body.find(comment_start_marker)
        cend = body.find(comment_end_marker)
        if cstart < 0 or cend < 0 or cend < cstart:
            errors.append(f"{path}: malformed comment markers for {key}")
            return None
        parsed_comment = body[cstart + len(comment_start_marker):cend]
        if parsed_comment.startswith("\n"):
            parsed_comment = parsed_comment[1:]
        if parsed_comment.endswith("\n"):
            parsed_comment = parsed_comment[:-1]
    return Block(key=key, path=path, start=start_match.start(), end=end, body=body, comment=parsed_comment)


def discover(vault: Path, extra_paths: tuple[Path, ...] = ()) -> Discovery:
    discovery = Discovery()
    candidates = {path.resolve() for path in _candidate_files(vault)}
    candidates.update(path.resolve() for path in extra_paths if path.exists())
    entry_occurrences: dict[str, list[Block]] = defaultdict(list)
    source_occurrences: dict[str, list[Block]] = defaultdict(list)
    for path in sorted(candidates):
        text = path.read_text(encoding="utf-8")
        intervals: list[tuple[int, int, str]] = []
        for match in ENTRY_START.finditer(text):
            block = _extract_block(text, match, ENTRY_END_TEMPLATE.format(key=match.group(1)), path,
                                   discovery.errors, comment=True)
            if block:
                entry_occurrences[block.key].append(block)
                intervals.append((block.start, block.end, f"entry {block.key}"))
        for match in SOURCE_START.finditer(text):
            block = _extract_block(text, match, SOURCE_END_TEMPLATE.format(key=match.group(1)), path,
                                   discovery.errors)
            if block:
                source_occurrences[block.key].append(block)
        root_start = text.find(ROOT_START)
        if root_start >= 0:
            root_end_start = text.find(ROOT_END, root_start)
            if root_end_start < 0:
                discovery.errors.append(f"{path}: missing Hypothesis root closing marker")
            elif text.find(ROOT_START, root_start + 1) >= 0:
                discovery.errors.append(f"{path}: duplicate Hypothesis root markers")
            else:
                discovery.roots[path] = Block("root", path, root_start, root_end_start + len(ROOT_END),
                                              text[root_start:root_end_start + len(ROOT_END)])
        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1]:
                discovery.errors.append(f"{path}: overlapping {previous[2]} and {current[2]}")
    for key, blocks in entry_occurrences.items():
        if len(blocks) > 1:
            locations = ", ".join(str(block.path) for block in blocks)
            discovery.errors.append(f"duplicate annotation {key}: {locations}")
        else:
            discovery.entries[key] = blocks[0]
    for key, blocks in source_occurrences.items():
        if len(blocks) > 1:
            locations = ", ".join(str(block.path) for block in blocks)
            discovery.errors.append(f"duplicate source section {key}: {locations}")
        else:
            discovery.sources[key] = blocks[0]
    return discovery


def _blockquote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in text.splitlines())


def render_entry(annotation: Annotation) -> str:
    parts = [f"<!-- hypothesis-entry:start id={annotation.id} -->"]
    for exact in annotation.exact_quotes:
        parts.extend([_blockquote(exact), ""])
    parts.append(COMMENT_START_TEMPLATE.format(key=annotation.id))
    if annotation.text:
        parts.append(annotation.text)
    parts.append(COMMENT_END_TEMPLATE.format(key=annotation.id))
    parts.extend([
        "",
        f"[Hypothesis](<{annotation.hypothesis_url}>) · `{annotation.created}`",
        ENTRY_END_TEMPLATE.format(key=annotation.id),
    ])
    return "\n".join(parts)


def render_source(key: str, annotation: Annotation, entries: list[str]) -> str:
    safe_title = " ".join(annotation.title.splitlines()).replace("[", "\\[").replace("]", "\\]")
    return "\n".join([
        f"<!-- hypothesis-source:start key={key} -->",
        f"### [{safe_title}](<{annotation.uri}>)",
        "",
        "\n\n".join(entries),
        SOURCE_END_TEMPLATE.format(key=key),
    ])


def render_root(section_heading: str, sources: list[str]) -> str:
    return "\n".join([
        ROOT_START,
        f"## {section_heading}",
        "",
        "\n\n".join(sources),
        ROOT_END,
    ])


def apply_edits(edits: list[FileEdit], expected_hashes: dict[Path, str], dry_run: bool = False) -> int:
    grouped: dict[Path, list[FileEdit]] = defaultdict(list)
    for edit in edits:
        grouped[edit.path].append(edit)
    if dry_run:
        return len(grouped)
    written = 0
    for path, path_edits in grouped.items():
        original = path.read_text(encoding="utf-8") if path.exists() else "#diary\n"
        actual_hash = hashlib.sha256(original.encode()).hexdigest()
        expected = expected_hashes.get(path)
        if expected is not None and expected != actual_hash:
            raise VaultError(f"{path} changed while synchronization was running")
        by_span: dict[tuple[int, int], list[str]] = defaultdict(list)
        for edit in path_edits:
            by_span[(edit.start, edit.end)].append(edit.replacement)
        merged = [(start, end, "\n\n".join(values)) for (start, end), values in by_span.items()]
        merged.sort(reverse=True)
        for (start, end, replacement), next_edit in zip(merged, merged[1:]):
            if next_edit[1] > start:
                raise VaultError(f"overlapping edits planned for {path}")
        updated = original
        for start, end, replacement in merged:
            updated = updated[:start] + replacement + updated[end:]
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        written += 1
    return written


def file_hash(path: Path) -> str:
    text = path.read_text(encoding="utf-8") if path.exists() else "#diary\n"
    return hashlib.sha256(text.encode()).hexdigest()

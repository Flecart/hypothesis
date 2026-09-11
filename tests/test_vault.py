from pathlib import Path

from hypothesis_exporter.models import Annotation
import pytest

from hypothesis_exporter.vault import (
    VaultError, discover, render_entry, render_root, render_source,
    resolve_note_by_basename, source_key,
)


def annotation(identifier: str = "abc", text: str = "My note") -> Annotation:
    return Annotation(
        id=identifier,
        created="2026-08-28T10:00:00+00:00",
        updated="2026-08-28T10:00:00+00:00",
        uri="https://example.com/paper",
        text=text,
        title="A [Useful] Paper",
        exact_quotes=("first line\nsecond line",),
        hypothesis_url=f"https://hypothes.is/a/{identifier}",
    )


def test_render_and_discover_movable_entry(tmp_path: Path):
    item = annotation()
    key = source_key("2026-08-28", item.uri)
    card = render_entry(item)
    source = render_source(key, item, [card])
    original = tmp_path / "daily" / "2026-08-28.md"
    original.parent.mkdir()
    original.write_text("#diary\n\n" + render_root("Hypothesis", [source]), encoding="utf-8")

    found = discover(tmp_path)
    assert found.errors == []
    assert found.entries["abc"].comment == "My note"
    assert "> first line\n> second line" in found.entries["abc"].body

    categorized = tmp_path / "topics" / "economics.md"
    categorized.parent.mkdir()
    categorized.write_text("# Economics\n\n" + card, encoding="utf-8")
    original.write_text(original.read_text().replace(card, ""), encoding="utf-8")
    moved = discover(tmp_path)
    assert moved.errors == []
    assert moved.entries["abc"].path == categorized.resolve()


def test_duplicate_entry_is_reported(tmp_path: Path):
    card = render_entry(annotation())
    (tmp_path / "one.md").write_text(card, encoding="utf-8")
    (tmp_path / "two.md").write_text(card, encoding="utf-8")
    found = discover(tmp_path)
    assert any("duplicate annotation abc" in error for error in found.errors)


def test_stversions_copies_are_ignored(tmp_path: Path):
    card = render_entry(annotation())
    live = tmp_path / "daily" / "2026-08-28.md"
    version = tmp_path / ".stversions" / "daily" / "2026-08-28.md"
    live.parent.mkdir()
    version.parent.mkdir(parents=True)
    live.write_text(card, encoding="utf-8")
    version.write_text(card, encoding="utf-8")

    found = discover(tmp_path)

    assert found.errors == []
    assert found.entries["abc"].path == live.resolve()


def test_resolve_note_by_basename_finds_moved_note(tmp_path: Path):
    moved = tmp_path / "daily" / "08" / "2026-08-28.md"
    moved.parent.mkdir(parents=True)
    moved.write_text("#diary\n", encoding="utf-8")

    resolved = resolve_note_by_basename(tmp_path, tmp_path / "daily", "2026-08-28.md")

    assert resolved == moved.resolve()


def test_resolve_note_by_basename_ignores_versions_and_rejects_live_duplicates(tmp_path: Path):
    live = tmp_path / "daily" / "2026-08-28.md"
    version = tmp_path / ".stversions" / "daily" / "2026-08-28.md"
    live.parent.mkdir()
    version.parent.mkdir(parents=True)
    live.write_text("#diary\n", encoding="utf-8")
    version.write_text("#diary\n", encoding="utf-8")
    assert resolve_note_by_basename(tmp_path, tmp_path / "daily", live.name) == live.resolve()

    duplicate = tmp_path / "archive" / live.name
    duplicate.parent.mkdir()
    duplicate.write_text("#diary\n", encoding="utf-8")
    with pytest.raises(VaultError, match="multiple vault notes"):
        resolve_note_by_basename(tmp_path, tmp_path / "daily", live.name)


def test_resolve_note_by_basename_returns_new_daily_path(tmp_path: Path):
    expected = tmp_path / "daily" / "2026-08-28.md"
    assert resolve_note_by_basename(tmp_path, tmp_path / "daily", expected.name) == expected.resolve()

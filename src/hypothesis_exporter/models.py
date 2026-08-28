from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal


Status = Literal["managed", "detached", "remote_missing"]


@dataclass(frozen=True)
class Annotation:
    id: str
    created: str
    updated: str
    uri: str
    text: str
    title: str
    exact_quotes: tuple[str, ...]
    hypothesis_url: str
    tags: tuple[str, ...] = ()

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> "Annotation":
        quotes: list[str] = []
        for target in value.get("target") or []:
            for selector in target.get("selector") or []:
                if selector.get("type") == "TextQuoteSelector" and isinstance(selector.get("exact"), str):
                    exact = selector["exact"]
                    if exact not in quotes:
                        quotes.append(exact)
        titles = (value.get("document") or {}).get("title") or []
        title = next((item for item in titles if isinstance(item, str) and item.strip()), value.get("uri", "Untitled"))
        links = value.get("links") or {}
        return cls(
            id=value["id"],
            created=value["created"],
            updated=value["updated"],
            uri=value.get("uri", ""),
            text=value.get("text") or "",
            title=title,
            exact_quotes=tuple(quotes),
            hypothesis_url=links.get("html") or f"https://hypothes.is/a/{value['id']}",
            tags=tuple(value.get("tags") or ()),
        )

    def with_text(self, text: str) -> "Annotation":
        return replace(self, text=text)


@dataclass(frozen=True)
class EntryState:
    annotation_id: str
    source_key: str
    created_at: str
    base_text: str
    remote_updated: str
    status: Status
    path: str | None


@dataclass(frozen=True)
class SourceState:
    source_key: str
    local_date: str
    uri: str
    title: str
    status: Literal["managed", "detached"]
    path: str | None


@dataclass(frozen=True)
class Block:
    key: str
    path: Path
    start: int
    end: int
    body: str
    comment: str | None = None


@dataclass
class Discovery:
    entries: dict[str, Block] = field(default_factory=dict)
    sources: dict[str, Block] = field(default_factory=dict)
    roots: dict[Path, Block] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FileEdit:
    path: Path
    start: int
    end: int
    replacement: str


@dataclass
class SyncResult:
    fetched: int = 0
    patched: int = 0
    written_files: int = 0
    created_entries: int = 0
    updated_entries: int = 0
    moved_entries: int = 0
    detached_entries: int = 0
    remote_missing: int = 0
    messages: list[str] = field(default_factory=list)

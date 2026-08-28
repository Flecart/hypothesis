from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import fcntl
from pathlib import Path
from zoneinfo import ZoneInfo

from .api import HypothesisClient
from .config import Config
from .models import Annotation, EntryState, FileEdit, SourceState, SyncResult
from .state import StateStore
from .vault import (
    ROOT_END, SOURCE_END_TEMPLATE, VaultError, apply_edits, discover, file_hash,
    render_entry, render_root, render_source, source_key,
)


def date_window(day: date, timezone_name: str) -> tuple[str, str]:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def annotation_local_date(annotation: Annotation, timezone_name: str) -> str:
    return datetime.fromisoformat(annotation.created.replace("Z", "+00:00")).astimezone(ZoneInfo(timezone_name)).date().isoformat()


def _append_position(path: Path) -> tuple[int, str]:
    original = path.read_text(encoding="utf-8") if path.exists() else "#diary\n"
    prefix = "\n\n" if original.rstrip() else ""
    return len(original), prefix


def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("another hypothesis-export synchronization is already running")
    return handle


class Synchronizer:
    def __init__(self, config: Config, client: HypothesisClient, store: StateStore):
        self.config = config
        self.client = client
        self.store = store

    def sync(self, day: date, *, dry_run: bool = False) -> SyncResult:
        result = SyncResult()
        lock_handle = _lock(self.config.state_path.with_suffix(".lock"))
        try:
            return self._sync_locked(day, dry_run=dry_run, result=result)
        finally:
            lock_handle.close()

    def _sync_locked(self, day: date, *, dry_run: bool, result: SyncResult) -> SyncResult:
        daily_path = (self.config.daily_dir / f"{day.isoformat()}.md").resolve()
        discovery = discover(self.config.vault, (daily_path,))
        if discovery.errors:
            raise VaultError("\n".join(discovery.errors))

        entries = self.store.entries()
        sources = self.store.sources()
        pending_entries = dict(entries)
        pending_sources = dict(sources)

        # First reconcile location state. Moving is represented by a changed path;
        # disappearance detaches locally and never deletes remote data.
        for key, source in sources.items():
            block = discovery.sources.get(key)
            if source.status == "managed" and block is None:
                pending_sources[key] = replace(source, status="detached")
            elif source.status == "managed" and block is not None:
                pending_sources[key] = replace(source, path=str(block.path))
        for annotation_id, state in entries.items():
            block = discovery.entries.get(annotation_id)
            if state.status == "managed" and block is None:
                pending_entries[annotation_id] = replace(state, status="detached")
                result.detached_entries += 1
            elif state.status == "managed" and block is not None:
                if state.path and Path(state.path) != block.path:
                    result.moved_entries += 1
                pending_entries[annotation_id] = replace(state, path=str(block.path))

        userid = self.client.profile_userid()
        start, end = date_window(day, self.config.timezone)
        selected = self.client.search_created(userid, start, end)
        result.fetched = len(selected)
        remote: dict[str, Annotation | None] = {item.id: item for item in selected}

        # Refresh all older, still-managed entries so edits anywhere in the vault sync.
        for annotation_id, state in pending_entries.items():
            if state.status == "managed" and annotation_id not in remote:
                remote[annotation_id] = self.client.get_annotation(annotation_id)

        edits: list[FileEdit] = []
        expected_hashes: dict[Path, str] = {}
        new_by_existing_source: dict[str, list[tuple[Annotation, str]]] = defaultdict(list)
        new_by_new_source: dict[str, list[tuple[Annotation, str]]] = defaultdict(list)

        # Reconcile existing movable cards.
        for annotation_id, state in list(pending_entries.items()):
            if state.status != "managed":
                continue
            local = discovery.entries[annotation_id]
            current = remote.get(annotation_id)
            if current is None:
                pending_entries[annotation_id] = replace(state, status="remote_missing", path=str(local.path))
                result.remote_missing += 1
                continue
            local_text = local.comment or ""
            desired = current
            if local_text != state.base_text:
                if current.text != local_text:
                    if not dry_run:
                        desired = self.client.patch_text(annotation_id, local_text)
                    else:
                        desired = current.with_text(local_text)
                    result.patched += 1
                else:
                    desired = current
            rendered = render_entry(desired)
            if rendered != local.body:
                edits.append(FileEdit(local.path, local.start, local.end, rendered))
                expected_hashes.setdefault(local.path, file_hash(local.path))
                result.updated_entries += 1
            pending_entries[annotation_id] = EntryState(
                annotation_id=annotation_id, source_key=state.source_key, created_at=desired.created,
                base_text=desired.text, remote_updated=desired.updated, status="managed", path=str(local.path),
            )

        # Queue annotations not seen before. Detached records are deliberately not recreated.
        for annotation in selected:
            if annotation.id in pending_entries:
                continue
            local_day = annotation_local_date(annotation, self.config.timezone)
            key = source_key(local_day, annotation.uri)
            source = pending_sources.get(key)
            if source and source.status == "detached":
                result.messages.append(f"skipped {annotation.id}: source section {key} is detached")
                continue
            rendered = render_entry(annotation)
            if key in discovery.sources:
                new_by_existing_source[key].append((annotation, rendered))
            else:
                new_by_new_source[key].append((annotation, rendered))

        # Insert cards into source sections that may have moved anywhere in the vault.
        for key, items in new_by_existing_source.items():
            source_block = discovery.sources[key]
            marker = SOURCE_END_TEMPLATE.format(key=key)
            insertion = source_block.end - len(marker)
            payload = "\n\n" + "\n\n".join(rendered for _, rendered in items)
            edits.append(FileEdit(source_block.path, insertion, insertion, payload))
            expected_hashes.setdefault(source_block.path, file_hash(source_block.path))
            existing_source = pending_sources.get(key)
            first = items[0][0]
            pending_sources[key] = SourceState(
                key, annotation_local_date(first, self.config.timezone), first.uri, first.title,
                "managed", str(source_block.path),
            ) if existing_source is None else replace(existing_source, path=str(source_block.path), status="managed")
            for annotation, _ in items:
                pending_entries[annotation.id] = EntryState(
                    annotation.id, key, annotation.created, annotation.text, annotation.updated,
                    "managed", str(source_block.path),
                )
                result.created_entries += 1

        # Create new publication sections in the target daily note.
        if new_by_new_source:
            rendered_sources: list[str] = []
            for key, items in sorted(new_by_new_source.items(), key=lambda pair: pair[1][0][0].created):
                first = items[0][0]
                rendered_sources.append(render_source(key, first, [rendered for _, rendered in items]))
                pending_sources[key] = SourceState(
                    key, annotation_local_date(first, self.config.timezone), first.uri, first.title,
                    "managed", str(daily_path),
                )
                for annotation, _ in items:
                    pending_entries[annotation.id] = EntryState(
                        annotation.id, key, annotation.created, annotation.text, annotation.updated,
                        "managed", str(daily_path),
                    )
                    result.created_entries += 1
            root = discovery.roots.get(daily_path)
            if root:
                insertion = root.end - len(ROOT_END)
                edits.append(FileEdit(daily_path, insertion, insertion, "\n\n" + "\n\n".join(rendered_sources)))
            else:
                insertion, prefix = _append_position(daily_path)
                edits.append(FileEdit(daily_path, insertion, insertion,
                                      prefix + render_root(self.config.section_heading, rendered_sources) + "\n"))
            expected_hashes.setdefault(daily_path, file_hash(daily_path))

        result.written_files = apply_edits(edits, expected_hashes, dry_run=dry_run)
        if not dry_run:
            with self.store.transaction():
                for source in pending_sources.values():
                    self.store.upsert_source(source)
                for entry in pending_entries.values():
                    self.store.upsert_entry(entry)
        return result

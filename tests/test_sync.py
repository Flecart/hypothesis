from dataclasses import replace
from datetime import date

from hypothesis_exporter.config import Config
from hypothesis_exporter.models import Annotation
from hypothesis_exporter.state import StateStore
from hypothesis_exporter.sync import Synchronizer, date_window
from hypothesis_exporter.vault import discover


class FakeClient:
    def __init__(self, annotations):
        self.annotations = {item.id: item for item in annotations}
        self.patches = []

    def profile_userid(self):
        return "acct:me@hypothes.is"

    def search_created(self, userid, start, end):
        return list(self.annotations.values())

    def get_annotation(self, annotation_id):
        return self.annotations.get(annotation_id)

    def patch_text(self, annotation_id, text):
        self.patches.append((annotation_id, text))
        updated = replace(self.annotations[annotation_id], text=text, updated="2026-08-28T12:00:00+00:00")
        self.annotations[annotation_id] = updated
        return updated


def make_annotation(text="Remote note"):
    return Annotation(
        id="ann1", created="2026-08-28T10:00:00+00:00", updated="2026-08-28T10:00:00+00:00",
        uri="https://example.com/article", text=text, title="Article",
        exact_quotes=("Exact highlight",), hypothesis_url="https://hypothes.is/a/ann1",
    )


def make_config(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return Config(tmp_path, vault, "daily", "Europe/Rome", "Hypothesis", "https://hypothes.is/api",
                  tmp_path / "state" / "state.sqlite3")


def test_date_window_handles_rome_dst():
    start, end = date_window(date(2026, 3, 29), "Europe/Rome")
    assert start == "2026-03-28T23:00:00+00:00"
    assert end == "2026-03-29T22:00:00+00:00"


def test_initial_sync_and_local_comment_wins(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient([make_annotation()])
    store = StateStore(config.state_path)
    try:
        first = Synchronizer(config, client, store).sync(date(2026, 8, 28))
        assert first.created_entries == 1
        note = config.daily_dir / "2026-08-28.md"
        content = note.read_text()
        assert content.startswith("#diary")
        assert "## Hypothesis" in content
        assert "### [Article]" in content
        assert "> Exact highlight" in content

        content = content.replace("Remote note", "Locally categorized thought")
        note.write_text(content)
        second = Synchronizer(config, client, store).sync(date(2026, 8, 28))
        assert second.patched == 1
        assert client.patches == [("ann1", "Locally categorized thought")]
        assert discover(config.vault).entries["ann1"].comment == "Locally categorized thought"
    finally:
        store.close()


def test_moved_card_continues_syncing(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient([make_annotation()])
    store = StateStore(config.state_path)
    try:
        Synchronizer(config, client, store).sync(date(2026, 8, 28))
        daily = config.daily_dir / "2026-08-28.md"
        block = discover(config.vault).entries["ann1"]
        text = daily.read_text()
        daily.write_text(text[:block.start] + text[block.end:])
        topic = config.vault / "topics" / "economics.md"
        topic.parent.mkdir()
        topic.write_text("# Economics\n\n" + block.body.replace("Remote note", "Moved thought"))

        result = Synchronizer(config, client, store).sync(date(2026, 8, 28))
        assert result.moved_entries == 1
        assert client.patches[-1] == ("ann1", "Moved thought")
        assert discover(config.vault).entries["ann1"].path == topic.resolve()
    finally:
        store.close()


def test_remote_only_comment_updates_local_card(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient([make_annotation()])
    store = StateStore(config.state_path)
    try:
        Synchronizer(config, client, store).sync(date(2026, 8, 28))
        client.annotations["ann1"] = replace(
            client.annotations["ann1"], text="Edited remotely", updated="2026-08-28T13:00:00+00:00"
        )
        result = Synchronizer(config, client, store).sync(date(2026, 8, 28))
        assert result.patched == 0
        assert result.updated_entries == 1
        assert discover(config.vault).entries["ann1"].comment == "Edited remotely"
    finally:
        store.close()


def test_removed_card_detaches_without_remote_delete(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient([make_annotation()])
    store = StateStore(config.state_path)
    try:
        Synchronizer(config, client, store).sync(date(2026, 8, 28))
        daily = config.daily_dir / "2026-08-28.md"
        block = discover(config.vault).entries["ann1"]
        text = daily.read_text()
        daily.write_text(text[:block.start] + text[block.end:])
        result = Synchronizer(config, client, store).sync(date(2026, 8, 28))
        assert result.detached_entries == 1
        assert store.entries()["ann1"].status == "detached"
        assert client.patches == []
    finally:
        store.close()

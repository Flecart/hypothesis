# Hypothesis exporter

Bidirectional synchronization between your Hypothesis annotations and Obsidian daily notes.

## Note format and moving quotes

Annotations initially appear under `## Hypothesis` and a `### [Publication](URL)` heading. Each annotation is enclosed by invisible `hypothesis-entry` markers. Move the entire marker-delimited card to categorize it elsewhere in the vault; the next run finds it and continues synchronizing its personal annotation in place. Moving an entire marker-delimited publication section also makes later annotations for that source/date follow it.

Only the personal annotation between `hypothesis-comment` markers is editable in both directions. The exact highlighted quote and generated metadata are owned by Hypothesis. Text surrounding a card is Obsidian-only and is preserved.

Do not copy marker-delimited cards. Duplicate IDs are ambiguous and cause synchronization to stop safely. Removing a card everywhere detaches it locally and never deletes the Hypothesis annotation.

## Commands

The project launcher loads `APP_TOKEN` from the process environment or the local `.env` file.

```bash
./hypothesis-export sync
./hypothesis-export sync --date yesterday
./hypothesis-export sync --date 2026-08-28
./hypothesis-export sync --dry-run
./hypothesis-export status
```

Copy `config.example.toml` to `config.toml` and set the absolute path to your vault. The local configuration is ignored by Git. SQLite state is stored by default at `~/.local/state/hypothesis-exporter/state.sqlite3` and is not placed in the vault.

## User systemd timer

Install and enable the supplied units:

```bash
install -Dm644 systemd/hypothesis-export.service ~/.config/systemd/user/hypothesis-export.service
install -Dm644 systemd/hypothesis-export.timer ~/.config/systemd/user/hypothesis-export.timer
systemctl --user daemon-reload
systemctl --user enable --now hypothesis-export.timer
```

Before installation, adapt `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` in the service template if the repository is not checked out at `~/Desktop/work/hypothesis-exporter`.

The persistent timer runs at 00:05 and synchronizes the previous day. Check it with:

```bash
systemctl --user status hypothesis-export.timer
journalctl --user -u hypothesis-export.service
```

Obsidian does not need to be open. Runs are serialized, file contents are hash-checked before atomic replacement, and API or marker-validation errors stop the run before vault writes.

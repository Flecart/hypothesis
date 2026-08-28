from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Config:
    project_dir: Path
    vault: Path
    daily_directory: str
    timezone: str
    section_heading: str
    api_url: str
    state_path: Path

    @property
    def daily_dir(self) -> Path:
        return self.vault / self.daily_directory


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(path: Path) -> Config:
    path = path.resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project_dir = path.parent
    _load_env(project_dir / ".env")
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return Config(
        project_dir=project_dir,
        vault=Path(data["vault"]).expanduser().resolve(),
        daily_directory=data.get("daily_directory", "daily"),
        timezone=data.get("timezone", "Europe/Rome"),
        section_heading=data.get("section_heading", "Hypothesis"),
        api_url=data.get("api_url", "https://hypothes.is/api").rstrip("/"),
        state_path=Path(data.get("state_path", state_home / "hypothesis-exporter" / "state.sqlite3")).expanduser(),
    )

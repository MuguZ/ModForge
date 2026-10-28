"""Application paths and safe defaults."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    data: Path
    config: Path
    state: Path

    @classmethod
    def default(cls) -> "AppPaths":
        home = Path.home()
        data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser()
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser()
        state_home = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")).expanduser()
        return cls(
            data_home / "modforge",
            config_home / "modforge",
            state_home / "modforge",
        )

    def initialize(self) -> None:
        for path in (self.data / "archives", self.data / "mods", self.data / "backups",
                     self.data / "journals", self.config, self.state):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def database(self) -> Path:
        return self.data / "modforge.sqlite3"

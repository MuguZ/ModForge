"""Application paths and safe defaults."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    data: Path
    config: Path
    state: Path

    @classmethod
    def default(cls) -> "AppPaths":
        home = Path.home()
        return cls(
            Path.home() / ".local/share/modforge",
            home / ".config/modforge",
            home / ".local/state/modforge",
        )

    def initialize(self) -> None:
        for path in (self.data / "archives", self.data / "mods", self.data / "backups",
                     self.data / "journals", self.config, self.state):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def database(self) -> Path:
        return self.data / "modforge.sqlite3"

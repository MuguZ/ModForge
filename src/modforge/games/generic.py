"""Fallback adapter for ordinary file-based games."""
from pathlib import Path

from modforge.games.base import GameAdapter


class GenericFileAdapter(GameAdapter):
    game_id = "generic-files"
    name = "Generic file-based game"

    def validate_root(self, root: Path) -> tuple[bool, str]:
        root = root.expanduser()
        if not root.exists():
            return False, "Installation directory is missing; edit or relocate this registration"
        if not root.is_dir():
            return False, "Installation path is not a directory"
        if not root.resolve().is_dir():
            return False, "Installation directory cannot be accessed"
        return True, "Directory is available; game-specific compatibility is not verified"

    def supported_locations(self) -> tuple[str, ...]:
        return ()

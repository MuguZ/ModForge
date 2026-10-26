from pathlib import Path
from modforge.games.base import GameAdapter


class Cyberpunk2077Adapter(GameAdapter):
    game_id = "cyberpunk2077"
    name = "Cyberpunk 2077"

    def validate_root(self, root: Path) -> tuple[bool, str]:
        root = root.expanduser()
        if not root.is_dir():
            return False, "Directory does not exist"
        if not (root / "bin" / "x64").is_dir():
            return False, "Expected bin/x64 was not found; select the game root"
        return True, "Cyberpunk 2077 installation detected"

    def supported_locations(self) -> tuple[str, ...]:
        return ("archive/pc/mod/", "bin/x64/", "r6/scripts/", "red4ext/plugins/")

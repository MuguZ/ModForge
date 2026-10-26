from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath


class GameAdapter(ABC):
    game_id: str
    name: str

    @abstractmethod
    def validate_root(self, root: Path) -> tuple[bool, str]: ...

    def validate_relative_path(self, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or not path.parts or ".." in path.parts or ":" in path.parts[0]:
            raise ValueError(f"Unsafe deployment path: {value}")
        return path.as_posix()

    @abstractmethod
    def supported_locations(self) -> tuple[str, ...]: ...

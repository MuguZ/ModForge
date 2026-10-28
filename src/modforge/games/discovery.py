"""Read-only, bounded Steam library discovery."""
from __future__ import annotations

import re
from pathlib import Path


_TOKEN = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """Parse Valve's quoted key/value VDF subset, including nested objects."""
    tokens = [match.group(1) if match.group(1) is not None else match.group(2)
              for match in _TOKEN.finditer(text)]
    index = 0

    def unescape(value: str) -> str:
        return re.sub(r'\\([\\"])', r'\1', value)

    def object_body(nested=False):
        nonlocal index
        result = {}
        while index < len(tokens):
            if tokens[index] == "}":
                index += 1
                if not nested:
                    raise ValueError("Unexpected VDF closing brace")
                return result
            key = unescape(tokens[index]); index += 1
            if key == "{":
                raise ValueError("VDF key expected")
            if index >= len(tokens):
                raise ValueError("VDF value missing")
            if tokens[index] == "{":
                index += 1
                value = object_body(True)
            elif tokens[index] == "}":
                raise ValueError("VDF value missing")
            else:
                value = unescape(tokens[index]); index += 1
            result[key] = value
        if nested:
            raise ValueError("Unterminated VDF object")
        return result

    return object_body()


def steam_roots(home: Path | None = None, extras=()) -> list[Path]:
    home = home or Path.home()
    candidates = [home / ".steam/steam", home / ".steam/root", home / ".local/share/Steam",
                  home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
                  Path("/usr/share/steam"), Path("/opt/steam"), *map(Path, extras)]
    found = []
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and resolved not in found:
            found.append(resolved)
    return found


def discover_steam_games(home: Path | None = None, extras=()) -> list[dict]:
    """Read Steam library manifests and return existing game installation roots."""
    libraries: list[Path] = []
    for root in steam_roots(home, extras):
        libraries.append(root)
        manifest = root / "steamapps/libraryfolders.vdf"
        try:
            parsed = parse_vdf(manifest.read_text(encoding="utf-8", errors="strict"))
        except (OSError, UnicodeError, ValueError):
            continue
        section = parsed.get("libraryfolders", parsed.get("LibraryFolders", {}))
        if not isinstance(section, dict):
            continue
        for entry in section.values():
            value = entry.get("path") if isinstance(entry, dict) else entry
            if isinstance(value, str):
                path = Path(value).expanduser()
                if path.is_absolute() and path not in libraries:
                    libraries.append(path)
    results = []
    seen = set()
    for library in libraries:
        apps = library / "steamapps"
        try:
            manifests = sorted(apps.glob("appmanifest_*.acf"))
        except OSError:
            continue
        for manifest in manifests:
            try:
                data = parse_vdf(manifest.read_text(encoding="utf-8", errors="strict"))
                state = data.get("AppState", data.get("appstate", {}))
                name, install_dir = state.get("name"), state.get("installdir")
                if not name or not install_dir or Path(install_dir).name != install_dir:
                    continue
                install = apps / "common" / install_dir
                normalized = str(install.resolve(strict=False))
                if normalized in seen:
                    continue
                seen.add(normalized)
                results.append({"name": name, "root": Path(normalized), "source": "steam",
                                "platform": "linux-or-proton", "app_id": state.get("appid")})
            except (OSError, UnicodeError, ValueError):
                continue
    return results

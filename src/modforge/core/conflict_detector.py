from collections import defaultdict


def detect_conflicts(mod_files: dict[str, list[str]]) -> dict[str, list[str]]:
    destinations: dict[str, list[str]] = defaultdict(list)
    for mod_id, paths in mod_files.items():
        for path in paths:
            destinations[path.casefold()].append(mod_id)
    return {path: mods for path, mods in destinations.items() if len(mods) > 1}

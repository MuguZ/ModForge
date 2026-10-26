import logging
from pathlib import Path


def configure_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(directory / "modforge.log"), logging.StreamHandler()],
    )

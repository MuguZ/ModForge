"""ModForge application entry point."""
import logging
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from modforge.config import AppPaths
from modforge.core.archive_importer import ArchiveImporter
from modforge.core.deployment_manager import DeploymentManager
from modforge.core.mod_library import ModLibrary
from modforge.database import Database
from modforge.logging_config import configure_logging
from modforge.ui.main_window import MainWindow
from modforge.ui.theme import STYLESHEET


def main() -> int:
    paths = AppPaths.default()
    paths.initialize()
    configure_logging(paths.state)
    logging.info("ModForge starting")
    app = QApplication(sys.argv)
    app.setApplicationName("ModForge")
    app.setStyleSheet(STYLESHEET)
    try:
        db = Database(paths.database)
        library = ModLibrary(db, ArchiveImporter(paths.data / "archives", paths.data / "mods"))
        deployments = DeploymentManager(db, paths.data / "backups", paths.data / "journals", paths.data / "locks")
        window = MainWindow(paths, db, library, deployments)
        window.show()
        result = app.exec()
        logging.info("ModForge stopped")
        return result
    except Exception as exc:
        logging.exception("Startup failed")
        QMessageBox.critical(None, "ModForge could not start", str(exc))
        return 1

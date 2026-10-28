"""Offscreen smoke coverage for the explicit deployment preview."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem

from modforge.config import AppPaths
from modforge.core.archive_importer import ArchiveImporter
from modforge.core.deployment_manager import DeploymentManager
from modforge.core.mod_library import ModLibrary
from modforge.core.profile_manager import ProfileManager
from modforge.database import Database
from modforge.ui.main_window import MainWindow
from modforge.games.generic import GenericFileAdapter
from modforge.games.registry import GameRegistry


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, qt_app):
    paths = AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "state")
    paths.initialize()
    db = Database(paths.database)
    library = ModLibrary(db, ArchiveImporter(paths.data / "archives", paths.data / "mods"))
    deployments = DeploymentManager(db, paths.data / "backups", paths.data / "journals", paths.data / "locks")
    return MainWindow(paths, db, library, deployments)


def test_preview_describes_existing_and_new_destinations(window, tmp_path, monkeypatch):
    root = tmp_path / "fake-game"
    (root / "bin/x64").mkdir(parents=True)
    old = root / "archive/pc/mod/old.archive"
    old.parent.mkdir(parents=True)
    old.write_text("original")
    old_source = tmp_path / "old-source"
    new_source = tmp_path / "new-source"
    old_source.write_text("replacement")
    new_source.write_text("new")
    captured = {}

    def accept(dialog):
        table = dialog.findChild(QTableWidget)
        captured["actions"] = [table.item(row, 3).text() for row in range(table.rowCount())]
        captured["enabled"] = dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Yes).isEnabled()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    accepted = window._show_deployment_preview(
        root, "default",
        [{"relative_path": "archive/pc/mod/old.archive", "name": "Old"},
         {"relative_path": "r6/scripts/new.lua", "name": "New"}],
        {"archive/pc/mod/old.archive": old_source, "r6/scripts/new.lua": new_source},
    )
    assert accepted
    assert captured["actions"] == ["Replace; create verified backup", "Create new file"]
    assert captured["enabled"]


def test_preview_blocks_symlink_destinations(window, tmp_path, monkeypatch):
    root = tmp_path / "fake-game"
    (root / "bin/x64").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("keep")
    (root / "unsafe.lua").symlink_to(outside)
    source = tmp_path / "source"
    source.write_text("mod")
    captured = {}

    def accept(dialog):
        table = dialog.findChild(QTableWidget)
        captured["action"] = table.item(0, 3).text()
        captured["enabled"] = dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Yes).isEnabled()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    accepted = window._show_deployment_preview(
        root, "default", [{"relative_path": "unsafe.lua", "name": "Unsafe"}], {"unsafe.lua": source}
    )
    assert accepted
    assert captured["action"] == "Blocked: symbolic link in destination path"
    assert not captured["enabled"]
    assert outside.read_text() == "keep"


def test_profile_preview_lists_modforge_owned_removals(window,tmp_path,monkeypatch):
    root=tmp_path/"fake-game";root.mkdir()
    game_id=GameRegistry(window.db).register("Preview Game",root)
    profile=ProfileManager(window.db).list_profiles(game_id)[0]["id"]
    source=tmp_path/"source";source.write_text("mod")
    deployment=window.deployments.deploy(root,GenericFileAdapter(),profile,{"mod/file.dat":source},game_id=game_id)
    captured={}
    def accept(dialog):
        table=dialog.findChild(QTableWidget)
        captured["action"]=table.item(0,3).text()
        captured["enabled"]=dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Yes).isEnabled()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,"exec",accept)
    assert window._show_deployment_preview(root,profile,[],{},game_id)
    assert captured["action"]=="Remove ModForge-created file if unchanged"
    assert captured["enabled"]
    assert (root/"mod/file.dat").exists()


def test_backup_restore_action_targets_the_selected_transaction(window,monkeypatch):
    window.backup_table.setRowCount(1)
    window.backup_table.setItem(0,0,QTableWidgetItem("transaction-id"))
    window.backup_table.selectRow(0)
    captured={}
    monkeypatch.setattr(window,"_rollback_deployment",lambda deployment_id:captured.setdefault("id",deployment_id))
    window.rollback_backup_selected()
    assert captured["id"]=="transaction-id"

from __future__ import annotations
import logging
import stat
from pathlib import Path
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QInputDialog, QDialog, QDialogButtonBox,
    QProgressBar)
from modforge.config import AppPaths
from modforge.core.archive_importer import ArchiveError
from modforge.core.conflict_detector import detect_conflicts
from modforge.core.deployment_manager import DeploymentError
from modforge.core.profile_manager import ProfileManager
from modforge.core.rollback_manager import RollbackManager
from modforge.core.recovery_manager import RecoveryManager
from modforge.database import Database
from modforge.games.discovery import discover_steam_games
from modforge.games.registry import GameRegistry
from modforge.ui.workers import FunctionWorker

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths, db: Database, library, deployments):
        super().__init__()
        self.paths, self.db, self.library, self.deployments = paths, db, library, deployments
        self.profiles = ProfileManager(db)
        self.games = GameRegistry(db)
        self.rollback_manager = RollbackManager(db, paths.data / "locks")
        self.recovery_manager = RecoveryManager(db, paths.data / "backups", paths.data / "journals", paths.data / "locks")
        self.thread_pool = QThreadPool.globalInstance()
        self.busy = False
        self.setWindowTitle("ModForge")
        self.resize(1280, 780)
        root = QWidget(); layout = QHBoxLayout(root); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        sidebar = QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar); side.setContentsMargins(14,10,14,18)
        brand = QLabel("◈  MODFORGE"); brand.setObjectName("brand"); side.addWidget(brand)
        self.stack = QStackedWidget(); self.pages = {}
        for name in ("Dashboard", "Games", "Mod Library", "Profiles", "Deployments", "Recovery", "Backups", "Settings"):
            button = QPushButton(name); button.setCheckable(True); button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, n=name: self.show_page(n))
            side.addWidget(button); self.pages[name] = self._make_page(name)
            self.stack.addWidget(self.pages[name])
            if name == "Dashboard": self.nav_buttons = []
            self.nav_buttons.append(button)
        side.addStretch()
        version = QLabel("SAFE FILE DEPLOYMENT\nVersion 0.1.0"); version.setObjectName("muted"); side.addWidget(version)
        self.operation_label = QLabel("Ready"); self.operation_label.setObjectName("muted"); self.operation_label.setWordWrap(True); side.addWidget(self.operation_label)
        self.operation_progress = QProgressBar(); self.operation_progress.setRange(0, 0); self.operation_progress.setTextVisible(False); self.operation_progress.hide(); side.addWidget(self.operation_progress)
        layout.addWidget(sidebar); layout.addWidget(self.stack, 1); self.setCentralWidget(root)
        self.show_page("Dashboard")
        self.refresh()

    def _make_page(self, name: str) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(34,28,34,28); layout.setSpacing(18)
        header = QHBoxLayout(); title = QLabel(name); title.setObjectName("heading"); header.addWidget(title); header.addStretch()
        if name == "Mod Library":
            self.search = QLineEdit(); self.search.setPlaceholderText("Search your library…"); self.search.textChanged.connect(self.refresh_mod_table)
            header.addWidget(self.search)
            button = QPushButton("＋  Import ZIP"); button.setObjectName("primary"); button.clicked.connect(self.import_zip); header.addWidget(button)
        if name == "Profiles":
            button = QPushButton("＋  New profile"); button.clicked.connect(self.create_profile); header.addWidget(button)
        layout.addLayout(header)
        if name == "Dashboard":
            self.dashboard_info = QLabel(); self.dashboard_info.setWordWrap(True); self.dashboard_info.setObjectName("muted"); layout.addWidget(self.dashboard_info)
            actions = QHBoxLayout()
            deploy = QPushButton("Deploy active profile"); deploy.setObjectName("primary"); deploy.clicked.connect(self.deploy_profile); actions.addWidget(deploy)
            actions.addStretch(); layout.addLayout(actions)
            self.activity = QLabel("No deployment activity yet."); self.activity.setObjectName("muted"); layout.addWidget(self.activity); layout.addStretch()
        elif name == "Games":
            actions=QHBoxLayout()
            add_game=QPushButton("＋  Add Game"); add_game.setObjectName("primary"); add_game.clicked.connect(self.add_game); actions.addWidget(add_game)
            discover=QPushButton("Discover Steam games"); discover.clicked.connect(self.discover_games); actions.addWidget(discover)
            set_active=QPushButton("Use selected"); set_active.clicked.connect(self.set_active_game); actions.addWidget(set_active)
            edit=QPushButton("Edit selected"); edit.clicked.connect(self.edit_game); actions.addWidget(edit)
            rescan=QPushButton("Rescan"); rescan.clicked.connect(self.rescan_games); actions.addWidget(rescan)
            remove=QPushButton("Remove registration"); remove.clicked.connect(self.remove_game); actions.addWidget(remove); actions.addStretch(); layout.addLayout(actions)
            self.games_table=QTableWidget(0,5); self.games_table.setHorizontalHeaderLabels(["Game", "Installation", "Status", "Strategy", "Source"]); self.games_table.horizontalHeader().setStretchLastSection(True); self.games_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); layout.addWidget(self.games_table,1)
            self.game_status = QLabel("Generic file-based management is available without a dedicated adapter. Game and mod compatibility remains game-specific."); self.game_status.setObjectName("muted"); self.game_status.setWordWrap(True); layout.addWidget(self.game_status)
        elif name == "Mod Library":
            self.mod_table = QTableWidget(0,5); self.mod_table.setHorizontalHeaderLabels(["Mod", "Version", "Imported", "Archive", "Files"]); self.mod_table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.mod_table,1)
            self.mod_empty = QLabel("Your library is empty. Import a ZIP archive to get started."); self.mod_empty.setObjectName("muted"); self.mod_empty.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(self.mod_empty)
            self.mod_table.cellDoubleClicked.connect(self.show_mod_contents)
        elif name == "Profiles":
            row = QHBoxLayout(); self.profile_combo = QComboBox(); self.profile_combo.currentIndexChanged.connect(self.refresh_profile_files); row.addWidget(QLabel("Active profile")); row.addWidget(self.profile_combo,1)
            duplicate = QPushButton("Duplicate"); duplicate.clicked.connect(self.duplicate_profile); row.addWidget(duplicate)
            rename = QPushButton("Rename"); rename.clicked.connect(self.rename_profile); row.addWidget(rename)
            delete = QPushButton("Delete"); delete.clicked.connect(self.delete_profile); row.addWidget(delete); layout.addLayout(row)
            self.profile_table = QTableWidget(0,4); self.profile_table.setHorizontalHeaderLabels(["Enabled", "Mod", "Version", "Imported"]); self.profile_table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.profile_table,1)
            self.profile_empty = QLabel("No imported mods yet. Add ZIP archives in Mod Library."); self.profile_empty.setObjectName("muted"); self.profile_empty.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(self.profile_empty)
            self.profile_table.cellChanged.connect(self.profile_toggled)
        elif name == "Deployments":
            self.deployment_table = QTableWidget(0,5); self.deployment_table.setHorizontalHeaderLabels(["ID", "Started", "Profile", "Files", "Status"]); self.deployment_table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.deployment_table,1)
            self.deployment_empty = QLabel("No deployments recorded yet."); self.deployment_empty.setObjectName("muted"); self.deployment_empty.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(self.deployment_empty)
            btn = QPushButton("Rollback selected deployment"); btn.clicked.connect(self.rollback_selected); layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        elif name == "Recovery":
            self.recovery_table = QTableWidget(0,5); self.recovery_table.setHorizontalHeaderLabels(["Deployment ID", "Started", "State", "Files", "Issue"]); self.recovery_table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.recovery_table,1)
            self.recovery_empty = QLabel("No unresolved deployment journals. Startup did not make any recovery changes."); self.recovery_empty.setObjectName("muted"); self.recovery_empty.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(self.recovery_empty)
            review = QPushButton("Review selected recovery…"); review.setObjectName("primary"); review.clicked.connect(self.review_recovery); layout.addWidget(review,alignment=Qt.AlignmentFlag.AlignLeft)
        elif name == "Backups":
            label = QLabel(f"Deployment snapshots are retained under {self.paths.data / 'backups'}."); label.setObjectName("muted"); layout.addWidget(label)
            self.backup_table = QTableWidget(0,4); self.backup_table.setHorizontalHeaderLabels(["Deployment ID", "Created", "Files", "Snapshot status"]); self.backup_table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.backup_table,1)
            self.backup_empty = QLabel("No deployment snapshots yet."); self.backup_empty.setObjectName("muted"); self.backup_empty.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(self.backup_empty)
            retained = QLabel("Snapshots remain available after rollback."); retained.setObjectName("muted"); layout.addWidget(retained)
        else:
            layout.addWidget(QLabel("Game installations are managed from the Games page. Select a registered game as active before previewing deployment."));
            layout.addWidget(QLabel(f"Application data: {self.paths.data}\nConfiguration: {self.paths.config}\nLogs: {self.paths.state}")); open_logs=QPushButton("Open logs folder"); open_logs.clicked.connect(lambda: self.open_folder(self.paths.state)); layout.addWidget(open_logs,alignment=Qt.AlignmentFlag.AlignLeft); layout.addStretch()
        return page

    def show_page(self, name: str) -> None:
        idx = list(self.pages).index(name); self.stack.setCurrentIndex(idx)
        for i, button in enumerate(self.nav_buttons): button.setChecked(i == idx)

    def refresh(self) -> None:
        self.refresh_games()
        if hasattr(self, "mod_table"): self.refresh_mod_table()
        if hasattr(self, "profile_combo"): self.refresh_profiles()
        if hasattr(self, "deployment_table"): self.refresh_deployments()
        if hasattr(self, "recovery_table"): self.refresh_recovery()
        if hasattr(self, "backup_table"): self.refresh_backups()
        if hasattr(self, "dashboard_info"):
            mods = self.library.list_mods()
            active = self.db.setting("active_profile", "default")
            profile = next((p["name"] for p in self.profiles.list_profiles() if p["id"] == active), "Default")
            library_status = "Import a ZIP in Mod Library to begin." if not mods else f"Imported mods: {len(mods)}"
            game=self._active_game()
            game_label=game["name"] if game else "No game selected"
            game_root=game["root"] if game else "Register a game on the Games page"
            self.dashboard_info.setText(f"<b>{game_label}</b>　 ·　Profile: {profile}　 ·　{library_status}\nGame root: {game_root}\nSelect a deployment after reviewing the destination preview.")
            unresolved_count = len(self.recovery_manager.unresolved())
            if unresolved_count:
                self.dashboard_info.setText(self.dashboard_info.text() + f"\n<span style='color:#ffbd69'>Recovery needs attention: {unresolved_count} operation(s)</span>")
        self.refresh_game_status()

    def _active_game(self):
        game_id=self.db.setting("active_game", "")
        game=self.games.get(game_id) if game_id else None
        if game is None:
            all_games=self.games.list_games()
            game=all_games[0] if all_games else None
            if game:
                self.db.set_setting("active_game",game["id"])
        return game

    def _game_context(self):
        game=self._active_game()
        if not game:
            return None, None
        return Path(game["root"]), self.games.adapter_for(game["id"])

    def refresh_games(self):
        if not hasattr(self,"games_table"): return
        games=self.games.list_games(); active=self.db.setting("active_game", "")
        self.games_table.setRowCount(len(games))
        for i,game in enumerate(games):
            status=game["validation_message"] or game["validation_status"]
            values=[game["name"]+ ("  ·  Active" if game["id"]==active else ""), game["root"], status, game["strategy"], game["source"]]
            for j,value in enumerate(values):
                item=QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole,game["id"]); self.games_table.setItem(i,j,item)

    def _selected_game_id(self):
        row=self.games_table.currentRow() if hasattr(self,"games_table") else -1
        if row < 0: return ""
        return self.games_table.item(row,0).data(Qt.ItemDataRole.UserRole)

    def add_game(self):
        name,ok=QInputDialog.getText(self,"Add Game","Display name")
        if not ok:return
        path=QFileDialog.getExistingDirectory(self,"Select installation directory",str(Path.home()))
        if not path:return
        executable,exec_ok=QInputDialog.getText(self,"Optional executable","Executable path relative to installation directory (leave blank if launcher-managed)")
        if not exec_ok:return
        try:
            game_id=self.games.register(name,path,executable=executable.strip() or None)
            self.db.set_setting("active_game",game_id)
            self.refresh()
        except Exception as exc:QMessageBox.warning(self,"Game not registered",str(exc))

    def discover_games(self):
        try: candidates=discover_steam_games()
        except Exception as exc:
            QMessageBox.warning(self,"Steam discovery failed",str(exc)); return
        if not candidates:
            QMessageBox.information(self,"No Steam installations found","No installed game manifests were found in the standard Steam libraries."); return
        choices=[f"{g['name']} — {g['root']}" for g in candidates]
        choice,ok=QInputDialog.getItem(self,"Discovered Steam games","Choose an installation to register",choices,0,False)
        if not ok:return
        game=candidates[choices.index(choice)]
        try:
            game_id=self.games.register(game["name"],game["root"],source="steam",platform=game["platform"])
            self.db.set_setting("active_game",game_id); self.refresh()
        except Exception as exc:QMessageBox.warning(self,"Game not registered",str(exc))

    def set_active_game(self):
        game_id=self._selected_game_id()
        if game_id:self.db.set_setting("active_game",game_id); self.refresh()

    def rescan_games(self):
        for game in self.games.list_games(): self.games.validate(game["id"])
        self.refresh()

    def remove_game(self):
        game_id=self._selected_game_id()
        if not game_id:return
        answer=QMessageBox.question(self,"Remove game registration","Remove this entry from ModForge? The game files will not be changed.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        self.games.remove(game_id)
        if self.db.setting("active_game")==game_id:self.db.set_setting("active_game","")
        self.refresh()

    def edit_game(self):
        game_id=self._selected_game_id()
        game=self.games.get(game_id) if game_id else None
        if not game:return
        name,ok=QInputDialog.getText(self,"Edit Game","Display name",text=game["name"])
        if not ok:return
        root,ok=QInputDialog.getText(self,"Edit Game","Installation directory",text=game["root"])
        if not ok:return
        executable,ok=QInputDialog.getText(self,"Edit Game","Optional executable path",text=game["executable"] or "")
        if not ok:return
        try:
            self.games.edit(game_id,name=name,root=root,executable=executable.strip() or None)
            self.refresh()
        except Exception as exc:QMessageBox.warning(self,"Game not updated",str(exc))

    def refresh_mod_table(self) -> None:
        if not hasattr(self,"mod_table"): return
        mods = self.library.list_mods(); query = self.search.text().casefold() if hasattr(self,"search") else ""
        with self.db.connect() as db:
            rows=[]
            for mod in mods:
                if query and query not in mod["name"].casefold(): continue
                count=db.execute("SELECT COUNT(*) FROM mod_files WHERE mod_id=?",(mod["id"],)).fetchone()[0]
                rows.append((mod,count))
        self.mod_table.setRowCount(len(rows))
        if hasattr(self,"mod_empty"):
            if rows: self.mod_empty.setText("")
            elif query: self.mod_empty.setText("No mods match this search.")
            else: self.mod_empty.setText("Your library is empty. Import a ZIP archive to get started.")
        self.mod_empty.setVisible(not rows)
        for i,(mod,count) in enumerate(rows):
            vals=[mod["name"],mod.get("version") or "—",mod["imported_at"][:10],Path(mod["archive"]).name,str(count)]
            for j,value in enumerate(vals):
                cell=QTableWidgetItem(value); cell.setData(Qt.ItemDataRole.UserRole,mod["id"]); self.mod_table.setItem(i,j,cell)

    def import_zip(self) -> None:
        filename,_=QFileDialog.getOpenFileName(self,"Import mod archive",str(Path.home()),"ZIP archives (*.zip)")
        if not filename:return
        self._run_background("Importing archive…", lambda: self.library.import_archive(Path(filename)),
            lambda mod_id: (log.info("Imported mod %s",mod_id), QMessageBox.information(self,"Import complete","Archive was validated and added to your library. Review its file paths before enabling it.")))

    def show_mod_contents(self,row:int,_column:int) -> None:
        mod_id=self.mod_table.item(row,0).data(Qt.ItemDataRole.UserRole)
        with self.db.connect() as db: files=db.execute("SELECT relative_path,size FROM mod_files WHERE mod_id=? ORDER BY relative_path",(mod_id,)).fetchall()
        text="\n".join(f"{r['relative_path']}  ({r['size']} bytes)" for r in files)
        QMessageBox.information(self,"Archive contents and destination preview",text or "No files")

    def refresh_profiles(self) -> None:
        if not hasattr(self,"profile_combo"):return
        current=self.db.setting("active_profile","default"); profiles=self.profiles.list_profiles()
        self.profile_combo.blockSignals(True); self.profile_combo.clear()
        for profile in profiles:self.profile_combo.addItem(profile["name"],profile["id"])
        index=self.profile_combo.findData(current); self.profile_combo.setCurrentIndex(max(index,0)); self.profile_combo.blockSignals(False)
        self.refresh_profile_files()

    def refresh_profile_files(self) -> None:
        if not hasattr(self,"profile_combo") or not self.profile_combo.count():return
        profile=self.profile_combo.currentData(); self.db.set_setting("active_profile",profile)
        with self.db.connect() as db:
            mods=db.execute("SELECT m.*, EXISTS(SELECT 1 FROM profile_mods p WHERE p.mod_id=m.id AND p.profile_id=?) enabled FROM mods m ORDER BY name",(profile,)).fetchall()
        self.profile_table.blockSignals(True); self.profile_table.setRowCount(len(mods))
        for i,mod in enumerate(mods):
            check=QTableWidgetItem(); check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable); check.setCheckState(Qt.CheckState.Checked if mod["enabled"] else Qt.CheckState.Unchecked); check.setData(Qt.ItemDataRole.UserRole,mod["id"]); self.profile_table.setItem(i,0,check)
            for j,val in enumerate([mod["name"],mod["version"] or "—",mod["imported_at"][:10],],1):self.profile_table.setItem(i,j,QTableWidgetItem(val))
        self.profile_table.blockSignals(False)
        self.profile_empty.setVisible(not mods)

    def profile_toggled(self,row:int,column:int) -> None:
        if column!=0:return
        item=self.profile_table.item(row,0); profile=self.profile_combo.currentData()
        self.profiles.set_enabled(profile,item.data(Qt.ItemDataRole.UserRole),item.checkState()==Qt.CheckState.Checked)

    def create_profile(self) -> None:
        name,ok=QInputDialog.getText(self,"New profile","Profile name")
        if ok:
            try:self.profiles.create(name); self.refresh_profiles()
            except Exception as exc:QMessageBox.warning(self,"Profile not created",str(exc))
    def duplicate_profile(self) -> None:
        name,ok=QInputDialog.getText(self,"Duplicate profile","New profile name")
        if ok:
            try:self.profiles.create(name,self.profile_combo.currentData()); self.refresh_profiles()
            except Exception as exc:QMessageBox.warning(self,"Profile not duplicated",str(exc))
    def rename_profile(self) -> None:
        name,ok=QInputDialog.getText(self,"Rename profile","New name",text=self.profile_combo.currentText())
        if ok:
            try:self.profiles.rename(self.profile_combo.currentData(),name); self.refresh_profiles()
            except Exception as exc:QMessageBox.warning(self,"Profile not renamed",str(exc))
    def delete_profile(self) -> None:
        try:self.profiles.delete(self.profile_combo.currentData(),self.db.setting("active_profile","default")); self.db.set_setting("active_profile","default"); self.refresh_profiles()
        except Exception as exc:QMessageBox.warning(self,"Profile not deleted",str(exc))

    def deploy_profile(self) -> None:
        root,adapter=self._game_context()
        if root is None:
            QMessageBox.warning(self,"No game selected","Register a game on the Games page before deploying."); return
        valid,reason=adapter.validate_root(root)
        if not valid:
            QMessageBox.warning(self,"Game directory not ready",reason+". Edit the installation on the Games page.")
            return
        profile=self.db.setting("active_profile","default")
        with self.db.connect() as db:
            rows=db.execute("SELECT m.id,m.extracted_path,m.name,mf.relative_path FROM profile_mods pm JOIN mods m ON m.id=pm.mod_id JOIN mod_files mf ON mf.mod_id=m.id WHERE pm.profile_id=?",(profile,)).fetchall()
        targets={}; by_mod={}; mod_names={}
        for row in rows:
            by_mod.setdefault(row["id"],[]).append(row["relative_path"])
            mod_names[row["id"]]=row["name"]
            targets.setdefault(row["relative_path"],Path(row["extracted_path"])/row["relative_path"])
        conflicts=detect_conflicts(by_mod)
        if conflicts:
            details=[]
            for destination,mod_ids in conflicts.items():
                details.append(f"{destination}\n  " + ", ".join(mod_names.get(mod_id,mod_id) for mod_id in mod_ids))
            QMessageBox.warning(self,"Conflicts block deployment","These enabled mods target the same destination. Disable one mod in this profile before deploying:\n\n"+"\n\n".join(details)); return
        if not targets:QMessageBox.information(self,"Nothing to deploy","Enable at least one mod in the active profile."); return
        if not self._show_deployment_preview(root,profile,rows,targets):return
        self._run_background("Deploying files; cancellation is unavailable during recovery-safe transaction…",
            lambda: self.deployments.deploy(root,adapter,profile,targets),
            lambda uid: QMessageBox.information(self,"Deployment complete",f"Deployment {uid} completed and verified."))

    def _show_deployment_preview(self, root: Path, profile: str, rows, targets: dict[str,Path]) -> bool:
        dialog=QDialog(self); dialog.setWindowTitle("Deployment preview"); dialog.resize(980,620)
        layout=QVBoxLayout(dialog)
        title=QLabel(f"Profile: {self.profile_combo.currentText()}\nGame root: {root}\nReview every destination before confirming.")
        title.setWordWrap(True); layout.addWidget(title)
        table=QTableWidget(len(targets),4); table.setHorizontalHeaderLabels(["Mod", "Relative destination", "Game file", "Planned action"]); table.horizontalHeader().setStretchLastSection(True); layout.addWidget(table,1)
        names={row["relative_path"]:row["name"] for row in rows}
        blocked=False
        for index,(relative,source) in enumerate(targets.items()):
            destination=root.joinpath(*Path(relative).parts)
            try:
                cursor=root
                parts=Path(relative).parts
                action=None
                for part_index,part in enumerate(parts):
                    cursor=cursor/part
                    try:mode=cursor.lstat().st_mode
                    except FileNotFoundError:
                        action="Create new file"
                        break
                    if stat.S_ISLNK(mode):
                        action="Blocked: symbolic link in destination path"
                        break
                    if part_index<len(parts)-1 and not stat.S_ISDIR(mode):
                        action="Blocked: destination parent is not a directory"
                        break
                    if part_index==len(parts)-1:
                        action="Replace; create verified backup" if stat.S_ISREG(mode) else "Blocked: not a regular file"
                if action is None:action="Blocked: invalid destination path"
            except OSError:
                action="Blocked: cannot inspect destination"
            blocked |= action.startswith("Blocked:")
            values=[names.get(relative,"Enabled mod"),relative,str(destination),action]
            for column,value in enumerate(values):table.setItem(index,column,QTableWidgetItem(value))
        note=QLabel("Existing regular files are backed up before replacement. Unrelated files are left untouched.")
        note.setObjectName("muted"); layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Yes|QDialogButtonBox.StandardButton.Cancel)
        deploy_button=buttons.button(QDialogButtonBox.StandardButton.Yes); deploy_button.setText("Deploy")
        deploy_button.setEnabled(not blocked)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        return dialog.exec()==QDialog.DialogCode.Accepted

    def _run_background(self, label: str, function, on_success, on_error=None) -> None:
        if self.busy:
            QMessageBox.information(self,"Operation in progress","Wait for the current filesystem operation to finish.")
            return
        self.busy = True
        self.operation_label.setText(label)
        self.operation_progress.show()
        if hasattr(self,"dashboard_info"):
            self.activity.setText(label)
        worker = FunctionWorker(function)
        worker.signals.finished.connect(lambda result: self._background_success(result,on_success))
        worker.signals.failed.connect(lambda error:self._background_failure(error,on_error))
        self.thread_pool.start(worker)

    def _background_success(self, result, callback) -> None:
        self.busy = False
        self.operation_progress.hide()
        self.operation_label.setText("Operation completed")
        callback(result)
        self.refresh()

    def _background_failure(self, error: str, on_error=None) -> None:
        self.busy = False
        self.operation_progress.hide()
        self.operation_label.setText("Operation needs attention")
        log.error("Background filesystem operation failed: %s",error)
        if on_error:
            on_error(error)
        else:
            QMessageBox.critical(self,"Operation failed",error)
        self.refresh()

    def refresh_deployments(self) -> None:
        if not hasattr(self,"deployment_table"):return
        with self.db.connect() as db:rows=db.execute("""SELECT d.*,p.name profile,
            (SELECT COUNT(*) FROM deployment_files f WHERE f.deployment_id=d.id) file_count
            FROM deployments d LEFT JOIN profiles p ON p.id=d.profile_id ORDER BY started_at DESC""").fetchall()
        self.deployment_table.setRowCount(len(rows))
        self.deployment_empty.setVisible(not rows)
        for i,row in enumerate(rows):
            for j,val in enumerate([row["id"],row["started_at"][:19],row["profile"] or "Deleted profile",str(row["file_count"]),row["status"]]):
                item=QTableWidgetItem(val); item.setData(Qt.ItemDataRole.UserRole,row["id"]); self.deployment_table.setItem(i,j,item)

    def refresh_recovery(self) -> None:
        items=self.recovery_manager.unresolved()
        self.recovery_table.setRowCount(len(items))
        self.recovery_empty.setVisible(not items)
        for i,entry in enumerate(items):
            values=[entry["id"],entry["started_at"][:19] if entry["started_at"]!="unknown" else "unknown",
                    entry["status"],str(len(entry["files"])),entry["issue"]]
            for j,value in enumerate(values):
                cell=QTableWidgetItem(value); cell.setData(Qt.ItemDataRole.UserRole,entry["id"]); self.recovery_table.setItem(i,j,cell)

    def refresh_backups(self) -> None:
        with self.db.connect() as db:
            rows=db.execute("""SELECT d.id,d.started_at,f.existed,f.backup_path
                FROM deployments d LEFT JOIN deployment_files f ON f.deployment_id=d.id
                ORDER BY d.started_at DESC""").fetchall()
        grouped={}
        for row in rows:
            entry=grouped.setdefault(row["id"],{"date":row["started_at"][:19],"paths":[]})
            if row["existed"]: entry["paths"].append(row["backup_path"])
        self.backup_table.setRowCount(len(grouped)); self.backup_empty.setVisible(not grouped)
        for i,(deployment_id,entry) in enumerate(grouped.items()):
            paths=entry["paths"]
            state="No overwritten files" if not paths else (
                "Missing — inspect Recovery" if any(not path or Path(path).is_symlink() or not Path(path).is_file() for path in paths) else
                "Present; hash checked on recovery")
            for j,value in enumerate([deployment_id,entry["date"],str(len(paths)),state]):
                self.backup_table.setItem(i,j,QTableWidgetItem(value))

    def review_recovery(self) -> None:
        row=self.recovery_table.currentRow()
        if row<0:return
        deployment_id=self.recovery_table.item(row,0).text()
        root,adapter=self._game_context()
        if root is None:
            QMessageBox.warning(self,"No game selected","Select a registered game before recovery."); return
        self._run_background("Inspecting deployment journal and backups…",
            lambda:self.recovery_manager.inspect(deployment_id,root,adapter),
            lambda report:self._confirm_recovery(deployment_id,root,adapter,report))

    def _confirm_recovery(self, deployment_id: str, root: Path, adapter, report: dict) -> None:
        lines=[f"Deployment: {report['id']}",f"Recorded state: {report['status']}","", "File assessment:"]
        lines.extend(f"• {f['path']} — {f['condition']}; backup: {f['backup_status']}" for f in report["files"])
        if report["status"] in {"committed", "rolled_back"}:
            action="This operation only verifies the recorded final files and reconciles its database history. It does not change game files."
        else:
            action="Recovery restores the pre-deployment state where the journal and verified backups permit it. Unknown files are preserved."
        lines.extend(["", action])
        result=QMessageBox.question(self,"Review recovery","\n".join(lines)+"\n\nAttempt recovery now?",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if result!=QMessageBox.StandardButton.Yes:return
        self._run_background("Recovering deployment; journal progress is being saved…",
            lambda:self.recovery_manager.recover(deployment_id,root,adapter,confirmed=True),
            lambda outcome:QMessageBox.information(self,"Recovery complete",
                f"Deployment {outcome['id']} is now recorded as {outcome['status']}."))
    def rollback_selected(self) -> None:
        row=self.deployment_table.currentRow()
        if row<0:return
        uid=self.deployment_table.item(row,0).text(); root,adapter=self._game_context()
        if root is None:
            QMessageBox.warning(self,"No game selected","Select a registered game before rollback."); return
        answer=QMessageBox.question(self,"Confirm rollback",f"Restore files from deployment {uid}? Backups will be retained.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        self._run_background("Rolling back deployment; progress is being journaled…",
            lambda:self.rollback_manager.rollback(uid,root,adapter),
            lambda _result:QMessageBox.information(self,"Rollback complete","Files were restored and backups were retained."),
            lambda error:self._rollback_failed(uid,root,adapter,error))

    def _rollback_failed(self, uid: str, root: Path, adapter, error: str) -> None:
        if "ambiguous files and needs confirmation" in error or "Files changed outside ModForge" in error:
            confirm=QMessageBox.question(self,"Files changed outside ModForge",
                f"{error}\n\nRestore verified original backups over these changes? Unknown files created by another process will be preserved.",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
            if confirm==QMessageBox.StandardButton.Yes:
                self._run_background("Restoring confirmed backup files…",
                    lambda:self.rollback_manager.rollback(uid,root,adapter,confirm_external_changes=True),
                    lambda _result:QMessageBox.information(self,"Rollback complete","Original files were restored. Backups remain available."))
        else:
            QMessageBox.critical(self,"Rollback stopped",error)
        self.refresh()

    def refresh_game_status(self) -> None:
        if hasattr(self,"game_status"):
            games=self.games.list_games()
            self.game_status.setText(f"{len(games)} registered game installation(s). Generic file deployment does not verify game-specific mod compatibility.")
    @staticmethod
    def open_folder(path:Path) -> None:
        # Use Qt's desktop integration so this remains compatible with Wayland environments.
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

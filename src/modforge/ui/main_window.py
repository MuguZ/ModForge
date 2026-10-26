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
from modforge.games.cyberpunk2077 import Cyberpunk2077Adapter
from modforge.ui.workers import FunctionWorker

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths, db: Database, library, deployments):
        super().__init__()
        self.paths, self.db, self.library, self.deployments = paths, db, library, deployments
        self.profiles = ProfileManager(db)
        self.rollback_manager = RollbackManager(db, paths.data / "locks")
        self.recovery_manager = RecoveryManager(db, paths.data / "backups", paths.data / "journals", paths.data / "locks")
        self.adapter = Cyberpunk2077Adapter()
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
            layout.addWidget(QLabel("Cyberpunk 2077 is the first supported game.")); self.game_status = QLabel(); self.game_status.setObjectName("muted"); layout.addWidget(self.game_status); layout.addStretch()
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
            layout.addWidget(QLabel("Cyberpunk 2077 game root")); row=QHBoxLayout(); self.game_path=QLineEdit(self.db.setting("game_root")); self.game_path.setPlaceholderText("Select the directory containing bin/x64"); row.addWidget(self.game_path)
            browse=QPushButton("Browse…"); browse.clicked.connect(self.browse_game); row.addWidget(browse); save=QPushButton("Save path"); save.clicked.connect(self.save_game); row.addWidget(save); layout.addLayout(row)
            self.settings_status=QLabel(); self.settings_status.setObjectName("muted"); layout.addWidget(self.settings_status)
            layout.addWidget(QLabel(f"Application data: {self.paths.data}\nConfiguration: {self.paths.config}\nLogs: {self.paths.state}")); open_logs=QPushButton("Open logs folder"); open_logs.clicked.connect(lambda: self.open_folder(self.paths.state)); layout.addWidget(open_logs,alignment=Qt.AlignmentFlag.AlignLeft); layout.addStretch()
        return page

    def show_page(self, name: str) -> None:
        idx = list(self.pages).index(name); self.stack.setCurrentIndex(idx)
        for i, button in enumerate(self.nav_buttons): button.setChecked(i == idx)
        if name == "Settings": self.validate_game_path()

    def refresh(self) -> None:
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
            self.dashboard_info.setText(f"<b>Cyberpunk 2077</b>　 ·　Profile: {profile}　 ·　{library_status}\nGame root: {self.db.setting('game_root', 'Not configured')}\nSelect a deployment after reviewing the destination preview.")
            unresolved_count = len(self.recovery_manager.unresolved())
            if unresolved_count:
                self.dashboard_info.setText(self.dashboard_info.text() + f"\n<span style='color:#ffbd69'>Recovery needs attention: {unresolved_count} operation(s)</span>")
        self.refresh_game_status()

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
        root=Path(self.db.setting("game_root",""))
        valid,reason=self.adapter.validate_root(root)
        if not valid:
            QMessageBox.warning(self,"Game directory not ready",reason+". Choose a valid Cyberpunk 2077 root in Settings before deploying.")
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
            lambda: self.deployments.deploy(root,self.adapter,profile,targets),
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
        root=Path(self.db.setting("game_root",""))
        self._run_background("Inspecting deployment journal and backups…",
            lambda:self.recovery_manager.inspect(deployment_id,root,self.adapter),
            lambda report:self._confirm_recovery(deployment_id,root,report))

    def _confirm_recovery(self, deployment_id: str, root: Path, report: dict) -> None:
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
            lambda:self.recovery_manager.recover(deployment_id,root,self.adapter,confirmed=True),
            lambda outcome:QMessageBox.information(self,"Recovery complete",
                f"Deployment {outcome['id']} is now recorded as {outcome['status']}."))
    def rollback_selected(self) -> None:
        row=self.deployment_table.currentRow()
        if row<0:return
        uid=self.deployment_table.item(row,0).text(); root=Path(self.db.setting("game_root",""))
        answer=QMessageBox.question(self,"Confirm rollback",f"Restore files from deployment {uid}? Backups will be retained.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        self._run_background("Rolling back deployment; progress is being journaled…",
            lambda:self.rollback_manager.rollback(uid,root,self.adapter),
            lambda _result:QMessageBox.information(self,"Rollback complete","Files were restored and backups were retained."),
            lambda error:self._rollback_failed(uid,root,error))

    def _rollback_failed(self, uid: str, root: Path, error: str) -> None:
        if "ambiguous files and needs confirmation" in error or "Files changed outside ModForge" in error:
            confirm=QMessageBox.question(self,"Files changed outside ModForge",
                f"{error}\n\nRestore verified original backups over these changes? Unknown files created by another process will be preserved.",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
            if confirm==QMessageBox.StandardButton.Yes:
                self._run_background("Restoring confirmed backup files…",
                    lambda:self.rollback_manager.rollback(uid,root,self.adapter,confirm_external_changes=True),
                    lambda _result:QMessageBox.information(self,"Rollback complete","Original files were restored. Backups remain available."))
        else:
            QMessageBox.critical(self,"Rollback stopped",error)
        self.refresh()

    def browse_game(self) -> None:
        path=QFileDialog.getExistingDirectory(self,"Select Cyberpunk 2077 game root",self.game_path.text() or str(Path.home()))
        if path:self.game_path.setText(path); self.validate_game_path()
    def save_game(self) -> None:
        self.db.set_setting("game_root",self.game_path.text().strip()); self.validate_game_path(); self.refresh()
    def validate_game_path(self) -> None:
        if hasattr(self,"game_path"):
            ok,msg=self.adapter.validate_root(Path(self.game_path.text())) if self.game_path.text() else (False,"Choose the game root directory")
            self.settings_status.setText(("✓ " if ok else "! ")+msg)
    def refresh_game_status(self) -> None:
        if hasattr(self,"game_status"):
            ok,msg=self.adapter.validate_root(Path(self.db.setting("game_root",""))) if self.db.setting("game_root") else (False,"No game root configured")
            self.game_status.setText(("✓ " if ok else "! ")+msg)
    @staticmethod
    def open_folder(path:Path) -> None:
        # Use Qt's desktop integration so this remains compatible with Wayland environments.
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

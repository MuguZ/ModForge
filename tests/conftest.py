import pytest
from pathlib import Path
from modforge.config import AppPaths
from modforge.core.archive_importer import ArchiveImporter
from modforge.core.deployment_manager import DeploymentManager
from modforge.core.mod_library import ModLibrary
from modforge.database import Database
from modforge.games.cyberpunk2077 import Cyberpunk2077Adapter


@pytest.fixture
def services(tmp_path):
    data=tmp_path/"data"; data.mkdir()
    db=Database(data/"db.sqlite3")
    importer=ArchiveImporter(data/"archives",data/"mods")
    library=ModLibrary(db,importer)
    deployment=DeploymentManager(db,data/"backups",data/"journals",data/"locks")
    root=tmp_path/"game"; (root/"bin/x64").mkdir(parents=True)
    return db,library,deployment,root,Cyberpunk2077Adapter(),data

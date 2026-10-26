import pytest
from modforge.core.deployment_manager import DeploymentError
from modforge.core.rollback_manager import RollbackManager


def test_deploy_backup_rollback_and_unrelated(services,tmp_path):
    db,_,manager,root,adapter,data=services
    target=root/"archive/pc/mod/old.archive";target.parent.mkdir(parents=True);target.write_bytes(b"original")
    unrelated=root/"keep.txt";unrelated.write_text("keep")
    source=tmp_path/"new.archive";source.write_bytes(b"updated")
    created=tmp_path/"created.archive";created.write_bytes(b"created")
    uid=manager.deploy(root,adapter,"default",{"archive/pc/mod/old.archive":source,"r6/scripts/new.lua":created})
    assert target.read_bytes()==b"updated" and (root/"r6/scripts/new.lua").read_bytes()==b"created"
    assert unrelated.read_text()=="keep"
    RollbackManager(db,data/"locks").rollback(uid,root,adapter)
    assert target.read_bytes()==b"original" and not (root/"r6/scripts/new.lua").exists()
    assert unrelated.exists() and list((data/"backups"/uid).rglob("*"))


def test_external_change_refused(services,tmp_path):
    db,_,manager,root,adapter,data=services
    original=root/"r6/scripts/a.lua"; original.parent.mkdir(parents=True); original.write_text("original")
    src=tmp_path/"mod";src.write_text("deployed")
    uid=manager.deploy(root,adapter,"default",{"r6/scripts/a.lua":src})
    (root/"r6/scripts/a.lua").write_text("user edit")
    rollback=RollbackManager(db,data/"locks")
    with pytest.raises(DeploymentError,match="ambiguous"):
        rollback.rollback(uid,root,adapter)
    rollback.rollback(uid,root,adapter,confirm_external_changes=True)
    assert (root/"r6/scripts/a.lua").read_text()=="original"


def test_symlink_escape_and_overlap_refused(services,tmp_path):
    _,_,manager,root,adapter,_=services
    outside=tmp_path/"outside";outside.mkdir()
    (root/"link").symlink_to(outside,target_is_directory=True)
    src=tmp_path/"src";src.write_text("x")
    with pytest.raises(DeploymentError):manager.deploy(root,adapter,"default",{"link/x":src})
    uid=manager.deploy(root,adapter,"default",{"r6/scripts/x.lua":src})
    with pytest.raises(DeploymentError,match="earlier deployment"):
        manager.deploy(root,adapter,"default",{"r6/scripts/x.lua":src})


def test_adapter_validation(services,tmp_path):
    _,_,manager,root,adapter,_=services
    src=tmp_path/"src";src.write_text("x")
    with pytest.raises(DeploymentError):manager.deploy(root,adapter,"default",{"../../bad":src})

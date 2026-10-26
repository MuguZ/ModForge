import pytest
from modforge.core.profile_manager import ProfileManager
from modforge.core.conflict_detector import detect_conflicts


def test_profile_crud_and_safety(services):
    db,*_=services; profiles=ProfileManager(db)
    new=profiles.create("Test", "default")
    assert any(row["id"]==new for row in profiles.list_profiles())
    with pytest.raises(ValueError):profiles.delete(new,new)
    profiles.rename(new,"Renamed")
    profiles.delete(new,"default")
    with pytest.raises(ValueError):profiles.delete("default","other")


def test_conflicts_case_insensitive():
    assert detect_conflicts({"a":["Archive/mod.archive"],"b":["archive/MOD.archive"]})
    assert not detect_conflicts({"a":["a"],"b":["b"]})

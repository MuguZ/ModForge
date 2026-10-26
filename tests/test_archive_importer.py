import zipfile
import pytest
from pathlib import Path
from modforge.core.archive_importer import ArchiveError, ArchiveImporter, ArchiveLimits


def make_zip(path:Path, items):
    with zipfile.ZipFile(path,"w") as z:
        for name,data in items:z.writestr(name,data)


def test_valid_and_duplicate_import(tmp_path):
    archive=tmp_path/"mod.zip"; make_zip(archive,[("archive/pc/mod/a.archive",b"data")])
    importer=ArchiveImporter(tmp_path/"archives",tmp_path/"mods")
    result=importer.import_zip(archive)
    assert (result.extracted_path/"archive/pc/mod/a.archive").read_bytes()==b"data"
    assert len(result.files)==1


def test_bad_archives_rejected_and_cleaned(tmp_path):
    for entries in [[("../escape",b"x")],[("/absolute",b"x")],[("A",b"x"),("a",b"y")]]:
        archive=tmp_path/"bad.zip"; make_zip(archive,entries)
        importer=ArchiveImporter(tmp_path/"archives",tmp_path/"mods")
        with pytest.raises(ArchiveError):importer.import_zip(archive)
        assert not list((tmp_path/"mods").glob(".import-*"))


def test_empty_corrupt_and_limits(tmp_path):
    importer=ArchiveImporter(tmp_path/"a",tmp_path/"m",ArchiveLimits(files=1,total_size=2))
    empty=tmp_path/"empty.zip"; make_zip(empty,[])
    with pytest.raises(ArchiveError):importer.import_zip(empty)
    corrupt=tmp_path/"corrupt.zip"; corrupt.write_bytes(b"no zip")
    with pytest.raises(ArchiveError):importer.import_zip(corrupt)
    too_many=tmp_path/"many.zip"; make_zip(too_many,[("a",b"1"),("b",b"2")])
    with pytest.raises(ArchiveError):importer.import_zip(too_many)
    too_big=tmp_path/"big.zip"; make_zip(too_big,[("a",b"123")])
    with pytest.raises(ArchiveError):importer.import_zip(too_big)


def test_symlink_zip_rejected(tmp_path):
    archive=tmp_path/"link.zip"
    info=zipfile.ZipInfo("link"); info.create_system=3; info.external_attr=(0o120777 << 16)
    with zipfile.ZipFile(archive,"w") as z:z.writestr(info,"../../outside")
    with pytest.raises(ArchiveError):ArchiveImporter(tmp_path/"a",tmp_path/"m").import_zip(archive)


def test_duplicate_archive_detected(tmp_path,services):
    _,library,*_=services
    archive=tmp_path/"mod.zip";make_zip(archive,[("x",b"x")])
    library.import_archive(archive)
    with pytest.raises(ArchiveError):library.import_archive(archive)

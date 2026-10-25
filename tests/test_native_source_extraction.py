"""Synthetic archive checks; no compilation, network, privilege or model use."""
from pathlib import Path
import io
import os
import tarfile
import pytest
import build_native as build

LICENSE = "codex-rs/vendor/bubblewrap/LICENSE"
COPYING = "codex-rs/vendor/bubblewrap/COPYING"
CONTENT = b"SYNTHETIC_SOURCE_LICENSE_ONLY\n"

def archive(tmp_path, *, target="COPYING", include_target=True, target_is_link=False, link_name=LICENSE):
    path = tmp_path / "source.tar"
    prefix = "codex-" + build.COMMIT + "/"
    with tarfile.open(path, "w") as stream:
        if include_target:
            info = tarfile.TarInfo(prefix + COPYING)
            if target_is_link:
                info.type = tarfile.SYMTYPE
                info.linkname = "not-a-regular-target"
                stream.addfile(info)
            else:
                info.size = len(CONTENT)
                stream.addfile(info, io.BytesIO(CONTENT))
        note = tarfile.TarInfo(prefix + "README.md")
        note.size = 10
        stream.addfile(note, io.BytesIO(b"synthetic\n"))
        link = tarfile.TarInfo(prefix + link_name)
        link.type = tarfile.SYMTYPE
        link.linkname = target
        stream.addfile(link)
    return path

@pytest.mark.skipif(os.name != "nt", reason="Requires Windows physical extended-path extraction")
def test_windows_materializes_exact_bytes_without_symlink(tmp_path, monkeypatch):
    path = archive(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Windows extraction must not request symlink privilege")
    monkeypatch.setattr(build.os, "symlink", forbidden)
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(path) as stream:
        build.extract_source_archive(stream, dest, windows=True)
    assert (dest / LICENSE).read_bytes() == CONTENT == (dest / COPYING).read_bytes()
    assert (dest / LICENSE).is_file() and not (dest / LICENSE).is_symlink()
    assert (dest / "README.md").read_bytes() == b"synthetic\n"

@pytest.mark.parametrize("target", ["/outside-license", "../outside-license", "OTHER"])
@pytest.mark.skipif(os.name != "nt", reason="Requires Windows physical extended-path extraction")
def test_windows_rejects_wrong_link_target(tmp_path, target):
    path = archive(tmp_path, target=target)
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(path) as stream, pytest.raises(RuntimeError, match="Unexpected Windows source archive link"):
        build.extract_source_archive(stream, dest, windows=True)
    assert not (dest / LICENSE).exists()

@pytest.mark.skipif(os.name != "nt", reason="Requires Windows physical extended-path extraction")
def test_windows_rejects_missing_regular_target(tmp_path):
    path = archive(tmp_path, include_target=False)
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(path) as stream, pytest.raises(KeyError):
        build.extract_source_archive(stream, dest, windows=True)
    assert not (dest / LICENSE).exists()

@pytest.mark.skipif(os.name != "nt", reason="Requires Windows physical extended-path extraction")
def test_windows_rejects_link_chain_instead_of_regular_target(tmp_path):
    path = archive(tmp_path, target_is_link=True)
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(path) as stream, pytest.raises(RuntimeError):
        build.extract_source_archive(stream, dest, windows=True)
    assert not (dest / LICENSE).exists()

@pytest.mark.skipif(os.name != "nt", reason="Requires Windows physical extended-path extraction")
def test_windows_rejects_unqualified_link_name(tmp_path):
    path = archive(tmp_path, link_name="not-the-qualified-source-link")
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(path) as stream, pytest.raises(RuntimeError):
        build.extract_source_archive(stream, dest, windows=True)

@pytest.mark.skipif(os.name != "nt", reason="Requires Windows physical extended-path extraction")
def test_windows_refuses_overwrite_on_materialization(tmp_path):
    path = archive(tmp_path)
    dest = tmp_path / "out"
    (dest / LICENSE).parent.mkdir(parents=True)
    (dest / LICENSE).write_bytes(b"existing-owned-marker")
    with tarfile.open(path) as stream, pytest.raises(FileExistsError):
        build.extract_source_archive(stream, dest, windows=True)
    assert (dest / LICENSE).read_bytes() == b"existing-owned-marker"

def test_unix_retains_original_member_link_semantics(tmp_path):
    path = archive(tmp_path)
    class Observe:
        def __init__(self, stream):
            self.stream, self.seen = stream, None
        def getmembers(self):
            return self.stream.getmembers()
        def extractall(self, destination, *, members, filter):
            self.seen = (destination, members, filter)
        def extractfile(self, member):
            raise AssertionError("Unix branch must not materialize archive links")
    with tarfile.open(path) as stream:
        original_names = [x.name for x in stream.getmembers()]
        observer = Observe(stream)
        build.extract_source_archive(observer, tmp_path / "unix", windows=False)
        destination, members, policy = observer.seen
        link = next(x for x in members if x.name == LICENSE)
        assert link.issym() and link.linkname == "COPYING" and policy == "data"
        assert destination == str(tmp_path / "unix")
        assert [x.name for x in stream.getmembers()] == original_names


@pytest.mark.skipif(os.name == "nt", reason="Requires native Unix symlink extraction")
def test_unix_extracts_real_symlink_target_and_bytes(tmp_path):
    path = archive(tmp_path)
    dest = tmp_path / "unix-physical"
    dest.mkdir()
    with tarfile.open(path) as stream:
        build.extract_source_archive(stream, dest, windows=False)
    link, target = dest / LICENSE, dest / COPYING
    assert link.is_symlink()
    assert os.readlink(link) == "COPYING"
    assert link.resolve(strict=True) == target.resolve(strict=True)
    assert link.read_bytes() == target.read_bytes() == CONTENT
    assert (dest / "README.md").read_bytes() == b"synthetic\n"

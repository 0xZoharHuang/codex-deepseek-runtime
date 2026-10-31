"""Actual pinned official tar link plus Windows materialization, without Rust."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("official_notice_collector", ROOT/"native_licenses.py")
collector = importlib.util.module_from_spec(spec); spec.loader.exec_module(collector)


@pytest.mark.parametrize("materialized", [False, True], ids=["official-tar-symlink", "Windows-materialization"])
def test_official_bubblewrap_LICENSE_alias_keeps_original_COPYING_bytes(tmp_path, materialized):
    value = os.environ.get("CODEX_DEEPSEEK_TEST_SOURCE_ARCHIVE")
    if not value:
        pytest.skip("requires explicitly supplied cached upstream archive")
    archive = Path(value)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == "3f9d7e83c9f152a438633813daefacee944d29165101c305e8ac08dc00a0e361"
    prefix = "codex-fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/vendor/bubblewrap/"
    with tarfile.open(archive) as tar:
        alias = tar.getmember(prefix+"LICENSE")
        assert alias.issym() and alias.linkname == "COPYING"
        content = tar.extractfile(prefix+"COPYING").read()
    assert len(content) > 10_000
    source = tmp_path/"official-crate"; source.mkdir()
    (source/"COPYING").write_bytes(content)
    if materialized:
        (source/"LICENSE").write_bytes(content)
    else:
        (source/"LICENSE").symlink_to(alias.linkname)
    copied, origin = collector.notice_content(source/"LICENSE", source)
    assert copied == content and hashlib.sha256(copied).digest() == hashlib.sha256(content).digest()
    assert (origin is None) is materialized
    if origin:
        assert origin["resolved_source_file"] == "COPYING"
    assert (source/"COPYING").read_bytes() == content

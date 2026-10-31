"""Existing archives keep original Unix modes and bytes, but must prove every member."""
import copy
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("existing_native_packager",ROOT/"package_native.py")
packager=importlib.util.module_from_spec(spec);spec.loader.exec_module(packager)
TARGETS={
 "x86_64-unknown-linux-gnu":["bin/codex","bin/codex-code-mode-host","bin/codex-http-transport","codex-path/rg","codex-resources/bwrap"],
 "aarch64-apple-darwin":["bin/codex","bin/codex-code-mode-host","bin/codex-http-transport","codex-path/rg"],
 "x86_64-pc-windows-msvc":["bin/codex.exe","bin/codex-code-mode-host.exe","bin/codex-http-transport.exe","codex-path/rg.exe","bin/codex-windows-sandbox-setup.exe","bin/codex-command-runner.exe","bin/codex-windows-managed-deny-probe.exe","bin/codex-windows-sandbox-service.exe","codex-resources/codex-windows-sandbox-setup.exe","codex-resources/codex-command-runner.exe"],
}

def fixture_entries(target="x86_64-unknown-linux-gnu",*,metadata_change=None,receipt_change=None):
    recipe,toolchain=packager.expected_native_recipe(target)
    metadata={"distribution":"codex-deepseek-runtime","target":target,"variant":"codex","resourcesDir":"codex-resources","pathDir":"codex-path",
              "version":"fixture","upstreamCommit":"fixed-commit","patchSha256":"fixed-patch","recipeId":recipe}
    if metadata_change:metadata_change(metadata)
    data={name:("REAL_FIXTURE_"+name).encode() for name in TARGETS[target]}
    data["licenses/notice.txt"]=b"COMPLETE ORIGINAL LICENSE CONTENT\n"
    data["codex-package.json"]=json.dumps(metadata).encode()
    receipt={"profile":"release","upstream_commit":"fixed-commit","patch_sha256":"fixed-patch","native_recipe_id":recipe,"build_toolchain_pin":toolchain,
             "files":{name:hashlib.sha256(raw).hexdigest() for name,raw in data.items()}}
    if receipt_change:receipt_change(receipt)
    data["build-receipt.json"]=json.dumps(receipt).encode()
    entries=[]
    for name in [".","bin","codex-resources","codex-path","licenses"]:
        item=tarfile.TarInfo(name if name=="." else "./"+name);item.type=tarfile.DIRTYPE;item.mode=0o755;item.uid=501;item.gid=20;item.mtime=1793429818.125
        entries.append((item,None))
    for name,raw in data.items():
        item=tarfile.TarInfo("./"+name);item.size=len(raw);item.mode=0o755 if name in TARGETS[target] else 0o644
        item.uid=501;item.gid=20;item.mtime=1793429818.125
        entries.append((item,raw))
    return entries,data

def write_archive(path,entries,*,trailing=b""):
    with path.open("xb") as raw,gzip.GzipFile(fileobj=raw,mode="wb",mtime=0) as zipped:
        with tarfile.open(fileobj=zipped,mode="w",format=tarfile.PAX_FORMAT) as bundle:
            for member,data in entries:bundle.addfile(member,io.BytesIO(data) if data is not None else None)
        if trailing:zipped.write(trailing)
    return path

def emit(archive,manifest,target="x86_64-unknown-linux-gnu",**overrides):
    kwargs={"expected_target":target,"archive_sha256":packager.sha(archive),"archive_size":archive.stat().st_size}
    kwargs.update(overrides)
    return packager.manifest_from_existing_archive(archive,manifest,"https://example.invalid/not-published",**kwargs)

@pytest.mark.parametrize("target",TARGETS)
def test_original_bytes_modes_metadata_and_empty_resource_dir_are_preserved(tmp_path,target):
    entries,data=fixture_entries(target)
    archive=write_archive(tmp_path/"original.tar.gz",entries)
    before=(archive.read_bytes(),archive.stat().st_mtime_ns,archive.stat().st_ino)
    manifest=emit(archive,tmp_path/"candidate.json",target)
    assert (archive.read_bytes(),archive.stat().st_mtime_ns,archive.stat().st_ino)==before
    assert manifest["files"]=={name:hashlib.sha256(raw).hexdigest() for name,raw in data.items()}
    assert manifest["target"]==target and manifest["status"]=="ready"
    assert manifest["archive"]["sha256"]==hashlib.sha256(before[0]).hexdigest()
    assert manifest["archive"]["url"]=="https://example.invalid/not-published"
    assert "public_release_ready" not in manifest
    raw=(tmp_path/"candidate.json").read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    with tarfile.open(archive) as bundle:
        observed={str(Path(m.name).as_posix()).removeprefix("./"):m for m in bundle}
        for name in TARGETS[target]:assert observed[name].mode==0o755
        assert observed["licenses/notice.txt"].mode==0o644
        assert observed["codex-resources"].isdir()

@pytest.mark.parametrize("change",[
 "symlink","hardlink","fifo","device","unknown-type","sparse","absolute","traversal","backslash","drive","duplicate","alias-duplicate",
 "noncanonical","privileged-file","privileged-dir","binary-not-executable","data-executable","missing-file","extra-file","extra-empty-dir","file-as-parent",
 "pax-unknown","trailing-data","missing-metadata","missing-receipt","bad-json","duplicate-json","nonfinite-json",
])
def test_unattested_or_unsafe_archive_is_rejected_without_output_or_repack(tmp_path,change):
    entries,data=fixture_entries()
    target=next(m for m,_ in entries if m.name=="./bin/codex")
    if change in {"symlink","hardlink","fifo","device","unknown-type","sparse"}:
        target.type={"symlink":tarfile.SYMTYPE,"hardlink":tarfile.LNKTYPE,"fifo":tarfile.FIFOTYPE,"device":tarfile.CHRTYPE,"unknown-type":b"Z","sparse":tarfile.GNUTYPE_SPARSE}[change]
        target.linkname="../outside" if change in {"symlink","hardlink"} else ""
        target.size=0
        entries=[(m,None if m is target else raw) for m,raw in entries]
    elif change in {"absolute","traversal","backslash","drive","noncanonical"}:
        target.name={"absolute":"/bin/codex","traversal":"../bin/codex","backslash":"bin\\codex","drive":"C:/bin/codex","noncanonical":"./bin//codex"}[change]
    elif change in {"duplicate","alias-duplicate"}:
        duplicate=copy.copy(target);duplicate.name=target.name if change=="duplicate" else "bin/codex";entries.append((duplicate,data["bin/codex"]))
    elif change=="privileged-file":target.mode=0o4755
    elif change=="privileged-dir":entries[1][0].mode=0o1777
    elif change=="binary-not-executable":target.mode=0o644
    elif change=="data-executable":next(m for m,_ in entries if m.name=="./licenses/notice.txt").mode=0o755
    elif change in {"missing-file","missing-metadata","missing-receipt"}:
        missing={"missing-file":"./bin/codex","missing-metadata":"./codex-package.json","missing-receipt":"./build-receipt.json"}[change]
        entries=[(m,raw) for m,raw in entries if m.name!=missing]
    elif change in {"extra-file","extra-empty-dir","file-as-parent"}:
        m=tarfile.TarInfo("./unattested" if change!="file-as-parent" else "./bin")
        if change=="extra-empty-dir":m.type=tarfile.DIRTYPE;m.mode=0o755;entries.append((m,None))
        else:m.mode=0o644;m.size=1;entries.append((m,b"x"))
    elif change=="pax-unknown":target.pax_headers={"SCHILY.xattr.security.capability":"unapproved"}
    elif change in {"bad-json","duplicate-json","nonfinite-json"}:
        raw={"bad-json":b"{","duplicate-json":b'{"target":"one","target":"two"}',"nonfinite-json":b'{"target":NaN}'}[change]
        member=next(m for m,_ in entries if m.name=="./codex-package.json");member.size=len(raw)
        entries=[(m,raw if m is member else content) for m,content in entries]
    archive=write_archive(tmp_path/"original.tar.gz",entries,trailing=b"NOT_TAR_PADDING" if change=="trailing-data" else b"")
    before=archive.read_bytes()
    with pytest.raises((ValueError,tarfile.TarError)):
        emit(archive,tmp_path/"should-not-exist.json")
    assert not (tmp_path/"should-not-exist.json").exists() and archive.read_bytes()==before

@pytest.mark.parametrize("change",["profile","receipt-file-hash","receipt-upstream","recipe","toolchain","layout","distribution"])
def test_original_attestation_and_current_recipe_are_required(tmp_path,change):
    meta=None;receipt=None
    if change=="profile":receipt=lambda r:r.update(profile="debug")
    elif change=="receipt-file-hash":receipt=lambda r:r["files"].update({"bin/codex":"0"*64})
    elif change=="receipt-upstream":receipt=lambda r:r.update(upstream_commit="different")
    elif change=="recipe":
        meta=lambda m:m.update(recipeId="0"*64);receipt=lambda r:r.update(native_recipe_id="0"*64)
    elif change=="toolchain":receipt=lambda r:r.update(build_toolchain_pin="different")
    elif change=="layout":meta=lambda m:m.update(resourcesDir="another")
    else:meta=lambda m:m.update(distribution="another")
    entries,_=fixture_entries(metadata_change=meta,receipt_change=receipt)
    archive=write_archive(tmp_path/"original.tar.gz",entries)
    with pytest.raises(ValueError):emit(archive,tmp_path/"should-not-exist.json")
    assert not (tmp_path/"should-not-exist.json").exists()

@pytest.mark.parametrize("overrides",[
 {"expected_target":"aarch64-apple-darwin"},{"archive_sha256":"0"*64},{"archive_sha256":"not-sha"},{"archive_size":1},
 {"archive_size":True},{"archive_size":0},{"expected_target":""},
])
def test_independently_supplied_identity_pins_cannot_be_ignored(tmp_path,overrides):
    entries,_=fixture_entries();archive=write_archive(tmp_path/"original.tar.gz",entries)
    with pytest.raises(ValueError):emit(archive,tmp_path/"no.json",**overrides)
    assert not (tmp_path/"no.json").exists()

def test_existing_manifest_is_never_overwritten(tmp_path):
    entries,_=fixture_entries();archive=write_archive(tmp_path/"original.tar.gz",entries)
    manifest=tmp_path/"existing.json";manifest.write_bytes(b"OLD EVIDENCE\n")
    with pytest.raises(ValueError,match="Preserving existing manifest"):emit(archive,manifest)
    assert manifest.read_bytes()==b"OLD EVIDENCE\n"

def test_truncated_gzip_is_rejected_even_with_recomputed_outer_pins(tmp_path):
    entries,_=fixture_entries();archive=write_archive(tmp_path/"original.tar.gz",entries)
    raw=archive.read_bytes();archive.write_bytes(raw[:-6])
    with pytest.raises(ValueError):emit(archive,tmp_path/"no.json")
    assert not (tmp_path/"no.json").exists()

@pytest.mark.parametrize("extra",[
 ["--package","fixture"],["--archive","another.tar.gz"],["--archive-size","0"],["--expected-target",""],
])
def test_cli_rejects_ambiguous_or_missing_existing_archive_contract(tmp_path,extra):
    entries,_=fixture_entries();archive=write_archive(tmp_path/"original.tar.gz",entries)
    args=[sys.executable,"-I","-B",str(ROOT/"package_native.py"),"--existing-archive",str(archive),
          "--manifest",str(tmp_path/"no.json"),"--url","https://example.invalid/not-published",*extra]
    result=subprocess.run(args,capture_output=True,text=True)
    assert result.returncode==2 and not (tmp_path/"no.json").exists()

def test_public_cli_reuses_without_claiming_publication(tmp_path):
    entries,_=fixture_entries();archive=write_archive(tmp_path/"original.tar.gz",entries)
    before=archive.read_bytes()
    command=[sys.executable,"-I","-B",str(ROOT/"package_native.py"),"--existing-archive",str(archive),
             "--expected-target","x86_64-unknown-linux-gnu","--archive-sha256",packager.sha(archive),
             "--archive-size",str(archive.stat().st_size),"--manifest",str(tmp_path/"candidate.json"),
             "--url","https://example.invalid/not-published"]
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)["archive_reused"] is True
    assert json.loads(result.stdout)["public_release_asserted"] is False
    assert archive.read_bytes()==before


@pytest.mark.parametrize("change",["missing-end-markers","one-end-block","hidden-nonzero-padding"])
def test_complete_gzip_does_not_hide_truncated_or_noncanonical_TAR_end(tmp_path,change):
    entries,_=fixture_entries();archive=write_archive(tmp_path/"original.tar.gz",entries)
    with tarfile.open(archive) as bundle:
        members=bundle.getmembers()
        end=max(m.offset_data+((m.size+511)//512)*512 for m in members)
    raw=gzip.decompress(archive.read_bytes())
    if change=="missing-end-markers":raw=raw[:end]
    elif change=="one-end-block":raw=raw[:end]+b"\0"*512
    else:
        changed=bytearray(raw);changed[end+1024+5]=1;raw=bytes(changed)
    archive.write_bytes(gzip.compress(raw,mtime=0))
    with pytest.raises(ValueError):emit(archive,tmp_path/"no.json")
    assert not (tmp_path/"no.json").exists()


@pytest.mark.parametrize("unsafe",["./licenses/new\nline","./licenses/del\x7f"])
def test_control_characters_in_archive_paths_are_rejected(tmp_path,unsafe):
    entries,_=fixture_entries()
    entries[1][0].name=unsafe
    archive=write_archive(tmp_path/"original.tar.gz",entries)
    with pytest.raises(ValueError,match="Unsafe path"):emit(archive,tmp_path/"no.json")

@pytest.mark.parametrize("target,alias",[
 ("x86_64-pc-windows-msvc","bin/CODEX.EXE"),
 ("x86_64-unknown-linux-gnu","licenses/NOTICE.txt"),
])
def test_platform_case_semantics_do_not_hide_a_self_consistent_alias(tmp_path,target,alias):
    entries,data=fixture_entries(target)
    content=b"ATTESTED DISTINCT EXTRA FILE"
    extra=tarfile.TarInfo("./"+alias);extra.mode=0o644;extra.size=len(content);entries.append((extra,content))
    receipt=json.loads(data["build-receipt.json"]);receipt["files"][alias]=hashlib.sha256(content).hexdigest()
    sealed=json.dumps(receipt).encode()
    receipt_member=next(m for m,_ in entries if m.name=="./build-receipt.json");receipt_member.size=len(sealed)
    entries=[(m,sealed if m is receipt_member else raw) for m,raw in entries]
    archive=write_archive(tmp_path/"original.tar.gz",entries)
    if target.endswith("windows-msvc"):
        with pytest.raises(ValueError,match="Case-colliding"):emit(archive,tmp_path/"no.json",target)
    else:
        manifest=emit(archive,tmp_path/"candidate.json",target)
        assert alias in manifest["files"] and "licenses/notice.txt" in manifest["files"]

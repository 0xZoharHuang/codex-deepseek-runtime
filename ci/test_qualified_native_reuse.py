"""Pure rejection checks; no network, native binary, provider or Cargo execution."""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import urllib.error
import zipfile
import pytest

spec=importlib.util.spec_from_file_location("qualified_build_reuse",Path(__file__).with_name("reuse_qualified_native.py"))
reuse=importlib.util.module_from_spec(spec);spec.loader.exec_module(reuse)
digest=lambda data:hashlib.sha256(data).hexdigest()

def pins_fixture():
    return {"schema":1,"source":"actions-artifact","repository":"0xZoharHuang/codex-deepseek-runtime","repository_id":1427487253,
            "run_id":40156375058,"run_attempt":1,"producer_commit":"b"*40,
            "workflow_path":".github/workflows/native-platform-qualification.yml"}

def producer(pins):
    return {"id":pins["run_id"],"run_attempt":pins["run_attempt"],"head_sha":pins["producer_commit"],
            "path":pins["workflow_path"],"status":"completed","conclusion":"success",
            "repository":{"id":pins["repository_id"],"full_name":pins["repository"]}}

@pytest.mark.parametrize("bad",[None,"id","attempt","sha","path","status","conclusion","repoid","repo"])
def test_original_run_must_be_the_exact_successful_producer(bad):
    pins=pins_fixture();run=producer(pins)
    if bad=="id":run["id"]+=1
    elif bad=="attempt":run["run_attempt"]+=1
    elif bad=="sha":run["head_sha"]="c"*40
    elif bad=="path":run["path"]="other"
    elif bad=="status":run["status"]="in_progress"
    elif bad=="conclusion":run["conclusion"]="failure"
    elif bad=="repoid":run["repository"]["id"]+=1
    elif bad=="repo":run["repository"]["full_name"]="other/repo"
    if bad is None:reuse.validate_run(run,pins)
    else:
        with pytest.raises(ValueError):reuse.validate_run(run,pins)

def artifact_fixture(pins):
    pin={"artifact_id":25,"artifact_name":"qualified-target","zip_sha256":"c"*64,"zip_bytes":123}
    value={"id":25,"name":pin["artifact_name"],"expired":False,"size_in_bytes":123,
           "digest":"sha256:"+pin["zip_sha256"],"workflow_run":{"id":pins["run_id"],
           "head_sha":pins["producer_commit"],"repository_id":pins["repository_id"],
           "head_repository_id":pins["repository_id"]}}
    return pin,value

@pytest.mark.parametrize("bad",[None,"id","name","expired","size","digest","runid","sha","repo","headrepo"])
def test_artifact_metadata_cannot_select_another_or_expired_input(bad):
    pins=pins_fixture();pin,value=artifact_fixture(pins)
    if bad=="id":value["id"]+=1
    elif bad=="name":value["name"]="same-count-other-artifact"
    elif bad=="expired":value["expired"]=True
    elif bad=="size":value["size_in_bytes"]+=1
    elif bad=="digest":value["digest"]="sha256:"+"0"*64
    elif bad=="runid":value["workflow_run"]["id"]+=1
    elif bad=="sha":value["workflow_run"]["head_sha"]="d"*40
    elif bad=="repo":value["workflow_run"]["repository_id"]+=1
    elif bad=="headrepo":value["workflow_run"]["head_repository_id"]+=1
    if bad is None:reuse.validate_artifact(value,pins,pin)
    else:
        with pytest.raises(ValueError):reuse.validate_artifact(value,pins,pin)

def selected_fixture(tmp_path,monkeypatch):
    (tmp_path/"native").mkdir()
    recipe=json.dumps({"schema":1,"upstream_commit":"c"*40,"upstream_toolchain":"1.99.0",
        "build_toolchain":"1.99.1","required_base_inputs":{"aarch64-apple-darwin":[],
        "x86_64-unknown-linux-gnu":[],"x86_64-pc-windows-msvc":[]},"overlays":[]}).encode()
    (tmp_path/"native/runtime-overlays.json").write_bytes(recipe)
    manifest={"target":"aarch64-apple-darwin","distribution_version":"0.6.2","status":"ready",
              "archive":{"sha256":"e"*64},"files":{"build-receipt.json":"f"*64}}
    from build_native import native_recipe_identity
    manifest["native_recipe_id"]=native_recipe_identity("aarch64-apple-darwin",json.loads(recipe))
    path=tmp_path/"native/runtime.json";path.write_text(json.dumps(manifest))
    pin={"manifest":"runtime.json","manifest_sha256":reuse.sha(path),"archive_sha256":"e"*64,
         "receipt_sha256":"f"*64,"file_count":1}
    pins=pins_fixture();pins.update(recipe_sha256=digest(recipe),current_recipe_sha256=digest(recipe),targets={"aarch64-apple-darwin":pin})
    monkeypatch.setattr(reuse,"ROOT",tmp_path)
    return pins,pin,path,manifest

@pytest.mark.parametrize("bad",[None,"recipe","manifest-bytes","old-version","target","status","archive","receipt","count"])
def test_reuse_requires_complete_current_062_manifest_and_exact_full_recipe(tmp_path,monkeypatch,bad):
    pins,pin,path,manifest=selected_fixture(tmp_path,monkeypatch)
    if bad=="recipe":(tmp_path/"native/runtime-overlays.json").write_bytes(b"changed")
    elif bad=="manifest-bytes":path.write_text(path.read_text()+"\n")
    elif bad:
        if bad=="old-version":manifest["distribution_version"]="0.6.0"
        elif bad=="target":manifest["target"]="x86_64-unknown-linux-gnu"
        elif bad=="status":manifest["status"]="pending"
        elif bad=="archive":manifest["archive"]["sha256"]="0"*64
        elif bad=="receipt":manifest["files"]["build-receipt.json"]="0"*64
        elif bad=="count":manifest["files"]["extra"]="0"*64
        path.write_text(json.dumps(manifest));pin["manifest_sha256"]=reuse.sha(path)
    if bad is None:assert reuse.selected_pin(pins,"aarch64-apple-darwin")[0] is pin
    else:
        with pytest.raises(ValueError):reuse.selected_pin(pins,"aarch64-apple-darwin")
    with pytest.raises(ValueError):reuse.selected_pin(pins,"x86_64-pc-windows-msvc")

@pytest.mark.parametrize("bad",[None,"zip-size","zip-hash","duplicate","member-size","recipe","source-commit"])
def test_only_three_pinned_zip_members_are_read_to_owned_destinations(tmp_path,bad):
    pins=pins_fixture();pins.update(recipe_bytes=6,recipe_sha256=digest(b"recipe"))
    archive=tmp_path/"artifact.zip";content=b"native tar";entries=[
        ("native-package.tar.gz",content),("runtime-overlays.json",b"recipe"),
        ("source-commit.txt",(pins["producer_commit"]+"\n").encode())]
    if bad=="duplicate":entries.append(entries[0])
    elif bad=="member-size":entries[0]=(entries[0][0],content+b"x")
    elif bad=="recipe":entries[1]=(entries[1][0],b"wrong!")
    elif bad=="source-commit":entries[2]=(entries[2][0],("d"*40+"\n").encode())
    entries.append(("../never-extract",b"do not extract"))
    with zipfile.ZipFile(archive,"w") as z:
        for name,data in entries:
            if bad=="duplicate" and name=="native-package.tar.gz" and name in z.namelist():
                with pytest.warns(UserWarning):z.writestr(name,data)
            else:z.writestr(name,data)
    pin={"zip_bytes":archive.stat().st_size,"zip_sha256":reuse.sha(archive),
         "archive_bytes":len(content),"archive_sha256":digest(content)}
    if bad=="zip-size":pin["zip_bytes"]+=1
    elif bad=="zip-hash":pin["zip_sha256"]="0"*64
    out=tmp_path/"selected";out.mkdir()
    if bad is None:
        assert reuse.zip_inputs(archive,pin,pins,out)==(out/"native-package.tar.gz",out/"runtime-overlays.json")
        assert {p.name for p in out.iterdir()}=={"native-package.tar.gz","runtime-overlays.json","source-commit.txt"}
    else:
        with pytest.raises(ValueError):reuse.zip_inputs(archive,pin,pins,out)
    assert not (tmp_path/"never-extract").exists()

@pytest.mark.parametrize("bad",[None,"short","oversize","hash","existing"])
def test_download_preserves_exact_failure_without_retry(tmp_path,monkeypatch,bad):
    data=b"immutable native archive";calls=[];path=tmp_path/"archive"
    def request(url,timeout):
        calls.append(url);assert isinstance(url,str) and timeout==90
        return io.BytesIO(data[:-1] if bad=="short" else data+b"x" if bad=="oversize" else data)
    monkeypatch.setattr(reuse.urllib.request,"urlopen",request)
    if bad=="existing":path.write_bytes(b"preserve")
    if bad is None:assert reuse.download("https://storage.example/one",path,digest(data),exact_bytes=len(data))==len(data)
    else:
        with pytest.raises(ValueError):reuse.download("https://storage.example/one",path,"0"*64 if bad=="hash" else digest(data),exact_bytes=len(data))
    assert len(calls)==(0 if bad=="existing" else 1)

@pytest.mark.parametrize("location",["https://storage.example/file?signed=private","http://storage.example/file","https://user:password@storage.example/file","https://storage.example:444/file"])
def test_api_redirect_is_explicit_and_api_token_is_not_a_download_header(monkeypatch,location):
    calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            assert request.full_url.startswith("https://api.github.com/")
            assert request.get_header("Authorization")=="Bearer fixture-only-token"
            raise urllib.error.HTTPError(request.full_url,302,"Found",{"Location":location},None)
    monkeypatch.setattr(reuse.urllib.request,"build_opener",lambda *args:Opener())
    path="repos/0xZoharHuang/codex-deepseek-runtime/actions/artifacts/25/zip"
    if location.startswith("https://storage.example/file"):
        assert reuse.api_get(path,"fixture-only-token",redirect=True)==location
    else:
        with pytest.raises(ValueError):reuse.api_get(path,"fixture-only-token",redirect=True)
    assert len(calls)==1

def test_duplicate_security_fields_are_rejected():
    with pytest.raises(ValueError):reuse.unique_json('{"digest":"a","digest":"b"}')


def release_fixture(target="aarch64-apple-darwin"):
    pins=pins_fixture();pins.update(source="release-asset",recipe_bytes=52848,recipe_sha256="a"*64)
    pin={"archive_sha256":"b"*64,"archive_bytes":12345}
    filename="codex-deepseek-native-0.6.2-"+target+".tar.gz"
    manifest={"distribution_version":"0.6.2","status":"ready","target":target,
              "archive":{"filename":filename,"url":reuse.RELEASE_BASE+"/"+filename,"sha256":"b"*64}}
    return pins,pin,manifest

@pytest.mark.parametrize("target",["aarch64-apple-darwin","x86_64-unknown-linux-gnu"])
@pytest.mark.parametrize("bad",[None,"mode","old-version","target","status","filename","host","old-tag","latest","digest"])
def test_stable_route_accepts_only_the_exact_public_062_asset_and_original_recipe(target,bad):
    pins,pin,manifest=release_fixture(target)
    if bad=="mode":pins["source"]="actions-artifact"
    elif bad=="old-version":manifest["distribution_version"]="0.6.0"
    elif bad=="target":manifest["target"]="x86_64-pc-windows-msvc"
    elif bad=="status":manifest["status"]="pending"
    elif bad=="filename":manifest["archive"]["filename"]="other.tar.gz"
    elif bad=="host":manifest["archive"]["url"]="https://mirror.invalid/"+manifest["archive"]["filename"]
    elif bad=="old-tag":manifest["archive"]["url"]=manifest["archive"]["url"].replace("/v0.6.2/","/v0.6.1/")
    elif bad=="latest":manifest["archive"]["url"]=manifest["archive"]["url"].replace("/download/v0.6.2/","/latest/download/")
    elif bad=="digest":manifest["archive"]["sha256"]="0"*64
    if bad is None:
        inputs=reuse.release_inputs(pins,pin,manifest)
        assert inputs["archive"]=={"url":manifest["archive"]["url"],"sha256":"b"*64,"bytes":12345}
        assert inputs["recipe"]=={"url":reuse.RELEASE_BASE+"/native-build-recipe-5c9e004f.json",
                                   "sha256":"a"*64,"bytes":52848}
    else:
        with pytest.raises(ValueError):reuse.release_inputs(pins,pin,manifest)

@pytest.mark.parametrize("source",[None,"latest","release","auto","actions-artifact-then-release"])
def test_unknown_source_is_rejected_before_any_network(source,tmp_path,monkeypatch):
    pins,pin,manifest=release_fixture();pins["source"]=source
    calls=[]
    monkeypatch.setattr(reuse,"api_get",lambda *a,**k:calls.append("api"))
    monkeypatch.setattr(reuse,"download",lambda *a,**k:calls.append("download"))
    with pytest.raises(ValueError,match="Unknown acquisition source"):
        reuse.acquire_inputs(pins,pin,manifest,tmp_path)
    assert calls==[]

def test_expired_actions_artifact_cannot_fall_back_to_release(tmp_path,monkeypatch):
    pins=pins_fixture();pin,artifact=artifact_fixture(pins);manifest={}
    monkeypatch.setitem(reuse.os.environ,"GITHUB_TOKEN","fixture-only-token")
    calls=[]
    def error(path,token,**kwargs):
        calls.append(path)
        if "/runs/" in path:return producer(pins)
        if path.endswith("/zip"):
            raise urllib.error.HTTPError("https://api.github.com/"+path,410,"Gone",{},None)
        return artifact
    monkeypatch.setattr(reuse,"api_get",error)
    monkeypatch.setattr(reuse,"download",lambda *a,**k:calls.append("download"))
    with pytest.raises(urllib.error.HTTPError):
        reuse.acquire_inputs(pins,pin,manifest,tmp_path)
    base="repos/"+pins["repository"]+"/actions/"
    assert calls==[base+"runs/"+str(pins["run_id"]),base+"artifacts/"+str(pin["artifact_id"]),
                   base+"artifacts/"+str(pin["artifact_id"])+"/zip"]

def test_missing_release_asset_cannot_fall_back_to_actions_or_another_release(tmp_path,monkeypatch):
    pins,pin,manifest=release_fixture()
    calls=[]
    monkeypatch.setattr(reuse,"api_get",lambda *a,**k:calls.append("api"))
    def error(url,*args,**kwargs):
        calls.append(url)
        raise urllib.error.HTTPError(url,404,"Missing",{},None)
    monkeypatch.setattr(reuse,"download",error)
    with pytest.raises(urllib.error.HTTPError):
        reuse.acquire_inputs(pins,pin,manifest,tmp_path)
    assert calls==[manifest["archive"]["url"]]

def test_stable_source_needs_no_actions_lookup_or_token_and_preserves_original_recipe(tmp_path,monkeypatch):
    pins,pin,manifest=release_fixture()
    monkeypatch.delenv("GITHUB_TOKEN",raising=False)
    calls=[]
    def no_api(*args,**kwargs):raise AssertionError("Stable mode must not query expiring artifacts")
    def downloaded(url,path,expected_hash,**kwargs):
        calls.append((url,path.name,expected_hash,kwargs))
        path.write_bytes(b"controlled downloader placeholder")
    monkeypatch.setattr(reuse,"api_get",no_api);monkeypatch.setattr(reuse,"download",downloaded)
    archive,recipe,proof=reuse.acquire_inputs(pins,pin,manifest,tmp_path)
    assert archive==tmp_path/"native-package.tar.gz" and recipe==tmp_path/"runtime-overlays.json"
    assert calls==[(manifest["archive"]["url"],"native-package.tar.gz",pin["archive_sha256"],{"exact_bytes":pin["archive_bytes"]}),
                   (reuse.RELEASE_BASE+"/native-build-recipe-5c9e004f.json","runtime-overlays.json",pins["recipe_sha256"],{"exact_bytes":pins["recipe_bytes"]})]
    assert proof["acquisition"]=="pinned_public_release_assets"
    assert proof["original_build_metadata_checked_live"] is False and proof["release_tag"]=="v0.6.2"
    assert "original_run" not in proof and "original_artifact" not in proof

def test_actions_source_requires_explicit_token_before_network(tmp_path,monkeypatch):
    pins,pin,manifest=release_fixture();pins["source"]="actions-artifact"
    monkeypatch.delenv("GITHUB_TOKEN",raising=False);calls=[]
    monkeypatch.setattr(reuse,"api_get",lambda *a,**k:calls.append("api"))
    with pytest.raises(ValueError,match="token"):
        reuse.acquire_inputs(pins,pin,manifest,tmp_path)
    assert calls==[]


def equivalence_fixture(tmp_path,monkeypatch,target):
    from build_native import native_recipe_identity
    (tmp_path/"native").mkdir()
    original={"schema":1,"upstream_commit":"c"*40,"upstream_toolchain":"1.99.0",
              "build_toolchain":"1.99.1","required_base_inputs":{"aarch64-apple-darwin":[],
              "x86_64-unknown-linux-gnu":[],"x86_64-pc-windows-msvc":[]},"overlays":[]}
    current=deepcopy(original)
    current["required_base_inputs"]["x86_64-pc-windows-msvc"]=[{"file":"windows-base.patch","sha256":"d"*64}]
    current["overlays"].append({"file":"windows-only.patch","sha256":"e"*64,
        "changes_by_target":{"x86_64-pc-windows-msvc":[{"path":"windows.rs","fork_sha256":"f"*64}]}})
    archived=tmp_path/"original-runtime-overlays.json";archived.write_text(json.dumps(original))
    path=tmp_path/"native/runtime-overlays.json";path.write_text(json.dumps(current))
    native_id=native_recipe_identity(target,original)
    pins=pins_fixture();pins.update(recipe_sha256=reuse.sha(archived),recipe_bytes=archived.stat().st_size,
                                   current_recipe_sha256=reuse.sha(path))
    manifest={"native_recipe_id":native_id}
    receipt={"native_recipe_id":native_id,"overlay_recipe_sha256":pins["recipe_sha256"]}
    monkeypatch.setattr(reuse,"ROOT",tmp_path)
    return pins,manifest,receipt,archived,path,current

@pytest.mark.parametrize("target",["aarch64-apple-darwin","x86_64-unknown-linux-gnu"])
def test_windows_only_full_recipe_delta_preserves_original_unix_compilation_identity(tmp_path,monkeypatch,target):
    pins,manifest,receipt,archived,path,current=equivalence_fixture(tmp_path,monkeypatch,target)
    assert pins["recipe_sha256"]!=pins["current_recipe_sha256"]
    proof=reuse.target_recipe_equivalence(pins,target,manifest,receipt,archived)
    assert proof["target_recipe_id"]==manifest["native_recipe_id"]==receipt["native_recipe_id"]
    assert proof["original_compilation_recipe_sha256"]==reuse.sha(archived)
    assert proof["current_validation_recipe_sha256"]==reuse.sha(path)
    assert not proof["full_recipe_equal"] and proof["native_builds"]==0
    # Retain the existing, stricter archived-recipe package guard.
    from ci import qualify_native as native
    monkeypatch.setattr(native,"ROOT",tmp_path)
    assert native.recipe_scope_identity(receipt,current,target,archived)["only_windows_recipe_changes"] is True

@pytest.mark.parametrize("target",["aarch64-apple-darwin","x86_64-unknown-linux-gnu"])
@pytest.mark.parametrize("bad",["current-unpinned","archived-unpinned","manifest-id","receipt-id","receipt-full-recipe",
                               "selected-base","selected-overlay","selected-upstream","selected-toolchain"])
def test_target_equivalence_rejects_input_or_selected_target_drift(tmp_path,monkeypatch,target,bad):
    pins,manifest,receipt,archived,path,current=equivalence_fixture(tmp_path,monkeypatch,target)
    if bad=="current-unpinned":path.write_text(path.read_text()+"\n")
    elif bad=="archived-unpinned":archived.write_text(archived.read_text()+"\n")
    elif bad=="manifest-id":manifest["native_recipe_id"]="0"*64
    elif bad=="receipt-id":receipt["native_recipe_id"]="0"*64
    elif bad=="receipt-full-recipe":receipt["overlay_recipe_sha256"]=pins["current_recipe_sha256"]
    else:
        if bad=="selected-base":current["required_base_inputs"][target]=[{"file":"changed.patch","sha256":"a"*64}]
        elif bad=="selected-overlay":current["overlays"].append({"file":"shared.patch","sha256":"b"*64,
            "changes_by_target":{target:[{"path":"shared.rs","fork_sha256":"a"*64}]}})
        elif bad=="selected-upstream":current["upstream_commit"]="d"*40
        elif bad=="selected-toolchain":current["build_toolchain"]="9.9.9"
        path.write_text(json.dumps(current));pins["current_recipe_sha256"]=reuse.sha(path)
    with pytest.raises(ValueError):reuse.target_recipe_equivalence(pins,target,manifest,receipt,archived)

@pytest.mark.parametrize("current", [None,"","PENDING_ROOT_SOLEWRITER_FINAL_FULL_RECIPE_SHA256","g"*64,"A"*64])
def test_current_full_recipe_pin_is_mandatory_and_never_derived_implicitly(tmp_path,monkeypatch,current):
    pins,pin,path,manifest=selected_fixture(tmp_path,monkeypatch)
    pins["current_recipe_sha256"]=current
    with pytest.raises(ValueError,match="explicit current full recipe"):
        reuse.selected_pin(pins,"aarch64-apple-darwin")

def test_current_selected_target_must_equal_manifest_before_download(tmp_path,monkeypatch):
    pins,pin,path,manifest=selected_fixture(tmp_path,monkeypatch)
    recipe_path=tmp_path/"native/runtime-overlays.json";recipe=json.loads(recipe_path.read_bytes())
    recipe["required_base_inputs"]["aarch64-apple-darwin"]=[{"file":"changed.patch","sha256":"c"*64}]
    recipe_path.write_text(json.dumps(recipe));pins["current_recipe_sha256"]=reuse.sha(recipe_path)
    with pytest.raises(ValueError,match="selected target recipe"):
        reuse.selected_pin(pins,"aarch64-apple-darwin")

def test_stable_recipe_download_still_uses_original_build_recipe_not_new_windows_file():
    pins,pin,manifest=release_fixture()
    pins["current_recipe_sha256"]="c"*64
    inputs=reuse.release_inputs(pins,pin,manifest)
    assert inputs["recipe"]["sha256"]==pins["recipe_sha256"]=="a"*64
    assert inputs["recipe"]["url"].endswith("/native-build-recipe-5c9e004f.json")

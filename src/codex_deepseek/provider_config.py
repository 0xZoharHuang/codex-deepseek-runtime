"""Explicit provider policy per isolated home; no inference fallback."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .protocol import PROVIDER, STRUCTURED_PROVIDER, SERIAL_PROVIDER, PEER_HOSTED_PROVIDER
from .image_transport import _open_private_file, _private_file_stat, _strict_json, ImageTransportError
from .platforms import file_lock, private_directory, private_fd, replace_file

FILENAME = "provider-routing.json"
LIMIT = 4096
ALLOWED = frozenset({PROVIDER, STRUCTURED_PROVIDER})
PEER_HOSTED_ALLOWED = frozenset({PEER_HOSTED_PROVIDER, SERIAL_PROVIDER})


def validate(value):
    if (not isinstance(value, dict) or not {"schema", "ordinary_default_provider"} <= set(value)
            or set(value) - {"schema", "ordinary_default_provider", "peer_hosted_provider"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["ordinary_default_provider"], str)
            or value["ordinary_default_provider"] not in ALLOWED):
        raise ValueError("Provider policy must contain schema=1 and ordinary_default_provider=deepseek or morph/fp8")
    if ("peer_hosted_provider" in value and (not isinstance(value["peer_hosted_provider"], str)
            or value["peer_hosted_provider"] not in PEER_HOSTED_ALLOWED)):
        raise ValueError("Peer hosted provider must be morph/fp8 or primeintellect")
    return dict(value)


def digest(value):
    return hashlib.sha256(json.dumps(validate(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def default():
    return {"schema": 1, "ordinary_default_provider": PROVIDER}


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink]


def load(home):
    """Read one bounded private-file snapshot; reject replacement/in-place races."""
    try: return _load(home)
    except ImageTransportError as error:
        raise ValueError("Invalid private provider policy path: " + str(error)) from error


def _load(home):
    path = Path(home).absolute() / "provider-state" / FILENAME
    before = _private_file_stat(path, missing=True)
    if before is None:
        if _private_file_stat(path, missing=True) is not None:
            raise ValueError("Provider policy appeared during its read")
        value = default()
        return {"policy": value, "sha256": digest(value), "source_sha256": None, "revision": "absent"}
    if before.st_size > LIMIT:
        raise ValueError("Provider policy exceeds its read limit")
    with _open_private_file(path, os.O_RDONLY, "rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())
        current = _private_file_stat(path)
    # Windows lstat and CRT fstat can expose different ctime semantics after
    # atomic rename. Preserve ctime checks within each API, plus exact common
    # file/content identity across APIs; never compare unlike timestamps.
    common = lambda info: [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink]
    if (len(raw) > LIMIT or _identity(before) != _identity(current)
            or _identity(opened) != _identity(after)
            or common(before) != common(opened) or common(after) != common(current)):
        raise ValueError("Provider policy changed during its read")
    value = validate(_strict_json(raw))
    source = hashlib.sha256(raw).hexdigest()
    revision = hashlib.sha256(json.dumps([_identity(current), source], separators=(",", ":")).encode()).hexdigest()
    return {"policy": value, "sha256": digest(value), "source_sha256": source, "revision": revision}


@contextmanager
def locked(home, *, create=False):
    home = Path(home).absolute()
    state = home / "provider-state"
    if create:
        # The same private file guard checks every ancestor before construction.
        _private_file_stat(state / FILENAME, missing=True)
        if not home.exists(): private_directory(home)
        if not state.exists(): private_directory(state)
    with _open_private_file(state / "gateway.lock", os.O_RDWR | os.O_CREAT | os.O_APPEND, "a+b") as stream:
        with file_lock(stream): yield state


def _status_locked(home, loaded):
    from .service import read_descriptor, native_active, _admitted_provider
    from .processes import matches
    admitted = read_descriptor(home)
    running = (matches(admitted.get("pid"), admitted.get("birth"))
               or matches(admitted.get("worker_pid"), admitted.get("worker_birth")))
    admitted_policy = None
    if running and any(key in admitted for key in ("provider_config", "provider_config_sha256", "ordinary_default_provider", "peer_hosted_provider")):
        admitted_policy = _admitted_provider(admitted)
    return {"configured_provider": loaded["policy"]["ordinary_default_provider"],
            "configured_peer_hosted_provider": loaded["policy"].get("peer_hosted_provider", PEER_HOSTED_PROVIDER),
            "configured_config_sha256": loaded["sha256"], "configured_revision": loaded["revision"],
            "configured_source_sha256": loaded["source_sha256"],
            "admitted_provider": admitted.get("ordinary_default_provider") if running else None,
            "admitted_peer_hosted_provider": admitted_policy.get("peer_hosted_provider", PEER_HOSTED_PROVIDER) if admitted_policy is not None else None,
            "admitted_config_sha256": admitted.get("provider_config_sha256") if running else None,
            "service_running": running, "native_active": native_active(home)}


def status(home):
    home = Path(home).absolute()
    if not (home / "provider-state").exists():
        return _status_locked(home, load(home))
    with locked(home): return _status_locked(home, load(home))


def _service_lease_held(state):
    # Same kernel lease and lock primitive, without following a redirected path.
    with _open_private_file(state / "gateway.lease", os.O_RDWR | os.O_CREAT | os.O_APPEND, "a+b") as stream:
        try:
            with file_lock(stream, blocking=False): return False
        except BlockingIOError:
            return True


def set_provider(home, provider, *, expected_revision=None):
    validate({"schema": 1, "ordinary_default_provider": provider})
    return _set_policy(home, lambda value: {**value, "ordinary_default_provider": provider},
                       expected_revision=expected_revision)


def set_peer_hosted_provider(home, provider, *, expected_revision=None):
    # "default" removes the extension, restoring schema-1 bytes accepted by older releases.
    if provider != "default":
        validate({**default(), "peer_hosted_provider": provider})
    def update(value):
        value = dict(value)
        value.pop("peer_hosted_provider", None)
        if provider != "default":
            value["peer_hosted_provider"] = provider
        return value
    return _set_policy(home, update, expected_revision=expected_revision)


def _set_policy(home, update, *, expected_revision=None):
    """Cooperative CAS under the existing service lock; never stop native/service owners."""
    from .service import native_active, read_descriptor
    from .processes import matches
    observed = load(home)
    expected = observed["revision"] if expected_revision is None else expected_revision
    if not isinstance(expected, str) or (expected != "absent" and (len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected))):
        raise ValueError("Expected provider revision must be absent or a lowercase SHA256")
    with locked(home, create=True) as state:
        before = load(home)
        if before["revision"] != expected:
            raise ValueError("Provider policy CAS failed: configuration changed")
        if native_active(home):
            raise RuntimeError("Provider policy cannot change while this home has active native owners; wait for natural retirement")
        admitted = read_descriptor(home)
        if (matches(admitted.get("pid"), admitted.get("birth"))
                or matches(admitted.get("worker_pid"), admitted.get("worker_birth"))
                or _service_lease_held(state)):
            raise RuntimeError("Provider service is still admitted for this home; wait for its natural idle retirement before changing policy")
        value = validate(update(before["policy"]))
        path = state / FILENAME
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=state, prefix=".provider-", delete=False) as stream:
                temporary = Path(stream.name); private_fd(stream.fileno())
                stream.write((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode())
                stream.flush(); os.fsync(stream.fileno())
            if load(home)["revision"] != before["revision"]:
                raise ValueError("Provider policy CAS failed before publication")
            replace_file(temporary, path)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
        after = load(home)
        if after["policy"] != value: raise ValueError("Provider policy changed after publication")
        return _status_locked(home, after)


def command(home, arguments):
    if arguments == ["status"]: result = status(home)
    elif len(arguments) in {2, 4} and arguments[0] in {"set", "set-peer-hosted"} and (len(arguments) == 2 or arguments[2] == "--if-match"):
        setter = set_provider if arguments[0] == "set" else set_peer_hosted_provider
        result = setter(home, arguments[1], expected_revision=arguments[3] if len(arguments) == 4 else None)
    else:
        raise ValueError("Usage: runtime provider status | runtime provider set deepseek|morph/fp8 [--if-match REVISION] | runtime provider set-peer-hosted morph/fp8|primeintellect|default [--if-match REVISION]")
    print(json.dumps(result, indent=2)); return 0

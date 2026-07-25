# GPDataCache URI Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GPDataCache` facade that manages `GPDCDomain` instances from a `GPConfigFolder` and resolves GPDC URIs (including multi-segment path URIs into anonymous nested objects) to `GPDCDataObject` instances.

**Architecture:** Single-file facade `src/gpdatacached/data_cache.py`. Domains are lazily constructed via gpconfig's object factory (`folder.get_object(name)`), enabled by import-time registration of `GPDCDomain`/`GPDCDomainConfig`. URI resolution parses the URI, fetches the root object via `ns.get_object(name)` (returns the object itself, not a value), then traverses `__getitem__` along path segments — coercing numeric segments to `int` for `ListObject`. A single `ObjectNotFoundByURIError` covers every failure mode.

**Tech Stack:** Python ≥3.10, redis ≥5, pydantic ≥2, gpconfig (sibling lib at `../gpconfig`).

## Global Constraints

- **Spec:** `reviews/superpowers/specs/2026-07-23-gpdatacache-design.md` is authoritative; this plan implements it.
- **Branch:** `test-configs`. Commit after every task. Rollback floor is `472e9c7` (the spec commit) — do not reset below it.
- **Redis for tests:** `127.0.0.1:6379 db=1 password=123456`. DB 1 is flushed per test by the `clean_redis` fixture in `tests/conftest.py`.
- **Line length 120** (ruff). `from __future__ import annotations` at top of every new module.
- **Sample configs already exist** at `tests/configs/global_env.yaml` + `tests/configs/gpdc/core_domain.yaml` + `tests/configs/gpdc/edge_domain.yaml` — use these, do not create new ones.
- **Container element scalar fact (verified by demo):** scalars inside a container are always inlined values; a scalar encountered during `__getitem__` path traversal is always a native Python value, never a referenced data object. Path traversal hitting a scalar = dead end = raise.
- **Root accessor is `ns.get_object(name)`, NOT `ns[name]`:** `__getitem__` unwraps scalar roots to native values (violates requirement 11); `get_object` returns the `GPDCDataObject` itself.
- **Namespace accessor is `domain.get_namespace(name)`:** it raises `KeyError` if the namespace is absent (correct for URI resolution — we do NOT want `ensure_namespace` which auto-creates).
- **Do not run tests with `-m "not slow"`** unless a task says so; the full suite is the safety net.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/gpdatacached/exceptions.py` | Exception hierarchy | MODIFY — append `ObjectNotFoundByURIError` |
| `src/gpdatacached/data_cache.py` | `GPDataCache` facade (single file) | CREATE |
| `src/gpdatacached/__init__.py` | Public API + import-time registration | MODIFY — add registration + exports |
| `tests/test_data_cache.py` | All tests for `GPDataCache` (local fixtures) | CREATE |

---

## Task 1: Add `ObjectNotFoundByURIError` exception

**Files:**
- Modify: `src/gpdatacached/exceptions.py` (append at end of file)

**Interfaces:**
- Produces: `ObjectNotFoundByURIError(uri: str, reason: str = "")` — a `GPDCError` subclass storing `self.uri` and `self.reason`. Later tasks raise it; the message is `f"Cannot resolve GPDC URI {uri!r}"` plus `: {reason}` if reason is non-empty.

- [ ] **Step 1: Write the failing test**

Create `tests/test_data_cache.py` with just the exception test and a local conftest-free fixture setup. The test file needs Redis (it will grow); start it minimal.

```python
# tests/test_data_cache.py
from __future__ import annotations

import pytest

from gpdatacached.exceptions import ObjectNotFoundByURIError, GPDCError


class TestObjectNotFoundByURIError:
    def test_is_gpdc_error_subclass(self):
        assert issubclass(ObjectNotFoundByURIError, GPDCError)

    def test_carries_uri_and_reason(self):
        err = ObjectNotFoundByURIError("gpdc://d/ns/missing", "object not found")
        assert err.uri == "gpdc://d/ns/missing"
        assert err.reason == "object not found"
        assert "gpdc://d/ns/missing" in str(err)
        assert "object not found" in str(err)

    def test_default_reason_empty(self):
        err = ObjectNotFoundByURIError("gpdc://d/ns/x")
        assert err.reason == ""
        assert "gpdc://d/ns/x" in str(err)
        assert ":" not in str(err).split("gpdc://d/ns/x")[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_data_cache.py::TestObjectNotFoundByURIError -v`
Expected: FAIL with `ImportError: cannot import name 'ObjectNotFoundByURIError'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/gpdatacached/exceptions.py` (after the `GPDCLockConfigInconsistentError` class):

```python
class ObjectNotFoundByURIError(GPDCError):
    """Raised when a GPDC URI cannot be resolved to a GPDCDataObject.

    Covers all failure modes of GPDataCache.get_object_by_uri / has_uri:
    missing domain config, missing namespace, missing root object, broken
    path traversal, scalar terminal (path traversal hit an inline scalar
    value), and malformed URI syntax.
    """

    def __init__(self, uri: str, reason: str = "") -> None:
        self.uri = uri
        self.reason = reason
        msg = f"Cannot resolve GPDC URI {uri!r}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_data_cache.py::TestObjectNotFoundByURIError -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/exceptions.py tests/test_data_cache.py
git commit -m "feat(exceptions): add ObjectNotFoundByURIError for URI resolution failures"
```

---

## Task 2: Import-time registration of `GPDCDomain` + `GPDCDomainConfig`

**Files:**
- Modify: `src/gpdatacached/__init__.py`

**Interfaces:**
- Produces: import side-effect — `GPConfigManager._configurable_classes["GPDCDomain"] = GPDCDomain` and `GPConfigManager._config_classes["GPDCDomainConfig"] = GPDCDomainConfig`. This makes `folder.get_object(name)` construct a `GPDCDomain`. Consumed by Task 4 (`GPDataCache.get_domain`).

**Context for the implementer:** Currently `__init__.py` does NOT register these. The sample config YAMLs (`tests/configs/gpdc/*.yaml`) already declare `cfg_class_name: GPDCDomainConfig` and `configured_class_name: GPDCDomain`, but nothing consumes those fields. `GPConfigManager.register_configurable_class` and `register_config_class` are both idempotent for the same class (verified in gpconfig's `manager.py`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_data_cache.py`:

```python
class TestImportTimeRegistration:
    def test_gpdc_domain_is_configurable(self):
        from gpconfig import GPConfigManager
        from gpdatacached.domain import GPDCDomain

        import gpdatacached  # ensure registration ran
        assert GPConfigManager._configurable_classes.get("GPDCDomain") is GPDCDomain

    def test_gpdc_domain_config_is_registered(self):
        from gpconfig import GPConfigManager
        from gpdatacached.config import GPDCDomainConfig

        import gpdatacached  # ensure registration ran
        assert GPConfigManager._config_classes.get("GPDCDomainConfig") is GPDCDomainConfig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_data_cache.py::TestImportTimeRegistration -v`
Expected: FAIL with `AssertionError` (classes not registered)

- [ ] **Step 3: Write minimal implementation**

In `src/gpdatacached/__init__.py`, add the registration calls AFTER the existing imports (after line 32, before line 34 the codec re-exports, OR anywhere after `.domain` and `.config` are imported). Insert after the `from .builtins.tuple_object import TupleObject` line (line 32):

```python
# Register GPDCDomain / GPDCDomainConfig with gpconfig's object factory so
# that folder.get_object(name) can construct a GPDCDomain from a config file.
# Both calls are idempotent for the same class (gpconfig guarantees this).
from gpconfig import GPConfigManager as _GPConfigManager

_GPConfigManager.register_configurable_class(GPDCDomain)
_GPConfigManager.register_config_class(GPDCDomainConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_data_cache.py::TestImportTimeRegistration -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run full suite to confirm no regression**

Run: `pytest -x -q`
Expected: PASS (no new failures; existing tests unaffected by registration — it's idempotent)

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/__init__.py tests/test_data_cache.py
git commit -m "feat(init): register GPDCDomain/GPDCDomainConfig with gpconfig factory at import"
```

---

## Task 3: Test fixtures for `GPDataCache` (config-folder-based)

**Files:**
- Modify: `tests/test_data_cache.py` (add fixtures at top, after imports)

**Interfaces:**
- Produces: pytest fixtures `gpdc_config_folder`, `data_cache` usable by all later test classes in this file. `data_cache` is a context-managed `GPDataCache` that closes on teardown.

**Context for the implementer:** `GPDataCache` doesn't exist yet (Task 4), so the `data_cache` fixture will fail to import until Task 4. This task only adds the fixtures and a `GPDataCache` placeholder import guard. To keep this task independently testable, the `gpdc_config_folder` fixture is verified here; the `data_cache` fixture is defined but marked to be exercised starting Task 4.

- [ ] **Step 1: Write the fixtures and a smoke test for the folder fixture**

Insert at the top of `tests/test_data_cache.py` (after the existing imports, before `TestObjectNotFoundByURIError`). Add the missing imports too:

```python
from pathlib import Path

import pytest

from gpdatacached.exceptions import ObjectNotFoundByURIError, GPDCError

CONFIGS_DIR = Path(__file__).parent / "configs"


@pytest.fixture
def gpdc_config_folder():
    """A GPConfigFolder rooted at tests/configs/gpdc, built from tests/configs."""
    from gpconfig import GPConfigManager

    mgr = GPConfigManager("gpdatacached", cfg_folder=str(CONFIGS_DIR))
    return mgr.get_config("gpdc")


@pytest.fixture
def data_cache(gpdc_config_folder, clean_redis):
    """A GPDataCache over the sample configs. Closes on teardown.

    clean_redis (from conftest.py) flushes db 1 before the test, so the
    domains constructed from core_domain.yaml / edge_domain.yaml start empty.
    """
    from gpdatacached.data_cache import GPDataCache

    cache = GPDataCache(gpdc_config_folder)
    yield cache
    cache.close()


class TestConfigFolderFixture:
    def test_folder_lists_sample_domains(self, gpdc_config_folder):
        names = gpdc_config_folder.list_configs()
        assert "core_domain" in names
        assert "edge_domain" in names
```

- [ ] **Step 2: Run test to verify the folder fixture works (data_cache not yet used)**

Run: `pytest tests/test_data_cache.py::TestConfigFolderFixture -v`
Expected: PASS (1 test). The `data_cache` fixture is defined but not exercised yet; that's fine.

- [ ] **Step 3: Commit**

```bash
git add tests/test_data_cache.py
git commit -m "test(data_cache): add config-folder fixtures for GPDataCache tests"
```

---

## Task 4: `GPDataCache` skeleton — `__init__`, `__contains__`, `get_domain`, `close`, context manager

**Files:**
- Create: `src/gpdatacached/data_cache.py`
- Modify: `src/gpdatacached/__init__.py` (add export)

**Interfaces:**
- Consumes: `GPConfigFolder` (from gpconfig), `GPDCDomain` (from `.domain`), gpconfig's `ConfigNotFoundError`.
- Produces: `GPDataCache` class with `__init__(folder)`, `__contains__(name)->bool`, `get_domain(name)->GPDCDomain`, `close()`, `__enter__`, `__exit__`. Later tasks add `get_object_by_uri` and `has_uri`.

**Context for the implementer:**
- `get_domain` calls `self._folder.get_object(name)` — this works because Task 2 registered the classes.
- `get_domain` must PROPAGATE `ConfigNotFoundError` (from gpconfig) when the domain has no config — do NOT wrap it. (The URI resolver in Task 5 wraps it; the direct getter does not.)
- `__contains__` uses `folder.list_configs()` — a pure config-snapshot check, no Redis, no instance creation.
- `close` must be idempotent and must close every cached domain.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_data_cache.py`:

```python
class TestGPDataCacheDomainManagement:
    def test_contains_known_domain(self, data_cache):
        assert "core_domain" in data_cache
        assert "edge_domain" in data_cache

    def test_contains_unknown_domain(self, data_cache):
        assert "no_such_domain" not in data_cache

    def test_get_domain_constructs_and_caches(self, data_cache):
        d1 = data_cache.get_domain("core_domain")
        d2 = data_cache.get_domain("core_domain")
        assert d1 is d2  # same cached instance

    def test_get_domain_returns_working_domain(self, data_cache):
        from gpdatacached.domain import GPDCDomain

        d = data_cache.get_domain("core_domain")
        assert isinstance(d, GPDCDomain)
        assert d.name == "core_domain"
        # The constructed domain actually works against Redis
        ns = d.get_namespace("core_app")
        ns["k"] = "v"
        assert ns["k"] == "v"

    def test_get_domain_unknown_raises_config_not_found(self, data_cache):
        from gpconfig import ConfigNotFoundError

        with pytest.raises(ConfigNotFoundError):
            data_cache.get_domain("no_such_domain")

    def test_close_closes_cached_domains(self, data_cache):
        d = data_cache.get_domain("core_domain")
        assert d._closed is False
        data_cache.close()
        assert d._closed is True

    def test_close_is_idempotent(self, data_cache):
        data_cache.get_domain("core_domain")
        data_cache.close()
        data_cache.close()  # must not raise

    def test_context_manager_closes_on_exit(self, gpdc_config_folder, clean_redis):
        from gpdatacached.data_cache import GPDataCache

        with GPDataCache(gpdc_config_folder) as cache:
            d = cache.get_domain("core_domain")
            assert d._closed is False
        assert d._closed is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_data_cache.py::TestGPDataCacheDomainManagement -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gpdatacached.data_cache'`

- [ ] **Step 3: Write minimal implementation**

Create `src/gpdatacached/data_cache.py`:

```python
"""GPDataCache: a facade managing GPDCDomain instances and GPDC-URI access."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gpconfig import GPConfigFolder

    from .domain import GPDCDomain
    from .object import GPDCDataObject

logger = logging.getLogger(__name__)


class GPDataCache:
    """Manage GPDCDomain instances from a GPConfigFolder and resolve GPDC URIs.

    Domains are constructed lazily via gpconfig's object factory
    (``folder.get_object(name)``) and cached. Construction happens on first
    ``get_domain`` access; ``__init__`` does not create any domain.

    The cache is a snapshot: config files edited after construction are NOT
    re-read. Rebuild the GPDataCache to pick up changes.

    Use as a context manager to ensure all cached domains are closed::

        with GPDataCache(folder) as cache:
            ...
    """

    def __init__(self, folder: "GPConfigFolder") -> None:
        self._folder = folder
        self._domains: dict[str, GPDCDomain] = {}
        self._closed = False

    def __contains__(self, domain_name: object) -> bool:
        """Return True iff ``domain_name`` has a config in this folder.

        Pure config-snapshot check via ``folder.list_configs()``: does not
        touch Redis and does not construct a GPDCDomain.
        """
        if not isinstance(domain_name, str):
            return False
        return domain_name in self._folder.list_configs()

    def get_domain(self, domain_name: str) -> "GPDCDomain":
        """Return the cached GPDCDomain for ``domain_name``, creating it lazily.

        Propagates gpconfig's ``ConfigNotFoundError`` if the domain has no
        config file in this folder. GPDataCache does not create domains
        without a config (use GPDCDomain directly for ad-hoc domains).
        """
        self._check_open()
        if domain_name not in self._domains:
            self._domains[domain_name] = self._folder.get_object(domain_name)
        return self._domains[domain_name]

    def close(self) -> None:
        """Close every cached GPDCDomain. Idempotent."""
        if self._closed:
            return
        for name, domain in self._domains.items():
            try:
                domain.close()
            except Exception:
                logger.warning("Error closing domain %r during GPDataCache.close()", name, exc_info=True)
        self._domains.clear()
        self._closed = True

    def __enter__(self) -> "GPDataCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("GPDataCache is closed")
```

Add the export in `src/gpdatacached/__init__.py`. In the imports block, after `from .data_cache import GPDataCache` — add this import near the other `from .X import Y` lines (e.g. after line 22 `from .container import GPDCContainerObject`):

```python
from .data_cache import GPDataCache
```

And add `"GPDataCache"` to the `__all__` list (e.g. after `"GPDCDomain"`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_data_cache.py::TestGPDataCacheDomainManagement -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run full suite to confirm no regression**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/data_cache.py src/gpdatacached/__init__.py tests/test_data_cache.py
git commit -m "feat(data_cache): add GPDataCache with lazy domain cache, __contains__, close"
```

---

## Task 5: `get_object_by_uri` — URI parsing + root object access

**Files:**
- Modify: `src/gpdatacached/data_cache.py` (add `_parse_uri` + partial `get_object_by_uri`)
- Modify: `src/gpdatacached/__init__.py` (export `ObjectNotFoundByURIError`)

**Interfaces:**
- Produces: `get_object_by_uri(uri: str) -> GPDCDataObject`. This task implements the **no-path case** (URI with exactly 3 segments → root object) plus URI parsing. Task 6 adds path traversal; Task 7 adds `has_uri`.

**Context for the implementer:**
- `_parse_uri` must reject: non-`gpdc://` scheme, < 3 segments, empty segments.
- Root object fetched via `ns.get_object(name)` (NOT `ns[name]` — `__getitem__` unwraps scalars to values).
- Namespace fetched via `domain.get_namespace(name)` which raises `KeyError` if absent — wrap that into `ObjectNotFoundByURIError`.
- `get_domain`'s `ConfigNotFoundError` (when domain has no config) must be caught and wrapped into `ObjectNotFoundByURIError` here (single-exception contract for URI access).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_data_cache.py`:

```python
class TestGetObjectByURIRoot:
    def test_scalar_root_returns_object_not_value(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["counter"] = 42

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/counter")
        from gpdatacached import IntObject

        assert isinstance(obj, IntObject)
        assert obj.value == 42

    def test_container_root_returns_object(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["config"] = {"a": 1}

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/config")
        from gpdatacached import DictObject

        assert isinstance(obj, DictObject)

    def test_missing_scheme_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("http://d/ns/x")
        assert "gpdc://" in str(ei.value.reason)

    def test_too_few_segments_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://d/ns")

    def test_unknown_domain_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("gpdc://no_such_domain/ns/x")
        assert ei.value.uri == "gpdc://no_such_domain/ns/x"

    def test_missing_namespace_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/no_such_ns/x")

    def test_missing_root_object_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/no_such_obj")
        assert "no_such_obj" in str(ei.value.reason) or "no_such_obj" in str(ei.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_data_cache.py::TestGetObjectByURIRoot -v`
Expected: FAIL with `AttributeError: 'GPDataCache' object has no attribute 'get_object_by_uri'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/gpdatacached/data_cache.py` (inside the `GPDataCache` class, after `get_domain`):

```python
    def get_object_by_uri(self, uri: str) -> "GPDCDataObject":
        """Resolve a GPDC URI to a GPDCDataObject.

        ``uri`` must be ``gpdc://{domain}/{namespace}/{name}`` optionally
        followed by path segments that descend into nested containers via
        ``__getitem__`` (e.g. ``gpdc://d/ns/obj/key/0``).

        Always returns a GPDCDataObject — never a native value. A path that
        terminates on a scalar value raises ObjectNotFoundByURIError.

        Raises ObjectNotFoundByURIError for every failure mode: malformed
        URI, missing domain config, missing namespace, missing root object,
        broken path, scalar terminal.
        """
        self._check_open()
        domain_name, ns_name, obj_name, path_parts = self._parse_uri(uri)

        # Domain: wrap ConfigNotFoundError into the URI exception contract.
        try:
            domain = self.get_domain(domain_name)
        except ConfigNotFoundError as e:
            raise ObjectNotFoundByURIError(uri, f"domain {domain_name!r} not configured") from e

        # Namespace: get_namespace raises KeyError if absent.
        try:
            ns = domain.get_namespace(ns_name)
        except KeyError:
            raise ObjectNotFoundByURIError(
                uri, f"namespace {ns_name!r} not found in domain {domain_name!r}"
            )

        # Root object: get_object returns the object itself (not a value).
        try:
            root = ns.get_object(obj_name)
        except KeyError:
            raise ObjectNotFoundByURIError(
                uri, f"object {obj_name!r} not found in namespace {ns_name!r}"
            )

        # Path traversal is added in a later task; with no path_parts the
        # root IS the terminal. A root scalar object is itself a GPDCDataObject,
        # so it passes the terminal check.
        return self._traverse(root, path_parts, uri)

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str, str, list[str]]:
        """Split a GPDC URI into (domain, namespace, name, path_parts)."""
        if not uri.startswith("gpdc://"):
            raise ObjectNotFoundByURIError(uri, "URI must start with 'gpdc://'")
        rest = uri[len("gpdc://"):]
        parts = rest.split("/")
        if len(parts) < 3:
            raise ObjectNotFoundByURIError(
                uri, "URI must have at least domain/namespace/name segments"
            )
        if any(p == "" for p in parts):
            raise ObjectNotFoundByURIError(uri, "URI contains an empty segment")
        domain, ns_name, obj_name = parts[0], parts[1], parts[2]
        path_parts = parts[3:]
        return domain, ns_name, obj_name, path_parts

    @staticmethod
    def _traverse(root, path_parts, uri):
        """Descend from root along path_parts. Fully implemented in Task 6."""
        # Placeholder: Task 6 fills in real traversal. For now, only the
        # no-path case (path_parts empty) is valid and returns root.
        if path_parts:
            raise ObjectNotFoundByURIError(uri, "path traversal not yet supported")
        return root
```

Also add the imports at the top of `src/gpdatacached/data_cache.py` (in the `if TYPE_CHECKING:` block is wrong — these are runtime imports). Add after the `import logging` line:

```python
from gpconfig import ConfigNotFoundError

from .exceptions import ObjectNotFoundByURIError
```

(Remove `ConfigNotFoundError` from the TYPE_CHECKING block if it was added there; it must be a real import.)

Update `src/gpdatacached/__init__.py` to export the exception. Add `ObjectNotFoundByURIError` to the `from .exceptions import (...)` block and to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_data_cache.py::TestGetObjectByURIRoot -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run full suite**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/data_cache.py src/gpdatacached/__init__.py tests/test_data_cache.py
git commit -m "feat(data_cache): add get_object_by_uri with URI parsing and root access"
```

---

## Task 6: `get_object_by_uri` — path traversal into nested objects

**Files:**
- Modify: `src/gpdatacached/data_cache.py` (replace `_traverse` placeholder with real implementation)

**Interfaces:**
- Produces: full `_traverse(root, path_parts, uri) -> GPDCDataObject` handling: type-based key coercion (`ListObject` → int via `str.isdigit()`), `__getitem__` failure wrapping, no-`__getitem__` rejection (SetObject), scalar-terminal rejection.

**Context for the implementer:**
- Key coercion is by **current object type** (decision D9): `ListObject` → must be a non-negative integer string, else raise; `DictObject`/pydantic container → keep string.
- `str.isdigit()` is the coercion check (rejects negatives, decimals, whitespace).
- A scalar encountered mid-path or as terminal → `isinstance(current, GPDCDataObject)` is False → raise.
- `SetObject` has no `__getitem__`, so `hasattr(current, "__getitem__")` is False → raise.
- Import `ListObject` lazily inside `_traverse` (avoid circular import at module load).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_data_cache.py`:

```python
class TestGetObjectByURIPathTraversal:
    def test_nested_dict_path(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"server": {"host": "localhost", "port": 8080}}

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/cfg/server")
        from gpdatacached import DictObject

        assert isinstance(obj, DictObject)

    def test_deeply_nested_path(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["deep"] = {"a": {"b": {"c": {"d": 1}}}}

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/deep/a/b/c")
        from gpdatacached import DictObject

        assert isinstance(obj, DictObject)

    def test_list_numeric_index_to_container(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["mixed"] = [{"k": 1}, {"k": 2}]  # list of dicts

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/mixed/1")
        from gpdatacached import DictObject

        assert isinstance(obj, DictObject)
        assert obj["k"] == 2

    def test_scalar_terminal_in_path_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"port": 8080}

        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/cfg/port")
        assert "scalar" in str(ei.value.reason).lower()

    def test_list_scalar_terminal_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["nums"] = [10, 20, 30]

        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/nums/0")

    def test_list_non_numeric_index_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["nums"] = [10, 20]

        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/nums/abc")
        assert "non-negative integer" in str(ei.value.reason)

    def test_missing_dict_key_in_path_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"a": 1}

        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/cfg/missing")

    def test_set_descent_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["s"] = {1, 2, 3}

        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/s/anything")

    def test_set_root_without_path_ok(self, data_cache):
        # SetObject as root (no path) is valid — it IS a GPDCDataObject.
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["s"] = {1, 2, 3}

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/s")
        from gpdatacached import SetObject

        assert isinstance(obj, SetObject)

    def test_path_descends_past_scalar_raises(self, data_cache):
        # tags[0] is scalar "web"; descending further must raise.
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"tags": ["web", "api"]}

        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/cfg/tags/0/extra")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_data_cache.py::TestGetObjectByURIPathTraversal -v`
Expected: FAIL (placeholder raises "path traversal not yet supported")

- [ ] **Step 3: Write minimal implementation**

Replace the `_traverse` placeholder in `src/gpdatacached/data_cache.py` with the full implementation:

```python
    @staticmethod
    def _traverse(root, path_parts, uri):
        """Descend from ``root`` along ``path_parts`` via __getitem__.

        Key coercion is by current object type: ListObject segments must be
        non-negative integers; DictObject/pydantic-container segments stay
        strings. A scalar terminal (or a scalar encountered mid-path) raises
        ObjectNotFoundByURIError — get_object_by_uri returns objects only.
        """
        from .object import GPDCDataObject
        from .builtins.list_object import ListObject

        current = root
        for part in path_parts:
            if not isinstance(current, GPDCDataObject):
                # Defensive: a scalar was reached but path continues.
                raise ObjectNotFoundByURIError(
                    uri, f"path segment {part!r} cannot descend: current node is not a GPDCDataObject"
                )
            if not hasattr(current, "__getitem__"):
                raise ObjectNotFoundByURIError(
                    uri,
                    f"path segment {part!r} cannot descend: "
                    f"{current.canonical_uri} does not support subscript access",
                )
            # Type-based key coercion (decision D9).
            if isinstance(current, ListObject):
                if not part.isdigit():
                    raise ObjectNotFoundByURIError(
                        uri,
                        f"path segment {part!r} invalid for ListObject: "
                        f"must be a non-negative integer",
                    )
                key = int(part)
            else:
                key = part  # DictObject / pydantic container / etc.
            try:
                current = current[key]
            except (KeyError, IndexError, TypeError) as e:
                raise ObjectNotFoundByURIError(
                    uri, f"path segment {part!r} failed at {current.canonical_uri}: {e}"
                ) from e
        # Terminal check: must be a GPDCDataObject, not a scalar value.
        if not isinstance(current, GPDCDataObject):
            raise ObjectNotFoundByURIError(
                uri, "terminal is a scalar value; get_object_by_uri returns objects only"
            )
        return current
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_data_cache.py::TestGetObjectByURIPathTraversal -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Run the full data_cache test file**

Run: `pytest tests/test_data_cache.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 6: Run full suite**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/gpdatacached/data_cache.py tests/test_data_cache.py
git commit -m "feat(data_cache): add path traversal in get_object_by_uri for nested objects"
```

---

## Task 7: `has_uri` — existence check aligned with `get_object_by_uri`

**Files:**
- Modify: `src/gpdatacached/data_cache.py` (add `has_uri`)

**Interfaces:**
- Produces: `has_uri(uri: str) -> bool`. Returns True iff `get_object_by_uri(uri)` would succeed (return a GPDCDataObject). Swallows ONLY `ObjectNotFoundByURIError`; infrastructure errors (Redis down, etc.) propagate.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_data_cache.py`:

```python
class TestHasURI:
    def test_existing_scalar_root(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["counter"] = 42
        assert data_cache.has_uri("gpdc://core_domain/core_app/counter") is True

    def test_existing_container_root(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"a": 1}
        assert data_cache.has_uri("gpdc://core_domain/core_app/cfg") is True

    def test_existing_nested_path(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"server": {"host": "x"}}
        assert data_cache.has_uri("gpdc://core_domain/core_app/cfg/server") is True

    def test_missing_root(self, data_cache):
        assert data_cache.has_uri("gpdc://core_domain/core_app/no_such") is False

    def test_missing_domain(self, data_cache):
        assert data_cache.has_uri("gpdc://no_such_domain/ns/x") is False

    def test_missing_namespace(self, data_cache):
        assert data_cache.has_uri("gpdc://core_domain/no_such_ns/x") is False

    def test_scalar_terminal(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"port": 8080}
        assert data_cache.has_uri("gpdc://core_domain/core_app/cfg/port") is False

    def test_broken_path(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"a": 1}
        assert data_cache.has_uri("gpdc://core_domain/core_app/cfg/missing") is False

    def test_malformed_uri(self, data_cache):
        assert data_cache.has_uri("http://d/ns/x") is False

    def test_too_few_segments(self, data_cache):
        assert data_cache.has_uri("gpdc://d/ns") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_data_cache.py::TestHasURI -v`
Expected: FAIL with `AttributeError: 'GPDataCache' object has no attribute 'has_uri'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/gpdatacached/data_cache.py` (inside `GPDataCache`, after `get_object_by_uri`):

```python
    def has_uri(self, uri: str) -> bool:
        """Return True iff ``get_object_by_uri(uri)`` would succeed.

        Returns False for every ObjectNotFoundByURIError failure mode
        (missing domain/namespace/object, broken path, scalar terminal,
        malformed URI). Infrastructure errors (e.g. Redis connection
        failures) propagate — they are not "the URI doesn't exist".
        """
        self._check_open()
        try:
            self.get_object_by_uri(uri)
        except ObjectNotFoundByURIError:
            return False
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_data_cache.py::TestHasURI -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Run full suite**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 6: Lint check**

Run: `ruff check src/gpdatacached/data_cache.py src/gpdatacached/__init__.py src/gpdatacached/exceptions.py tests/test_data_cache.py`
Expected: no errors (fix any that appear before committing)

- [ ] **Step 7: Commit**

```bash
git add src/gpdatacached/data_cache.py tests/test_data_cache.py
git commit -m "feat(data_cache): add has_uri existence check aligned with get_object_by_uri"
```

---

## Task 8: Cross-domain isolation and final integration test

**Files:**
- Modify: `tests/test_data_cache.py` (add integration tests)

**Interfaces:**
- Consumes: everything from Tasks 1-7.

**Context for the implementer:** This task verifies the two-domain sample (`core_domain` and `edge_domain` coexist on the same Redis DB 1), that URI resolution is domain-scoped, and that `has_uri`/`get_object_by_uri` agree across a representative matrix.

- [ ] **Step 1: Write the integration tests**

Append to `tests/test_data_cache.py`:

```python
class TestCrossDomainIntegration:
    def test_two_domains_coexist(self, data_cache):
        d_core = data_cache.get_domain("core_domain")
        d_edge = data_cache.get_domain("edge_domain")
        assert d_core is not d_edge
        assert d_core.name == "core_domain"
        assert d_edge.name == "edge_domain"

    def test_uri_is_domain_scoped(self, data_cache):
        # Write to core_domain, ensure edge_domain doesn't see it via URI.
        d_core = data_cache.get_domain("core_domain")
        ns_core = d_core.get_namespace("core_app")
        ns_core["secret"] = "core"

        # edge_blob namespace in edge_domain is empty for this object.
        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://edge_domain/edge_blob/secret")

    def test_has_uri_and_get_object_agree(self, data_cache):
        d = data_cache.get_domain("edge_domain")
        ns = d.get_namespace("edge_session")
        ns["nested"] = {"inner": {"deep": [1, 2, 3]}}

        # Matrix of agreement
        cases = [
            ("gpdc://edge_domain/edge_session/nested", True),
            ("gpdc://edge_domain/edge_session/nested/inner", True),
            # deep is a dict; deep/nosuch missing
            ("gpdc://edge_domain/edge_session/nested/inner/nosuch", False),
            # deep contains list [1,2,3]; deep/0 is scalar 1 -> not an object
            ("gpdc://edge_domain/edge_session/nested/inner/deep/0", False),
            ("gpdc://edge_domain/edge_session/missing", False),
            ("gpdc://nope/ns/x", False),
        ]
        for uri, expected in cases:
            assert data_cache.has_uri(uri) is expected, f"has_uri mismatch for {uri}"
            if expected:
                obj = data_cache.get_object_by_uri(uri)
                assert obj is not None
            else:
                with pytest.raises(ObjectNotFoundByURIError):
                    data_cache.get_object_by_uri(uri)

    def test_get_domain_unknown_after_successful_get(self, data_cache):
        # Cache is populated by a successful get; an unknown domain still raises.
        data_cache.get_domain("core_domain")
        from gpconfig import ConfigNotFoundError

        with pytest.raises(ConfigNotFoundError):
            data_cache.get_domain("no_such_domain")
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_data_cache.py::TestCrossDomainIntegration -v`
Expected: PASS (4 tests)

- [ ] **Step 3: Run the entire data_cache test file verbosely as a final check**

Run: `pytest tests/test_data_cache.py -v`
Expected: PASS (all ~45 tests across all classes)

- [ ] **Step 4: Run the full test suite as the final safety net**

Run: `pytest -q`
Expected: PASS (no regressions)

- [ ] **Step 5: Lint everything touched**

Run: `ruff check src tests`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add tests/test_data_cache.py
git commit -m "test(data_cache): add cross-domain isolation and has_uri/get_object agreement tests"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Req 1 (GPDataCache manages domains): Task 4 ✓
- Req 2 (init from GPConfigFolder): Task 4 ✓
- Req 3 (GPConfigManager → gpdc folder): Task 3 fixture + §8 of spec ✓
- Req 4 (get_object_by_uri exists→object, else raise): Tasks 5, 6 ✓
- Req 5 (lazy init + cache GPDCDomain): Task 4 ✓
- Req 6 (__contains__ → bool): Task 4 ✓
- Req 7 (has_uri): Task 7 ✓
- Req 8 (get_domain lazy create+cache): Task 4 ✓
- Req 9 (no config-less domain creation): Task 4 (get_domain propagates ConfigNotFoundError) ✓
- Req 10 (path traversal + ListObject int coercion + no slice + objects only): Task 6 ✓
- Req 11 (objects only, never values): Tasks 5, 6 (terminal check) ✓
- Req 12 (must start with gpdc://): Task 5 (_parse_uri) ✓

**Placeholder scan:** Task 5 has an intentionally temporary `_traverse` that Task 6 replaces — this is staged TDD, not a placeholder bug; both stages have runnable tests. No TBD/TODO/"add error handling" anywhere.

**Type consistency:** `get_domain`, `get_object_by_uri`, `has_uri`, `__contains__`, `close`, `__enter__`, `__exit__` signatures match across all tasks. `_parse_uri` returns the same `tuple[str, str, str, list[str]]` in Task 5 definition and Task 5 caller. `_traverse(root, path_parts, uri)` signature identical in Task 5 (placeholder) and Task 6 (full). `ObjectNotFoundByURIError(uri, reason="")` consistent everywhere.

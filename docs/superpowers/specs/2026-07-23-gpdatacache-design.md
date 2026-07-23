# GPDataCache: URI-Based Access to GPDC Data Objects

**Date:** 2026-07-23
**Status:** Approved (design phase)
**Author:** Lynn / Linnet Codes
**Scope:** Add a `GPDataCache` facade class that manages GPDC domains from a
`GPConfigFolder` and exposes GPDC-URI-based access to data objects (including
anonymous nested objects via path traversal).

---

## 1. Background & Motivation

The library defines a URI form `gpdc://{domain}/{namespace}/{name}` and ships
two helpers — `make_uri()` and `uri_to_redis_key()` — but there is **no
interface that actually uses a URI to access a data object**. `uri_to_redis_key`
has zero callers outside its own unit tests; `make_uri` is only used to build
identity/error strings (`GPDCDataObject.uri`, exception messages). The URI is a
display label, not an access path.

This design closes that gap by introducing `GPDataCache`: a facade that owns a
set of `GPDCDomain` instances (lazily constructed from a `GPConfigFolder`) and
resolves GPDC URIs — including multi-segment **path** URIs that traverse into
anonymous nested objects — back to `GPDCDataObject` instances.

### Architectural facts established by live demos (2026-07-23)

These were verified against a live Redis (127.0.0.1:6379 db 1), not inferred:

1. **`GPDCNamespace.get_object(key)` returns the data object itself** for both
   scalars and containers, whereas `__getitem__` (`ns[key]`) returns the native
   value for scalars (by design: "标量返回原生值,容器返回对象本身"). The root
   accessor for URI resolution must therefore be `get_object`, not `[]`.
2. **Scalars inside a container are always inlined values.** `encode_element`
   (`types.py:118-126`) only creates objects/refs for containers; scalars are
   stored as inline encoded strings (`int:42`, `str:hello`), never as `ref:`
   pointers. `decode_element` therefore returns a native value for any scalar
   element encountered during `__getitem__` traversal.
3. **A container cannot reference a scalar data object at all** — attempting
   `ns["w"] = {"k": scalar_object}` raises `TypeError: Unsupported Python type:
   'IntObject'`. So "container references a scalar data object" does not occur
   in the current architecture; a scalar encountered during path traversal is
   *always* an inline terminal value.
4. **The gpconfig factory path works end-to-end.** After
   `register_configurable_class(GPDCDomain)` + `register_config_class
   (GPDCDomainConfig)`, `folder.get_object("core_domain")` returns a fully
   constructed, working `GPDCDomain` (name derived from filename stem). This was
   verified against the sample configs in `tests/configs/gpdc/`.

These facts are load-bearing for the rules in §4.

---

## 2. Requirements (from the user's brief, 11 items)

1. Add a `GPDataCache` class that manages all domains whose configs are provided.
2. `GPDataCache` is initialized from a `GPConfigFolder` instance.
3. Using `GPDataCache` requires first creating a `GPConfigManager` from a config
   folder, then obtaining the `GPConfigFolder` for the `gpdc` subfolder from it.
   (The "gpdc" name is a *convention/example*, not an enforced constraint — see
   decision D7.)
4. `get_object_by_uri(uri)` returns a `GPDCDataObject` subclass instance of the
   URI's type if it exists; raises otherwise.
5. `GPDataCache` initializes `GPDCDomain` instances from the folder's configs and
   caches them internally (lazily).
6. `__contains__(domain_name: str) -> bool` reports whether a named domain exists.
7. `has_uri(uri: str) -> bool` reports whether an object exists at a GPDC URI.
8. `get_domain(domain_name: str)` returns a configured domain, creating and
   caching it on first access.
9. `GPDataCache` does **not** create domains lacking a config file. Use
   `GPDCDomain` directly for ad-hoc domains.
10. `get_object_by_uri` supports **path traversal** into anonymous objects via
    the URI (e.g. `gpdc://domain/namespace/obj_a/key_a/key_1`), descending via
    `__getitem__`. For `ListObject`, pure-numeric path segments are converted to
    `int`; only integer indices, never slices. Traversal stops when the current
    node is no longer a `GPDCDataObject` or lacks `__getitem__`.
    *(Clarified: a scalar terminal is illegal — see §4.2.)*
11. `get_object_by_uri` returns **data objects only**, never values — even for
    scalar roots and even when a path could be written to an element value.
    Illegal accesses raise with a reason.
12. All URIs must begin with `gpdc://`.

---

## 3. Decision Summary

| # | Decision point | Choice |
|---|---|---|
| D1 | Domain creation path | gpconfig standard factory: import-time `register_configurable_class(GPDCDomain)` + `register_config_class(GPDCDomainConfig)`; `folder.get_object(name)` |
| D2 | Registration timing | At import time in `gpdatacached/__init__.py`, alongside `_register_builtin_types()` |
| D3 | Scalar terminal in path | Illegal → raise `ObjectNotFoundByURIError` |
| D4 | `has_uri` semantics | Fully aligned with `get_object_by_uri`; returns `False` for every failure mode |
| D5 | Domain cache lifecycle | Fully lazy creation + context manager + `close()` + never auto-reload |
| D6 | Exception type | Single `ObjectNotFoundByURIError(GPDCError)` carrying `.uri` and `.reason` |
| D7 | `GPConfigFolder` constraint | Lenient: accepts any `relative_path` |
| D8 | `__contains__` basis | Config snapshot via `folder.list_configs()`; does not touch Redis, does not create instances |
| D9 | Numeric key coercion | Decided by the current object's type: `ListObject` → non-negative `int`; `DictObject`/pydantic container → string; `SetObject` → no descent |
| D10 | Code organization | Single file `src/gpdatacached/data_cache.py` |

---

## 4. Design

### 4.1 Components

```
src/gpdatacached/data_cache.py     NEW  — GPDataCache facade (single file)
src/gpdatacached/exceptions.py     ADD  — ObjectNotFoundByURIError
src/gpdatacached/__init__.py       MOD  — import-time registration + exports
```

Relationships:

```
                    GPDataCache
                    ─────────────────────────
   _folder : GPConfigFolder          (injected, read-only)
   _domains: dict[str, GPDCDomain]   (lazy cache)
   _closed : bool

   __init__(folder)
   __contains__(domain_name) -> bool     # folder.list_configs()
   get_domain(name)         -> GPDCDomain  # get_object + cache
   get_object_by_uri(uri)   -> GPDCDataObject
   has_uri(uri)             -> bool
   close() / __enter__ / __exit__
```

### 4.2 URI parsing and path traversal — the core contract

#### 4.2.1 Root-object access: `get_object`, not `__getitem__`

Per fact #1, the root object (3rd URI segment) is fetched via
`ns.get_object(name)`, which returns the `GPDCDataObject` itself for both scalars
and containers. Using `ns[name]` would wrongly unwrap scalar roots to native
values, violating requirement 11.

#### 4.2.2 Final access rules (verified by demo)

| Layer | Rule | Accessor |
|---|---|---|
| **Root object** (URI segment 3, `name`) | Scalar *or* container → return the object itself | `ns.get_object(name)` |
| **Path descent** (segments 4+, `path_parts`) | Traverse via `__getitem__`; a scalar element encountered en route is *always* an inline native value (facts #2, #3) → terminal is illegal → raise | `obj[key]` |

The scalar/container asymmetry between root and path is not an artificial
restriction — it is forced by the architecture: `get_object` returns objects;
`__getitem__` returns native values for scalar elements (and there is no way for
a container to reference a scalar data object, per fact #3).

#### 4.2.3 URI parsing (`_parse_uri`, private)

```
input:  uri (str)
output: (domain, namespace, name, path_parts: list[str])
rules:
  1. must start with "gpdc://"                    else ObjectNotFoundByURIError
  2. strip prefix, split on "/"
  3. >= 3 leading segments (domain, namespace, name); fewer → error
  4. segments 4+ are path_parts (may be empty)
  5. every segment non-empty (no empty segments)
```

`uri_to_redis_key` is intentionally **not** reused: it validates exactly three
segments and produces a Redis key, whereas path URIs need arbitrary trailing
segments and no key. Extending it would break its documented contract.

#### 4.2.4 Path traversal (`_traverse`, private)

```
current = root  # a GPDCDataObject
for part in path_parts:
    if not isinstance(current, GPDCDataObject):
        raise ObjectNotFoundByURIError(uri, "encountered non-object mid-path")  # defensive
    if not hasattr(current, "__getitem__"):
        raise ObjectNotFoundByURIError(uri, f"{current.canonical_uri} does not support subscript access")
    # D9: coerce by current object type
    if isinstance(current, ListObject):
        if not part.isdigit():
            raise ObjectNotFoundByURIError(uri, f"ListObject path segment must be a non-negative integer, got {part!r}")
        key = int(part)
    else:
        key = part   # DictObject / pydantic container / etc.
    try:
        current = current[key]
    except (KeyError, IndexError, TypeError) as e:
        raise ObjectNotFoundByURIError(uri, f"path segment {part!r} failed at {current.canonical_uri}: {e}") from e
# terminal check
if not isinstance(current, GPDCDataObject):
    raise ObjectNotFoundByURIError(uri, "terminal is a scalar value; get_object_by_uri returns objects only")
return current
```

`str.isdigit()` is used (not `try: int(part)`) because it rejects negative
signs, decimals, and whitespace in one strict check, matching "non-negative
integer only".

#### 4.2.5 `SetObject`

`SetObject` has no `__getitem__`. A URI ending at a SetObject (no path_parts)
returns it; any path_parts into a SetObject fails the `hasattr(__getitem__)`
check → `ObjectNotFoundByURIError`.

#### 4.2.6 End-to-end examples

Given `ns["config"] = {"server": {"host": "localhost", "port": 8080}, "tags":
["web","api"], "counters": [10,20]}` and `ns["counter"] = 42`:

| URI | Result |
|---|---|
| `gpdc://d/ns/counter` | `IntObject` (root scalar → object itself) ✓ |
| `gpdc://d/ns/config` | `DictObject` (config) ✓ |
| `gpdc://d/ns/config/server` | `DictObject` (server, nested container) ✓ |
| `gpdc://d/ns/config/server/port` | raise — terminal `8080` is scalar |
| `gpdc://d/ns/config/tags` | `ListObject` (tags) ✓ |
| `gpdc://d/ns/config/tags/0` | raise — `tags[0]` = `"web"` scalar |
| `gpdc://d/ns/config/counters/0` | raise — `counters[0]` = `10` scalar |
| `gpdc://d/ns/config/missing` | raise — `KeyError` on path segment |
| `gpdc://d/ns/config/tags/0/x` | raise — `tags[0]` scalar, cannot descend |

### 4.3 Error handling

#### `ObjectNotFoundByURIError` (in `exceptions.py`)

```python
class ObjectNotFoundByURIError(GPDCError):
    """Raised when a GPDC URI cannot be resolved to a GPDCDataObject."""

    def __init__(self, uri: str, reason: str = "") -> None:
        self.uri = uri
        self.reason = reason
        msg = f"Cannot resolve GPDC URI {uri!r}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
```

Failure-mode → reason mapping:

| Scenario | reason |
|---|---|
| Not `gpdc://` | `must start with 'gpdc://'` |
| < 3 segments | `URI must have at least domain/namespace/name` |
| Empty segment | `URI contains empty path segment` |
| Domain not configured | `domain 'x' not configured` (wraps gpconfig's `ConfigNotFoundError`) |
| Namespace missing | `namespace 'x' not found in domain 'd'` |
| Root object missing | `object 'x' not found in namespace 'ns'` |
| Path segment failed | `path segment 'k' failed at <uri>: <orig>` |
| No `__getitem__` (SetObject) | `<uri> does not support subscript access` |
| Scalar terminal | `terminal is a scalar value; get_object_by_uri returns objects only` |

#### `has_uri`

Calls the resolution logic inside `try/except ObjectNotFoundByURIError: return
False`. **Only** `ObjectNotFoundByURIError` is swallowed; infrastructure errors
(Redis connection failures, etc.) propagate — they are not "URI doesn't exist".

#### `get_domain` vs `get_object_by_uri` exception layering

`get_domain(name)` propagates gpconfig's `ConfigNotFoundError` verbatim when the
domain has no config (it is a config-layer getter, not a URI resolver). But
`get_object_by_uri` catches that `ConfigNotFoundError` internally and wraps it as
`ObjectNotFoundByURIError`, preserving the single-exception contract for URI
access.

### 4.4 Domain initialization & registration integration

#### Import-time registration (`gpdatacached/__init__.py`)

```python
from gpconfig import GPConfigManager
GPConfigManager.register_configurable_class(GPDCDomain)
GPConfigManager.register_config_class(GPDCDomainConfig)
```

Both calls are required:
- `register_configurable_class(GPDCDomain)` — so `get_object` can find the class
  by `configured_class_name: GPDCDomain`.
- `register_config_class(GPDCDomainConfig)` — so `get_config` loads the YAML as a
  `GPDCDomainConfig` instance (auto-detected from `cfg_class_name:
  GPDCDomainConfig`) rather than a bare dict; without it `get_object` fails to
  find `configured_class_name`.

Registration is idempotent for the same class, so the import-time global write
is safe (gpconfig guarantees this).

#### `get_domain`

```python
def get_domain(self, name: str) -> GPDCDomain:
    if name not in self._domains:
        self._domains[name] = self._folder.get_object(name)   # gpconfig factory
    return self._domains[name]
```

#### `__contains__`

```python
def __contains__(self, name: str) -> bool:
    return name in self._folder.list_configs()
```

Pure config-snapshot query — no Redis, no instance creation.

### 4.5 Lifecycle

- **Lazy:** `__init__` stores the folder and an empty `_domains` dict; no domain
  is constructed until `get_domain` is called.
- **No auto-reload:** a cached domain is a snapshot of its config at construction
  time; later external edits to the YAML are not picked up. Rebuild `GPDataCache`
  to reload.
- **Context manager + `close()`:** `close()` closes every cached `GPDCDomain`
  (releases pools, stops GC threads). Idempotent. `__exit__` calls `close()`.

---

## 5. File change list

| File | Change |
|---|---|
| `src/gpdatacached/exceptions.py` | Add `ObjectNotFoundByURIError(GPDCError)` |
| `src/gpdatacached/data_cache.py` | **New** — `GPDataCache` facade |
| `src/gpdatacached/__init__.py` | Import-time registration; export `GPDataCache`, `ObjectNotFoundByURIError` in `__all__` |

### Public API of `GPDataCache`

```python
class GPDataCache:
    def __init__(self, folder: GPConfigFolder) -> None: ...
    def __contains__(self, domain_name: str) -> bool: ...
    def __enter__(self) -> "GPDataCache": ...
    def __exit__(self, *exc) -> None: ...
    def get_domain(self, domain_name: str) -> GPDCDomain: ...
    def get_object_by_uri(self, uri: str) -> GPDCDataObject: ...
    def has_uri(self, uri: str) -> bool: ...
    def close(self) -> None: ...
```

---

## 6. Test plan

New file `tests/test_data_cache.py`, using the existing sample configs in
`tests/configs/` (`global_env.yaml` + `gpdc/core_domain.yaml` +
`gpdc/edge_domain.yaml`). The test file owns its own local fixtures (does not
pollute the global `conftest.py`).

**A. Domain cache & lifecycle**
- `__contains__`: known domain → True; unknown → False.
- `get_domain`: first call constructs & caches; second returns same instance.
- `get_domain` unknown → `ConfigNotFoundError` propagated.
- `close`: closes all cached domains; idempotent; `with` block closes on exit.

**B. `get_object_by_uri` — root layer**
- Scalar root → returns object itself (`IntObject`), not native value.
- Container root → `DictObject` / `ListObject`.

**C. `get_object_by_uri` — path traversal**
- Nested container path returns nested `DictObject`.
- Multi-level nesting returns deep object.
- `ListObject` numeric index: scalar element → raises; nested container element
  → returns object.

**D. `get_object_by_uri` — exceptions**
- Not `gpdc://`; < 3 segments; empty segment; missing domain; missing namespace;
  missing root; broken path (KeyError); scalar terminal; SetObject descent.
- `.uri` and `.reason` attributes are correct in each case.

**E. `has_uri`**
- Existing object → True; all failure modes from (D) → False.
- Non-URI infrastructure errors (e.g. Redis down) propagate, not swallowed.

---

## 7. Out of scope

- Extending the architecture so containers can reference scalar data objects
  (currently explicitly forbidden by `encode_element`; see fact #3). Treated as a
  separate topic if ever desired.
- A process-global `domain_name → GPDCDomain` registry that would let URIs cross
  Redis instances. `GPDataCache` resolves only domains present in its own folder.
- Bulk preloading of all domains at `__init__` (rejected in favor of lazy
  creation, D5).

---

## 8. Usage example

```python
from gpconfig import GPConfigManager
from gpdatacached import GPDataCache
import gpdatacached  # triggers registration of GPDCDomain/GPDCDomainConfig

mgr = GPConfigManager("gpdatacached", cfg_folder="path/to/configs")
# When the path points to a subfolder, get_config returns a GPConfigFolder
# (gpconfig's public API — see manager.py get_config folder-exists branch).
folder = mgr.get_config("gpdc")

with GPDataCache(folder) as cache:
    "core_domain" in cache                       # True — config snapshot check
    domain = cache.get_domain("core_domain")     # lazy construct + cache

    # Root scalar object — returns the object, not the value
    obj = cache.get_object_by_uri("gpdc://core_domain/core_app/counter")

    # Path traversal into an anonymous nested container
    nested = cache.get_object_by_uri("gpdc://core_domain/core_app/config/server")

    # Existence checks
    cache.has_uri("gpdc://core_domain/core_app/missing")   # False
```

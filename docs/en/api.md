# API Reference

`gpdatacached` is a Redis-based cross-process data sharing and caching library. It exposes a hierarchical model — **Domain → Namespace → Data Object** — backed by Redis, so any Python process with access to the same Redis instance can share live, mutable, typed data structures.

This document is the complete reference for the public API exported by `gpdatacached.__init__`.

## Installation

```bash
pip install gpdatacached
```

Optional extras:

```bash
pip install "gpdatacached[pandas]"   # pandas DataFrame / Series support
pip install -e ".[dev]"              # development dependencies (pytest, ruff, ...)
```

Requirements: Python ≥ 3.10, Redis ≥ 5.0.

---

## Core Concepts

```
GPDCDomain  ──┬── GPDCNamespace ──┬── GPDCDataObject (scalar)
              │                    └── GPDCDataObject (container)
              └── GPDCNamespace ──── ...
```

| Concept | Role |
|---|---|
| **`GPDCDomain`** | The configuration root and Redis connection pool. Identified by a name. Holds lock config, GC config, and the default cache policy. |
| **`GPDCNamespace`** | A logical isolation space inside a domain. Behaves like a `MutableMapping` of object-name → value. Cross-namespace references are intentionally unsupported. |
| **`GPDCDataObject`** | A named, typed, Redis-backed value. Subclassed into **scalar** objects (single value) and **container** objects (`list`/`dict`/`set`). |

**Key design rules:**

- Scalar reads (`ns["count"]`) return native Python values (`int`, `str`, …).
- Container reads (`ns["my_list"]`) return a `GPDCContainerObject` so you can call mutating methods (`append`, `pop`, …) that operate directly on Redis.
- Object names may use letters, digits, `.` and `_`; names wrapped in `__xxx__` are reserved.
- All data lives under the Redis key prefix `gpdc:{domain}:{namespace}:...`.

---

## Quick Start { #api-quick-start }

```python
import gpdatacached
from gpdatacached import GPDCDomain, GPDCDomainConfig

# 1. Open a domain (writes/verifies metadata in Redis).
config = GPDCDomainConfig(
    redis_host="127.0.0.1",
    redis_port=6379,
    redis_db=0,
    redis_password="secret",
)
domain = GPDCDomain(config, name="mydomain")

# 2. Get or create a namespace.
ns = domain.ensure_namespace("analytics")

# 3. Scalars read back as native Python values.
ns["counter"] = 0
ns["counter"] = 42
assert ns["counter"] == 42

# 4. Containers come back as live Redis-backed objects.
ns["tags"] = ["a", "b", "c"]
tags = ns["tags"]            # ListObject
tags.append("d")
tags.pop(0)
assert tags.value == ["b", "c", "d"]

# 5. Nested containers share data by reference (inside the same namespace).
ns["matrix"] = [[1, 2], [3, 4]]
ns["alias"] = ns["matrix"]   # alias binding to the same canonical owner

# 6. Lifecycle control.
ns["temp"] = "x"
obj = ns.get_object("temp")
obj.update_cache_mode_and_ttl("ttl", 60)   # switch to ttl with a 60s TTL
obj.expire_in(120)                          # adjust the TTL (requires cache_mode='ttl')
obj.persist()                               # switch back to permanent

domain.close()
```

---

## Managing Domains via Configuration: `GPDataCache`

When your application drives many domains from configuration files managed by
[`gpconfig`](https://github.com/linnetcodes/gpconfig), `GPDataCache` is the
higher-level entry point. It holds a `GPConfigFolder`, lazily constructs and
caches `GPDCDomain` instances from it, and resolves GPDC URIs to data objects.

```python
from gpconfig import GPConfigManager
import gpdatacached                                       # registers GPDCDomain with gpconfig
from gpdatacached import GPDataCache

# 1. Build a GPConfigFolder for the "gpdc" subfolder of your config folder.
mgr = GPConfigManager("myproject", cfg_folder="/path/to/configs")
folder = mgr.get_config("gpdc")        # -> GPConfigFolder; sample layouts in tests/configs/gpdc

# 2. GPDataCache manages the domains found in that folder.
with GPDataCache(folder) as cache:
    "core_domain" in cache                                  # True iff a config exists (no Redis)
    domain = cache.get_domain("core_domain")                # lazy construct + cache
    site = cache.get_domain("domain_name.com")             # dotted name -> domain_name@com.yaml

    # 3. Resolve objects by GPDC URI — including anonymous nested objects.
    ns = domain.get_namespace("core_app")
    ns["config"] = {"server": {"host": "localhost", "port": 8080}, "tags": ["a", "b"]}

    cfg = cache.get_object_by_uri("gpdc://core_domain/core_app/config")
    server = cache.get_object_by_uri("gpdc://core_domain/core_app/config/server")
    cache.has_uri("gpdc://core_domain/core_app/missing")    # False
# close() on exit closes every cached domain (idempotent).
```

See [`GPDataCache`](#gpdatacache) below for the full API and the [URI resolution
rules](#get_object_by_uri) (path traversal, scalar terminals, `ListObject`
index coercion).

---

## Core Types

### `GPDCDomain`

```python
class GPDCDomain(config: GPDCDomainConfig, name: str | None = None)
```

The configuration root and Redis connection pool. On construction it:

1. Connects to Redis using `config.redis_*` fields.
2. Verifies the global GPDC protocol version in Redis.
3. Writes or verifies domain metadata (legacy fields are write-once; lock fields are write-or-verify — raises `GPDCLockConfigInconsistentError` on mismatch).
4. Creates predefined namespaces declared in `config.namespaces`.
5. Starts the background GC thread if `config.gc_enabled` is `True`.

Supports the context-manager protocol and `__del__` safety net — always prefer explicit `close()`.

**Properties**

| Property | Type | Description |
|---|---|---|
| `name` | `str` | Domain name. |
| `redis` | `redis.Redis` | Underlying Redis client (raises `RuntimeError` after `close()`). |

**Methods**

| Method | Description |
|---|---|
| `ensure_namespace(name)` | Get a namespace by name; create it if absent. Returns `GPDCNamespace`. |
| `get_namespace(name)` | Alias for `domain[name]`. |
| `get_default_cache_mode()` | Effective `default_cache_mode` for this domain. |
| `get_default_ttl_seconds()` | Effective `default_ttl_seconds` for this domain. |
| `get_default_lock_ttl_seconds()` | Effective `default_lock_ttl_seconds`. |
| `get_default_lock_acquire_timeout_seconds()` | Effective `default_lock_acquire_timeout_seconds`. |
| `get_gc_lock_ttl_seconds()` | Effective `gc_lock_ttl_seconds`. |
| `get_gc_lock_wait_seconds()` | Effective `gc_lock_wait_seconds`. |
| `run_gc(namespace=None)` | Run garbage collection manually (one namespace or all); returns number of reclaimed objects. |
| `delete_namespace(name)` | Delete a namespace and every key it owns. Raises `KeyError` if absent. |
| `destroy()` | Delete all namespaces, domain metadata, and close the connection. |
| `close()` | Stop GC thread, close the Redis connection. Idempotent. |

**Dunder protocols**

- `name in domain` → `True` if namespace `name` is registered.
- `for ns_name in domain` → iterate namespace names.
- `len(domain)` → number of namespaces.
- `domain[name]` → fetch a `GPDCNamespace` (raises `KeyError` if absent).

---

### `GPDCDomainConfig`

```python
class GPDCDomainConfig(**fields)   # subclass of gpconfig.GPConfig / pydantic BaseModel
```

Configuration for a `GPDCDomain`. All fields are validated; lock fields must be consistent across every process that opens the same domain.

| Field | Type | Default | Description |
|---|---|---|---|
| `redis_host` | `str` | `"localhost"` | Redis host. |
| `redis_port` | `int` | `6379` | Redis port. |
| `redis_db` | `int` | `0` | Redis logical database. |
| `redis_password` | `str` | `""` | Redis password (empty = no auth). |
| `default_cache_mode` | `str` | `"permanent"` | One of `"permanent"`, `"ttl"`, `"sliding"`. |
| `default_ttl_seconds` | `int` | `0` | Default TTL; must be `> 0` when mode is `"ttl"`/`"sliding"`. |
| `gc_enabled` | `bool` | `False` | Start the background GC thread. |
| `gc_interval_seconds` | `int` | `300` | Background GC cycle interval (must be `≥ 30`; smaller values busy-loop the GC thread and flood Redis). |
| `namespaces` | `dict[str, GPDCNamespaceConfig]` | `{}` | Predefined namespaces to create eagerly. |
| `default_lock_ttl_seconds` | `int` | `30` | TTL on lock keys (must be `≥ 1`). |
| `default_lock_acquire_timeout_seconds` | `int` | `30` | Blocking timeout for `obj.lock()` (must be `≥ 0`). |
| `gc_lock_ttl_seconds` | `int` | `30` | TTL for locks acquired by GC (must be `≥ 1`). |
| `gc_lock_wait_seconds` | `int` | `5` | GC per-object try-lock timeout (must be `≥ 0`). |

👉 These four fields must be kept consistent across every process that opens the same domain (mismatch raises `GPDCLockConfigInconsistentError` at domain construction). See [Multi-Process Locking](./locking.md) for what each controls, the cost locks impose, and the GC contract.

---

### `GPDCNamespace`

```python
class GPDCNamespace(name, domain, *, config=None)   # MutableMapping
```

A logical isolation space inside a `GPDCDomain`. Implements the full `MutableMapping` protocol backed by Redis.

**Naming rule:** names may use letters, digits, `.` and `_`; names wrapped in `__xxx__` are reserved. Anonymous objects (internal names starting with `~`) are hidden from the public mapping API. Common characters that are **rejected** because they fall outside the allow-list (and, for `:`, collide with the Redis key separator): hyphen `-`, spaces, and `:` — use `_` or `.` instead. This rule applies to **object and namespace names**, and to **domain names** (a `.` in a domain name is legal everywhere in GPDC — but when the domain is driven by a config file through `GPDataCache`, the `.` must be spelled `@` in the filename; see the [`GPDataCache` `@` escape](#gpdatacache)).

**Properties**

| Property | Type | Description |
|---|---|---|
| `name` | `str` | Namespace name. |
| `domain` | `GPDCDomain` | Owning domain. |

**Mapping semantics**

| Operation | Behaviour |
|---|---|
| `ns[key] = value` | Create / overwrite a binding. Scalars store their value; containers are stored and re-bound by reference. |
| `ns[key]` | Scalars return a **native Python value**; containers return a **`GPDCContainerObject`**. |
| `del ns[key]` | Delete the binding. Raises `CanonicalObjectReferencedError` if the object still has referrers. |
| `key in ns` | `True` if a named binding exists (anonymous bindings are excluded). |
| `iter(ns)` | Iterate named object names via `SCAN` (anonymous bindings and stale aliases are excluded). |
| `len(ns)` | Number of named objects. |

**Methods**

| Method | Description |
|---|---|
| `get_object(key)` | Return a `GPDCDataObject` (always returns the object, never a native value). |
| `ensure_object(key, default)` | Get the object for `key`, creating it from `default` if it does not exist. |
| `clear()` | Bulk-delete every object in the namespace (preserves the namespace meta key). |
| `destroy()` | Delete the namespace from its domain. |

**Cross-namespace rule:** all container references must point to objects within the same namespace. This guarantees `clear()` can delete in bulk safely.

---

### `GPDCNamespaceConfig`

```python
class GPDCNamespaceConfig(**fields)   # subclass of gpconfig.GPConfig / pydantic BaseModel
```

Per-namespace cache-policy overrides. Only applied when the namespace is first created in Redis; existing namespaces keep their stored metadata.

| Field | Type | Default | Description |
|---|---|---|---|
| `default_cache_mode` | `Optional[str]` | `None` | One of `"permanent"`, `"ttl"`, `"sliding"`; `None` inherits from the domain. |
| `default_ttl_seconds` | `Optional[int]` | `None` | TTL override; `None` inherits from the domain. Must be `≥ 0`. |

When both fields are provided they must satisfy the strict mode/TTL consistency rule (`ttl`/`sliding` require `ttl > 0`).

---

### `reset_domain_config`

```python
def reset_domain_config(name: str, config: GPDCDomainConfig) -> None
```

Delete the four lock-config fields (`default_lock_ttl_seconds`, `default_lock_acquire_timeout_seconds`, `gc_lock_ttl_seconds`, `gc_lock_wait_seconds`) from the domain's meta hash in Redis. No-op if those fields are absent.

**Intended usage** — change lock config on an existing domain:

```python
reset_domain_config("mydomain", old_config)     # must be called BEFORE construction
domain = GPDCDomain(new_config, name="mydomain")     # writes the new lock config
```

Only the Redis connection fields on `config` are read; lock fields on the passed config are ignored.

---

### `GPDataCache`

```python
class GPDataCache(folder: gpconfig.GPConfigFolder)   # context manager
```

A facade that manages `GPDCDomain` instances discovered in a
[`gpconfig`](https://github.com/linnetcodes/gpconfig) `GPConfigFolder`, and
resolves GPDC URIs to [`GPDCDataObject`](#gpdcdataobject) instances. It is the
recommended entry point when your domains are driven by config files rather
than constructed ad-hoc in code.

**Construction.** Built from a `GPConfigFolder`. On construction it stores the
folder reference only — **no domain is created** (it does scan the folder once
to warn about unaddressable config filenames; see below). Domains are
constructed lazily on first [`get_domain`](#gpdatacache) access, and cached in
an internal dict. The cache is a snapshot: config files edited after
construction are **not** re-read; rebuild the `GPDataCache` to pick up changes.

`GPDataCache` does **not** create domains lacking a config file — for ad-hoc
domains use [`GPDCDomain`](#gpdcdomain) directly.

> **Dots in domain names — the `@` escape.** gpconfig addresses configs by
> dot-separated paths, so a `.` in a config filename is consumed as a path
> separator and the file becomes unaddressable. To use a dotted domain name —
> the natural shape when a domain mirrors an internet domain, e.g.
> `domain_name.com` — write each `.` as `@` in the config filename:
> `domain_name@com.yaml` configures the domain `"domain_name.com"`, and
> `cache.get_domain("domain_name.com")` /
> `cache.get_object_by_uri("gpdc://domain_name.com/ns/obj")` resolve it
> transparently. The mapping is unambiguous because `@` is not a legal
> character in GPDC names. `GPDCDomain` itself performs the `@` → `.`
> restore for names derived from a config-file stem, so gpconfig's object
> factory path also works: `folder.get_object("domain_name@com")`
> constructs a domain named `domain_name.com`, identical to
> `cache.get_domain("domain_name.com")`. An explicit `name=` argument
> containing `@` is still rejected — the escape exists for filenames;
> in code, write the `.` directly. Config files whose names contain a real `.` are
> listed by the folder but can never be loaded; `GPDataCache` logs a warning
> about them once at construction.

> **Registration side effect.** `import gpdatacached` registers `GPDCDomain`
> and `GPDCDomainConfig` with gpconfig's object factory (idempotent), which is
> what makes `folder.get_object(name)` able to construct a `GPDCDomain`. No
> extra setup is needed.

**Dunder protocols**

- `name in cache` → `True` iff a config for `name` exists in the folder (dotted names look up their `@`-escaped filename). This is a pure config-snapshot check: it does **not** touch Redis and does not construct (or require) a `GPDCDomain` instance — the answer depends only on whether the config file is present, not on whether `get_domain(name)` has been called. Names that are not legal GPDC domain names (e.g. containing `@`) return `False`. The domain instance is created lazily on first `get_domain(name)` access.
- `with cache as c` / `__exit__` → closes every cached domain on exit; `close()` is idempotent.

> **`close()` behaviour — the membership exception.** After `cache.close()`, the resource-touching methods (`get_domain`, `get_object_by_uri`, `has_uri`) raise `RuntimeError("GPDataCache is closed")`. The `name in cache` membership check is the **deliberate exception**: because it only reads the config snapshot and touches no resources, it keeps working after `close()`. This is intentional — "does a config for this domain exist?" is meaningful regardless of whether the cache is open.

**Methods**

| Method | Description |
|---|---|
| `get_domain(name)` | Return the cached `GPDCDomain` for `name`, constructing it lazily on first access. Dotted names resolve to their `@`-escaped config file (see above). Raises `ValueError` if `name` is not a legal domain name; propagates gpconfig's `ConfigNotFoundError` if no config exists for `name`. Raises `RuntimeError` after `close()`. |
| [`get_object_by_uri(uri)`](#get_object_by_uri) | Resolve a GPDC URI to a `GPDCDataObject`. Raises `RuntimeError` after `close()`. |
| `has_uri(uri)` | Return `True` iff `get_object_by_uri(uri)` would succeed. Raises `RuntimeError` after `close()`. |
| `close()` | Close every cached domain. Idempotent. |

---

#### `get_object_by_uri`

```python
def get_object_by_uri(self, uri: str) -> GPDCDataObject
```

Resolve a GPDC URI to a [`GPDCDataObject`](#gpdcdataobject). The URI must begin
with `gpdc://` and has the form:

```
gpdc://{domain}/{namespace}/{name}[/{path_segment}...]
```

- The first three segments (`domain` / `namespace` / `name`) locate the **root
  object**, fetched via `namespace.get_object(name)` — so the root is always
  returned as a `GPDCDataObject`, **even when it is a scalar** (unlike
  `ns[name]`, which unwraps scalars to native values).
- Any further segments form a **path** that descends into the object via
  `__getitem__`, one segment at a time. This is how anonymous nested containers
  become reachable: `gpdc://d/ns/obj/key/0` walks `obj["key"][0]`.

**Returns objects, never values.** `get_object_by_uri` always returns a
`GPDCDataObject`. It cannot be used to read a scalar value or an element value
directly.

**Path-segment rules**

| Current object | Segment handling |
|---|---|
| `ListObject` | The segment must be a **non-negative integer** (a pure-digit string); it is converted to `int` before indexing. Negative indices, decimals, and slices are rejected. |
| `DictObject` / pydantic container-mode model / other subscriptable container | The segment is used as-is (a string key). |
| `SetObject`, scalar objects | Have no `__getitem__` — any further path segment raises `ObjectNotFoundByURIError`. |

**Scalar terminals are rejected.** Scalars nested inside a container are stored
as inlined values (not as referenced objects), so descending into a scalar
element yields a native Python value. Because `get_object_by_uri` returns
objects only, a path that terminates on a scalar raises `ObjectNotFoundByURIError`.
For example, given `ns["cfg"] = {"port": 8080}`, `gpdc://d/ns/cfg/port` raises
(the value `8080` is a scalar, not a data object).

**Errors.** Every failure mode raises [`ObjectNotFoundByURIError`](#objectnotfoundbyurierror)
(carries `.uri` and `.reason`): malformed URI (missing `gpdc://`, fewer than
three segments, empty segment), unknown domain, missing namespace, missing root
object, a path segment that fails (`KeyError`/`IndexError`/`TypeError`), and a
scalar terminal.

**Example.** Given `ns["cfg"] = {"server": {"host": "x"}, "tags": ["a","b"]}`:

| URI | Result |
|---|---|
| `gpdc://d/ns/cfg` | the `DictObject` for `cfg` |
| `gpdc://d/ns/cfg/server` | the nested `DictObject` for `server` |
| `gpdc://d/ns/cfg/server/host` | raises — `host` is the scalar `"x"` |
| `gpdc://d/ns/cfg/tags/0` | raises — `tags[0]` is the scalar `"a"` |
| `gpdc://d/ns/cfg/missing` | raises — `KeyError` on the path segment |

---

### `ObjectNotFoundByURIError`

```python
class ObjectNotFoundByURIError(GPDCError)
```

Raised by [`GPDataCache.get_object_by_uri`](#get_object_by_uri) when a GPDC URI
cannot be resolved to a `GPDCDataObject`. Covers every failure mode: malformed
URI syntax, unknown domain (no config), missing namespace, missing root object,
an invalid domain name, a path segment that fails, and a scalar terminal. Carries `.uri` (the URI
string) and `.reason` (a short explanation).

`has_uri` swallows only this exception; infrastructure errors (e.g. Redis
connection failures) propagate.

---

## Data Objects

### `GPDCDataObject`

```python
class GPDCDataObject(name, namespace, *, canonical_name=None)   # ABC
```

Abstract base class for every GPDC data object. Subclasses are split into [`GPDCScalarObject`](#gpdcscalarobject) and [`GPDCContainerObject`](#gpdccontainerobject).

**Identity properties**

| Property | Type | Description |
|---|---|---|
| `name` | `str` | The binding name this object was fetched under. |
| `namespace` | `GPDCNamespace` | Owning namespace. |
| `domain` | `GPDCDomain` | Owning domain. |
| `uri` | `str` | `gpdc://{domain}/{namespace}/{name}`. |
| `canonical_name` | `str` | The name that actually owns the raw data (differs from `name` for reference bindings). |
| `canonical_uri` | `str` | URI of the canonical owner. |
| `is_reference` | `bool` | `True` if this object is a reference (alias) binding. |

**Metadata properties**

| Property | Type | Description |
|---|---|---|
| `type` | `str` | GPDC type string, e.g. `"scalar:int"`, `"collection:list"`. |
| `meta` | `ObjectMeta \| None` | Full metadata record; `None` if the object has vanished. |
| `cache_mode` | `str` | Effective cache mode. |
| `ttl` | `int` | Declared TTL in seconds (`0` = permanent). |
| `created_at` | `datetime \| None` | Creation timestamp. |
| `updated_at` | `datetime \| None` | Last update timestamp. |
| `value` | `object` | The Python value (abstract; subclasses implement). |

> The meta-backed properties (`cache_mode`, `ttl`, `created_at`, `updated_at`) each raise `ObjectGoneError` if the object's metadata no longer exists.

**Lifecycle methods**

> This is the API summary. For the full model — policy resolution, sliding refresh, ownership, anonymous/reference lifecycle, and pitfalls — see [Lifecycle Management](./lifecycle.md).

| Method | Description |
|---|---|
| `expire_in(seconds)` | Set a new TTL. Object must be in `cache_mode="ttl"`. `seconds` must be `> 0` — to delete now, use `expire_now()`. Requires ownership. |
| `expire_at(when)` | Set absolute expiry time. Object must be in `cache_mode="ttl"`. Requires ownership. Deletes the object immediately if `when` is in the past (equivalent to `del ns[name]`: cleans `obj:`/`raw:`/`ref:` and cascade-deletes anonymous subtrees; raises `CanonicalObjectReferencedError` if the owner is still referenced — check `has_referrers()` first). |
| `expire_now()` | Delete this object immediately. Equivalent to `del ns[canonical_name]`: full teardown (obj:/raw:/ref: + cascade-delete of anonymous subtrees). Works in any `cache_mode` (unlike `expire_in`/`expire_at`, which require `cache_mode="ttl"`). Requires ownership; raises `CanonicalObjectReferencedError` if the owner is still referenced by aliases — check `has_referrers()` first. |
| `persist()` | Switch to `cache_mode="permanent"` with `ttl=0`. Requires ownership. |
| `update_cache_mode_and_ttl(cache_mode, ttl)` | Replace the full cache policy. Requires ownership. |
| `refresh_ttl()` | Re-apply this object's own TTL to its keys and cascade-refresh its owned subtree (used by the sliding-window path). No-op for permanent objects. |
| `remaining_ttl()` | Remaining TTL in seconds on the canonical key, as reported by the Redis `TTL` command (`-1` = no expiry, `-2` = key missing). |
| `take_ownership()` | Promote **the referenced object** to own itself (primary use case: a named reference to an anonymous nested object). On a reference to a named (already self-owned) object it is a correct no-op. The call itself must be made via a named reference handle. See [Lifecycle §7](./lifecycle.md#7-the-ownership-model). |

**Locking methods** (opt-in, never called automatically by mutations)

| Method | Description |
|---|---|
| `lock(*, ttl=None, timeout=None)` | Context manager. Acquire an exclusive lock on the object's canonical name (blocks up to `timeout`). Raises `LockAcquisitionTimeout` on expiry. |
| `try_lock(*, ttl=None)` | Non-blocking lock attempt; returns a `HeldLock` or `None`. |

👉 **Locks are advisory and carry a per-operation Redis cost.** Before adopting them broadly, read [Multi-Process Locking](./locking.md) — it covers when locking is needed, the performance impact, recommended usage patterns, deadlock-avoidance ordering, and the GC contract.

**Other**

- `obj == other` compares by `(domain, namespace, canonical_name)`.
- `obj.referrers()` (containers only) — set of names referencing this container's raw data.
- `obj.has_referrers()` (containers only) — cheap boolean: whether any name references this container's raw data. Tells you whether a `del`/`expire_at` of the owner would raise `CanonicalObjectReferencedError`.
- `__hash__ = None` — GPDC objects are unhashable.

---

### `GPDCScalarObject`

```python
class GPDCScalarObject(name, namespace, *, canonical_name=None)   # GPDCDataObject
```

Base class for every scalar object. A scalar stores a single encoded value in its metadata hash (no dedicated raw key).

- `value` getter decodes the stored value via the registered codec.
- `value` setter validates the Python type against the registered codec, updates metadata, and refreshes TTL when `cache_mode="sliding"`.

Built-in subclasses: [`IntObject`](#intobject), [`FloatObject`](#floatobject), [`StrObject`](#strobject), [`BoolObject`](#boolobject), [`DateTimeObject`](#datetimeobject), [`DateObject`](#dateobject), [`TimeObject`](#timeobject), [`DecimalObject`](#decimalobject), [`NoneObject`](#noneobject), [`TupleObject`](#tupleobject).

---

### `GPDCContainerObject`

```python
class GPDCContainerObject(name, namespace, *, canonical_name=None)   # GPDCDataObject, ABC
```

Abstract base for every container object. A container stores its elements in a dedicated Redis key (`raw_key`) and tracks referrers in a Redis hash (`ref_key`). Subclasses mix in `MutableSequence`, `MutableMapping`, or `MutableSet`.

| Attribute | Description |
|---|---|
| `raw_key` | Redis key holding the container's element data (Redis list / hash / set). |
| `ref_key` | Redis hash recording `{referrer_name: ref_count}` for reference counting and cascade deletion. |

**Shared API**

| Method | Description |
|---|---|
| `value` | Property: decode the whole container into a native Python value. Setter always raises — containers must be mutated via their explicit methods. |
| `referrers()` | Set of object names that reference this container's raw data. |
| `has_referrers()` | Cheap boolean check: `True` if any object references this container's raw data. Use it to tell whether a `del`/`expire_at` of the owner would be blocked by outstanding references (raising `CanonicalObjectReferencedError`). |
| `copy()` | Shallow copy: returns the decoded Python value (sharing anonymous-object refs). |
| `deepcopy()` | Deep copy: recursively clone every anonymous child and return a fully independent Python value. |

**Reference semantics.** When you store a `GPDCContainerObject` under a new name (`ns["alias"] = ns["original"]`), GPDC creates a lightweight reference binding — both names share the same canonical owner and its raw data. Anonymous containers nested inside a container are tracked by ref count and cascade-deleted when their last referrer goes away.

Built-in subclasses: [`ListObject`](#listobject), [`DictObject`](#dictobject), [`SetObject`](#setobject).

---

## Built-in Scalar Types

All scalar classes inherit from [`GPDCScalarObject`](#gpdcscalarobject). Reading via `ns[name]` returns the **native Python value**, not the wrapper.

| Class | GPDC type | Python type | Encoding |
|---|---|---|---|
| [`IntObject`](#intobject) | `scalar:int` | `int` (excludes `bool`) | `int:{value}` |
| [`FloatObject`](#floatobject) | `scalar:float` | `float` | `float:{value}` |
| [`StrObject`](#strobject) | `scalar:str` | `str` | `str:{value}` |
| [`BoolObject`](#boolobject) | `scalar:bool` | `bool` | `bool:True` / `bool:False` |
| [`DateTimeObject`](#datetimeobject) | `scalar:datetime` | `datetime.datetime` | `datetime:{isoformat}` |
| [`DateObject`](#dateobject) | `scalar:date` | `datetime.date` (excludes `datetime`) | `date:{isoformat}` |
| [`TimeObject`](#timeobject) | `scalar:time` | `datetime.time` | `time:{isoformat}` |
| [`DecimalObject`](#decimalobject) | `scalar:decimal` | `decimal.Decimal` | `decimal:{str(d)}` |
| [`NoneObject`](#noneobject) | `scalar:none` | `NoneType` | `none:None` |
| [`TupleObject`](#tupleobject) | `scalar:tuple` | `tuple` (scalar elements only) | `tuple:{json}` |

### `IntObject`

Wraps `int`. `IntCodec` rejects `bool` explicitly (booleans are not integers here).

### `FloatObject`

Wraps `float`.

### `StrObject`

Wraps `str`.

### `BoolObject`

Wraps `bool`.

### `DateTimeObject`

Wraps `datetime.datetime`. Encoded via `datetime.isoformat()`.

### `DateObject`

Wraps `datetime.date`. Note: a `datetime.datetime` is rejected by `DateCodec` even though `isinstance(datetime, date)` is `True` — use `DateTimeObject` for datetimes.

### `TimeObject`

Wraps `datetime.time`.

### `DecimalObject`

Wraps `decimal.Decimal` (added in 0.2.1). Encoded as `decimal:{str(value)}`: the payload is `str(Decimal)`, which preserves the exact coefficient and exponent — `Decimal("1.10")` roundtrips as `1.10`, not `1.1`, and extreme exponents use scientific notation (`1E-27`). A malformed payload raises `ValueError` on decode.

- **Non-finite values** (`NaN`, `sNaN`, `±Infinity`) and `-0` are stored and restored as-is, matching the `float` codec's treatment of `nan`/`inf`. Comparison semantics follow the standard library: `NaN != NaN`, and comparing an `sNaN` with `==` raises `decimal.InvalidOperation`.
- **Exactness**: `str()` is context-independent — no rounding is applied on store or load. Construct from strings (`Decimal("0.1")`), not floats, if you mean the decimal literal; `Decimal(0.1)` faithfully stores the full binary expansion.
- **Cross-type equality**: GPDC matches dict keys and set members by their encoded wire form, so natively-equal values of different numeric types become distinct entries once stored — `d[1] = "a"` followed by `d[Decimal(1)] = "b"` yields two entries (the same already holds for `1` vs `1.0`). A native literal like `{1: "a", Decimal(1): "b"}` collapses to one key before GPDC ever sees it.

### `NoneObject`

Wraps `None`. Used to represent the presence of a `None` value distinctly from a missing key.

### `TupleObject`

A **composite scalar** — tuples are stored as a single JSON-encoded Redis string (no dedicated raw key). `TupleCodec` rejects any element that is a `list`, `dict`, or `set`; only scalar element types are allowed. This keeps tuples GC-free (they never produce nested references).

---

## Built-in Container Types

All container classes inherit from [`GPDCContainerObject`](#gpdccontainerobject). Reading via `ns[name]` returns the **live `GPDCContainerObject`** so you can call mutating methods directly on Redis.

### `ListObject`

```python
class ListObject(...)   # GPDCContainerObject, collections.abc.MutableSequence
```

GPDC type `collection:list`. Backed by a Redis list.

Implements the full `MutableSequence` interface plus:

- `append(value)`, `extend(values)`, `insert(index, value)`
- `pop(index=-1)`, `remove(value)`
- `clear()`, `reverse()`, `sort(*, key=None, reverse=False)`
- `count(value)`, `index(value, start=0, stop=None)`
- `deepcopy()`

Unsupported (raise `NotImplementedError`): `+`, `*` and their reflected forms — use `list(obj) + other` to concatenate, then re-assign if needed.

Slice indexing is unsupported on `__setitem__` and `__delitem__` (convert to a Python list first: `list(obj)[start:stop]`). `__getitem__` does support slices and returns a decoded Python list.

### `DictObject`

```python
class DictObject(...)   # GPDCContainerObject, collections.abc.MutableMapping
```

GPDC type `collection:dict`. Backed by a Redis hash.

Keys may be any scalar type except containers and the empty tuple. Implements the full `MutableMapping` interface plus:

- `keys()`, `values()`, `items()` — each returns a **frozen snapshot view**; later mutations of the `DictObject` are not reflected.
- `get(key, default=None)`, `setdefault(key, default=None)`
- `pop(key, default=…)`, `update(other=None, **kwargs)`
- `clear()`, `deepcopy()`

### `SetObject`

```python
class SetObject(...)   # GPDCContainerObject, collections.abc.MutableSet
```

GPDC type `collection:set`. Backed by a Redis set.

Members may be any hashable scalar (no containers, no empty tuple). Implements the full `MutableSet` interface plus:

- `add(value)`, `discard(value)`, `remove(value)`, `pop()`, `clear()`
- Set operators: `|`, `&`, `-`, `^` return a native Python `set` (no Redis side effect).
- In-place set operators: `|=`, `&=`, `-=`, `^=` mutate the Redis set.
- `deepcopy()` returns the decoded `set`.

---

## Codecs

Codecs convert between Python values and their Redis storage representation. Every type registered with [`GPDCTypeRegistry`](#api-type-registry) must supply a codec.

> This section is the API summary. For a full walkthrough of writing codecs — scalar and container, with worked examples (`Color`, `Counter`) and the mutation hooks every container must call — see the dedicated [Extensibility](./extensibility.md) document.

### `GPDCCodec`

```python
class GPDCCodec(ABC)
```

Abstract base class for all codecs.

| Classmethod | Description |
|---|---|
| `encode_value(value) -> str` | Encode a Python value into the string stored in Redis metadata. |
| `decode_value(encoded) -> object` | Decode a stored string back to a Python value. |

### `GPDCScalarCodec`

```python
class GPDCScalarCodec(GPDCCodec)
```

Abstract base for scalar codecs. Provides the shared helper:

| Classmethod | Description |
|---|---|
| `_parse_encoded(encoded) -> tuple[str, str]` | Split a `name:payload` encoded value into `(type_name, raw)`. Raises `ValueError` on empty or colon-less input. |

Built-in subclasses: `BoolCodec`, `DateTimeCodec`, `DateCodec`, `DecimalCodec`, `DictCodec` *(see note)*, `FloatCodec`, `IntCodec`, `ListCodec` *(see note)*, `NoneCodec`, `SetCodec` *(see note)*, `StrCodec`, `TimeCodec`, `TupleCodec`.

> Scalar codec classes for containers (`ListCodec`, `DictCodec`, `SetCodec`) are re-exported only for completeness — they actually inherit from [`GPDCContainerCodec`](#gpdccontainercodec).

### `GPDCContainerCodec`

```python
class GPDCContainerCodec(ABC)
```

Abstract base for container codecs. Containers store their elements in a dedicated Redis key, not inline in metadata, so the codec contract is raw-create/read/delete instead of encode/decode.

| Classmethod | Description |
|---|---|
| `create_raw(namespace, raw_key, value, owner_name, cache_mode, ttl)` | Create the raw Redis storage for `value`. |
| `read_raw(namespace, raw_key) -> object` | Reconstruct the Python object from raw Redis data. |
| `delete_raw(namespace, raw_key)` | Delete the raw Redis storage. |

---

## Type Registry { #api-type-registry }

### `GPDCTypeRegistry`

```python
class GPDCTypeRegistry   # classmethod-only facade
```

Thread-safe registry mapping Python types ↔ GPDC type strings ↔ (`object_class`, `codec_class`). All built-in types are registered automatically on import via `gpdatacached.builtins.registry._register_builtin_types`.

| Classmethod | Description |
|---|---|
| `register(*, python_type, gpdc_type, object_class, codec_class, priority=0)` | Register (or replace) an entry. Higher `priority` wins for ambiguous Python types (e.g. `bool` over `int`). |
| `lookup_by_python_type(value) -> TypeEntry` | Find the entry whose `python_type` matches `value`. Raises `TypeError` if unsupported. |
| `lookup_by_gpdc_type(gpdc_type) -> TypeEntry` | Find the entry by its GPDC type string. Raises `ValueError` if unknown. |

A `TypeEntry` exposes: `python_type`, `gpdc_type`, `object_class`, `codec_class`, `priority`, and the boolean property `is_container`.

> For the full type-resolution model (priority rules, subclass gotchas) and end-to-end worked examples of registering your own scalar and container types, see [Extensibility](./extensibility.md).

To register a custom type:

```python
from gpdatacached import GPDCTypeRegistry, GPDCScalarObject, GPDCScalarCodec

class MyCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value): ...
    @classmethod
    def decode_value(cls, encoded): ...

class MyObject(GPDCScalarObject):
    TYPE = "scalar:mytype"

GPDCTypeRegistry.register(
    python_type=MyType,
    gpdc_type="scalar:mytype",
    object_class=MyObject,
    codec_class=MyCodec,
)
```

---

## Key & URI Utilities

Low-level helpers for working with GPDC's key/URI scheme. For actually
*resolving* a URI to a data object, use
[`GPDataCache.get_object_by_uri`](#get_object_by_uri) — these helpers only build
or convert URI/key strings and are rarely needed directly.

### `make_uri`

```python
def make_uri(domain: str, namespace: str, name: str) -> str
```

Build a GPDC URI: `gpdc://{domain}/{namespace}/{name}`.

### `uri_to_redis_key`

```python
def uri_to_redis_key(uri: str) -> str
```

Convert `gpdc://{domain}/{namespace}/{name}` into the Redis object key `gpdc:{domain}:{namespace}:obj:{name}`. Validates every path component; raises `ValueError` for malformed URIs or invalid/reserved names.

### `validate_object_name`

```python
def validate_object_name(name: str) -> None
```

Validate that `name` contains only letters, digits, `.` or `_` and is not a reserved `__xxx__` name. Raises `ValueError` otherwise. (Equivalent validators exist internally for domain and namespace names.)

---

## Exceptions

All public exceptions descend from the public `GPDCError(Exception)` root. Use `except GPDCError` to catch every gpdatacached error, or catch them individually for precise handling. The lock-acquisition errors additionally share a public `GPDCLockError` base — `except GPDCLockError` catches every lock-acquisition/ownership failure (currently `LockAcquisitionTimeout`).

### `GPDCError`

```python
class GPDCError(Exception)
```

The root of the gpdatacached exception hierarchy. Every gpdatacached-specific exception raised by this library is a subclass of `GPDCError`, so `except GPDCError` is the broad catch-all. (Plain stdlib errors — e.g. `TypeError` for unsupported types or an invalid argument order, `ValueError` for invalid payloads — are raised as-is.) Prefer catching the specific subclass when you need to distinguish failure modes.

### `GPDCLockError`

```python
class GPDCLockError(GPDCError)
```

Base class for lock-acquisition/ownership errors. `except GPDCLockError` catches every lock-related failure (currently `LockAcquisitionTimeout`; `LockOwnershipError` also descends from it but is swallowed internally by `HeldLock.release()`).

### `ObjectGoneError`

```python
class ObjectGoneError(GPDCError)
```

Raised when a `GPDCDataObject` property or method tries to read metadata that has disappeared from Redis (e.g. expired TTL or deleted by another process). Carries `.object_uri`.

### `CanonicalObjectReferencedError`

```python
class CanonicalObjectReferencedError(GPDCError)
```

Raised when attempting to delete or overwrite a canonical container that still has live referrers. Carries `.canonical_name`, `.canonical_uri`, and `.referrers` (the list of offending binding names).

### `CyclicReferenceError`

```python
class CyclicReferenceError(GPDCError)
```

Raised during encoding when a container references itself (directly or transitively). Carries `.type_name`. Break the cycle before storing, or store the shared container separately and reference it by name.

### `LockAcquisitionTimeout`

```python
class LockAcquisitionTimeout(GPDCLockError)
```

Raised when `obj.lock()` cannot acquire the lock within the configured `timeout`. Carries `.lock_key` and `.timeout`. It extends the public `GPDCLockError` base, so `except GPDCLockError` catches it. See [Multi-Process Locking](./locking.md).

### `GPDCLockConfigInconsistentError`

```python
class GPDCLockConfigInconsistentError(GPDCError)
```

Raised at `GPDCDomain` construction when the lock config in the explicit `GPDCDomainConfig` conflicts with the values already stored in Redis domain meta. Carries `.domain`, `.conflicts` (`{field: (redis_value, config_value)}`), and `.legal_values`. Use [`reset_domain_config`](#reset_domain_config) to clear the stored values before re-opening the domain. See [Multi-Process Locking](./locking.md) for the lock configuration model.

---

## Cache Modes & Lifecycle

> This section is a brief API-level summary. For the full lifecycle model — policy resolution, sliding-window refresh mechanics, ownership, anonymous and reference object lifecycle, and a list of common pitfalls — see the dedicated [Lifecycle Management](./lifecycle.md) document.

Every object carries a `(cache_mode, ttl)` policy stored in its metadata.

| `cache_mode` | TTL | Behaviour |
|---|---|---|
| `"permanent"` | `0` | No expiry. Redis `PERSIST` is applied. (Default.) |
| `"ttl"` | `> 0` | Fixed expiry. The object expires once after `ttl` seconds. |
| `"sliding"` | `> 0` | Sliding window. Every mutation refreshes the TTL back to `ttl` seconds. |

**Validation rule:** `"ttl"` and `"sliding"` strictly require `ttl > 0`; `"permanent"` accepts any `ttl >= 0` (the value is silently ignored — Redis `PERSIST` is applied, so a permanent object never expires).

**Policy resolution.** When a new object is created, the policy comes from (in priority order):

1. Per-namespace config (`GPDCNamespaceConfig`), if the namespace is new.
2. Domain defaults (`default_cache_mode`, `default_ttl_seconds`).

Existing objects keep their stored policy until you change it explicitly via `expire_in`, `expire_at`, `persist`, or `update_cache_mode_and_ttl`.

**Propagation.** Changing a container's policy cascades to the owned subtree (the anonymous children it created), rewriting their metadata and re-applying TTL in one pipeline. Sliding-window refreshes likewise cascade up the owner chain on every mutation.

**Garbage collection.** When `gc_enabled=True`, a background thread runs `run_gc_for_namespace` for every namespace on each `gc_interval_seconds` cycle, reclaiming expired or orphaned anonymous objects. If GC is enabled, **all concurrent writers must use `obj.lock()`** — see [Multi-Process Locking](./locking.md) for the full contract (including the GC ↔ writer rule and the performance cost it imposes on every mutation).

---

## Extensions

`gpdatacached` ships two optional built-in extensions under `gpdatacached.extensions`. Each adds support for a family of Python types not covered by the built-in scalar/container registry. They are opt-in — register them explicitly to activate.

### Pydantic Support

**Module:** `gpdatacached.extensions.pydantic_support`

Makes [pydantic](https://docs.pydantic.dev/) `BaseModel` subclasses cacheable as GPDC objects. Offers two storage modes:

- **Scalar mode** — the whole model is encoded as a single Redis string (JSON of its fields). Atomic, no nested container fields.
- **Container mode** — the model is stored as a Redis hash with one slot per field. Enables field-level get/set and nested container fields (`list`/`dict`/`set`).

There is no `register()` function — you register each model individually (define a thin `GPDCScalarObject` / `BaseModelContainerObject` subclass + pair it with `PydanticModelScalarCodec` / `PydanticModelContainerCodec`). Pydantic is already a core dependency, so no extra install is needed.

👉 **Full reference:** [`docs/pydantic-support.md`](./pydantic-support.md)

### Pandas Support

**Module:** `gpdatacached.extensions.pandas_support`

Makes pandas `Series` and `DataFrame` cacheable as live `SeriesObject` / `DataFrameObject` wrappers. Designed for the *one writer caches, many readers pull a subset* pattern: online selective accessors (`.iloc`, `.loc`, `df[col]`) for cheap one-shot reads, and `.value` as the escape hatch for full restore. Mutation is append-focused (`extend`) plus structural column add/replace/drop on DataFrame.

Scope: `RangeIndex` and labelled/MultiIndex row indexes; single-level or MultiIndex columns; `int`/`float`/`bool`/`str`/`object`/`datetime`/`category` dtypes.

```python
from gpdatacached.extensions.pandas_support import register
register()   # idempotent — registers both Series and DataFrame
```

pandas is an optional dependency — install with `pip install "gpdatacached[pandas]"`.

👉 **Full reference:** [`docs/pandas-support.md`](./pandas-support.md)

# API Reference

`gpdatacached` is a Redis-based cross-process data sharing and caching library. It exposes a hierarchical model — **Domain → Namespace → Data Object** — backed by Redis, so any Python process with access to the same Redis instance can share live, mutable, typed data structures.

This document is the complete reference for the public API exported by `gpdatacached.__init__`.

> Read this in another language: [简体中文](./zh-CN/api.md)

---

## Table of Contents

- [Installation](#installation)
- [Core Concepts](#core-concepts)
- [Quick Start](#quick-start)
- [Core Types](#core-types)
  - [GPDCDomain](#gpdcdomain)
  - [GPDCDomainConfig](#gpdcdomainconfig)
  - [GPDCNamespace](#gpdcnamespace)
  - [GPDCNamespaceConfig](#gpdcnamespaceconfig)
  - [reset_domain_config](#reset_domain_config)
- [Data Objects](#data-objects)
  - [GPDCDataObject](#gpdcdataobject)
  - [GPDCScalarObject](#gpdcscalarobject)
  - [GPDCContainerObject](#gpdccontainerobject)
- [Built-in Scalar Types](#built-in-scalar-types)
- [Built-in Container Types](#built-in-container-types)
- [Codecs](#codecs)
  - [GPDCCodec](#gpdccodec)
  - [GPDCScalarCodec](#gpdcscalarcodec)
  - [GPDCContainerCodec](#gpdccontainercodec)
- [Type Registry](#type-registry)
- [Key & URI Utilities](#key--uri-utilities)
- [Exceptions](#exceptions)
- [Cache Modes & Lifecycle](#cache-modes--lifecycle)
- [Extensions](#extensions)
  - [Pydantic Support](#pydantic-support)
  - [Pandas Support](#pandas-support)

---

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

## Quick Start

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
domain = GPDCDomain("mydomain", config)

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
obj.expire_in(60)            # cache_mode must be 'ttl'
obj.persist()                # switch to permanent

domain.close()
```

---

## Core Types

### `GPDCDomain`

```python
class GPDCDomain(name: str, config: GPDCDomainConfig)
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
| `gc_interval_seconds` | `int` | `300` | Background GC cycle interval. |
| `namespaces` | `dict[str, GPDCNamespaceConfig]` | `{}` | Predefined namespaces to create eagerly. |
| `default_lock_ttl_seconds` | `int` | `30` | TTL on lock keys (must be `≥ 1`). |
| `default_lock_acquire_timeout_seconds` | `int` | `30` | Blocking timeout for `obj.lock()` (must be `≥ 0`). |
| `gc_lock_ttl_seconds` | `int` | `30` | TTL for locks acquired by GC (must be `≥ 1`). |
| `gc_lock_wait_seconds` | `int` | `5` | GC per-object try-lock timeout (must be `≥ 0`). |

👉 These four fields are consistent across every process that opens the same domain. See [Multi-Process Locking](./locking.md) for what each controls, the cost locks impose, and the GC contract.

---

### `GPDCNamespace`

```python
class GPDCNamespace(name, domain, *, config=None)   # MutableMapping
```

A logical isolation space inside a `GPDCDomain`. Implements the full `MutableMapping` protocol backed by Redis.

**Naming rule:** names may use letters, digits, `.` and `_`; names wrapped in `__xxx__` are reserved. Anonymous objects (internal names starting with `~`) are hidden from the public mapping API.

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
domain = GPDCDomain("mydomain", new_config)     # writes the new lock config
```

Only the Redis connection fields on `config` are read; lock fields on the passed config are ignored.

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
| `cache_mode` | `str` | Effective cache mode; raises `ObjectGoneError` if missing. |
| `ttl` | `int` | Declared TTL in seconds (`0` = permanent). |
| `created_at` | `datetime \| None` | Creation timestamp. |
| `updated_at` | `datetime \| None` | Last update timestamp. |
| `value` | `object` | The Python value (abstract; subclasses implement). |

**Lifecycle methods**

> This is the API summary. For the full model — policy resolution, sliding refresh, ownership, anonymous/reference lifecycle, and pitfalls — see [Lifecycle Management](./lifecycle.md).

| Method | Description |
|---|---|
| `expire_in(seconds)` | Set a new TTL. Object must be in `cache_mode="ttl"`. Requires ownership. |
| `expire_at(when)` | Set absolute expiry time. Object must be in `cache_mode="ttl"`. Requires ownership. Deletes the object immediately if `when` is in the past. |
| `persist()` | Switch to `cache_mode="permanent"` with `ttl=0`. Requires ownership. |
| `update_cache_mode_and_ttl(cache_mode, ttl)` | Replace the full cache policy. Requires ownership. |
| `refresh_ttl()` | Re-apply this object's own TTL to its keys and cascade-refresh its owned subtree (used by the sliding-window path). No-op for permanent objects. |
| `take_ownership()` | Promote a reference object to be the canonical owner of its own data. Only valid for named references. |

**Locking methods** (opt-in, never called automatically by mutations)

| Method | Description |
|---|---|
| `lock(*, ttl=None, timeout=None)` | Context manager. Acquire an exclusive lock on the object's canonical name (blocks up to `timeout`). Raises `LockAcquisitionTimeout` on expiry. |
| `try_lock(*, ttl=None)` | Non-blocking lock attempt; returns a `HeldLock` or `None`. |

👉 **Locks are advisory and carry a per-operation Redis cost.** Before adopting them broadly, read [Multi-Process Locking](./locking.md) — it covers when locking is needed, the performance impact, recommended usage patterns, deadlock-avoidance ordering, and the GC contract.

**Other**

- `obj == other` compares by `(domain, namespace, canonical_name)`.
- `obj.referrers()` (containers only) — set of names referencing this container's raw data.
- `__hash__ = None` — GPDC objects are unhashable.

---

### `GPDCScalarObject`

```python
class GPDCScalarObject(name, namespace, *, canonical_name=None)   # GPDCDataObject
```

Base class for every scalar object. A scalar stores a single encoded value in its metadata hash (no dedicated raw key).

- `value` getter decodes the stored value via the registered codec.
- `value` setter validates the Python type against the registered codec, updates metadata, and refreshes TTL when `cache_mode="sliding"`.

Built-in subclasses: [`IntObject`](#intobject), [`FloatObject`](#floatobject), [`StrObject`](#strobject), [`BoolObject`](#boolobject), [`DateTimeObject`](#datetimeobject), [`DateObject`](#dateobject), [`TimeObject`](#timeobject), [`NoneObject`](#noneobject), [`TupleObject`](#tupleobject).

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

Slice indexing is unsupported on `__getitem__`; convert to a Python list first (`list(obj)[start:stop]`).

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

Codecs convert between Python values and their Redis storage representation. Every type registered with [`GPDCTypeRegistry`](#type-registry) must supply a codec.

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

Built-in subclasses: `BoolCodec`, `DateTimeCodec`, `DateCodec`, `DictCodec` *(see note)*, `FloatCodec`, `IntCodec`, `ListCodec` *(see note)*, `NoneCodec`, `SetCodec` *(see note)*, `StrCodec`, `TimeCodec`, `TupleCodec`.

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

## Type Registry

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

Low-level helpers for working with GPDC's key/URI scheme. Most applications never need these directly.

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

All public exceptions descend from a private `GPDCError(Exception)` base. Catch them individually for precise error handling.

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
class LockAcquisitionTimeout(GPDCLockError, GPDCError)
```

Raised when `obj.lock()` cannot acquire the lock within the configured `timeout`. Carries `.lock_key` and `.timeout`. See [Multi-Process Locking](./locking.md).

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

**Validation rule:** `"ttl"` and `"sliding"` strictly require `ttl > 0`; `"permanent"` requires `ttl == 0`.

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

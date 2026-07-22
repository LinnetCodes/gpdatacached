# gpdatacached

> *General-Purpose Data Cached*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Development Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://pypi.org/project/gpdatacached/)

A Redis-based **cross-process data sharing and caching** library for Python. Any process that can reach the same Redis instance can share live, mutable, typed data structures — scalars, lists, dicts, sets — with reference semantics, lifecycle control, and optional distributed locking.

> Read this in another language: [简体中文](./README.zh-CN.md)

---

## Why

Sharing mutable state across processes is painful. `multiprocessing.Manager` is single-process-bottlenecked; raw Redis forces you to (de)serialize everything by hand and gives you no type safety, no reference tracking, no TTL cascades. `gpdatacached` gives you a dict-like API on top of Redis with:

- **Typed objects** — store `int`, `str`, `datetime`, `list`, `dict`, `set`, … and get the same type back.
- **Live containers** — `ns["tags"].append("x")` mutates Redis directly; no read-modify-write races on the whole structure.
- **Reference sharing** — bind the same container under multiple names inside a namespace; the data is stored once.
- **Lifecycle control** — per-object `permanent` / `ttl` / `sliding-window` cache modes, with cascade propagation to owned subtrees.
- **Background GC** — reclaims expired and orphaned anonymous objects.
- **Opt-in distributed locking** — serialize writes across processes with `obj.lock()`.
- **Optional extensions** — cache [pydantic](https://docs.pydantic.dev/) `BaseModel` records and [pandas](https://pandas.pydata.org/) `Series` / `DataFrame` (see [Extensions](#extensions)).

---

## Install

```bash
pip install gpdatacached
```

Optional extras:

```bash
pip install "gpdatacached[pandas]"   # pandas DataFrame / Series support
```

Requirements: **Python ≥ 3.10**, **Redis ≥ 5.0**.

---

## Quick Start

```python
from gpdatacached import GPDCDomain, GPDCDomainConfig

config = GPDCDomainConfig(
    redis_host="127.0.0.1",
    redis_port=6379,
    redis_password="secret",
)
domain = GPDCDomain(config, name="mydomain")
ns = domain.ensure_namespace("analytics")

# Scalars read back as native Python values.
ns["counter"] = 42
assert ns["counter"] == 42

# Containers are live Redis-backed objects.
ns["tags"] = ["a", "b", "c"]
ns["tags"].append("d")
ns["tags"].pop(0)
assert ns["tags"].value == ["b", "c", "d"]

# Lifecycle control.
ns["temp"] = "x"
ns.get_object("temp").expire_in(60)   # requires cache_mode="ttl"

domain.close()
```

---

## Core Concepts

```
GPDCDomain ── GPDCNamespace ── GPDCDataObject (scalar | container)
```

| Concept | Role |
|---|---|
| **Domain** | Configuration root + Redis connection pool + lock/GC defaults. |
| **Namespace** | Logical isolation space; behaves like a `MutableMapping`. Cross-namespace references are unsupported by design. |
| **Data Object** | A named, typed value. Scalars return native Python values on read; containers return live objects. |

**Cache modes:** `permanent` (default, never expires), `ttl` (fixed expiry), `sliding` (refreshed on every mutation).

---

## Documentation

📖 **[Full documentation index](./docs/index.md)** — the entry point to all guides and references below.

- **[Overview](./docs/overview.md)** — design purpose, philosophy, and end-to-end architecture (start here to understand the library as a whole).
- **[API Reference](./docs/api.md)** — complete public API reference.
- **[Lifecycle Management](./docs/lifecycle.md)** — how object creation, expiry, refresh, and deletion work, including anonymous and reference objects (read this to set correct expectations).
- **[Extensibility](./docs/extensibility.md)** — the type system explained, with worked examples for adding your own scalar and container types (codecs, object classes, registration).
- **[Performance Guide](./docs/performance-guide.md)** — usage patterns that degrade performance (sliding TTL overuse, deep nesting, unnecessary GC/locking) and their recommended alternatives. Read before putting GPDC on a hot path.
- **[Multi-Process Locking](./docs/locking.md)** — when to lock, performance impact, recommended usage, deadlock avoidance, and GC interaction.

## Extensions

Optional built-in extensions under `gpdatacached.extensions`:

- **[Pydantic Support](./docs/pydantic-support.md)** — cache pydantic `BaseModel` records. Two storage modes: scalar (atomic JSON) for scalar-only models, container (Redis hash) for models needing field-level access or nested `list`/`dict`/`set` fields. Pydantic is a core dependency; no extra install needed.
- **[Pandas Support](./docs/pandas-support.md)** — cache pandas `Series` / `DataFrame` as live objects. Online selective accessors (`.iloc`, `.loc`, `df[col]`) for one-shot subset reads; `.value` for full restore; `extend` for appends. Requires `pip install "gpdatacached[pandas]"`.

---

## Running Tests

The test suite expects a **local Redis instance** reachable at `127.0.0.1:6379` with password `123456`:

- **Linux** — run Redis directly on the host.
- **Windows** — run Redis inside WSL; it will be reachable at `localhost` / `127.0.0.1` from the Windows side.

> ⚠️ Some tests **flush the Redis database** (`FLUSHDB`). Always use a dedicated Redis instance for testing — never point the tests at a production database.

Run the suite with:

```bash
pytest
```

You can edit the Redis settings in `tests/conftest.py` to point at a different instance, but this is not recommended — running a dedicated throwaway test Redis is usually cheaper and safer.

---

## License

[MIT](./LICENSE) © Linnet Codes

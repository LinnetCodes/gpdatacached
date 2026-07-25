# gpdatacached

**gpdatacached** — *General-Purpose Data Cached* — is a Redis-based **cross-process data sharing and caching** library for Python. Any process that can reach the same Redis instance can share live, mutable, typed data structures — scalars, lists, dicts, sets, pydantic models, pandas Series/DataFrames — with reference semantics, per-object lifecycle control, and optional distributed locking.

This index is the entry point for the documentation. Each section below links to a focused document; start with the Overview if you are new to the library.

## Start here

- **[Overview](./overview.md)** — *design purpose, philosophy, and end-to-end architecture.* Read this first to understand the Domain → Namespace → Object model, the Redis key layout, the ownership/reference/anonymous-object model, the `GPDataCache` facade for managing multiple domains and resolving URIs, and where each subsequent document fits in.

## Reference

- **[API Reference](./api.md)** — *complete public API reference.* Every class, method, property, exception, and configuration field exported by `gpdatacached`, with signatures and semantics. The authoritative specification.

## Concepts in depth

- **[Lifecycle Management](./lifecycle.md)** — *creation, expiry, refresh, and deletion of objects.* Explains the three cache modes (`permanent` / `ttl` / `sliding`), policy resolution, sliding-window refresh, the ownership model, and the lifecycle of anonymous and reference objects. Read this to set correct expectations about when objects appear and disappear.
- **[Extensibility](./extensibility.md)** — *the type system and how to add your own types.* The codec contract, the object-class contract, the four-step registration recipe, and fully worked examples for a custom scalar (`Color`) and a custom container (`Counter`). Most users never need this; those who do should also read Lifecycle, Locking, and the Performance Guide.

## Operational guidance

- **[Performance Guide](./performance-guide.md)** — *usage patterns that degrade performance, and their recommended alternatives.* Covers sliding-TTL overuse, deep container nesting, unnecessary GC, lock misuse, round-trip anti-patterns, and a verified cost-per-operation table. Read before putting GPDC on a hot path.
- **[Multi-Process Locking](./locking.md)** — *opt-in distributed locking across processes.* When locking is needed, the per-operation cost it imposes, recommended usage patterns, deadlock-avoidance ordering, and the GC ↔ writer contract.

## Optional type extensions

- **[Pydantic Support](./pydantic-support.md)** — cache pydantic `BaseModel` records. Two storage modes: scalar (atomic JSON) for scalar-only models, container (Redis hash) for models needing field-level access or nested container fields. Pydantic is a core dependency; no extra install needed.
- **[Pandas Support](./pandas-support.md)** — cache pandas `Series` / `DataFrame` as live objects. Online selective accessors (`.iloc`, `.loc`, `df[col]`) for one-shot subset reads; `.value` for full restore; `extend` for appends. Requires `pip install "gpdatacached[pandas]"`.

---

## Suggested reading order

| You are… | Read |
|---|---|
| New to the library | Overview → API Reference → Quick Start (in API Reference) |
| Building a real workload | + Lifecycle Management → Performance Guide |
| Driving many domains from config / addressing objects by URI | + the `GPDataCache` section in API Reference |
| Going multi-process | + Multi-Process Locking |
| Needing a custom type | Extensibility → (+ Lifecycle, Locking, Performance Guide if the type is complex) |
| Caching structured records | Pydantic Support |
| Caching tabular data | Pandas Support |

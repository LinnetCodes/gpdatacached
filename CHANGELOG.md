# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

GitHub Releases (with download artifacts) are published at
<https://github.com/LinnetCodes/gpdatacached/releases>.

## [0.2.0] — 2026-07-26

### Added

- **`GPDataCache` facade** — a config-driven entry point that discovers
  `GPDCDomain` instances from a `gpconfig` `GPConfigFolder`, lazily constructs
  them on first access, and resolves GPDC URIs to live `GPDCDataObject`
  instances. Includes `__contains__` (config-snapshot membership, usable after
  `close()`), `close()` (closes all cached domains), and the URI accessors below.
- **GPDC URI access** — `GPDataCache.get_object_by_uri(uri)` and
  `has_uri(uri)`, with multi-segment path traversal into nested containers
  (e.g. `gpdc://domain/ns/order_obj/items/0`). Terminals on a scalar field
  raise `ObjectNotFoundByURIError`. The utilities `make_uri` /
  `uri_to_redis_key` / `validate_object_name` are part of the public surface.
- **`expire_now()`** on `GPDCDataObject` — an explicit, mode-independent
  "delete now" entry point (full teardown incl. anonymous subtrees; raises
  `CanonicalObjectReferencedError` if still referenced). Use this instead of
  `expire_in(0)` / `expire_at(past)` when you want unambiguous immediate
  deletion.
- **Public exception bases** — `GPDCError` (root of the whole hierarchy) and
  `GPDCLockError` (base for lock-acquisition/ownership errors) are now
  exported, so callers can catch by category: `except GPDCError` catches every
  library error; `except GPDCLockError` catches every lock failure.
- **`ObjectNotFoundByURIError`** — raised by URI resolution on any
  unresolvable URI (missing domain/namespace/object, broken traversal,
  scalar terminal, malformed syntax).
- **Pydantic container-mode field validation** —
  `BaseModelContainerObject.__setitem__` now validates the value against the
  field's declared type *before* any Redis write (single-field via pydantic's
  `TypeAdapter`); a type mismatch raises `pydantic.ValidationError` at the
  call site and leaves the object untouched. Cross-field validators still
  require the scalar codec.
- gpconfig integration: `GPDCDomain` / `GPDCDomainConfig` are registered with
  `gpconfig`'s object factory at import time, so a domain can be constructed
  directly from a config file via `folder.get_object(name)`.

### Changed

- **BREAKING:** `GPDCDomain.__init__` signature changed from
  `(name, config)` to `(config, name=None)`, conforming to gpconfig's
  `cls(config)` configurable convention. The old call order raises
  `TypeError` with migration guidance before any Redis connection is made.
  `config.name` (the filename stem when loaded from a file) is preferred as
  the domain name; the explicit `name` argument is the fallback.
- `expire_in(seconds)` now requires `seconds > 0` (raises `ValueError`
  otherwise). Use `expire_now()` for immediate deletion.
- `GC interval` validation: `gc_interval_seconds` must now be `>= 30`
  (default `300` unchanged). Smaller values busy-looped the background GC
  thread.

### Fixed

- **Pandas non-unit `RangeIndex`** — `DataFrameObject.__len__` and
  `SeriesObject`/`DataFrameObject` `extend` now honour the stored
  `range_step` instead of assuming `step == 1`. Extending a non-unit-step
  DataFrame now stays in the compact range representation (previously
  promoted to labelled levels).
- **`SeriesObject.name` identity** — `.name` now returns the namespace
  binding (inherited from `GPDCDataObject`, matching `.canonical_name` /
  `.uri`); the pandas label is exposed as `.series_name`. Restores the
  base-class identity contract that `SeriesObject` had been overriding.
- **Failed multi-element encodes** — `encode_session` now also reverses
  ref-count increments it made on pre-existing containers, and a failed
  first-time store cleans up the just-written binding `obj:`/`raw:`/`ref:`
  keys, so a failed encode no longer leaves phantom referrers or orphan
  metadata.
- **Stale-handle mutations** — every container mutator now runs a
  `_check_alive()` liveness guard *before* its first Redis write, so a
  handle whose object has been deleted/expired raises `ObjectGoneError`
  without first corrupting the `raw:` key.
- **Sliding-TTL performance characterization** — sliding refresh batches the
  TTL re-apply into one pipeline per tree level (the load-bearing cost is the
  per-child `HGETALL`), corrected in the Performance Guide.

### Documentation

- New MkDocs site (Material theme) with full EN + Simplified-Chinese (ZH)
  documentation: overview, API reference, lifecycle, locking, performance
  guide, extensibility, pydantic/pandas support.
- Durable explicit-anchor scheme (`{ #id }` attributes) + a `mkdocs build
  --strict` validation gate, so editing a heading never silently breaks an
  in-page cross-reference.

[0.2.0]: https://github.com/LinnetCodes/gpdatacached/releases/tag/version-0.2.0

## [0.1.0] — 2026-07-04

Initial public release.

Core typed cross-process data sharing and caching over Redis:

- `GPDCDomain` / `GPDCNamespace` / `GPDCDataObject` three-tier model.
- Typed scalars (int/float/str/bool/datetime/date/time/none/tuple) and live
  containers (list/dict/set) with reference semantics — mutate in place, no
  read-modify-write.
- Per-object lifecycle: `permanent` / `ttl` / `sliding-window` cache modes
  with cascade propagation.
- Anonymous nested objects with reference counting and cascade delete.
- Opt-in distributed locking (`obj.lock()` / `try_lock()`) via
  `SET NX EX` + `BLPOP` with Lua-atomic release.
- Background GC for orphaned/expired keys.
- Extensibility: `GPDCTypeRegistry`, codec/object ABCs, pandas
  (`Series`/`DataFrame`) and pydantic support extensions.
- Strict config validation; `reset_domain_config` utility.

[0.1.0]: https://github.com/LinnetCodes/gpdatacached/releases/tag/version-0.1.0

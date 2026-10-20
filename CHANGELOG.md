# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

GitHub Releases (with download artifacts) are published at
<https://github.com/LinnetCodes/gpdatacached/releases>.

## [0.3.1] — 2026-10-20

### Added

- **`py.typed` marker (PEP 561)** — the package now ships the inline-type
  marker, so downstream `mypy` / `pyright` users (including `--strict` mode)
  get the library's real type hints instead of `Any` for the whole API
  surface (`import-untyped` on `from gpdatacached import ...`). Downstream
  projects carrying an `ignore_missing_imports` per-module override as a
  workaround can drop it once they require `gpdatacached>=0.3.1`. Packaging
  only — no code changes; the annotations were already in place, the marker
  simply makes them authoritative for type checkers. Guarded by
  `tests/test_packaging.py` (marker presence); wheel inclusion verified
  against the built artifact (hatchling packages the whole `src/gpdatacached`
  directory, so no build-config change was needed).

## [0.3.0] — 2026-10-16

### Added

- **Dotted domain names via the `@` filename escape (`GPDataCache`)** —
  domain names may now contain `.` end-to-end through the config-file path.
  gpconfig addresses configs by dot-separated paths, so a `.` in a config
  filename is consumed as a path separator and the file becomes
  unaddressable; the new convention writes each `.` in a domain name as `@`
  in the filename (chosen for its internet-address connotation — the natural
  spelling when a domain mirrors an internet domain):
  `domain_name@com.yaml` configures the domain `"domain_name.com"`, and
  `cache.get_domain("domain_name.com")`, `name in cache`, and
  `gpdc://domain_name.com/{ns}/{obj}[...]` URIs resolve it transparently.
  The mapping is injective because `@` is not a legal character in GPDC
  names. `GPDataCache.get_domain` now validates the requested name against
  the GPDC domain-name rules (`ValueError` otherwise; URI resolution reports
  the same case as `ObjectNotFoundByURIError`, and `has_uri` stays aligned).
  `GPDCDomain`'s name resolution restores `@` → `.` for names derived from
  a config-file stem (before the name-mismatch check), so gpconfig's
  object factory path also constructs correctly-named domains:
  `folder.get_object("domain_name@com")` yields the domain
  `"domain_name.com"`, identical to `GPDataCache.get_domain(
  "domain_name.com")`. An explicit `name=` argument containing `@` is
  still rejected. The escape helpers (`DOMAIN_DOT_ESCAPE`,
  `escape_domain_name`, `unescape_domain_name`) are exported from the
  package root. **No gpconfig-side change is
  needed or wanted** (owner ruling 2026-10-16): gpconfig keeps
  `config.name` as the raw stem, preserving the
  `config.name == addressable path component` invariant; a gpconfig-side
  request (fail-fast validation of config filenames containing `.` at manager
  init, plus a docs note) is recorded in
  `dev_docs/issue-gpconfig-at-escape-in-config-names.md`); that request
  has since landed in gpconfig 0.4.0.
- **Startup warning for unaddressable config filenames** — `GPDataCache`
  construction logs one warning per config file whose name contains a real
  `.` (listed by `list_configs()` but never loadable by gpconfig's
  dot-notation), suggesting the `@` spelling.

### Fixed

- `GPDataCache.__contains__` and `get_domain` no longer disagree for
  config files whose stem contains `.` (previously `in` returned `True`
  while `get_domain` raised `ConfigNotFoundError`; such files are now
  warned about at construction and looked up only through valid names).

## [0.2.1] — 2026-10-12

### Added

- **`Decimal` builtin scalar type** — `decimal.Decimal` is auto-registered on
  `import gpdatacached` as `scalar:decimal`; `DecimalObject` and `DecimalCodec`
  are exported from the package root. Encoded losslessly as
  `decimal:{str(value)}`: coefficient/exponent (significance) are preserved
  exactly, extreme exponents use scientific notation, and non-finite values
  (`NaN`, `sNaN`, `±Infinity`) plus `-0` roundtrip as-is — the same policy as
  `float`'s `nan`/`inf`. Works everywhere a registered scalar does: standalone
  objects, container elements, dict keys, set members, tuple elements, and
  pandas object-dtype columns/index labels. Malformed payloads raise
  `ValueError` on decode. Note the documented cross-type-equality caveat:
  natively-equal values of different numeric types (`1` vs `Decimal(1)`) are
  distinct GPDC dict keys/set members, as `1` vs `1.0` already were.

### Changed

- Require `gpconfig>=0.4.0` (was `>=0.3.0`, a version never published).
  gpconfig 0.4.0 ships fail-fast validation of config filenames containing
  `.` plus the `@`-convention documentation; gpdatacached's own
  construction-time warning remains as defense for it.

### Documentation
- Performance guide §4: corrected "arbitrary nesting depth" — the write path recurses (~5 frames/level), so depth is bounded by `sys.setrecursionlimit()` (RecursionError around depth 200–300 at the default 1000; verified empirically). New recommendation: **keep nesting ≤ 5 levels** (owner ruling 2026-10-16); a mid-write `RecursionError` is not a clean failure — subsequent operations in the same process can fail misleadingly, restart the process. No depth pre-check is planned (owner ruling: cost outweighs benefit).

- Full docs-vs-code synchronization pass across `docs/` (English + zh-CN
  mirrors) and both READMEs: README now lists `Decimal` among the supported
  scalar types and its Quick Start lifecycle example is runnable as written
  (`update_cache_mode_and_ttl` instead of mode-restricted `expire_in`);
  `api.md` documents `remaining_ttl()` and the exact `ObjectGoneError`
  surface; the extensibility codec diagram and the overview layer-dependency
  edge were corrected to match the code (`GPDCContainerCodec` is a sibling
  root of `GPDCCodec`, and the extensions depend on the type-system layer,
  not the builtins); performance-guide RTT attributions were updated; the
  zh-CN mirrors gained the translated §7 ownership heading (with a durable
  explicit anchor) and several translation fixes. `keys.py`'s `make_ref_key`
  docstring and the pandas `UnsupportedElementTypeError` guidance message
  were corrected to match reality (hash, not set; `decimal` included). The
  README status badge now reads Beta, aligned with the packaging
  classifier `Development Status :: 4 - Beta`.

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

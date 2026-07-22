# Pandas Support

`gpdatacached.extensions.pandas_support` makes pandas [`Series`](https://pandas.pydata.org/docs/reference/api/pandas.Series.html) and [`DataFrame`](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.html) cacheable as GPDC objects, backed by live `SeriesObject` / `DataFrameObject` wrappers.

pandas is an **optional** dependency — install it with the `pandas` extra:

```bash
pip install "gpdatacached[pandas]"
```

## Design Philosophy

`gpdatacached` is a **cache shared across processes**, not an in-process pandas workspace. The intended usage pattern is:

> *One writer caches a large structure; many reader processes each pull the subset they need for the current task, then operate on that local copy.*

Two corollaries:

- **Fetch your subset in one selective pass, then work locally.** Use the online accessors (`.iloc`, `.loc`, `df[col]`) to read just the rows/columns you need; do all further processing (filtering, joining, aggregating) on the returned plain `pandas` object. **Do not ping the cache per-row or per-cell.**
- **Online reads are supported only when meaningfully cheaper than full restore.** Anything that approaches the cost of `.value` (e.g. `.loc[slice]`, `.loc[list]`, `.iloc[list]`) is rejected with a `TypeError` pointing at `.value`.

**Mutation is append-focused only.** Both `SeriesObject` and `DataFrameObject` support `extend(...)` for row appends. `DataFrameObject` additionally supports structural column add/replace/drop via `__setitem__` / `__delitem__`. There is **no cell-level** or in-place row mutation — for that, call `.value` and work on the local pandas object.

---

## Scope at a Glance

| Axis | Supported |
|---|---|
| **Row indexes** | `RangeIndex` (default int index, stored compactly) **and** any labelled index including `pd.MultiIndex` (stored one `collection:list` child per level). `.index_names` always returns a tuple. |
| **DataFrame columns** | Single-level (scalar names) **or** `pd.MultiIndex` (tuple names). The row and column axes are independent and compose (a DataFrame may have both a row and a column MultiIndex). |
| **Value dtypes** | `int64`/`Int64`, `float64`/`Float64`, `bool`/`boolean`, `str`/`string`, `object`, `datetime64` (naive & tz-aware), `category`. |
| **Mutation** | Row `extend` (Series & DataFrame); DataFrame column `__setitem__` / `__delitem__` (structural add/replace/drop). |

Notes:

- `.loc` matches labels with Python `==`; `nan` labels therefore do not match (`nan != nan`).
- Returned partial results retain the full stored index (no level is dropped).

---

## Registration

```python
from gpdatacached.extensions.pandas_support import register
register()   # idempotent
```

`register()` registers both `pd.Series` → `SeriesObject` and `pd.DataFrame` → `DataFrameObject` with `GPDCTypeRegistry`. It is idempotent — calling it more than once is harmless. Call it once at process startup, before storing or reading any pandas object.

---

## Quick Start

```python
import pandas as pd
from gpdatacached import GPDCDomain, GPDCDomainConfig
from gpdatacached.extensions.pandas_support import register

register()

config = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_password="secret")
domain = GPDCDomain(config, name="mydomain")
ns = domain.ensure_namespace("analytics")

# ── Series ───────────────────────────────────────────────────────
s = pd.Series([10, 20, 30], name="price", index=["a", "b", "c"])
ns["prices"] = s

series = ns["prices"]                  # → SeriesObject (live)
print(series.iloc[0])                  # 10  (O(1) positional read)
print(series.loc["b"])                 # 20  (O(N) selective label read)
print(series.value)                    # full pd.Series materialized locally

series.extend(pd.Series([40, 50], index=["d", "e"]))   # append rows in place

# ── DataFrame ────────────────────────────────────────────────────
df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
ns["table"] = df

table = ns["table"]                    # → DataFrameObject (live)
print(table["a"])                      # one column as pd.Series (O(N), one column)
print(table.iloc[0])                   # one row as pd.Series (O(1))
print(table.loc[[...]])                # ⚡ rejected — call .value instead

table["c"] = [10, 20]                  # add a column (structural)
del table["b"]                         # drop a column (structural)
table.extend(pd.DataFrame({"a": [3], "c": [30]}))   # append rows in place

print(table.value)                     # full pd.DataFrame materialized locally

domain.close()
```

---

## Accessor Cost Guide

The online accessors exist only when they are meaningfully cheaper than `.value`. Use this table to decide. (For the broader performance picture across all of GPDC — sliding TTL, nesting, GC, locking — see the [Performance Guide](./performance-guide.md).)

| Accessor | Cost | Returns |
|---|---|---|
| `.iloc[int]` | **O(1)** | a single positional value / row |
| `.iloc[a:b]` | **O(b−a)** | a positional slice |
| `.loc[key]` | O(N), selective | depth-k prefix label scan; decodes only matched rows but reads every index level — **do not put in a hot loop** |
| `df[col]` / `df[tuple]` | O(N), one column | (DataFrame) a single column as `pd.Series` |
| `df[level0]` / `df[list]` | O(N_cols) + O(matches·N) | column-axis selection (cheap; few cols) |
| `.value` | full restore | O(N) — the escape hatch |

**Decision rule:**

- **One-shot selective read** (rows/columns wanted ≪ total) → use the online accessor (`.iloc` / `.loc` / `__getitem__`).
- **Repeated or heavy work on the same data** — sorting, many label lookups, complex pandas semantics, or any operation the online API rejects → call `.value` once to materialize the local `pandas` object and work on it.

---

## SeriesObject

```python
class SeriesObject(GPDCContainerObject)
```

GPDC type `collection:pandas_series`. Backed by a Redis hash storing the data list (anonymous `ListObject` child), optional per-level index lists, and scalar metadata (`dtype`, `name`, `index_kind`, `index_names`, `index_dtypes`, `range_*`, category metadata).

**Properties**

| Property | Type | Description |
|---|---|---|
| `value` | `pd.Series` | Full materialization as a plain `pd.Series`. The setter always raises — use mutation methods instead. |
| `type` | `str` | `"collection:pandas_series"`. |
| `dtype` | `str` | Stored value dtype string (e.g. `"int64"`, `"category"`). |
| `name` | `Any` | Stored series name. |
| `index_names` | `tuple` | Index level names (always a tuple; one element for a flat index). |
| `iloc` | `_ILocIndexer` | Positional row accessor (read-only). Supports `int` and `slice`; rejects list. |
| `loc` | `_LocIndexer` | Label-based row accessor (read-only). Supports scalar / depth-k tuple prefix on a labelled/MultiIndex. Rejects `slice`, `list`, and any use on a `RangeIndex` with a `TypeError` pointing at `.value`. |

**Methods**

| Method | Description |
|---|---|
| `extend(other)` | Append rows in place. Accepts a `pd.Series`, a list of `pd.Series`, or an iterable of `(index, value)` tuples. Mutates Redis; returns `None`. (Differs from `pd.concat`, which is non-mutating.) Rejects categorical-dtype series with a `TypeError`. |
| `deepcopy()` | Return a fully independent `pd.Series` (`.value.copy(deep=True)`). |

**Dunder protocols**

- `len(series_obj)` → number of rows.
- `series_obj.iloc[int]` → O(1) positional scalar/cell.
- `series_obj.iloc[a:b]` → positional slice as a `pd.Series`.
- `series_obj.loc[key]` → label selection; full-depth (`k == L`) with exactly one match returns the scalar value (pandas semantics); otherwise returns a `pd.Series`.

---

## DataFrameObject

```python
class DataFrameObject(GPDCContainerObject)
```

GPDC type `collection:pandas_dataframe`. Backed by a Redis hash with column value-refs under `encode_key(col)`, per-level row index labels under `~index_l{i}`, and scalar metadata under `~`-prefixed fields (`~columns`, `~columns_names`, `~index_kind`, `~index_names`, `~index_dtypes` or `~range_*`, and per-column `~dt:{col}` / `~cat:{col}` / `~ord:{col}`).

**Properties**

| Property | Type | Description |
|---|---|---|
| `value` | `pd.DataFrame` | Full materialization as a plain `pd.DataFrame`. The setter always raises — use mutation methods instead. |
| `type` | `str` | `"collection:pandas_dataframe"`. |
| `columns` | `tuple` | Column names (scalars for single-level, tuples for MultiIndex columns). |
| `index_names` | `tuple` | Row index level names (always a tuple). |
| `iloc` | `_DfILocIndexer` | Positional row accessor (read-only). Supports `int` (single row as `pd.Series`) and `slice` (positional row slice as `pd.DataFrame`); rejects list. |
| `loc` | `_DfLocIndexer` | Label-based row accessor (read-only). Supports scalar / depth-k tuple prefix on a labelled/MultiIndex row index. Rejects `slice`, `list`, and any use on a `RangeIndex` with a `TypeError` pointing at `.value`. |

**Methods**

| Method | Description |
|---|---|
| `extend(other)` | Append rows in place. Accepts a `pd.DataFrame`, a `dict` of lists, or a list of `dict` rows. For a genuine MultiIndex-row DataFrame (`L ≥ 2`), only a `DataFrame` with a matching MultiIndex is accepted (dict/list inputs synthesise positional labels a MultiIndex cannot supply → `TypeError`; use `.value`). Mutates Redis; returns `None`. |
| `deepcopy()` | Return a fully independent `pd.DataFrame` (`.value.copy(deep=True)`). |

**Column-axis access (single-level columns)**

| Operation | Behaviour |
|---|---|
| `df[col]` | Select one column as a `pd.Series`. |
| `df[list_of_keys]` | Sub-`pd.DataFrame` of the selected columns (always, even for one key). |
| `df[col] = values` | Add or replace a column. `len(values)` must equal the current row count. Introducing rows by adding a column to an empty DataFrame is rejected. |
| `del df[col]` | Drop a column. The shared index is retained (even for the last column). |
| `col in df` | `True` if the column exists. |
| `iter(df)` | Iterate column names. |

**Column-axis access (MultiIndex columns)**

For MultiIndex columns, keys are tuples and prefix matching applies:

| Operation | Behaviour |
|---|---|
| `df[(a, b, ...)]` | Full-depth tuple → that single column as a `pd.Series`. |
| `df[level0]` or `df[(a, b)]` | Partial-depth prefix → sub-`pd.DataFrame` of the matching columns. |
| `df[list_of_keys]` | Sub-`pd.DataFrame` (each key may be a scalar prefix or a full-depth tuple). |
| `df[col_tuple] = values` | Add or replace a full-depth column. A partial-depth tuple raises `ValueError`; a non-tuple or list key raises `TypeError` — call `.value`. |
| `del df[col_tuple]` | Drop a full-depth column. A partial-depth tuple raises `ValueError`; a non-tuple or list key raises `TypeError` — call `.value`. |

**Dunder protocols**

- `len(df_obj)` → number of rows.
- `df_obj.iloc[int]` → single row as `pd.Series` (O(1)).
- `df_obj.iloc[a:b]` → positional row slice as `pd.DataFrame`.
- `df_obj.loc[key]` → label selection; full-depth (`k == L`) with exactly one match returns the row as a `pd.Series`; partial depth or multiple matches return a `pd.DataFrame`.

---

## Exceptions

All three exceptions descend from `TypeError`. Their messages guide you to either change the dtype or register a custom codec.

### `UnsupportedSeriesDtypeError`

```python
class UnsupportedSeriesDtypeError(TypeError)
```

Raised when a pandas dtype cannot be stored element-wise by `SeriesObject` (covers both value dtypes and index dtypes). Use `is_index=True`/`False` to disambiguate the cause in the message.

The message lists the supported dtypes and points at `GPDCTypeRegistry.register(...)` as the extension hook.

### `UnsupportedDataFrameError`

```python
class UnsupportedDataFrameError(TypeError)
```

Raised when a DataFrame cannot be stored element-wise by `DataFrameObject`. The message disambiguates the cause (per-column dtype, row index, or duplicate column names) and points at `GPDCTypeRegistry.register(...)`.

### `UnsupportedElementTypeError`

```python
class UnsupportedElementTypeError(TypeError)
```

Raised when a Series/DataFrame element is not encodable by a `GPDCScalarCodec` subclass — i.e. it is a container type (`list`/`dict`/`set`/container-registered pydantic model) or an unsupported type. Series/DataFrame elements must be scalar types encodable by a `GPDCScalarCodec` subclass. The message enumerates the allowed scalar types.

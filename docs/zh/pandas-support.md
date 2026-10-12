# Pandas 支持

`gpdatacached.extensions.pandas_support` 让 pandas 的 [`Series`](https://pandas.pydata.org/docs/reference/api/pandas.Series.html) 与 [`DataFrame`](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.html) 可以作为 GPDC 对象被缓存，底层由活跃的 `SeriesObject` / `DataFrameObject` 包装类支撑。

pandas 是**可选**依赖 —— 请使用 `pandas` 扩展进行安装：

```bash
pip install "gpdatacached[pandas]"
```

## 设计理念

`gpdatacached` 是一个**跨进程共享的缓存**，并非进程内的 pandas 工作区。其预期使用模式是：

> *一个写入者把大型结构缓存起来；多个读取进程各自按当前任务所需拉取子集，然后在本地副本上操作。*

由此引申出两点：

- **一次性把所需子集读取出来，然后在本地操作。** 使用在线访问器（`.iloc`、`.loc`、`df[col]`）只读取你需要的行/列；后续的所有处理（过滤、连接、聚合）都在返回的普通 `pandas` 对象上进行。**不要按行或按单元格反复访问缓存。**
- **仅当明显比完整还原更廉价时才提供在线读取。** 任何接近 `.value` 成本的操作（例如 `.loc[slice]`、`.loc[list]`、`.iloc[list]`）都会被拒绝，并抛出指向 `.value` 的 `TypeError`。

**变更仅以追加为主。** `SeriesObject` 与 `DataFrameObject` 均支持 `extend(...)` 进行行追加。`DataFrameObject` 另外支持通过 `__setitem__` / `__delitem__` 做结构性的列新增/替换/删除。**没有单元格级**或原地行变更能力 —— 如需此类操作，请调用 `.value` 在本地 pandas 对象上操作。

!!! warning "pandas 对象无法通过 GPDC URI 路径分段导航"

    [`GPDataCache.get_object_by_uri`](./api.md#get_object_by_uri) 可以把裸 `gpdc://d/ns/df_obj`（无路径）解析到 `DataFrameObject` / `SeriesObject` 自身，这与其他对象并无二致。但它**无法**通过额外的路径分段深入 pandas 对象：

    - `DataFrameObject.__getitem__(col)` 会选取一列并返回一个**普通 `pd.Series`**（原生值，而非 `GPDCDataObject`），因此终止于此处的 URI 会抛出 `ObjectNotFoundByURIError`（`get_object_by_uri` 只返回对象，绝不返回值）。
    - `SeriesObject` 根本没有 `__getitem__`，因此根对象之外的任何分段都会抛出 `ObjectNotFoundByURIError`。

    若需读取已缓存 DataFrame/Series 的子集，请对你获取到的对象使用在线访问器（`.iloc`、`.loc`、`df[col]`）或 `.value` —— 而非 URI 路径分段。

---

## 支持范围概览

| 轴 | 支持 |
|---|---|
| **行索引** | `RangeIndex`（默认整数索引，紧凑存储）**以及**任意带标签索引，包括 `pd.MultiIndex`（按每个 level 存一个 `collection:list` 子对象）。`.index_names` 始终返回元组。 |
| **DataFrame 列** | 单层（标量名称）**或** `pd.MultiIndex`（元组名称）。行轴与列轴相互独立，可组合（DataFrame 可同时拥有行 MultiIndex 与列 MultiIndex）。 |
| **值 dtype** | 任意宽度的整数/浮点/布尔 dtype（如 `int64`/`Int64`、`float64`/`Float64`、`bool`/`boolean`）、`str`/`string`、`object`（任何可标量编码的元素 —— 含 0.2.1 起内建的 `decimal.Decimal`）、`datetime64`（naive 与 tz-aware，任意单位）、`category`。 |
| **变更** | 行 `extend`（Series 与 DataFrame）；DataFrame 的列 `__setitem__` / `__delitem__`（结构性的新增/替换/删除）。 |

说明：

- `.loc` 使用 Python `==` 匹配标签；因此 `nan` 标签不会匹配（`nan != nan`）。
- 返回的部分结果会保留完整的存储索引（不会丢弃任何 level）。

---

## 注册

```python
from gpdatacached.extensions.pandas_support import register
register()   # 幂等
```

`register()` 会把 `pd.Series` → `SeriesObject` 与 `pd.DataFrame` → `DataFrameObject` 同时注册到 `GPDCTypeRegistry`。该函数幂等 —— 多次调用无害。在进程启动时、读写任何 pandas 对象之前调用一次即可。

---

## 快速开始

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

series = ns["prices"]                  # → SeriesObject（活跃对象）
print(series.iloc[0])                  # 10（O(1) 位置读取）
print(series.loc["b"])                 # 20（O(N) 选择性标签读取）
print(series.value)                    # 在本地物化的完整 pd.Series

series.extend(pd.Series([40, 50], index=["d", "e"]))   # 原地追加行

# ── DataFrame ────────────────────────────────────────────────────
df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
ns["table"] = df

table = ns["table"]                    # → DataFrameObject（活跃对象）
print(table["a"])                      # 单列作为 pd.Series（O(N)，单列）
print(table.iloc[0])                   # 单行作为 pd.Series（O(1)）
print(table.loc[[...]])                # ⚡ 被拒绝 —— 请改用 .value

table["c"] = [10, 20]                  # 新增一列（结构性）
del table["b"]                         # 删除一列（结构性）
table.extend(pd.DataFrame({"a": [3], "c": [30]}))   # 原地追加行

print(table.value)                     # 在本地物化的完整 pd.DataFrame

domain.close()
```

---

## 访问器成本指南 { #pandas-cost-guide }

在线访问器存在的唯一理由是明显比 `.value` 更廉价。请依据下表选择。（GPDC 全库的性能图景 —— sliding TTL、嵌套、GC、加锁 —— 见[性能指南](./performance-guide.md)。）

| 访问器 | 成本 | 返回 |
|---|---|---|
| `.iloc[int]` | **O(1)** | 单个位置值/行 |
| `.iloc[a:b]` | **O(b−a)** | 位置切片 |
| `.loc[key]` | O(N)，选择性 | 深度 k 的前缀标签扫描；仅解码匹配的行，但会读取所有索引 level —— **不要放进热循环** |
| `df[col]` / `df[tuple]` | O(N)，单列 | （DataFrame）单列，返回 `pd.Series` |
| `df[level0]` / `df[list]` | O(N_cols) + O(匹配数·N) | 列轴选择（成本低；列数少） |
| `.value` | **完整还原**（读取所有列 + 所有索引 level，重建整个 frame —— 尽管同属 `O(N)` 量级，但远比选择性 `.loc` 重得多） | 整个 `pd.Series` / `pd.DataFrame`；最终退路 |

**决策规则：**

- **一次性选择性读取**（所需行/列远小于总量）→ 使用在线访问器（`.iloc` / `.loc` / `__getitem__`）。
- **对同一份数据反复或重度操作** —— 排序、大量标签查找、复杂 pandas 语义，或任何被在线 API 拒绝的操作 → 调用一次 `.value` 在本地物化 `pandas` 对象后再操作。

---

## SeriesObject

```python
class SeriesObject(GPDCContainerObject)
```

GPDC 类型 `collection:pandas_series`。底层是一个 Redis hash，存储数据列表（匿名 `ListObject` 子对象）、可选的每个 level 的索引列表，以及标量元信息（`dtype`、`name`、`index_kind`、`index_names`、`index_dtypes`、`range_*`、category 元信息）。

> **身份 vs pandas 标签。** `SeriesObject` 继承自 `GPDCDataObject` 的 `name` 返回**命名空间绑定名**（GPDC 身份，与 `.uri`/`.canonical_name` 及 `DataFrameObject` 一致）。存储的 pandas `Series.name` 通过 `series_name` 暴露（也可经 `.value.name` 访问）。

**属性**

| 属性 | 类型 | 说明 |
|---|---|---|
| `value` | `pd.Series` | 物化为普通 `pd.Series`。setter 永远抛错 —— 请使用变更方法。 |
| `type` | `str` | `"collection:pandas_series"`。 |
| `dtype` | `str` | 存储的值 dtype 字符串（如 `"int64"`、`"category"`）。 |
| `name` | `str` | 命名空间绑定名（继承自 `GPDCDataObject`；与 `.uri`/`.canonical_name` 共享的身份契约）。 |
| `series_name` | `Any` | 存储的 pandas `Series.name`（payload 标签）。 |
| `index_names` | `tuple` | 索引 level 名（始终是元组；扁平索引为单元素）。 |
| `iloc` | `_ILocIndexer` | 位置行访问器（只读）。支持 `int` 和 `slice`；拒绝 list。 |
| `loc` | `_LocIndexer` | 基于标签的行访问器（只读）。在带标签/MultiIndex 上支持标量 / 深度 k 的元组前缀。拒绝 `slice`、`list`，以及对 `RangeIndex` 的任何使用，并以指向 `.value` 的 `TypeError` 提示。 |

**方法**

| 方法 | 说明 |
|---|---|
| `extend(other)` | 原地追加行。接受 `pd.Series`、`pd.Series` 列表，或 `(index, value)` 元组的可迭代对象。修改 Redis；返回 `None`。（与 `pd.concat` 不同 —— 后者非修改式。）对 category-dtype series 会抛出 `TypeError`。 |
| `deepcopy()` | 返回完全独立的 `pd.Series`（`.value.copy(deep=True)`）。 |

**协议方法**

- `len(series_obj)` → 行数。
- `series_obj.iloc[int]` → O(1) 位置标量/单元格。
- `series_obj.iloc[a:b]` → 位置切片，返回 `pd.Series`。
- `series_obj.loc[key]` → 标签选择；全深度（`k == L`）且仅匹配一行时返回标量值（pandas 语义）；否则返回 `pd.Series`。

---

## DataFrameObject

```python
class DataFrameObject(GPDCContainerObject)
```

GPDC 类型 `collection:pandas_dataframe`。底层是一个 Redis hash：列值引用存于 `encode_key(col)` 之下，每个 level 的行索引标签存于 `~index_l{i}` 之下，标量元信息存于 `~` 前缀字段（`~columns`、`~columns_names`、`~index_kind`、`~index_names`、`~index_dtypes` 或 `~range_*`，以及每列的 `~dt:{col}` / `~cat:{col}` / `~ord:{col}`）。

**属性**

| 属性 | 类型 | 说明 |
|---|---|---|
| `value` | `pd.DataFrame` | 物化为普通 `pd.DataFrame`。setter 永远抛错 —— 请使用变更方法。 |
| `type` | `str` | `"collection:pandas_dataframe"`。 |
| `columns` | `tuple` | 列名（单层为标量，MultiIndex 列为元组）。 |
| `index_names` | `tuple` | 行索引 level 名（始终是元组）。 |
| `iloc` | `_DfILocIndexer` | 位置行访问器（只读）。支持 `int`（单行作为 `pd.Series`）和 `slice`（位置行切片作为 `pd.DataFrame`）；拒绝 list。 |
| `loc` | `_DfLocIndexer` | 基于标签的行访问器（只读）。在带标签/MultiIndex 行索引上支持标量 / 深度 k 的元组前缀。拒绝 `slice`、`list`，以及对 `RangeIndex` 的任何使用，并以指向 `.value` 的 `TypeError` 提示。 |

**方法**

| 方法 | 说明 |
|---|---|
| `extend(other)` | 原地追加行。接受 `pd.DataFrame`、list 的 dict，或 dict 行的列表。对于真正的 MultiIndex 行 DataFrame（`L ≥ 2`），只接受带匹配 MultiIndex 的 `DataFrame`（dict/list 输入会合成 MultiIndex 无法提供的位置标签 → `TypeError`；请使用 `.value`）。category 列会拒绝不在既有分类中的值（`ValueError`，发生在任何 Redis 写入之前）。修改 Redis；返回 `None`。 |
| `deepcopy()` | 返回完全独立的 `pd.DataFrame`（`.value.copy(deep=True)`）。 |

**列轴访问（单层列）**

| 操作 | 行为 |
|---|---|
| `df[col]` | 选取单列，返回 `pd.Series`。 |
| `df[list_of_keys]` | 由所选列组成的子 `pd.DataFrame`（即便只有一个 key 也始终返回子 DataFrame）。 |
| `df[col] = values` | 新增或替换列。`len(values)` 必须等于当前行数。对空 DataFrame 通过新增列来引入行会被拒绝。 |
| `del df[col]` | 删除列。共享的索引会被保留（即便删到最后一列）。 |
| `col in df` | 列存在时为 `True`。 |
| `iter(df)` | 遍历列名。 |

**列轴访问（MultiIndex 列）**

对于 MultiIndex 列，key 为元组，并应用前缀匹配：

| 操作 | 行为 |
|---|---|
| `df[(a, b, ...)]` | 全深度元组 → 该单列作为 `pd.Series`。 |
| `df[level0]` 或 `df[(a, b)]` | 部分深度前缀 → 由匹配列组成的子 `pd.DataFrame`。 |
| `df[list_of_keys]` | 子 `pd.DataFrame`（每个 key 可以是标量前缀或全深度元组）。 |
| `df[col_tuple] = values` | 新增或替换全深度列。部分深度元组会抛出 `ValueError`；非元组或 list key 会抛出 `TypeError` —— 请调用 `.value`。 |
| `del df[col_tuple]` | 删除全深度列。部分深度元组会抛出 `ValueError`；非元组或 list key 会抛出 `TypeError` —— 请调用 `.value`。 |

**协议方法**

- `len(df_obj)` → 行数。
- `df_obj.iloc[int]` → 单行作为 `pd.Series`（O(1)）。
- `df_obj.iloc[a:b]` → 位置行切片，返回 `pd.DataFrame`。
- `df_obj.loc[key]` → 标签选择；全深度（`k == L`）且仅匹配一行时返回该行作为 `pd.Series`；部分深度或匹配多行时返回 `pd.DataFrame`。

---

## 异常

三个异常都派生自 `TypeError`。它们的错误信息会引导你修改 dtype 或注册自定义 codec。

### `UnsupportedSeriesDtypeError`

```python
class UnsupportedSeriesDtypeError(TypeError)
```

当某个 pandas dtype 无法被 `SeriesObject` 按元素存储时抛出（值 dtype 与索引 dtype 均适用）。在构造时通过 `is_index=True`/`False` 在错误信息中区分原因。

错误信息会列出支持的 dtype，并指向 `GPDCTypeRegistry.register(...)` 作为扩展点。

### `UnsupportedDataFrameError`

```python
class UnsupportedDataFrameError(TypeError)
```

当某个 DataFrame 无法被 `DataFrameObject` 按元素存储时抛出。错误信息会区分原因（每列 dtype、行索引，或重复列名），并指向 `GPDCTypeRegistry.register(...)`。

### `UnsupportedElementTypeError`

```python
class UnsupportedElementTypeError(TypeError)
```

当某个 Series/DataFrame 元素无法被 `GPDCScalarCodec` 子类编码时抛出 —— 即它是容器类型（`list`/`dict`/`set`/容器注册的 pydantic 模型）或不支持的类型。Series/DataFrame 元素必须是能被 `GPDCScalarCodec` 子类编码的标量类型。错误信息会列举允许的标量类型。允许的内建标量（0.2.1+）：int/float/bool/str/datetime/date/time/decimal/tuple/None，外加任意标量注册的自定义类型；object 列与索引标签可持有其中任何一种 —— 包括 `decimal.Decimal`。

# API 参考

`gpdatacached` 是一个基于 Redis 的跨进程数据共享与缓存库。它对外暴露一套层级模型 —— **Domain → Namespace → Data Object** —— 底层由 Redis 支撑，因此任何能够访问同一 Redis 实例的 Python 进程都可以共享活跃的、可变的、带类型的数据结构。

本文档是 `gpdatacached.__init__` 所导出公共 API 的完整参考。

## 安装

```bash
pip install gpdatacached
```

可选扩展：

```bash
pip install "gpdatacached[pandas]"   # 支持 pandas DataFrame / Series
pip install -e ".[dev]"              # 安装开发依赖（pytest、ruff 等）
```

环境要求：Python ≥ 3.10，Redis ≥ 5.0。

---

## 核心概念

```
GPDCDomain  ──┬── GPDCNamespace ──┬── GPDCDataObject (标量)
              │                    └── GPDCDataObject (容器)
              └── GPDCNamespace ──── ...
```

| 概念 | 角色 |
|---|---|
| **`GPDCDomain`** | 配置根与 Redis 连接池，以名称标识。持有锁配置、GC 配置以及默认缓存策略。 |
| **`GPDCNamespace`** | Domain 内部的逻辑隔离空间，行为类似 `MutableMapping`（对象名 → 值）。**不支持**跨 namespace 的引用。 |
| **`GPDCDataObject`** | 一个具名、带类型、由 Redis 支撑的值。进一步派生出**标量**对象（单值）与**容器**对象（`list`/`dict`/`set`）。 |

**关键设计规则：**

- 读取标量（`ns["count"]`）会返回原生 Python 值（`int`、`str` …）。
- 读取容器（`ns["my_list"]`）会返回一个 `GPDCContainerObject`，因此你可以调用其变更方法（`append`、`pop` …）直接操作 Redis。
- 对象名只能使用字母、数字、`.` 和 `_`；形如 `__xxx__` 的名称被保留。
- 所有数据都存放在 Redis key 前缀 `gpdc:{domain}:{namespace}:...` 之下。

---

## 快速开始

```python
import gpdatacached
from gpdatacached import GPDCDomain, GPDCDomainConfig

# 1. 打开一个 domain（会写入/校验 Redis 中的元信息）。
config = GPDCDomainConfig(
    redis_host="127.0.0.1",
    redis_port=6379,
    redis_db=0,
    redis_password="secret",
)
domain = GPDCDomain("mydomain", config)

# 2. 获取或创建 namespace。
ns = domain.ensure_namespace("analytics")

# 3. 标量读取出来是原生 Python 值。
ns["counter"] = 0
ns["counter"] = 42
assert ns["counter"] == 42

# 4. 容器读取出来是活跃的、由 Redis 支撑的对象。
ns["tags"] = ["a", "b", "c"]
tags = ns["tags"]            # ListObject
tags.append("d")
tags.pop(0)
assert tags.value == ["b", "c", "d"]

# 5. 同一 namespace 内，嵌套容器按引用共享数据。
ns["matrix"] = [[1, 2], [3, 4]]
ns["alias"] = ns["matrix"]   # 绑定到同一 canonical owner 的别名

# 6. 生命周期控制。
ns["temp"] = "x"
obj = ns.get_object("temp")
obj.update_cache_mode_and_ttl("ttl", 60)   # 切换为 ttl，TTL 为 60 秒
obj.expire_in(120)                         # 调整 TTL（要求 cache_mode='ttl'）
obj.persist()                              # 切换回 permanent

domain.close()
```

---

## 核心类型

### `GPDCDomain`

```python
class GPDCDomain(name: str, config: GPDCDomainConfig)
```

配置根与 Redis 连接池。构造时会：

1. 使用 `config.redis_*` 字段连接 Redis。
2. 校验 Redis 中的全局 GPDC 协议版本。
3. 写入或校验 domain 元信息（遗留字段为一次性写入；锁字段为“写入或校验”模式 —— 不一致时抛出 `GPDCLockConfigInconsistentError`）。
4. 创建 `config.namespaces` 中声明的预定义 namespace。
5. 若 `config.gc_enabled` 为 `True`，启动后台 GC 线程。

支持上下文管理器协议，`__del__` 中也带有兜底清理 —— 但请始终优先显式调用 `close()`。

**属性**

| 属性 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | Domain 名称。 |
| `redis` | `redis.Redis` | 底层 Redis 客户端（`close()` 后访问会抛 `RuntimeError`）。 |

**方法**

| 方法 | 说明 |
|---|---|
| `ensure_namespace(name)` | 按名称获取 namespace，不存在则创建。返回 `GPDCNamespace`。 |
| `get_namespace(name)` | 等价于 `domain[name]`。 |
| `get_default_cache_mode()` | 当前 domain 的生效 `default_cache_mode`。 |
| `get_default_ttl_seconds()` | 当前 domain 的生效 `default_ttl_seconds`。 |
| `get_default_lock_ttl_seconds()` | 生效的 `default_lock_ttl_seconds`。 |
| `get_default_lock_acquire_timeout_seconds()` | 生效的 `default_lock_acquire_timeout_seconds`。 |
| `get_gc_lock_ttl_seconds()` | 生效的 `gc_lock_ttl_seconds`。 |
| `get_gc_lock_wait_seconds()` | 生效的 `gc_lock_wait_seconds`。 |
| `run_gc(namespace=None)` | 手动触发垃圾回收（指定单个 namespace 或全部）；返回回收的对象数量。 |
| `delete_namespace(name)` | 删除 namespace 及其名下的所有 key。不存在则抛 `KeyError`。 |
| `destroy()` | 删除所有 namespace、domain 元信息，并关闭连接。 |
| `close()` | 停止 GC 线程，关闭 Redis 连接。幂等。 |

**支持的协议方法**

- `name in domain` → namespace `name` 已注册时为 `True`。
- `for ns_name in domain` → 遍历 namespace 名称。
- `len(domain)` → namespace 数量。
- `domain[name]` → 获取 `GPDCNamespace`（不存在则抛 `KeyError`）。

---

### `GPDCDomainConfig`

```python
class GPDCDomainConfig(**fields)   # 继承自 gpconfig.GPConfig / pydantic BaseModel
```

`GPDCDomain` 的配置。所有字段都会被校验；其中锁相关字段必须与打开同一 domain 的每个进程保持一致。

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `redis_host` | `str` | `"localhost"` | Redis 主机。 |
| `redis_port` | `int` | `6379` | Redis 端口。 |
| `redis_db` | `int` | `0` | Redis 逻辑库编号。 |
| `redis_password` | `str` | `""` | Redis 密码（空字符串 = 不鉴权）。 |
| `default_cache_mode` | `str` | `"permanent"` | 取值：`"permanent"`、`"ttl"`、`"sliding"`。 |
| `default_ttl_seconds` | `int` | `0` | 默认 TTL；当模式为 `"ttl"`/`"sliding"` 时必须 `> 0`。 |
| `gc_enabled` | `bool` | `False` | 是否启动后台 GC 线程。 |
| `gc_interval_seconds` | `int` | `300` | 后台 GC 周期间隔（秒）。 |
| `namespaces` | `dict[str, GPDCNamespaceConfig]` | `{}` | 需要预创建的 namespace。 |
| `default_lock_ttl_seconds` | `int` | `30` | 锁 key 的 TTL（必须 `≥ 1`）。 |
| `default_lock_acquire_timeout_seconds` | `int` | `30` | `obj.lock()` 的阻塞超时（必须 `≥ 0`）。 |
| `gc_lock_ttl_seconds` | `int` | `30` | GC 获取的锁的 TTL（必须 `≥ 1`）。 |
| `gc_lock_wait_seconds` | `int` | `5` | GC 对单个对象的 try-lock 超时（必须 `≥ 0`）。 |

👉 这四个字段必须与打开同一 domain 的每个进程保持一致。每个字段的含义、锁带来的代价以及与 GC 的契约，详见[多进程加锁](./locking.md)。

---

### `GPDCNamespace`

```python
class GPDCNamespace(name, domain, *, config=None)   # MutableMapping
```

`GPDCDomain` 内部的逻辑隔离空间，完整实现了由 Redis 支撑的 `MutableMapping` 协议。

**命名规则：** 名称只能使用字母、数字、`.` 和 `_`；形如 `__xxx__` 的名称被保留。匿名对象（内部名称以 `~` 开头）不会出现在公开的映射 API 中。常见的**被拒绝**字符（不在允许清单内；且 `:` 还会与 Redis key 分隔符冲突）：连字符 `-`、空格、`:` —— 请改用 `_` 或 `.`。

**属性**

| 属性 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | Namespace 名称。 |
| `domain` | `GPDCDomain` | 所属 domain。 |

**映射语义**

| 操作 | 行为 |
|---|---|
| `ns[key] = value` | 创建/覆盖一个绑定。标量存入其值；容器则被存储并以引用方式重新绑定。 |
| `ns[key]` | 标量返回**原生 Python 值**；容器返回**`GPDCContainerObject`**。 |
| `del ns[key]` | 删除该绑定。若对象仍被引用，则抛出 `CanonicalObjectReferencedError`。 |
| `key in ns` | 存在具名绑定时返回 `True`（匿名绑定被排除）。 |
| `iter(ns)` | 通过 `SCAN` 遍历具名对象（匿名绑定和失效别名被排除）。 |
| `len(ns)` | 具名对象的数量。 |

**方法**

| 方法 | 说明 |
|---|---|
| `get_object(key)` | 返回一个 `GPDCDataObject`（始终返回对象本身，而非原生值）。 |
| `ensure_object(key, default)` | 获取 `key` 对应的对象，不存在则以 `default` 创建。 |
| `clear()` | 批量删除 namespace 下的所有对象（保留 namespace 的 meta key）。 |
| `destroy()` | 从所属 domain 中删除该 namespace。 |

**跨 namespace 规则：** 所有容器引用都必须指向同一 namespace 内的对象。这一约束保证了 `clear()` 可以安全地批量删除。

---

### `GPDCNamespaceConfig`

```python
class GPDCNamespaceConfig(**fields)   # 继承自 gpconfig.GPConfig / pydantic BaseModel
```

针对单个 namespace 的缓存策略覆盖。仅在 namespace 首次在 Redis 中创建时生效；已存在的 namespace 会保留其已存储的元信息。

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `default_cache_mode` | `Optional[str]` | `None` | 取值：`"permanent"`、`"ttl"`、`"sliding"`；`None` 表示继承 domain 设置。 |
| `default_ttl_seconds` | `Optional[int]` | `None` | TTL 覆盖；`None` 表示继承 domain 设置。必须 `≥ 0`。 |

两个字段同时提供时，必须满足严格的 mode/TTL 一致性规则（`ttl`/`sliding` 要求 `ttl > 0`）。

---

### `reset_domain_config`

```python
def reset_domain_config(name: str, config: GPDCDomainConfig) -> None
```

从 domain 的 meta hash 中删除四个锁配置字段（`default_lock_ttl_seconds`、`default_lock_acquire_timeout_seconds`、`gc_lock_ttl_seconds`、`gc_lock_wait_seconds`）。字段不存在时为空操作。

**典型用法** —— 修改已存在 domain 的锁配置：

```python
reset_domain_config("mydomain", old_config)     # 必须在构造之前调用
domain = GPDCDomain("mydomain", new_config)     # 写入新的锁配置
```

只会读取 `config` 中的 Redis 连接字段；传入 config 上的锁字段会被忽略。

---

## 数据对象

### `GPDCDataObject`

```python
class GPDCDataObject(name, namespace, *, canonical_name=None)   # ABC
```

所有 GPDC 数据对象的抽象基类。其子类分为 [`GPDCScalarObject`](#gpdcscalarobject) 与 [`GPDCContainerObject`](#gpdccontainerobject)。

**身份属性**

| 属性 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | 该对象被获取时所用的绑定名。 |
| `namespace` | `GPDCNamespace` | 所属 namespace。 |
| `domain` | `GPDCDomain` | 所属 domain。 |
| `uri` | `str` | `gpdc://{domain}/{namespace}/{name}`。 |
| `canonical_name` | `str` | 实际持有原始数据所有者的名称（引用绑定时与 `name` 不同）。 |
| `canonical_uri` | `str` | canonical owner 的 URI。 |
| `is_reference` | `bool` | 当该对象是引用（别名）绑定时为 `True`。 |

**元信息属性**

| 属性 | 类型 | 说明 |
|---|---|---|
| `type` | `str` | GPDC 类型字符串，如 `"scalar:int"`、`"collection:list"`。 |
| `meta` | `ObjectMeta \| None` | 完整元信息记录；对象已消失时为 `None`。 |
| `cache_mode` | `str` | 生效的缓存模式；不存在则抛 `ObjectGoneError`。 |
| `ttl` | `int` | 声明的 TTL（秒），`0` 表示永久。 |
| `created_at` | `datetime \| None` | 创建时间戳。 |
| `updated_at` | `datetime \| None` | 最近更新时间戳。 |
| `value` | `object` | Python 值（抽象；由子类实现）。 |

**生命周期方法**

> 本节是 API 摘要。完整的模型 —— 策略来源、滑动刷新、所有权、匿名/引用对象的生命周期与陷阱 —— 见[生命周期管理](./lifecycle.md)。

| 方法 | 说明 |
|---|---|
| `expire_in(seconds)` | 设置新的 TTL。对象必须处于 `cache_mode="ttl"`。需要 ownership。 |
| `expire_at(when)` | 设置绝对过期时间。对象必须处于 `cache_mode="ttl"`。需要 ownership。若 `when` 已过去则立即删除对象（等价于 `del ns[name]`：清理 `obj:`/`raw:`/`ref:` 并级联删除匿名子树；若 owner 仍被引用则抛出 `CanonicalObjectReferencedError` —— 可先用 `has_referrers()` 检查）。 |
| `persist()` | 切换为 `cache_mode="permanent"` 且 `ttl=0`。需要 ownership。 |
| `update_cache_mode_and_ttl(cache_mode, ttl)` | 替换整个缓存策略。需要 ownership。 |
| `refresh_ttl()` | 将对象自身的 TTL 重新应用到其 key，并级联刷新其拥有的子树（用于滑动窗口刷新路径）。永久对象为空操作。 |
| `take_ownership()` | 将一个引用对象提升为其数据的 canonical owner。仅对具名引用有效。 |

**加锁方法**（可选使用，变更操作永远不会自动调用）

| 方法 | 说明 |
|---|---|
| `lock(*, ttl=None, timeout=None)` | 上下文管理器。对对象的 canonical name 获取排他锁（阻塞最多 `timeout` 秒）。超时抛出 `LockAcquisitionTimeout`。 |
| `try_lock(*, ttl=None)` | 非阻塞尝试加锁；返回 `HeldLock` 或 `None`。 |

👉 **锁是建议性的，且每次操作都会产生 Redis 开销。** 在广泛采用之前，请先阅读[多进程加锁](./locking.md) —— 其中说明了何时需要加锁、性能影响、推荐用法、避免死锁的顺序以及与 GC 的契约。

**其他**

- `obj == other` 按 `(domain, namespace, canonical_name)` 比较。
- `obj.referrers()`（仅容器）—— 引用该容器原始数据的名称集合。
- `obj.has_referrers()`（仅容器）—— 轻量布尔：是否有名称引用该容器的原始数据。用于判断对 owner 执行 `del`/`expire_at` 是否会抛出 `CanonicalObjectReferencedError`。
- `__hash__ = None` —— GPDC 对象不可哈希。

---

### `GPDCScalarObject`

```python
class GPDCScalarObject(name, namespace, *, canonical_name=None)   # GPDCDataObject
```

所有标量对象的基类。标量在自身的 meta hash 中存储单个编码后的值（不占用独立的 raw key）。

- `value` 的 getter 通过已注册的 codec 解码存储的值。
- `value` 的 setter 会校验 Python 类型是否与已注册 codec 匹配，更新元信息，并在 `cache_mode="sliding"` 时刷新 TTL。

内建子类：[`IntObject`](#intobject)、[`FloatObject`](#floatobject)、[`StrObject`](#strobject)、[`BoolObject`](#boolobject)、[`DateTimeObject`](#datetimeobject)、[`DateObject`](#dateobject)、[`TimeObject`](#timeobject)、[`NoneObject`](#noneobject)、[`TupleObject`](#tupleobject)。

---

### `GPDCContainerObject`

```python
class GPDCContainerObject(name, namespace, *, canonical_name=None)   # GPDCDataObject, ABC
```

所有容器对象的抽象基类。容器将元素存储在独立的 Redis key（`raw_key`）中，并在 Redis hash（`ref_key`）中跟踪引用者。子类会混入 `MutableSequence`、`MutableMapping` 或 `MutableSet`。

| 属性 | 说明 |
|---|---|
| `raw_key` | 存放容器元素数据的 Redis key（Redis list / hash / set）。 |
| `ref_key` | 记录 `{referrer_name: ref_count}` 的 Redis hash，用于引用计数与级联删除。 |

**共享 API**

| 方法 | 说明 |
|---|---|
| `value` | 属性：将整个容器解码为原生 Python 值。setter 永远抛错 —— 容器必须通过显式方法进行变更。 |
| `referrers()` | 引用该容器原始数据的对象名集合。 |
| `has_referrers()` | 轻量布尔检查：若任何对象引用了该容器的原始数据则返回 `True`。用于判断对 owner 执行 `del`/`expire_at` 是否会因存在未解除的引用而被阻塞（抛出 `CanonicalObjectReferencedError`）。 |
| `copy()` | 浅拷贝：返回解码后的 Python 值（共享匿名对象引用）。 |
| `deepcopy()` | 深拷贝：递归克隆所有匿名子对象，返回完全独立的 Python 值。 |

**引用语义。** 当你将一个 `GPDCContainerObject` 存到新名称下（`ns["alias"] = ns["original"]`），GPDC 会创建一个轻量的引用绑定 —— 两个名称共享同一个 canonical owner 及其原始数据。嵌套在容器内部的匿名容器通过引用计数跟踪，当最后一个引用者消失时会级联删除。

内建子类：[`ListObject`](#listobject)、[`DictObject`](#dictobject)、[`SetObject`](#setobject)。

---

## 内建标量类型

所有标量类都继承自 [`GPDCScalarObject`](#gpdcscalarobject)。通过 `ns[name]` 读取返回的是**原生 Python 值**，而非包装对象。

| 类 | GPDC 类型 | Python 类型 | 编码 |
|---|---|---|---|
| [`IntObject`](#intobject) | `scalar:int` | `int`（不含 `bool`） | `int:{value}` |
| [`FloatObject`](#floatobject) | `scalar:float` | `float` | `float:{value}` |
| [`StrObject`](#strobject) | `scalar:str` | `str` | `str:{value}` |
| [`BoolObject`](#boolobject) | `scalar:bool` | `bool` | `bool:True` / `bool:False` |
| [`DateTimeObject`](#datetimeobject) | `scalar:datetime` | `datetime.datetime` | `datetime:{isoformat}` |
| [`DateObject`](#dateobject) | `scalar:date` | `datetime.date`（不含 `datetime`） | `date:{isoformat}` |
| [`TimeObject`](#timeobject) | `scalar:time` | `datetime.time` | `time:{isoformat}` |
| [`NoneObject`](#noneobject) | `scalar:none` | `NoneType` | `none:None` |
| [`TupleObject`](#tupleobject) | `scalar:tuple` | `tuple`（仅标量元素） | `tuple:{json}` |

### `IntObject`

封装 `int`。`IntCodec` 会显式拒绝 `bool`（此处布尔值不被视为整数）。

### `FloatObject`

封装 `float`。

### `StrObject`

封装 `str`。

### `BoolObject`

封装 `bool`。

### `DateTimeObject`

封装 `datetime.datetime`。通过 `datetime.isoformat()` 编码。

### `DateObject`

封装 `datetime.date`。注意：尽管 `isinstance(datetime, date)` 为 `True`，`DateCodec` 仍会拒绝 `datetime.datetime` —— 日期时间请使用 `DateTimeObject`。

### `TimeObject`

封装 `datetime.time`。

### `NoneObject`

封装 `None`。用于将“值为 `None`”与“键不存在”区分开。

### `TupleObject`

一种**复合标量** —— 元组被存为单个 JSON 编码的 Redis 字符串（不占用独立的 raw key）。`TupleCodec` 会拒绝任何 `list`、`dict` 或 `set` 元素，只允许标量元素类型。这使得元组完全不受 GC 影响（它们永远不会产生嵌套引用）。

---

## 内建容器类型

所有容器类都继承自 [`GPDCContainerObject`](#gpdccontainerobject)。通过 `ns[name]` 读取返回的是**活跃的 `GPDCContainerObject`**，可直接调用变更方法操作 Redis。

### `ListObject`

```python
class ListObject(...)   # GPDCContainerObject, collections.abc.MutableSequence
```

GPDC 类型 `collection:list`，底层为 Redis list。

完整实现 `MutableSequence` 接口，另加：

- `append(value)`、`extend(values)`、`insert(index, value)`
- `pop(index=-1)`、`remove(value)`
- `clear()`、`reverse()`、`sort(*, key=None, reverse=False)`
- `count(value)`、`index(value, start=0, stop=None)`
- `deepcopy()`

不支持（会抛 `NotImplementedError`）：`+`、`*` 及其反向形式 —— 请使用 `list(obj) + other` 进行拼接，必要时再重新赋值。

`__setitem__` 和 `__delitem__` 不支持切片索引（请先转换为 Python list：`list(obj)[start:stop]`）。`__getitem__` 支持切片，返回解码后的 Python list。

### `DictObject`

```python
class DictObject(...)   # GPDCContainerObject, collections.abc.MutableMapping
```

GPDC 类型 `collection:dict`，底层为 Redis hash。

键可以是除容器和空元组之外的任意标量类型。完整实现 `MutableMapping` 接口，另加：

- `keys()`、`values()`、`items()` —— 每个都返回**冻结快照视图**；之后对 `DictObject` 的变更不会反映其中。
- `get(key, default=None)`、`setdefault(key, default=None)`
- `pop(key, default=…)`、`update(other=None, **kwargs)`
- `clear()`、`deepcopy()`

### `SetObject`

```python
class SetObject(...)   # GPDCContainerObject, collections.abc.MutableSet
```

GPDC 类型 `collection:set`，底层为 Redis set。

成员可以是任意可哈希标量（不含容器、不含空元组）。完整实现 `MutableSet` 接口，另加：

- `add(value)`、`discard(value)`、`remove(value)`、`pop()`、`clear()`
- 集合运算符：`|`、`&`、`-`、`^` 返回原生 Python `set`（不会产生 Redis 副作用）。
- 原地集合运算符：`|=`、`&=`、`-=`、`^=` 会修改 Redis set。
- `deepcopy()` 返回解码后的 `set`。

---

## 编解码器（Codec）

Codec 负责 Python 值与 Redis 存储表示之间的相互转换。注册到 [`GPDCTypeRegistry`](#_8) 的每种类型都必须提供一个 codec。

> 本节为 API 摘要。有关编写 codec 的完整指南 —— 标量与容器，含实战示例（`Color`、`Counter`）以及每个容器都必须调用的变更钩子 —— 见专门的[可扩展性](./extensibility.md)文档。

### `GPDCCodec`

```python
class GPDCCodec(ABC)
```

所有 codec 的抽象基类。

| 类方法 | 说明 |
|---|---|
| `encode_value(value) -> str` | 将 Python 值编码为存入 Redis 元信息的字符串。 |
| `decode_value(encoded) -> object` | 将存储的字符串解码回 Python 值。 |

### `GPDCScalarCodec`

```python
class GPDCScalarCodec(GPDCCodec)
```

标量 codec 的抽象基类。提供共享辅助方法：

| 类方法 | 说明 |
|---|---|
| `_parse_encoded(encoded) -> tuple[str, str]` | 将 `name:payload` 形式的编码值拆分为 `(type_name, raw)`。输入为空或不含冒号时抛 `ValueError`。 |

内建子类：`BoolCodec`、`DateTimeCodec`、`DateCodec`、`DictCodec` *（见下注）*、`FloatCodec`、`IntCodec`、`ListCodec` *（见下注）*、`NoneCodec`、`SetCodec` *（见下注）*、`StrCodec`、`TimeCodec`、`TupleCodec`。

> 容器的标量 codec 类（`ListCodec`、`DictCodec`、`SetCodec`）被再次导出仅为完整性 —— 它们实际上继承自 [`GPDCContainerCodec`](#gpdccontainercodec)。

### `GPDCContainerCodec`

```python
class GPDCContainerCodec(ABC)
```

容器 codec 的抽象基类。容器将元素存储在独立的 Redis key 中（而非内联在元信息里），因此其契约是原始数据的创建/读取/删除，而不是 encode/decode。

| 类方法 | 说明 |
|---|---|
| `create_raw(namespace, raw_key, value, owner_name, cache_mode, ttl)` | 为 `value` 创建原始 Redis 存储。 |
| `read_raw(namespace, raw_key) -> object` | 从原始 Redis 数据重建 Python 对象。 |
| `delete_raw(namespace, raw_key)` | 删除原始 Redis 存储。 |

---

## 类型注册表

### `GPDCTypeRegistry`

```python
class GPDCTypeRegistry   # 仅提供类方法的门面
```

线程安全的注册表，负责在 Python 类型 ↔ GPDC 类型字符串 ↔（`object_class`、`codec_class`）之间建立映射。所有内建类型在导入时通过 `gpdatacached.builtins.registry._register_builtin_types` 自动注册。

| 类方法 | 说明 |
|---|---|
| `register(*, python_type, gpdc_type, object_class, codec_class, priority=0)` | 注册（或替换）一个条目。`priority` 越高越优先匹配有歧义的 Python 类型（如 `bool` 优先于 `int`）。 |
| `lookup_by_python_type(value) -> TypeEntry` | 查找其 `python_type` 能匹配 `value` 的条目。不支持时抛 `TypeError`。 |
| `lookup_by_gpdc_type(gpdc_type) -> TypeEntry` | 按 GPDC 类型字符串查找条目。未知时抛 `ValueError`。 |

`TypeEntry` 暴露：`python_type`、`gpdc_type`、`object_class`、`codec_class`、`priority`，以及布尔属性 `is_container`。

> 完整的类型解析模型（优先级规则、子类陷阱）以及注册自定义标量与容器类型的端到端实战示例，见[可扩展性](./extensibility.md)。

注册自定义类型：

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

## Key 与 URI 工具函数

用于操作 GPDC 的 key/URI 方案的低层辅助函数。大多数应用不需要直接使用。

### `make_uri`

```python
def make_uri(domain: str, namespace: str, name: str) -> str
```

构造 GPDC URI：`gpdc://{domain}/{namespace}/{name}`。

### `uri_to_redis_key`

```python
def uri_to_redis_key(uri: str) -> str
```

将 `gpdc://{domain}/{namespace}/{name}` 转换为 Redis 对象 key `gpdc:{domain}:{namespace}:obj:{name}`。会校验每个路径分段；URI 格式错误或名称非法/被保留时抛出 `ValueError`。

### `validate_object_name`

```python
def validate_object_name(name: str) -> None
```

校验 `name` 仅包含字母、数字、`.` 或 `_`，且不是被保留的 `__xxx__` 名称。否则抛出 `ValueError`。（内部还存在等价的 domain 名称与 namespace 名称校验器。）

---

## 异常

所有公开异常都派生自一个私有的 `GPDCError(Exception)` 基类。请按需逐个捕获以获得精确的错误处理。

### `ObjectGoneError`

```python
class ObjectGoneError(GPDCError)
```

当 `GPDCDataObject` 的属性或方法尝试读取已从 Redis 消失的元信息（例如 TTL 到期或被其他进程删除）时抛出。携带 `.object_uri`。

### `CanonicalObjectReferencedError`

```python
class CanonicalObjectReferencedError(GPDCError)
```

当尝试删除或覆盖一个仍存在活跃引用者的 canonical 容器时抛出。携带 `.canonical_name`、`.canonical_uri` 以及 `.referrers`（相关的绑定名列表）。

### `CyclicReferenceError`

```python
class CyclicReferenceError(GPDCError)
```

在编码过程中检测到容器自引用（直接或间接）时抛出。携带 `.type_name`。请在存储前打破循环，或将共享容器单独存储后通过名称引用。

### `LockAcquisitionTimeout`

```python
class LockAcquisitionTimeout(GPDCLockError)
```

当 `obj.lock()` 在配置的 `timeout` 内未能获取到锁时抛出。携带 `.lock_key` 和 `.timeout`。详见[多进程加锁](./locking.md)。

### `GPDCLockConfigInconsistentError`

```python
class GPDCLockConfigInconsistentError(GPDCError)
```

在构造 `GPDCDomain` 时，如果显式 `GPDCDomainConfig` 中的锁配置与 Redis 中已存储的 domain meta 不一致则抛出。携带 `.domain`、`.conflicts`（`{field: (redis_value, config_value)}`）以及 `.legal_values`。请使用 [`reset_domain_config`](#reset_domain_config) 清除已存储的值后再重新打开 domain。锁配置模型详见[多进程加锁](./locking.md)。

---

## 缓存模式与生命周期

> 本节是 API 层面的简要概述。完整的生命周期模型 —— 策略来源、滑动窗口刷新机制、所有权、匿名与引用对象的生命周期，以及常见陷阱清单 —— 见专门的[生命周期管理](./lifecycle.md)文档。

每个对象在其元信息中携带一份 `(cache_mode, ttl)` 策略。

| `cache_mode` | TTL | 行为 |
|---|---|---|
| `"permanent"` | `0` | 不过期。会调用 Redis `PERSIST`。（默认。） |
| `"ttl"` | `> 0` | 固定过期。对象在 `ttl` 秒后过期一次。 |
| `"sliding"` | `> 0` | 滑动窗口。每次变更都会把 TTL 重置为 `ttl` 秒。 |

**校验规则：** `"ttl"` 和 `"sliding"` 严格要求 `ttl > 0`；`"permanent"` 接受任意 `ttl >= 0`（该值会被静默忽略 —— 实际应用 Redis `PERSIST`，因此永久对象永不过期）。

**策略来源。** 创建新对象时，策略按如下优先级确定：

1. namespace 级配置（`GPDCNamespaceConfig`），仅在该 namespace 为新建时生效。
2. domain 级默认值（`default_cache_mode`、`default_ttl_seconds`）。

已存在的对象会保留其存储的策略，直到你通过 `expire_in`、`expire_at`、`persist` 或 `update_cache_mode_and_ttl` 显式修改。

**传播。** 修改容器的策略会级联到其拥有的子树（由它创建的匿名子对象），在一次流水线中重写它们的元信息并重新应用 TTL。滑动窗口刷新同样会在每次变更时沿 owner 链向上传播。

**垃圾回收。** 当 `gc_enabled=True` 时，后台线程会按 `gc_interval_seconds` 的周期，为每个 namespace 运行 `run_gc_for_namespace`，回收已过期或成为孤儿的匿名对象。**若启用 GC，所有并发写入者都必须使用 `obj.lock()`** —— 完整契约（包括 GC 与写入者的规则，以及它给每次变更带来的性能代价）详见[多进程加锁](./locking.md)。

---

## 扩展（Extensions）

`gpdatacached` 在 `gpdatacached.extensions` 下提供了两个可选的内建扩展，分别为一组未被内建标量/容器注册表覆盖的 Python 类型添加支持。它们都是可选的 —— 需要显式注册后才会生效。

### Pydantic 支持

**模块：** `gpdatacached.extensions.pydantic_support`

让 [pydantic](https://docs.pydantic.dev/) 的 `BaseModel` 子类可以作为 GPDC 对象被缓存。提供两种存储模式：

- **标量模式** —— 整个模型被编码为单个 Redis 字符串（其字段的 JSON）。原子读写，不支持嵌套容器字段。
- **容器模式** —— 模型以 Redis hash 存储，每个字段一个槽位。支持字段级 get/set，以及嵌套容器字段（`list`/`dict`/`set`）。

本模块没有 `register()` 函数 —— 需要逐个注册每个模型（定义一个轻量的 `GPDCScalarObject` / `BaseModelContainerObject` 子类，并配对 `PydanticModelScalarCodec` / `PydanticModelContainerCodec`）。pydantic 本就是核心依赖，无需额外安装。

👉 **完整参考：** [`docs/pydantic-support.md`](./pydantic-support.md)

### Pandas 支持

**模块：** `gpdatacached.extensions.pandas_support`

让 pandas 的 `Series` 与 `DataFrame` 可以作为活跃的 `SeriesObject` / `DataFrameObject` 包装对象被缓存。面向*一个写入者缓存、多个读取者各取子集*的模式设计：提供在线选择性访问器（`.iloc`、`.loc`、`df[col]`）用于低成本的一次性读取，并以 `.value` 作为完整还原的最终退路。变更以追加（`extend`）为主，DataFrame 另支持结构性的列新增/替换/删除。

支持范围：`RangeIndex` 与带标签/MultiIndex 行索引；单层或 MultiIndex 列；`int`/`float`/`bool`/`str`/`object`/`datetime`/`category` dtype。

```python
from gpdatacached.extensions.pandas_support import register
register()   # 幂等 —— 同时注册 Series 与 DataFrame
```

pandas 是可选依赖 —— 使用 `pip install "gpdatacached[pandas]"` 安装。

👉 **完整参考：** [`docs/pandas-support.md`](./pandas-support.md)

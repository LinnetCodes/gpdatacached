# 未修复问题清单

日期: 2026-06-06
来源: 代码评审报告 (`review/2026-06-01-code-review-report.zh-CN.md`) 中尚未处理的 Low 优先级问题
前置文档: `dev_docs/2026-06-05-remaining-issues-deep-dive.md`（问题 A-F 已全部修复）

以下 9 个问题按严重程度排列，每个问题均包含原因分析、影响、复现步骤和建议处理方式。

---

## 1. GC 执行顺序导致孤 raw key 多存活一个周期（#7）

### 文件

`src/gpdatacached/gc.py:170-185`

### 原因

`run_gc_for_namespace` 按以下顺序执行：

```python
deleted = _gc_scan_obj_keys(r, obj_prefix, raw_to_obj)       # 第 1 步
deleted += _gc_scan_raw_keys(r, domain, namespace, ...)       # 第 2 步
deleted += _gc_scan_ref_keys(r, domain, namespace, ref_prefix) # 第 3 步
```

第 2 步扫描 raw key 时，通过 `r.smembers(ref_key)` 判断该 raw key 是否仍被引用。但此时第 3 步还没执行，ref-set 中可能仍有已失效的 stale member。这些 stale member 导致 ref-set 看起来非空，raw key 不会被删除。下一个 GC 周期第 3 步清理了 stale member 后，raw key 才会在再下一个周期的第 2 步中被发现为孤立并删除。

### 影响

- 孤立 raw key 至少多存活一个 GC 周期（默认 300 秒），浪费 Redis 内存
- 清理逻辑的正确性依赖多轮执行，增加推理复杂度

### 复现

```python
import redis
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.gc import run_gc_for_namespace

r = redis.Redis(host="127.0.0.1", port=6379, db=1, password="123456", decode_responses=True)
r.flushdb()

config = GPDCDomainConfig(
    redis_host="127.0.0.1", redis_port=6379, redis_db=1, redis_password="123456",
)
domain = GPDCDomain("demo", config)
ns = domain.get_namespace("ns1")

# 创建容器并获取匿名子对象引用
ns["lst"] = [{"a": 1}, {"b": 2}]
lst = ns["lst"]

# 删除父容器，触发 cascade 删除
del ns["lst"]

# 此时一些 raw key 可能仍有 stale ref-set member

# 第一轮 GC
deleted1 = run_gc_for_namespace(r, "demo", "ns1")
print(f"Round 1 deleted: {deleted1}")

# 检查是否还有残留 raw key
raw_prefix = "gpdc:demo:ns1:raw:"
remaining = list(r.scan_iter(match=f"{raw_prefix}*"))
print(f"Remaining raw keys after round 1: {remaining}")
# 可能有残留

# 第二轮 GC 才清理干净
deleted2 = run_gc_for_namespace(r, "demo", "ns1")
remaining2 = list(r.scan_iter(match=f"{raw_prefix}*"))
print(f"Remaining raw keys after round 2: {remaining2}")
# 第二轮才清理干净

domain.close()
r.flushdb()
```

### 建议处理方式

将第 3 步（`_gc_scan_ref_keys`）移到第 2 步（`_gc_scan_raw_keys`）之前执行：

```python
def run_gc_for_namespace(r, domain, namespace):
    ...
    deleted = _gc_scan_obj_keys(r, obj_prefix, raw_to_obj)
    deleted += _gc_scan_ref_keys(r, domain, namespace, ref_prefix)  # 先清 stale ref
    deleted += _gc_scan_raw_keys(r, domain, namespace, raw_to_obj)  # 再扫 raw key
    return deleted
```

或者让 `_gc_scan_raw_keys` 在检查 referrers 时自行验证每个 referrer 是否真实存在（当前已部分实现，会检查 `r.exists(check_obj_key)`），这样就不依赖执行顺序。需确认当前实现是否已足够。

---

## 2. 失效对象句柄读路径返回 sentinel 而非异常（#8）

### 文件

`src/gpdatacached/objects.py:92-96, 179-182, 193-196`

### 原因

只读属性 `cache_mode`、`ttl`、`created_at`、`updated_at` 在对象元数据已不存在时返回 sentinel 值（`""`、`0`、`None`），而不是抛 `ObjectGoneError`：

```python
@property
def cache_mode(self) -> str:
    meta = self.meta
    return meta.cache_mode if meta else ""    # ← 对象已删除，返回空字符串

@property
def ttl(self) -> int:
    meta = self.meta
    return meta.ttl if meta else 0            # ← 对象已删除，返回 0

@property
def created_at(self) -> datetime.datetime | None:
    meta = self.meta
    return meta.created_at if meta else None  # ← 对象已删除，返回 None
```

对比写路径（`_load_lifecycle_context`、`take_ownership` 等）在 meta 为 None 时抛 `ObjectGoneError`，读路径的行为不一致。

### 影响

- 调用方无法区分"对象已删除"和"cache_mode 为空/ttl 为 0"
- API 语义不统一，增加使用者的认知负担
- `ttl` 返回 `0` 时，调用方可能误以为对象是 permanent 模式，实际对象已不存在

### 复现

```python
import redis
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.exceptions import ObjectGoneError

r = redis.Redis(host="127.0.0.1", port=6379, db=1, password="123456", decode_responses=True)
r.flushdb()

config = GPDCDomainConfig(
    redis_host="127.0.0.1", redis_port=6379, redis_db=1, redis_password="123456",
)
domain = GPDCDomain("demo", config)
ns = domain.get_namespace("ns1")

# 创建并删除对象
ns["x"] = 42
obj = ns.get_object("x")
del ns["x"]

# 读路径返回 sentinel — 无法区分"已删除"和"值为空"
print(f"cache_mode: {obj.cache_mode!r}")  # '' — 看起来像空字符串，实际对象已不存在
print(f"ttl: {obj.ttl!r}")                # 0  — 看起来像 permanent，实际对象已不存在
print(f"created_at: {obj.created_at!r}")  # None

# 写路径正确抛异常
try:
    obj.cache_mode = "ttl"
except ObjectGoneError:
    print("Write path correctly raises ObjectGoneError")

domain.close()
r.flushdb()
```

### 建议处理方式

在读属性中检查 meta 是否为 None，如果是则抛 `ObjectGoneError`：

```python
@property
def cache_mode(self) -> str:
    meta = self.meta
    if meta is None:
        raise ObjectGoneError(self.canonical_uri)
    return meta.cache_mode
```

对所有只读属性（`cache_mode`、`ttl`、`created_at`、`updated_at`）做同样处理。需要评估是否有现有代码依赖 sentinel 行为。

---

## 3. DictObject 的 keys()/values()/items() 返回冻结快照而非 live view（#13）

### 文件

`src/gpdatacached/container.py:456-482`

### 原因

`keys()`、`values()`、`items()` 均先从 Redis 读取全部数据到临时 `dict`，然后返回该临时 `dict` 的 view：

```python
def keys(self) -> collections.abc.KeysView:
    decoded = [decode_key(k) for k in self._redis().hkeys(self.raw_key)]
    return dict.fromkeys(decoded).keys()  # ← 临时 dict 的 view

def values(self) -> collections.abc.ValuesView:
    return self.value.values()  # ← self.value 是完整读取后的临时 dict

def items(self) -> collections.abc.ItemsView:
    return self.value.items()   # ← 同上
```

Python 标准 `dict.keys()`/`values()`/`items()` 返回的是 live view — 反映后续对 dict 的修改。但这里的返回值是快照，后续对 DictObject 的修改不会反映在已返回的 view 中。

### 影响

- 与 Python 标准 `dict` 行为不一致，违反最小惊讶原则
- 调用方可能基于 live view 假设编写代码，导致难以发现的 bug
- 方法签名（返回 `KeysView`/`ValuesView`/`ItemsView`）暗示是 live view

### 复现

```python
import redis
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain

r = redis.Redis(host="127.0.0.1", port=6379, db=1, password="123456", decode_responses=True)
r.flushdb()

config = GPDCDomainConfig(
    redis_host="127.0.0.1", redis_port=6379, redis_db=1, redis_password="123456",
)
domain = GPDCDomain("demo", config)
ns = domain.get_namespace("ns1")

ns["d"] = {"a": 1, "b": 2}
d = ns["d"]

# 标准 dict 的 keys() 是 live view
regular_dict = {"a": 1, "b": 2}
keys = regular_dict.keys()
regular_dict["c"] = 3
print(f"Standard dict keys after mutation: {list(keys)}")  # ['a', 'b', 'c'] ← live

# DictObject 的 keys() 是快照
d_keys = d.keys()
d["c"] = 3
print(f"DictObject keys after mutation: {list(d_keys)}")   # ['a', 'b'] ← 快照，不含 'c'

domain.close()
r.flushdb()
```

### 建议处理方式

**方案 A（推荐）**：删除这三个方法 override，让 `MutableMapping` 的默认实现生效。默认实现基于 `__iter__` + `__getitem__`，天然是 live view（每次访问都查 Redis）。缺点是每次迭代都有 Redis 调用。

**方案 B**：保留快照行为，但将方法重命名为 `snapshot_keys()`/`snapshot_values()`/`snapshot_items()`，在 docstring 中明确说明返回的是快照。

---

## 4. Extra metadata 解码有损且含糊（#14）

### 文件

`src/gpdatacached/meta.py:18-33`

### 原因

`_decode_extra_value` 用启发式方式解码：如果字符串包含 `:`，则取 `:` 前半部分作为类型名尝试解码：

```python
def _decode_extra_value(encoded: str) -> Any:
    if not encoded or ":" not in encoded:
        return encoded
    colon_idx = encoded.index(":")
    type_name = encoded[:colon_idx]
    gpdc_type = f"scalar:{type_name}"
    try:
        entry = GPDCTypeRegistry.lookup_by_gpdc_type(gpdc_type)
    except ValueError:
        return encoded  # ← 未知类型，返回原字符串
    try:
        return entry.codec_class.decode_value(encoded)
    except ValueError:
        return encoded  # ← 解码失败，返回原字符串
```

问题：
1. 普通字符串如 `"int:1"` 会被当作 `scalar:int` 类型解码为整数 `1`，而非保留为字符串
2. 用户存入的字符串 `"hello:world"` 会尝试按 `scalar:hello` 解码（失败后返回原值，但走了一条不必要的解码路径）
3. 解码失败时静默返回原字符串，调用方无法知道发生了什么

### 影响

- extra metadata 的 round-trip 不保证类型一致性：存入 `str`，读出可能是 `int`
- 看起来像类型编码前缀的普通字符串会被错误解码
- 静默失败增加了调试难度

### 复现

```python
from gpdatacached.meta import ObjectMeta

# 存入一个看起来像类型编码的字符串
meta = ObjectMeta(type="scalar:int", value="int:42", extra={"label": "int:1"})
data = meta.to_redis_mapping()
print(f"Stored: {data['extra:label']}")  # "int:1"

# 读回来 — label 被解码成了整数 1
loaded = ObjectMeta.from_redis(data)
print(f"Loaded extra label: {loaded.extra['label']!r}")  # 1 (int) — 不是 "int:1" (str)
print(f"Type changed: {type(loaded.extra['label']).__name__}")  # int

# 存入含冒号的普通字符串
meta2 = ObjectMeta(type="scalar:str", value="str:hello", extra={"path": "namespace:key"})
data2 = meta2.to_redis_mapping()
loaded2 = ObjectMeta.from_redis(data2)
print(f"Path: {loaded2.extra['path']!r}")  # 尝试按 "scalar:namespace" 解码 → 失败 → 返回原字符串
```

### 建议处理方式

给 typed extra 加显式 envelope，使编码值与普通字符串可区分：

```python
# 编码时加前缀
def _encode_extra_value(value: Any) -> str:
    ...
    encoded = entry.codec_class.encode_value(value)
    return f"__gpdc__:{encoded}"  # 显式标记为编码值

# 解码时检查前缀
def _decode_extra_value(raw: str) -> Any:
    if raw.startswith("__gpdc__:"):
        encoded = raw[len("__gpdc__:"):]
        ...  # 正常解码流程
    return raw  # 无前缀，原样返回
```

---

## 5. GPDCScalarObject.value 将损坏状态伪装为 None（#15）

### 文件

`src/gpdatacached/scalar.py:21-30`

### 原因

`value` getter 在两种情况下返回 `None`：

```python
@property
def value(self) -> object:
    key = self._canonical_redis_key()
    meta = ObjectMeta.load(self._redis(), key)
    if meta is None or not meta.value:  # ← meta 不存在 或 value 为空
        return None
    ...
```

1. `meta is None`：对象已被删除
2. `not meta.value`：meta 存在但 value 字段为空（`""`），这是数据损坏

对于 `IntObject`、`StrObject` 等非 None 类型，返回 `None` 意味着损坏被静默吞掉。只有 `NoneObject` 返回 `None` 是合法的。调用方无法区分"对象不存在"、"数据损坏"和"合法的 None 值"。

### 影响

- 损坏的 `int`、`str` 等对象被误看成合法的 `None` 值
- 对象已删除也返回 `None`，不抛异常
- `NoneObject` 与其他损坏对象行为相同，无法区分

### 复现

```python
import redis
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain

r = redis.Redis(host="127.0.0.1", port=6379, db=1, password="123456", decode_responses=True)
r.flushdb()

config = GPDCDomainConfig(
    redis_host="127.0.0.1", redis_port=6379, redis_db=1, redis_password="123456",
)
domain = GPDCDomain("demo", config)
ns = domain.get_namespace("ns1")

# 场景 1：正常 int
ns["count"] = 42
print(ns["count"])  # 42

# 场景 2：模拟数据损坏 — 清空 value 字段
obj_key = "gpdc:demo:ns1:obj:count"
r.hset(obj_key, "value", "")  # 损坏：value 清空

obj = ns.get_object("count")
print(f"Corrupted int value: {obj.value!r}")  # None — 看起来像合法的 NoneObject

# 场景 3：对象已删除
del ns["count"]
print(f"Deleted int value: {obj.value!r}")  # None — 与损坏行为相同

# 场景 4：真正的 NoneObject
ns["nothing"] = None
print(f"Real NoneObject value: {ns.get_object('nothing').value!r}")  # None — 三种情况无法区分

domain.close()
r.flushdb()
```

### 建议处理方式

区分两种情况：

```python
@property
def value(self) -> object:
    key = self._canonical_redis_key()
    meta = ObjectMeta.load(self._redis(), key)
    if meta is None:
        raise ObjectGoneError(self.canonical_uri)  # 对象已删除 → 异常
    if self.TYPE == "scalar:none":
        return None  # NoneObject 的合法值
    if not meta.value:
        raise GPDCError(f"Corrupted scalar object {self.canonical_uri!r}: empty value field")
    ...  # 正常解码
```

---

## 6. ListObject 不支持的运算符抛 NotImplementedError 而非返回 NotImplemented（#20）

### 文件

`src/gpdatacached/container.py:366-380`

### 原因

`__add__`、`__radd__`、`__mul__`、`__rmul__` 抛出 `NotImplementedError`：

```python
def __add__(self, other):
    raise NotImplementedError("ListObject does not support +; use list(obj) + other instead.")

def __radd__(self, other):
    raise NotImplementedError("ListObject does not support +; use other + list(obj) instead.")

def __mul__(self, other):
    raise NotImplementedError("ListObject does not support *; use list(obj) * n instead.")

def __rmul__(self, other):
    raise NotImplementedError("ListObject does not support *; use n * list(obj) instead.")
```

Python 数据模型规定，二进制运算符在不支持给定操作数时应返回 `NotImplemented` 单例（不是 `NotImplementedError` 异常）。返回 `NotImplemented` 允许 Python 继续尝试另一个操作数的反射方法；抛出异常会立即中断运算链。

### 影响

- `1 + lst` 或 `"x" + lst` 等表达式无法触发 Python 的反射运算回退
- 与 Python 序列协议约定不一致
- 错误消息虽然有用，但异常类型不对

### 复现

```python
import redis
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain

r = redis.Redis(host="127.0.0.1", port=6379, db=1, password="123456", decode_responses=True)
r.flushdb()

config = GPDCDomainConfig(
    redis_host="127.0.0.1", redis_port=6379, redis_db=1, redis_password="123456",
)
domain = GPDCDomain("demo", config)
ns = domain.get_namespace("ns1")

ns["lst"] = [1, 2, 3]
lst = ns["lst"]

# Python 标准序列协议：返回 NotImplemented 时，Python 尝试 other.__radd__
# 但当前实现抛 NotImplementedError，直接中断
try:
    result = [0] + lst  # 应该尝试 list.__radd__(lst)，但因为 lst.__radd__ 抛异常
except NotImplementedError as e:
    print(f"NotImplementedError: {e}")  # 不是预期的行为

# 对比：标准 list 的行为
# 返回 NotImplemented 时 Python 会继续尝试另一个操作数的方法

domain.close()
r.flushdb()
```

### 建议处理方式

返回 `NotImplemented` 单例而非抛异常：

```python
def __add__(self, other):
    return NotImplemented

def __radd__(self, other):
    return NotImplemented

def __mul__(self, other):
    return NotImplemented

def __rmul__(self, other):
    return NotImplemented
```

如果需要保留错误消息，可以在 `__add__` 中检查 other 类型后决定返回 `NotImplemented` 还是抛 `TypeError`（但不建议 — 标准 Python 容器直接返回 `NotImplemented`）。

---

## 7. 类型注册表 TypeEntry 是可变共享状态（#23）

### 文件

`src/gpdatacached/types.py:16-22`

### 原因

`TypeEntry` 是普通 `@dataclass`，没有设置 `frozen=True`：

```python
@dataclass
class TypeEntry:
    python_type: type | tuple[type, ...]
    gpdc_type: str
    object_class: type[GPDCDataObject]
    codec_class: type
    priority: int
```

`GPDCTypeRegistry.lookup_by_gpdc_type()` 和 `lookup_by_python_type()` 返回的是内部活对象本身。持有引用的调用方可以在注册表锁外部直接修改字段值，绕过线程安全保护。

### 影响

- 在多线程环境下，任何持有 `TypeEntry` 引用的代码都能无锁修改 `priority`、`codec_class` 等字段
- 可能导致类型注册表状态不一致（修改后其他线程看到半更新状态）
- 注册表虽然有 `_lock` 保护写操作，但读操作返回的是可变引用，锁的保护不完整

### 复现

```python
from gpdatacached.types import GPDCTypeRegistry

# 正常 lookup 返回内部活对象
entry = GPDCTypeRegistry.lookup_by_gpdc_type("scalar:int")
print(f"Original priority: {entry.priority}")

# 在锁外部直接修改 — 绕过线程安全
entry.priority = -999
entry.gpdc_type = "scalar:hacked"

# 其他线程/代码看到被篡改的值
entry2 = GPDCTypeRegistry.lookup_by_gpdc_type("scalar:hacked")  # 能通过被篡改的 key 找到
print(f"Hacked type: {entry2.gpdc_type}, priority: {entry2.priority}")

# 恢复（避免影响其他测试）
entry2.gpdc_type = "scalar:int"
entry2.priority = 10
```

### 建议处理方式

将 `TypeEntry` 设为 frozen dataclass，lookup 返回时返回副本或直接返回 frozen 对象：

```python
@dataclass(frozen=True)
class TypeEntry:
    python_type: type | tuple[type, ...]
    gpdc_type: str
    object_class: type[GPDCDataObject]
    codec_class: type
    priority: int
```

设为 frozen 后，任何对字段的赋值都会抛 `FrozenInstanceError`，完全杜绝外部修改。

---

## 8. 顶层包 eager import 过重且扩展面过宽（#24/25）

### 文件

`src/gpdatacached/__init__.py:1-61`

### 原因

`__init__.py` 在顶层 eager import 了几乎所有实现模块：

```python
from .codec import GPDCCodec, GPDCContainerCodec, GPDCScalarCodec
from .config import GPDCDomainConfig, GPDCNamespaceConfig
from .exceptions import CanonicalObjectReferencedError, ObjectGoneError
from .domain import GPDCDomain
from .keys import make_uri, uri_to_redis_key, validate_object_name
from .namespace import GPDCNamespace
from .types import GPDCTypeRegistry
from .objects import GPDCDataObject
from .scalar import (...)
from .container import (...)
```

问题：
1. `import gpdatacached` 会触发所有子模块加载，包括类型注册、容器类定义等
2. `GPDCTypeRegistry` 被导出为公共 API — 这是一个可变全局状态，外部可以直接操作
3. `GPDCContainerCodec` 在 `__all__` 中声明但实际不在 `__init__.py` 中直接可见（它在 `__all__` 里，也从 `.codec` import 了，但扩展者可能找不到它）
4. 对于只需使用部分功能的场景，import 开销过大

### 影响

- `import gpdatacached` 的开销比必要的大
- 导出的可变全局注册表可能被意外修改
- 扩展边界模糊：哪些是公共 API、哪些是内部实现不清晰

### 复现

```python
# import 开销
import time
start = time.time()
import gpdatacached
print(f"Import time: {time.time() - start:.3f}s")

# 可变全局状态暴露
from gpdatacached import GPDCTypeRegistry
print(f"Registry has {len(GPDCTypeRegistry._by_gpdc_type)} types")
# 外部可以直接修改
GPDCTypeRegistry._by_gpdc_type["fake:type"] = None  # 可以直接修改内部状态

# GPDCContainerCodec 在 __all__ 中但不易发现
from gpdatacached import GPDCContainerCodec  # 可用，但不在常见 IDE 补全中
```

### 建议处理方式

**方案 A（保守）**：保持当前 eager import，但：
- 将 `GPDCTypeRegistry` 的内部数据结构（`_by_gpdc_type`、`_by_python_type`）改为私有或 protected
- 确保 `__all__` 中列出的所有名称在文档中说明

**方案 B（推荐）**：
- 常用类（`GPDCDomain`、`GPDCNamespace`、config、exceptions）保持 eager import
- `GPDCTypeRegistry` 和扩展相关类移到 `gpdatacached.ext` 子模块
- 使用 `__getattr__` 实现延迟加载

---

## 9. 密码配置以明文字符串存储（#27）

### 文件

`src/gpdatacached/config.py:53`

### 原因

`redis_password` 是普通 `str` 字段：

```python
class GPDCDomainConfig(GPConfig):
    ...
    redis_password: str = ""
```

当配置对象被 `repr()`、`str()`、日志打印或序列化时，密码会以明文形式暴露。

### 影响

- `print(config)` 或日志输出会暴露 Redis 密码
- 序列化配置到 JSON/文件时会包含明文密码
- 在调试场景中可能意外泄露凭证

### 复现

```python
from gpdatacached.config import GPDCDomainConfig

config = GPDCDomainConfig(
    redis_host="127.0.0.1",
    redis_port=6379,
    redis_db=1,
    redis_password="my_secret_password",
)

# repr 暴露密码
print(repr(config))
# GPDCDomainConfig(redis_host='127.0.0.1', redis_port=6379, redis_db=1, redis_password='my_secret_password', ...)

# str 暴露密码
print(config)

# model_dump 暴露密码
print(config.model_dump())
# {'redis_password': 'my_secret_password', ...}
```

### 建议处理方式

使用 Pydantic 的 `SecretStr`：

```python
from pydantic import SecretStr

class GPDCDomainConfig(GPConfig):
    ...
    redis_password: SecretStr = SecretStr("")

    def get_redis_password(self) -> str:
        return self.redis_password.get_secret_value()
```

需注意：`GPDomain` 和所有创建 Redis 连接的地方需要调用 `get_secret_value()` 获取实际密码。如果 `GPConfig` 基类或其他框架对 `SecretStr` 支持不好，也可用自定义 `__repr__` 方法对密码字段做遮蔽。

# Pydantic 支持

`gpdatacached.extensions.pydantic_support` 让 [pydantic](https://docs.pydantic.dev/) 的 `BaseModel` 子类可以作为 GPDC 对象被缓存。pydantic 本身就是 `gpdatacached` 的核心依赖，因此无需额外安装。

## 概述

pydantic 的 `BaseModel` 是一组具名字段。`pydantic_support` 提供两种缓存方式：

- **标量模式（scalar）** —— 整个模型被编码为单个 Redis 字符串（其字段的 JSON）。原子读写；**不允许嵌套容器字段**。
- **容器模式（container）** —— 模型以 Redis hash 存储，每个字段占一个槽位。支持**字段级 get/set**，以及**嵌套容器字段**（`list`/`dict`/`set`），它们会作为匿名 GPDC 子对象，按引用追踪。

两种模式都基于 codec 驱动：你需要逐个注册模型类，并为之配对一个轻量的对象包装类与对应的 codec。

---

## 两种存储模式

| 维度 | 标量模式 | 容器模式 |
|---|---|---|
| Redis 布局 | 对象 key 下单个字符串（JSON） | 一个 hash，每个模型字段一个槽 |
| 对象基类 | `GPDCScalarObject` | `BaseModelContainerObject` |
| Codec | `PydanticModelScalarCodec` | `PydanticModelContainerCodec` |
| GPDC 类型前缀 | `scalar:` | `collection:` |
| 字段级访问 | 否 —— 整个模型原子读写 | 是 —— 支持 `obj["field"] = value` |
| 嵌套容器字段（`list`/`dict`/`set`） | 编码时即拒绝 | 支持（以匿名子对象存储） |
| 存储成本 | 一个 Redis key | 一个 Redis key + 每个容器字段一个匿名子 key |

---

## 注册

与 `pandas_support` 不同，本模块**没有 `register()` 函数** —— 由于 GPDC 类型字符串和对象包装类都随模型而异，必须逐个注册。注册模式固定如下：

```python
from gpdatacached import GPDCTypeRegistry, GPDCScalarObject
from gpdatacached.extensions.pydantic_support import (
    BaseModelContainerObject,
    PydanticModelScalarCodec,
    PydanticModelContainerCodec,
)
from pydantic import BaseModel


class User(BaseModel):
    name: str
    age: int


# 1. 定义一个轻量的对象包装类。
class UserObject(GPDCScalarObject):
    TYPE = "scalar:User"      # 必须与下面的 gpdc_type 一致

# 2. 注册“模型 ↔ codec ↔ 对象类”三元组。
GPDCTypeRegistry.register(
    python_type=User,
    gpdc_type="scalar:User",  # 必须与 UserObject.TYPE 一致
    object_class=UserObject,
    codec_class=PydanticModelScalarCodec,
)
```

容器模式同理 —— 只需把 `GPDCScalarObject` 换成 `BaseModelContainerObject`，把 `PydanticModelScalarCodec` 换成 `PydanticModelContainerCodec`，并使用 `collection:` 前缀。

> GPDC 类型字符串对每个模型必须唯一。常见约定是 `scalar:{ClassName}` / `collection:{ClassName}`。

---

## 快速开始

```python
from pydantic import BaseModel
from gpdatacached import GPDCDomain, GPDCDomainConfig, GPDCTypeRegistry, GPDCScalarObject
from gpdatacached.extensions.pydantic_support import (
    BaseModelContainerObject,
    PydanticModelScalarCodec,
    PydanticModelContainerCodec,
)

# ── 定义两个模型 ────────────────────────────────────────────────
class User(BaseModel):           # 纯标量字段 → 标量模式
    name: str
    age: int

class Order(BaseModel):          # 含 list 字段 → 容器模式
    order_id: str
    items: list[str]

# ── 定义包装类并注册 ────────────────────────────────────────────
class UserObject(GPDCScalarObject):
    TYPE = "scalar:User"

class OrderObject(BaseModelContainerObject):
    TYPE = "collection:Order"

GPDCTypeRegistry.register(
    python_type=User, gpdc_type="scalar:User",
    object_class=UserObject, codec_class=PydanticModelScalarCodec,
)
GPDCTypeRegistry.register(
    python_type=Order, gpdc_type="collection:Order",
    object_class=OrderObject, codec_class=PydanticModelContainerCodec,
)

# ── 使用 ────────────────────────────────────────────────────────
config = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_password="secret")
domain = GPDCDomain(config, name="mydomain")
ns = domain.ensure_namespace("app")

# 标量模式：整个模型原子读写。
ns["user1"] = User(name="Alice", age=30)
assert ns["user1"].name == "Alice"          # 返回原生 User 实例

# 容器模式：字段级访问 + 嵌套容器。
ns["order1"] = Order(order_id="ORD-001", items=["a", "b"])
order = ns["order1"]                         # → OrderObject（活跃对象）
assert order["order_id"] == "ORD-001"
order["order_id"] = "ORD-002"                # 在 Redis 中修改单个字段
assert order.value.order_id == "ORD-002"

domain.close()
```

---

## 标量模式

将整个模型存储为单个 JSON 编码的 Redis 字符串。读取时返回一个新构造的模型实例。

**编码方式。** 每个字段都通过其自身已注册的标量 codec 编码，并汇总成一个 JSON 对象：

```json
{"name": "str:Alice", "age": "int:30"}
```

整个对象随后以 `{type_prefix}:{json}` 的形式存储，其中 `type_prefix` 是 GPDC 类型中 `scalar:` 之后的部分。

**字段类型限制。** 每个字段都必须能被某个 `GPDCScalarCodec` 子类编码（int / float / bool / str / datetime / date / time / tuple / None / 任意标量注册的自定义类型）。容器字段类型（`list`、`dict`、`set`、容器注册的 pydantic 模型）在编码时会抛出 `TypeError` —— 请改用容器模式。

### `PydanticModelScalarCodec`

```python
class PydanticModelScalarCodec(GPDCScalarCodec)
```

一个可被所有标量模式 pydantic 模型复用的 codec。它本身不通过构造参数感知具体模型，而是在解码时通过 GPDC 类型注册表解析目标模型类。

| 类方法 | 说明 |
|---|---|
| `encode_value(value) -> str` | 将一个 `BaseModel` 实例编码为 `{prefix}:{json}` 字符串。若 `value` 不是 `BaseModel`，或任一字段值为容器类型，则抛出 `TypeError`。 |
| `decode_value(encoded) -> Any` | 将字符串解码回模型实例。通过 `GPDCTypeRegistry.lookup_by_gpdc_type("scalar:" + type_name)` 查找模型类，用各字段自身的 codec 解码后，以 `model_class(**fields)` 构造实例。 |

---

## 容器模式

将模型以 Redis hash 存储，每个字段一个槽位。这带来了标量模式无法提供的两项能力：

1. **字段级访问** —— 无需重写整个模型即可读写单个字段。
2. **嵌套容器字段** —— `list` / `dict` / `set` 字段会作为匿名 GPDC 子对象存储，并按引用追踪，行为与内建容器完全一致。

### `PydanticModelContainerCodec`

```python
class PydanticModelContainerCodec(GPDCContainerCodec)
```

| 类方法 | 说明 |
|---|---|
| `create_raw(namespace, raw_key, value, owner_name, cache_mode, ttl)` | 通过 `encode_element` 编码 `BaseModel` 的每个字段（因此容器字段会成为匿名子对象），并以字段名为 key 写入 Redis hash。若 `value` 不是 `BaseModel` 则抛出 `TypeError`。 |
| `read_raw(namespace, raw_key) -> dict` | 将所有 hash 槽读回 `{field_name: python_value}` 字典（容器引用会被解码为活跃的 `GPDCContainerObject` 实例）。key 不存在时返回 `{}`。 |
| `delete_raw(namespace, raw_key)` | 删除原始 hash key。 |

> `read_raw` 返回的是 `dict`，而非模型实例 —— 包装类（`BaseModelContainerObject`）负责在你访问 `.value` 时用该字典构造模型。

### `BaseModelContainerObject`

```python
class BaseModelContainerObject(GPDCContainerObject, collections.abc.MutableMapping)
```

容器模式 pydantic 对象的基类。子类必须设置 `TYPE = "collection:{ModelName}"`。模型类在运行时从注册表解析，因此一个基类即可服务所有模型。

**针对模型字段的映射接口**

| 操作 | 行为 |
|---|---|
| `obj["field"]` | 读取单个字段。若存储的 hash 槽存在，返回存储的值。若槽位缺失（模型演进、外部操作、GC 边界情况），则回退到字段声明的 `default_factory`（调用）或 `default` —— 包括显式 `default=None`，此时正确返回 `None`。*必填*字段（无 `default`、无 `default_factory`）槽位缺失时抛出 `KeyError`。未知字段抛出 `KeyError`。 |
| `obj["field"] = value` | 写入单个字段。未知字段抛出 `KeyError`。会释放被覆盖值原先持有的引用，并应用滑动 TTL 语义。 |
| `del obj["field"]` | **被拒绝** —— 模型字段属于结构，不能删除。始终抛出 `TypeError`。 |
| `"field" in obj` | 当 `field` 是声明的模型字段时为 `True`。 |
| `iter(obj)` | 遍历声明的字段名。 |
| `len(obj)` | 声明字段的数量。 |

!!! tip "URI 路径遍历可作用于容器模式模型"

    由于 `BaseModelContainerObject` 是 `MutableMapping`，带有额外路径分段的 GPDC URI 会按字段名深入容器模式模型：`gpdc://d/ns/order_obj/order_id` 等价于 `obj["order_id"]`。如果某个字段的值本身是嵌套容器，还可以继续遍历（`gpdc://d/ns/order_obj/items/0`）。终止于**标量**字段的 URI 会抛出 `ObjectNotFoundByURIError` —— `get_object_by_uri` 返回数据对象，绝不返回值。

    标量模式的 pydantic 模型（即基于原子 JSON 的 `PydanticModelScalarCodec` / 普通 `GPDCScalarObject` 子类）**没有 `__getitem__`**，因此根对象之外的任何路径分段都会抛出 `ObjectNotFoundByURIError`。根对象本身（`gpdc://d/ns/order_obj`，无路径）在两种模式下都能正常解析。

**其他成员**

| 成员 | 说明 |
|---|---|
| `value`（属性） | 通过 `PydanticModelContainerCodec.read_raw` + `model_class(**fields)` 重构完整模型实例。setter 永远抛错 —— 请用 `__setitem__` 进行变更。 |
| `type`（属性） | 返回 `cls.TYPE`。 |
| `deepcopy()` | 递归深拷贝每个字段值，重构一个完全独立的模型实例。 |

**示例：嵌套容器字段**

```python
class Order(BaseModel):
    order_id: str
    items: list[str]

class OrderObject(BaseModelContainerObject):
    TYPE = "collection:Order"

GPDCTypeRegistry.register(
    python_type=Order, gpdc_type="collection:Order",
    object_class=OrderObject, codec_class=PydanticModelContainerCodec,
)

ns["order"] = Order(order_id="ORD-001", items=["a", "b", "c"])
order = ns["order"]

# items 字段是活跃的 ListObject —— 可直接在 Redis 中变更。
order["items"].append("d")
assert order["items"].value == ["a", "b", "c", "d"]
```

---

## 如何选择模式

| 当你的模型… | 选择 |
|---|---|
| 只含标量字段，且整体原子读写 | **标量模式** —— 成本更低（单个 Redis key）、原子、更简单。 |
| 含 `list` / `dict` / `set` 字段 | **容器模式**（标量模式会拒绝）。 |
| 需要在不重写整条记录的前提下做字段级变更 | **容器模式**。 |
| 体量较小，且总是作为整体变化 | **标量模式**。 |

两种模式都能与 GPDC 的其他部分组合：pydantic 模型可以作为 `DictObject` 中的值、`ListObject` 中的元素，或另一个容器模式模型的字段。

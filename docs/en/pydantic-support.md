# Pydantic Support

`gpdatacached.extensions.pydantic_support` makes [pydantic](https://docs.pydantic.dev/) `BaseModel` subclasses cacheable as GPDC objects. Pydantic is already a core dependency of `gpdatacached`, so no extra install is required.

## Overview

A pydantic `BaseModel` is a record of named fields. `pydantic_support` gives you two ways to cache one:

- **Scalar mode** — the whole model is encoded into a single Redis string (JSON of its fields). Atomic read/write; **no nested container fields allowed**.
- **Container mode** — the model is stored as a Redis hash with one slot per field. Supports **field-level get/set** and **nested container fields** (`list` / `dict` / `set`) which become anonymous GPDC children tracked by reference.

Both modes are codec-driven: you register one model class at a time, pairing it with a thin object-class wrapper and the appropriate codec.

---

## Two Storage Modes

| Aspect | Scalar mode | Container mode |
|---|---|---|
| Redis layout | One string (JSON) under the object key | One hash with one field per model field |
| Object base class | `GPDCScalarObject` | `BaseModelContainerObject` |
| Codec | `PydanticModelScalarCodec` | `PydanticModelContainerCodec` |
| GPDC type prefix | `scalar:` | `collection:` |
| Field-level access | No — atomic read/write of the whole model | Yes — `obj["field"] = value` |
| Nested container fields (`list`/`dict`/`set`) | Rejected at encode time | Supported (stored as anonymous children) |
| Storage cost | One Redis key | One Redis key + one anonymous child key per container field |

---

## Registration

Unlike `pandas_support`, there is **no `register()` function** — each model is registered individually because both the GPDC type string and the object-class wrapper are model-specific. The pattern is always:

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


# 1. Define a thin object-class wrapper.
class UserObject(GPDCScalarObject):
    TYPE = "scalar:User"      # must match the gpdc_type below

# 2. Register the model ↔ codec ↔ object class triple.
GPDCTypeRegistry.register(
    python_type=User,
    gpdc_type="scalar:User",  # must match UserObject.TYPE
    object_class=UserObject,
    codec_class=PydanticModelScalarCodec,
)
```

The same pattern applies to container mode — swap `GPDCScalarObject` for `BaseModelContainerObject`, `PydanticModelScalarCodec` for `PydanticModelContainerCodec`, and use a `collection:` prefix.

> The GPDC type string must be unique per model. A common convention is `scalar:{ClassName}` / `collection:{ClassName}`.

---

## Quick Start

```python
from pydantic import BaseModel
from gpdatacached import GPDCDomain, GPDCDomainConfig, GPDCTypeRegistry, GPDCScalarObject
from gpdatacached.extensions.pydantic_support import (
    BaseModelContainerObject,
    PydanticModelScalarCodec,
    PydanticModelContainerCodec,
)

# ── Define two models ────────────────────────────────────────────
class User(BaseModel):           # scalar-only fields → scalar mode
    name: str
    age: int

class Order(BaseModel):          # has a list field → container mode
    order_id: str
    items: list[str]

# ── Define wrappers + register ───────────────────────────────────
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

# ── Use ──────────────────────────────────────────────────────────
config = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_password="secret")
domain = GPDCDomain("mydomain", config)
ns = domain.ensure_namespace("app")

# Scalar mode: atomic read/write of the whole model.
ns["user1"] = User(name="Alice", age=30)
assert ns["user1"].name == "Alice"          # returns a native User instance

# Container mode: field-level access + nested containers.
ns["order1"] = Order(order_id="ORD-001", items=["a", "b"])
order = ns["order1"]                         # → OrderObject (live)
assert order["order_id"] == "ORD-001"
order["order_id"] = "ORD-002"                # mutate one field in Redis
assert order.value.order_id == "ORD-002"

domain.close()
```

---

## Scalar Mode

Stores the entire model as a single JSON-encoded Redis string. Reads return a freshly constructed model instance.

**Encoding.** Each field is encoded via its own registered scalar codec and collected into a JSON object:

```json
{"name": "str:Alice", "age": "int:30"}
```

The whole object is then stored as `{type_prefix}:{json}`, where `type_prefix` is the part of the GPDC type after `scalar:`.

**Field type restriction.** Every field must be encodable by a `GPDCScalarCodec` subclass (int / float / bool / str / datetime / date / time / tuple / None / any scalar-registered custom type). Container field types (`list`, `dict`, `set`, container-registered pydantic models) raise `TypeError` at encode time — use container mode instead.

### `PydanticModelScalarCodec`

```python
class PydanticModelScalarCodec(GPDCScalarCodec)
```

A single reusable codec for every scalar-mode pydantic model. It is **not** model-aware via construction; it resolves the target model class from the GPDC type registry at decode time.

| Classmethod | Description |
|---|---|
| `encode_value(value) -> str` | Encode a `BaseModel` instance into a `{prefix}:{json}` string. Raises `TypeError` if `value` is not a `BaseModel`, or if any field value is a container type. |
| `decode_value(encoded) -> Any` | Decode the string back into a model instance. Looks up the model class via `GPDCTypeRegistry.lookup_by_gpdc_type("scalar:" + type_name)`, decodes each field via its own codec, and constructs the model with `model_class(**fields)`. |

---

## Container Mode

Stores the model as a Redis hash with one slot per field. This unlocks two capabilities that scalar mode cannot offer:

1. **Field-level access** — get/set individual fields without rewriting the whole model.
2. **Nested container fields** — `list` / `dict` / `set` fields are stored as anonymous GPDC children and tracked by reference, exactly like the built-in containers.

### `PydanticModelContainerCodec`

```python
class PydanticModelContainerCodec(GPDCContainerCodec)
```

| Classmethod | Description |
|---|---|
| `create_raw(namespace, raw_key, value, owner_name, cache_mode, ttl)` | Encode each field of the `BaseModel` via `encode_element` (so container fields become anonymous children) and write them as a Redis hash keyed by field name. Raises `TypeError` if `value` is not a `BaseModel`. |
| `read_raw(namespace, raw_key) -> dict` | Read every hash field back into a `{field_name: python_value}` dict (decoding container refs into live `GPDCContainerObject` instances). Returns `{}` for a missing key. |
| `delete_raw(namespace, raw_key)` | Delete the raw hash key. |

> `read_raw` returns a `dict`, not a model instance — the wrapper class (`BaseModelContainerObject`) is responsible for constructing the model from that dict when you access `.value`.

### `BaseModelContainerObject`

```python
class BaseModelContainerObject(GPDCContainerObject, collections.abc.MutableMapping)
```

Base class for container-mode pydantic objects. Subclasses must set `TYPE = "collection:{ModelName}"`. The model class is resolved at runtime from the registry, so a single base class serves every model.

**Mapping interface over model fields**

| Operation | Behaviour |
|---|---|
| `obj["field"]` | Read one field. If the stored hash slot is present, returns the stored value. If the slot is missing (model evolution, external ops, GC edge cases), falls back to the field's declared `default_factory` (called) or `default` — including an explicit `default=None`, which correctly returns `None`. A *required* field (no `default`, no `default_factory`) with a missing slot raises `KeyError`. Unknown fields raise `KeyError`. |
| `obj["field"] = value` | Write one field. Unknown fields raise `KeyError`. Releases the previous ref held by the overwritten value and applies sliding-TTL semantics. |
| `del obj["field"]` | **Rejected** — model fields are structural and cannot be deleted. Always raises `TypeError`. |
| `"field" in obj` | `True` if `field` is a declared model field. |
| `iter(obj)` | Iterate the declared field names. |
| `len(obj)` | Number of declared fields. |

**Other members**

| Member | Description |
|---|---|
| `value` (property) | Reconstruct the full model instance via `PydanticModelContainerCodec.read_raw` + `model_class(**fields)`. The setter always raises — mutate via `__setitem__` instead. |
| `type` (property) | Returns `cls.TYPE`. |
| `deepcopy()` | Recursively deep-copy every field value and reconstruct a fully independent model instance. |

**Example: nested container field**

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

# The items field is a live ListObject — mutate it directly in Redis.
order["items"].append("d")
assert order["items"].value == ["a", "b", "c", "d"]
```

---

## Choosing a Mode

| If your model… | Use |
|---|---|
| Has only scalar fields and is read/written atomically | **Scalar mode** — cheaper (one Redis key), atomic, simpler. |
| Has `list` / `dict` / `set` fields | **Container mode** (scalar mode will reject it). |
| Needs field-level mutation without rewriting the whole record | **Container mode**. |
| Is small and changes as a whole | **Scalar mode**. |

Both modes compose with the rest of GPDC: a pydantic model can be a value in a `DictObject`, an element of a `ListObject`, or a field of another container-mode model.

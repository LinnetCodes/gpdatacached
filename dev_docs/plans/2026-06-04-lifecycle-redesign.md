# 生命周期管理重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 gpdatacached 的生命周期管理从多入口、`cache_mode="inherited"` 模型重构为基于 `meta.owner` 的统一模型，所有变更通过 `set_lifecycle()` 单一入口。

**Architecture:** 在 ObjectMeta 中新增 `owner: str` 字段，替代 `cache_mode="inherited"` 标记。删除 `effective_policy()` 回溯逻辑，子对象 meta 直接存储有效值。所有生命周期变更（cache_mode/ttl/expire_in/expire_at/persist/_touch）收敛到 `set_lifecycle()` 统一入口。新增 `take_ownership()` API 允许命名引用接管匿名对象的生命周期管理权。`_binding_owner_name()` 重命名为 `_binding_name()` 以和 `meta.owner` 职责分离。

**Tech Stack:** Python 3.12+, Pydantic v2, Redis, pytest

**Design Spec:** `dev_docs/2026-06-02-lifecycle-redesign.md`

**Commands:**
- Install: `./venv/bin/python -m pip install -e ".[dev]"`
- Tests: `./venv/bin/python -m pytest`
- Single test: `./venv/bin/python -m pytest tests/test_file.py::test_name -v`
- Lint: `./venv/bin/python -m ruff check src tests`
- Format: `./venv/bin/python -m ruff format src tests`

**Redis Test Config:** Host `127.0.0.1:6379`, DB `1`, Password `123456`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/gpdatacached/exceptions.py` | 新增 `LifecyclePermissionError` |
| `src/gpdatacached/meta.py` | ObjectMeta 增加 `owner` 字段，移除 `"inherited"` cache_mode，`from_redis`/`to_redis_mapping` 适配 |
| `src/gpdatacached/ttl.py` | 删除 `effective_policy()`，重写 `propagate_lifecycle()` 增加 owner 检查 + pipeline，新增 `_validate_policy()` |
| `src/gpdatacached/objects.py` | 新增 `set_lifecycle()`、`take_ownership()`；所有生命周期方法改用 `set_lifecycle()` + P10 权限检查；`meta.setter` 改用 `set_lifecycle()` |
| `src/gpdatacached/container.py` | `_binding_owner_name()` → `_binding_name()`；`_touch()` 重写支持 owner 链向上传播；`encode_element` 调用点改为传 policy；`__setitem__` 增加 meta.owner 覆盖行为判断 |
| `src/gpdatacached/namespace.py` | `_write_scalar_meta`/`_write_container_meta` 增加 owner；`__setitem__` 容器覆盖行为；`_get_object()` alias 容器覆盖 ref_key |
| `src/gpdatacached/types.py` | `encode_element`/`_create_anonymous_object` 签名变更（cache_mode/ttl → policy） |
| `src/gpdatacached/scalar.py` | value setter 中 sliding touch 改用 `set_lifecycle()` |

---

## Task 1: 新增 LifecyclePermissionError 异常

**Files:**
- Modify: `src/gpdatacached/exceptions.py`
- Test: `tests/test_exceptions.py`

这是所有后续 P10 权限检查的基础依赖。

- [ ] **Step 1: Write the failing test**

在 `tests/test_exceptions.py` 中追加测试：

```python
def test_lifecycle_permission_error_is_gpdc_error():
    from gpdatacached.exceptions import LifecyclePermissionError, GPDCError
    assert issubclass(LifecyclePermissionError, GPDCError)


def test_lifecycle_permission_error_message():
    from gpdatacached.exceptions import LifecyclePermissionError
    err = LifecyclePermissionError(canonical_name="~anon:123", owner_name="myobj")
    assert "~anon:123" in str(err)
    assert "myobj" in str(err)
    assert err.canonical_name == "~anon:123"
    assert err.owner_name == "myobj"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_exceptions.py::test_lifecycle_permission_error_is_gpdc_error tests/test_exceptions.py::test_lifecycle_permission_error_message -v`
Expected: FAIL — `ImportError: cannot import name 'LifecyclePermissionError'`

- [ ] **Step 3: Write minimal implementation**

在 `src/gpdatacached/exceptions.py` 中追加：

```python
class LifecyclePermissionError(GPDCError):
    """Raised when attempting to modify lifecycle of an object not owned by the caller."""

    def __init__(self, canonical_name: str, owner_name: str) -> None:
        self.canonical_name = canonical_name
        self.owner_name = owner_name
        super().__init__(
            f"Cannot modify lifecycle of {canonical_name!r}: "
            f"owned by {owner_name!r}. "
            f"Call take_ownership() first to gain lifecycle management rights."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_exceptions.py::test_lifecycle_permission_error_is_gpdc_error tests/test_exceptions.py::test_lifecycle_permission_error_message -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/exceptions.py tests/test_exceptions.py
git commit -m "feat: add LifecyclePermissionError exception"
```

---

## Task 2: ObjectMeta 增加 owner 字段，移除 "inherited" cache_mode

**Files:**
- Modify: `src/gpdatacached/meta.py`
- Test: `tests/test_meta.py`

这是核心模型变更，所有后续任务依赖此任务。

- [ ] **Step 1: Write the failing tests**

在 `tests/test_meta.py` 中追加测试：

```python
def test_meta_has_owner_field():
    from gpdatacached.meta import ObjectMeta
    meta = ObjectMeta(type="scalar:int", value="int:42", owner="myobj")
    assert meta.owner == "myobj"


def test_meta_owner_defaults_empty():
    from gpdatacached.meta import ObjectMeta
    meta = ObjectMeta(type="scalar:int", value="int:42")
    assert meta.owner == ""


def test_meta_cache_mode_rejects_inherited():
    import pydantic
    from gpdatacached.meta import ObjectMeta
    with pydantic.ValidationError:
        ObjectMeta(type="scalar:int", value="int:42", cache_mode="inherited")


def test_meta_is_inherited_removed():
    from gpdatacached.meta import ObjectMeta
    meta = ObjectMeta(type="scalar:int", value="int:42", cache_mode="permanent")
    assert not hasattr(meta, "is_inherited") or not callable(getattr(meta, "is_inherited", None))


def test_meta_from_redis_backward_compat_inherited():
    from gpdatacached.meta import ObjectMeta
    data = {"type": "collection:dict", "value": "gpdc:d:ns:raw:~anon:abc", "cache_mode": "inherited", "ttl": "0"}
    meta = ObjectMeta.from_redis(data)
    assert meta.cache_mode == "permanent"
    assert meta.ttl == 0
    assert meta.owner == ""


def test_meta_from_redis_reads_owner():
    from gpdatacached.meta import ObjectMeta
    data = {"type": "scalar:int", "value": "int:42", "cache_mode": "ttl", "ttl": "60", "owner": "myobj"}
    meta = ObjectMeta.from_redis(data)
    assert meta.owner == "myobj"
    assert meta.cache_mode == "ttl"
    assert meta.ttl == 60


def test_meta_to_redis_mapping_includes_owner():
    from gpdatacached.meta import ObjectMeta
    meta = ObjectMeta(type="scalar:int", value="int:42", cache_mode="permanent", owner="myobj")
    mapping = meta.to_redis_mapping()
    assert mapping["owner"] == "myobj"


def test_meta_to_redis_mapping_omits_empty_owner():
    from gpdatacached.meta import ObjectMeta
    meta = ObjectMeta(type="scalar:int", value="int:42", cache_mode="permanent")
    mapping = meta.to_redis_mapping()
    assert mapping.get("owner", "") == ""


def test_meta_with_updates_validates():
    import pydantic
    from gpdatacached.meta import ObjectMeta
    meta = ObjectMeta(type="scalar:int", value="int:42", owner="myobj")
    with pydantic.ValidationError:
        meta.with_updates(ttl=-5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_meta.py::test_meta_has_owner_field tests/test_meta.py::test_meta_cache_mode_rejects_inherited tests/test_meta.py::test_meta_from_redis_backward_compat_inherited tests/test_meta.py::test_meta_from_redis_reads_owner tests/test_meta.py::test_meta_to_redis_mapping_includes_owner tests/test_meta.py::test_meta_with_updates_validates -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

修改 `src/gpdatacached/meta.py`：

1. 将 `cache_mode` 的 `Literal` 类型改为 `Literal["permanent", "ttl", "sliding"]`（移除 `"inherited"`）
2. 在 `ttl` 字段后新增 `owner: str = ""`
3. 修改 `from_redis`：读取 `owner` 字段；处理 `cache_mode="inherited"` 向后兼容
4. 修改 `to_redis_mapping`：输出 `owner` 字段
5. 删除 `is_inherited()` 方法
6. 修改 `with_updates` 使用 `model_validate` 代替 `model_copy`

完整变更后的 `meta.py`：

```python
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def _encode_extra_value(value: Any) -> str:
    from .types import GPDCTypeRegistry

    entry = GPDCTypeRegistry.lookup_by_python_type(value)
    if entry.is_container:
        raise TypeError(
            f"Container type {type(value).__name__!r} is not supported in extra metadata"
        )
    return entry.codec_class.encode_value(value)


def _decode_extra_value(encoded: str) -> Any:
    if not encoded or ":" not in encoded:
        return encoded
    from .types import GPDCTypeRegistry

    colon_idx = encoded.index(":")
    type_name = encoded[:colon_idx]
    gpdc_type = f"scalar:{type_name}"
    try:
        entry = GPDCTypeRegistry.lookup_by_gpdc_type(gpdc_type)
    except ValueError:
        return encoded
    try:
        return entry.codec_class.decode_value(encoded)
    except ValueError:
        return encoded


class ObjectMeta(BaseModel):
    type: str
    value: str = ""
    cache_mode: Literal["permanent", "ttl", "sliding"] = "permanent"
    ttl: int = 0
    owner: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    item_count: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> ObjectMeta:
        if self.ttl < 0:
            raise ValueError("ttl must be >= 0")
        for v in self.extra.values():
            _encode_extra_value(v)
        return self

    @classmethod
    def from_redis(cls, data: dict) -> ObjectMeta:
        builtin: dict[str, str] = {}
        extra_raw: dict[str, str] = {}
        for k, v in data.items():
            if k.startswith("$"):
                extra_raw[k[1:]] = v
            else:
                builtin[k] = v
        extra = {k: _decode_extra_value(v) for k, v in extra_raw.items()}
        cache_mode = builtin.get("cache_mode") or "permanent"
        ttl_str = builtin.get("ttl", "")
        ttl = int(ttl_str) if ttl_str else 0
        if cache_mode == "inherited":
            cache_mode = "permanent"
            ttl = 0
        created_at_str = builtin.get("created_at")
        updated_at_str = builtin.get("updated_at")
        item_count_str = builtin.get("item_count")
        return cls(
            type=builtin.get("type", ""),
            value=builtin.get("value", ""),
            cache_mode=cache_mode,
            ttl=ttl,
            owner=builtin.get("owner", ""),
            created_at=datetime.fromisoformat(created_at_str) if created_at_str else None,
            updated_at=datetime.fromisoformat(updated_at_str) if updated_at_str else None,
            item_count=int(item_count_str) if item_count_str is not None else None,
            extra=extra,
        )

    def to_redis_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {
            "type": self.type,
            "value": self.value,
            "cache_mode": self.cache_mode,
            "ttl": str(self.ttl) if self.ttl else "",
        }
        if self.owner:
            mapping["owner"] = self.owner
        if self.created_at is not None:
            mapping["created_at"] = self.created_at.isoformat()
        if self.updated_at is not None:
            mapping["updated_at"] = self.updated_at.isoformat()
        if self.item_count is not None:
            mapping["item_count"] = str(self.item_count)
        for k, v in self.extra.items():
            mapping[f"${k}"] = _encode_extra_value(v)
        return mapping

    def is_collection(self) -> bool:
        return self.type.startswith("collection:")

    def declares_expiry(self) -> bool:
        return self.cache_mode in ("ttl", "sliding") and self.ttl > 0

    @classmethod
    def load(cls, r, obj_key: str) -> ObjectMeta | None:
        data = r.hgetall(obj_key)
        return cls.from_redis(data) if data else None

    def save(self, r, obj_key: str) -> None:
        r.hset(obj_key, mapping=self.to_redis_mapping())

    def with_updates(self, **kwargs) -> ObjectMeta:
        return self.model_validate(self.model_copy(update=kwargs))

    def touch(self, now: datetime, *, item_count: int | None = None) -> ObjectMeta:
        updates: dict = {"updated_at": now}
        if item_count is not None:
            updates["item_count"] = item_count
        return self.model_copy(update=updates)
```

- [ ] **Step 4: Run all meta tests to verify**

Run: `./venv/bin/python -m pytest tests/test_meta.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `./venv/bin/python -m pytest --tb=short 2>&1 | tail -20`
Expected: 可能有一些测试因 `is_inherited` 移除或 `cache_mode="inherited"` 不再合法而失败。记录失败测试，后续任务修复。关键确认：meta.py 本身逻辑正确。

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/meta.py tests/test_meta.py
git commit -m "feat: add owner field to ObjectMeta, remove inherited cache_mode"
```

---

## Task 3: 删除 effective_policy，重写 propagate_lifecycle

**Files:**
- Modify: `src/gpdatacached/ttl.py`
- Test: `tests/test_ttl_policy.py`

删除 `effective_policy()` 函数，重写 `propagate_lifecycle()` 增加 owner 检查和 pipeline 优化。

- [ ] **Step 1: Write the failing tests**

在 `tests/test_ttl_policy.py` 中追加测试：

```python
def test_effective_policy_removed():
    import gpdatacached.ttl as ttl_mod
    assert not hasattr(ttl_mod, "effective_policy")


def test_propagate_lifecycle_uses_owner_check(redis_client, clean_redis):
    from gpdatacached.meta import ObjectMeta
    from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
    from gpdatacached.ttl import propagate_lifecycle, TTLPolicy

    r = redis_client
    parent_name = "parent"
    child_owned = "~anon:owned"
    child_not_owned = "~anon:free"

    parent_key = make_object_key("d", "ns", parent_name)
    parent_raw = make_raw_key("d", "ns", parent_name)
    parent_ref = make_ref_key("d", "ns", parent_name)
    parent_meta = ObjectMeta(
        type="collection:list", value=parent_raw,
        cache_mode="ttl", ttl=60, owner="parent",
    )
    parent_meta.save(r, parent_key)
    r.rpush(parent_raw, f"ref:{make_object_key('d', 'ns', child_owned)}", f"ref:{make_object_key('d', 'ns', child_not_owned)}")

    owned_key = make_object_key("d", "ns", child_owned)
    owned_raw = make_raw_key("d", "ns", child_owned)
    owned_ref = make_ref_key("d", "ns", child_owned)
    owned_meta = ObjectMeta(
        type="collection:dict", value=owned_raw,
        cache_mode="ttl", ttl=30, owner="parent",
    )
    owned_meta.save(r, owned_key)

    not_owned_key = make_object_key("d", "ns", child_not_owned)
    not_owned_raw = make_raw_key("d", "ns", child_not_owned)
    not_owned_ref = make_ref_key("d", "ns", child_not_owned)
    not_owned_meta = ObjectMeta(
        type="collection:dict", value=not_owned_raw,
        cache_mode="ttl", ttl=30, owner="other_obj",
    )
    not_owned_meta.save(r, not_owned_key)

    policy = TTLPolicy(cache_mode="ttl", declared_ttl=120, expire_seconds=120)
    propagate_lifecycle(r, "d", "ns", parent_name, policy, set())

    updated_owned = ObjectMeta.load(r, owned_key)
    assert updated_owned.ttl == 120
    assert updated_owned.owner == "parent"

    updated_not_owned = ObjectMeta.load(r, not_owned_key)
    assert updated_not_owned.ttl == 30
    assert updated_not_owned.owner == "other_obj"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_ttl_policy.py::test_effective_policy_removed tests/test_ttl_policy.py::test_propagate_lifecycle_uses_owner_check -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

修改 `src/gpdatacached/ttl.py`：

1. 删除 `effective_policy()` 函数
2. 重写 `propagate_lifecycle()` 使用新签名 `(r, domain, namespace, parent_name, policy, visited)`
3. 增加 owner 检查（`child_meta.owner != parent_name` 时跳过）
4. 使用 pipeline 批量更新
5. 仅在有变化时更新
6. 新增 `_validate_policy()` 函数

完整变更后的 `ttl.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

from .keys import collect_ref_names_from_raw, make_object_key, make_ref_key
from .meta import ObjectMeta

if TYPE_CHECKING:
    import redis


class _LifecycleContext(NamedTuple):
    r: object
    key: str
    meta: ObjectMeta
    raw_key: str
    ref_key: str
    is_collection: bool


@dataclass(frozen=True)
class TTLPolicy:
    cache_mode: str
    declared_ttl: int
    expire_seconds: int | None


def _validate_policy(policy: TTLPolicy) -> None:
    if policy.cache_mode not in ("permanent", "ttl", "sliding"):
        raise ValueError(f"Invalid cache_mode: {policy.cache_mode!r}")
    if policy.declared_ttl < 0:
        raise ValueError(f"ttl must be >= 0: {policy.declared_ttl}")


def policy_from_defaults(cache_mode: str, ttl: int) -> TTLPolicy:
    if cache_mode not in ("permanent", "ttl", "sliding"):
        raise ValueError(f"Invalid cache_mode: {cache_mode!r}")
    expire_seconds = ttl if cache_mode in ("ttl", "sliding") and ttl > 0 else None
    return TTLPolicy(cache_mode=cache_mode, declared_ttl=ttl, expire_seconds=expire_seconds)


def policy_from_meta(meta: ObjectMeta) -> TTLPolicy:
    if meta.cache_mode in ("ttl", "sliding") and meta.ttl > 0:
        return TTLPolicy(cache_mode=meta.cache_mode, declared_ttl=meta.ttl, expire_seconds=meta.ttl)
    return TTLPolicy(cache_mode=meta.cache_mode, declared_ttl=meta.ttl, expire_seconds=None)


def policy_for_cache_mode_change(current: TTLPolicy, new_mode: str) -> TTLPolicy:
    if new_mode not in ("permanent", "ttl", "sliding"):
        raise ValueError(f"Invalid cache_mode: {new_mode!r}")
    if new_mode == "permanent":
        return TTLPolicy(cache_mode=new_mode, declared_ttl=current.declared_ttl, expire_seconds=None)
    if current.declared_ttl > 0:
        return TTLPolicy(cache_mode=new_mode, declared_ttl=current.declared_ttl, expire_seconds=current.declared_ttl)
    return TTLPolicy(cache_mode=new_mode, declared_ttl=current.declared_ttl, expire_seconds=None)


def policy_for_ttl_change(current: TTLPolicy, new_ttl: int) -> TTLPolicy:
    if current.cache_mode == "permanent":
        return TTLPolicy(cache_mode=current.cache_mode, declared_ttl=new_ttl, expire_seconds=None)
    if new_ttl > 0:
        return TTLPolicy(cache_mode=current.cache_mode, declared_ttl=new_ttl, expire_seconds=new_ttl)
    return TTLPolicy(cache_mode=current.cache_mode, declared_ttl=new_ttl, expire_seconds=None)


def policy_for_sliding_touch(current: TTLPolicy) -> TTLPolicy:
    if current.cache_mode == "sliding" and current.declared_ttl > 0:
        return TTLPolicy(
            cache_mode=current.cache_mode,
            declared_ttl=current.declared_ttl,
            expire_seconds=current.declared_ttl,
        )
    return current


def apply_policy(r: "redis.Redis", obj_key: str, policy: TTLPolicy) -> None:
    if policy.expire_seconds is None:
        r.persist(obj_key)
    else:
        r.expire(obj_key, policy.expire_seconds)


def apply_lifecycle(r, obj_key: str, raw_key: str, ref_key: str, is_collection: bool, policy) -> None:
    apply_policy(r, obj_key, policy)
    if is_collection:
        if raw_key:
            apply_policy(r, raw_key, policy)
        if ref_key:
            apply_policy(r, ref_key, policy)


def propagate_lifecycle(r, domain: str, namespace: str, parent_name: str, policy: TTLPolicy, visited: set[str]) -> None:
    parent_obj_key = make_object_key(domain, namespace, parent_name)
    parent_meta = ObjectMeta.load(r, parent_obj_key)
    if parent_meta is None or not parent_meta.is_collection():
        return

    child_names = collect_ref_names_from_raw(r, parent_meta.value, parent_meta.type)

    updates = []
    for child_name in child_names:
        if child_name in visited:
            continue
        visited.add(child_name)

        child_obj_key = make_object_key(domain, namespace, child_name)
        child_meta = ObjectMeta.load(r, child_obj_key)
        if child_meta is None or child_meta.owner != parent_name:
            continue

        current = policy_from_meta(child_meta)
        if (current.cache_mode == policy.cache_mode
                and current.expire_seconds == policy.expire_seconds):
            continue

        updates.append((child_name, child_obj_key, child_meta))

    if updates:
        pipe = r.pipeline(transaction=False)
        now = datetime.now(UTC)
        for child_name, child_obj_key, child_meta in updates:
            updated = child_meta.with_updates(
                cache_mode=policy.cache_mode,
                ttl=policy.declared_ttl,
                updated_at=now,
            )
            updated.save(pipe, child_obj_key)

            child_raw_key = child_meta.value if child_meta.is_collection() else ""
            child_ref_key = make_ref_key(domain, namespace, child_name)
            apply_lifecycle(pipe, child_obj_key, child_raw_key, child_ref_key,
                            child_meta.is_collection(), policy)
        pipe.execute()

    for child_name, _, _ in updates:
        propagate_lifecycle(r, domain, namespace, child_name, policy, visited)
```

- [ ] **Step 4: Run tests to verify**

Run: `./venv/bin/python -m pytest tests/test_ttl_policy.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to check regressions**

Run: `./venv/bin/python -m pytest --tb=short 2>&1 | tail -30`
Expected: `effective_policy` 的调用方会失败。这些失败将在 Task 4-7 中修复。`propagate_lifecycle` 的旧签名调用方也会失败，将在后续任务中修复。

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/ttl.py tests/test_ttl_policy.py
git commit -m "feat: remove effective_policy, rewrite propagate_lifecycle with owner check"
```

---

## Task 4: 新增 set_lifecycle 和 take_ownership 到 GPDCDataObject

**Files:**
- Modify: `src/gpdatacached/objects.py`
- Test: `tests/test_objects_lifecycle.py`（新建）

这是核心统一入口。

- [ ] **Step 1: Write the failing tests**

创建 `tests/test_objects_lifecycle.py`：

```python
import datetime
import pytest
from conftest import redis_client, clean_redis


def test_set_lifecycle_updates_meta_and_redis(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.meta import ObjectMeta
    from gpdatacached.ttl import TTLPolicy

    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["obj"] = 42
        obj = ns.get_object("obj")
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=60, expire_seconds=60)
        obj.set_lifecycle(policy)
        meta = obj.meta
        assert meta.cache_mode == "ttl"
        assert meta.ttl == 60
        assert redis_client.ttl(obj._canonical_redis_key()) > 0


def test_set_lifecycle_propagates_to_owned_children(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.meta import ObjectMeta
    from gpdatacached.keys import make_object_key
    from gpdatacached.ttl import TTLPolicy

    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["parent"] = [{"a": 1}]
        parent = ns.get_object("parent")
        parent.set_lifecycle(TTLPolicy(cache_mode="ttl", declared_ttl=120, expire_seconds=120))
        child_name = [n for n in redis_client.lrange(parent.raw_key, 0, -1) if n.startswith("ref:")][0]
        child_name = child_name.split(":obj:")[1]
        child_key = make_object_key("test", "ns", child_name)
        child_meta = ObjectMeta.load(redis_client, child_key)
        assert child_meta.cache_mode == "ttl"
        assert child_meta.ttl == 120


def test_set_lifecycle_skips_not_owned_children(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.meta import ObjectMeta
    from gpdatacached.keys import make_object_key
    from gpdatacached.ttl import TTLPolicy

    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["parent"] = [{"a": 1}]
        ns["ref"] = ns["parent"][0]
        ns["ref"].take_ownership()
        parent = ns.get_object("parent")
        child_name = [n for n in redis_client.lrange(parent.raw_key, 0, -1) if n.startswith("ref:")][0]
        child_name = child_name.split(":obj:")[1]
        child_key = make_object_key("test", "ns", child_name)
        ObjectMeta.load(redis_client, child_key).with_updates(cache_mode="ttl", ttl=30, owner="ref").save(redis_client, child_key)
        parent.set_lifecycle(TTLPolicy(cache_mode="ttl", declared_ttl=120, expire_seconds=120))
        child_meta = ObjectMeta.load(redis_client, child_key)
        assert child_meta.ttl == 30


def test_take_ownership_requires_reference(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["obj"] = 42
        obj = ns.get_object("obj")
        with pytest.raises(TypeError, match="named reference"):
            obj.take_ownership()


def test_take_ownership_transfers_owner(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.meta import ObjectMeta
    from gpdatacached.keys import make_object_key

    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["parent"] = [{"a": 1}]
        ns["ref"] = ns["parent"][0]
        ns["ref"].take_ownership()
        obj = ns.get_object("ref")
        meta = obj.meta
        assert meta.owner == "ref"


def test_cache_mode_setter_checks_owner(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.exceptions import LifecyclePermissionError

    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["parent"] = [{"a": 1}]
        child = ns["parent"][0]
        with pytest.raises(LifecyclePermissionError):
            child.cache_mode = "ttl"


def test_persist_checks_owner(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.exceptions import LifecyclePermissionError

    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["parent"] = [{"a": 1}]
        child = ns["parent"][0]
        with pytest.raises(LifecyclePermissionError):
            child.persist()


def test_expire_in_checks_owner(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.exceptions import LifecyclePermissionError

    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns.config(default_cache_mode="ttl", default_ttl_seconds=60)
        ns["parent"] = [{"a": 1}]
        parent = ns.get_object("parent")
        parent.set_lifecycle(__import__("gpdatacached.ttl", fromlist=["TTLPolicy"]).TTLPolicy(cache_mode="ttl", declared_ttl=60, expire_seconds=60))
        child = ns["parent"][0]
        with pytest.raises(LifecyclePermissionError):
            child.expire_in(30)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_objects_lifecycle.py -v`
Expected: FAIL — `set_lifecycle` / `take_ownership` not found

- [ ] **Step 3: Write implementation**

修改 `src/gpdatacached/objects.py`，完整替换：

```python
from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .domain import GPDCDomain
    from .namespace import GPDCNamespace

from .exceptions import LifecyclePermissionError, ObjectGoneError
from .keys import make_object_key, make_uri
from .meta import ObjectMeta
from .ttl import _LifecycleContext


class GPDCDataObject(ABC):
    """Abstract base class for all GPDC data objects."""

    def __init__(
        self,
        name: str,
        namespace: "GPDCNamespace",
        *,
        canonical_name: str | None = None,
    ) -> None:
        self._name = name
        self._namespace = namespace
        self._canonical_name = canonical_name if canonical_name is not None else name
        self._is_reference = canonical_name is not None and canonical_name != name

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> "GPDCNamespace":
        return self._namespace

    @property
    def domain(self) -> "GPDCDomain":
        return self._namespace.domain

    @property
    def uri(self) -> str:
        return make_uri(self.domain.name, self._namespace.name, self._name)

    @property
    def is_reference(self) -> bool:
        return self._is_reference

    @property
    def canonical_name(self) -> str:
        return self._canonical_name

    @property
    def canonical_uri(self) -> str:
        return make_uri(self.domain.name, self._namespace.name, self._canonical_name)

    @property
    @abstractmethod
    def type(self) -> str:
        """GPDC type string, e.g., 'scalar:int'."""

    @property
    @abstractmethod
    def value(self) -> object:
        """The Python value of this data object."""

    def _redis_key(self) -> str:
        return make_object_key(self.domain.name, self._namespace.name, self._name)

    def _canonical_redis_key(self) -> str:
        return make_object_key(self.domain.name, self._namespace.name, self._canonical_name)

    def _redis(self):
        return self.domain.redis

    @property
    def meta(self) -> ObjectMeta | None:
        return ObjectMeta.load(self._redis(), self._canonical_redis_key())

    @meta.setter
    def meta(self, new_meta: ObjectMeta) -> None:
        from .ttl import policy_from_meta
        policy = policy_from_meta(new_meta)
        self.set_lifecycle(policy)

    @property
    def cache_mode(self) -> str:
        meta = self.meta
        return meta.cache_mode if meta else ""

    def _load_lifecycle_context(self) -> _LifecycleContext:
        from .keys import make_ref_key

        key = self._canonical_redis_key()
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        is_collection = meta.is_collection()
        raw_key = meta.value if is_collection else ""
        ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name) if is_collection else ""
        return _LifecycleContext(r=r, key=key, meta=meta, raw_key=raw_key, ref_key=ref_key, is_collection=is_collection)

    def _check_owner_permission(self, meta: ObjectMeta) -> None:
        if meta.owner != self.canonical_name:
            raise LifecyclePermissionError(
                canonical_name=self.canonical_name,
                owner_name=meta.owner,
            )

    def set_lifecycle(self, policy, *, propagate: bool = True) -> None:
        from .ttl import (
            _validate_policy,
            policy_from_meta,
            apply_lifecycle,
            propagate_lifecycle,
        )

        ctx = self._load_lifecycle_context()
        _validate_policy(policy)

        current = policy_from_meta(ctx.meta)
        changed = (current.cache_mode != policy.cache_mode
                   or current.expire_seconds != policy.expire_seconds)

        updated_meta = ctx.meta.with_updates(
            cache_mode=policy.cache_mode,
            ttl=policy.declared_ttl,
            updated_at=datetime.datetime.now(datetime.UTC),
        )
        updated_meta.save(ctx.r, ctx.key)

        if changed:
            apply_lifecycle(ctx.r, ctx.key, ctx.raw_key, ctx.ref_key,
                            ctx.is_collection, policy)

        if changed and propagate:
            propagate_lifecycle(
                ctx.r, self.domain.name, self.namespace.name,
                self.canonical_name, policy, set(),
            )

    def take_ownership(self) -> None:
        if not self._is_reference:
            raise TypeError(
                "take_ownership() can only be called on named reference objects"
            )
        if self._name.startswith("~"):
            raise TypeError(
                "take_ownership() can only be called on named reference objects"
            )

        key = self._canonical_redis_key()
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)

        if meta.owner == self.canonical_name:
            return

        meta.with_updates(owner=self.canonical_name).save(r, key)

    @cache_mode.setter
    def cache_mode(self, mode: str) -> None:
        from .ttl import policy_from_meta, policy_for_cache_mode_change

        if mode not in ("permanent", "ttl", "sliding"):
            raise ValueError(f"Invalid cache_mode: {mode!r}")
        ctx = self._load_lifecycle_context()
        self._check_owner_permission(ctx.meta)
        policy = policy_for_cache_mode_change(policy_from_meta(ctx.meta), mode)
        self.set_lifecycle(policy)

    @property
    def ttl(self) -> int:
        meta = self.meta
        return meta.ttl if meta else 0

    @ttl.setter
    def ttl(self, seconds: int) -> None:
        from .ttl import policy_from_meta, policy_for_ttl_change

        ctx = self._load_lifecycle_context()
        self._check_owner_permission(ctx.meta)
        policy = policy_for_ttl_change(policy_from_meta(ctx.meta), seconds)
        self.set_lifecycle(policy)

    @property
    def created_at(self) -> datetime.datetime | None:
        meta = self.meta
        return meta.created_at if meta else None

    @property
    def updated_at(self) -> datetime.datetime | None:
        meta = self.meta
        return meta.updated_at if meta else None

    def expire_in(self, seconds: int) -> None:
        from .ttl import TTLPolicy

        ctx = self._load_lifecycle_context()
        if ctx.meta.cache_mode != "ttl":
            raise TypeError("expire_in() only applies to objects with cache_mode='ttl'")
        self._check_owner_permission(ctx.meta)
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds, expire_seconds=seconds)
        self.set_lifecycle(policy)

    def expire_at(self, when: datetime.datetime) -> None:
        from .ttl import TTLPolicy

        ctx = self._load_lifecycle_context()
        if ctx.meta.cache_mode != "ttl":
            raise TypeError("expire_at() only applies to objects with cache_mode='ttl'")
        self._check_owner_permission(ctx.meta)
        seconds = max(0, int((when - datetime.datetime.now(datetime.UTC)).total_seconds()))
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds, expire_seconds=seconds)
        self.set_lifecycle(policy)

    def persist(self) -> None:
        from .ttl import TTLPolicy

        ctx = self._load_lifecycle_context()
        self._check_owner_permission(ctx.meta)
        policy = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        self.set_lifecycle(policy)

    def remaining_ttl(self) -> int:
        return self._redis().ttl(self._canonical_redis_key())

    __hash__ = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GPDCDataObject):
            return NotImplemented
        return (
            self.domain.name == other.domain.name
            and self._namespace.name == other._namespace.name
            and self._canonical_name == other._canonical_name
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(uri={self.uri!r})"
```

- [ ] **Step 4: Run tests to verify**

Run: `./venv/bin/python -m pytest tests/test_objects_lifecycle.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to check regressions**

Run: `./venv/bin/python -m pytest --tb=short 2>&1 | tail -30`
Expected: 仍有一些失败（container/namespace/types 的旧调用方式），后续任务修复。

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/objects.py tests/test_objects_lifecycle.py
git commit -m "feat: add set_lifecycle/take_ownership to GPDCDataObject, unify lifecycle entry"
```

---

## Task 5: 重命名 _binding_owner_name → _binding_name，更新 encode_element 签名

**Files:**
- Modify: `src/gpdatacached/container.py`
- Modify: `src/gpdatacached/types.py`
- Test: `tests/test_namespace.py`（已有，确认不回归）

此任务重命名方法并更新函数签名，是 container/types 改造的前置。

- [ ] **Step 1: Rename `_binding_owner_name` to `_binding_name` in `container.py`**

在 `container.py` 中全局替换 `_binding_owner_name` 为 `_binding_name`（方法定义和所有调用点）。

- [ ] **Step 2: Update `encode_element` and `_create_anonymous_object` signatures in `types.py`**

将 `types.py` 中的：

```python
def encode_element(value, namespace, owner_name, cache_mode, ttl) -> str
```

改为：

```python
def encode_element(value, namespace, owner_name, policy) -> str
```

将 `_create_anonymous_object` 的签名从 `(namespace, python_value, parent_name, cache_mode, ttl)` 改为 `(namespace, python_value, parent_name, policy)`。

更新 `_create_anonymous_object` 内部：
- ObjectMeta 构造使用 `cache_mode=policy.cache_mode`、`ttl=policy.declared_ttl`、`owner=parent_name`
- 删除 `cache_mode="inherited"`
- 删除 `policy_from_defaults(cache_mode, ttl)` 调用，直接使用传入的 `policy`

- [ ] **Step 3: Update all `encode_element` call sites in `container.py`**

所有 `container.py` 中调用 `encode_element` 的地方，将 `self.cache_mode, self.ttl` 替换为 `policy`，其中 policy 从 `self.meta` 获取。涉及的调用点：
- `ListObject.__setitem__`
- `ListObject.insert`
- `ListObject.append`
- `ListObject.extend`
- `ListObject.sort`
- `DictObject.__setitem__`
- `DictObject.update`

每个调用点需要改为：
```python
from .ttl import policy_from_meta
meta = self.meta
policy = policy_from_meta(meta) if meta else ...
encoded = encode_element(value, self._namespace, self._binding_name(), policy)
```

- [ ] **Step 4: Run tests to verify no regressions**

Run: `./venv/bin/python -m pytest tests/test_namespace.py tests/test_nested_containers.py tests/test_anonymous_object_creation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/container.py src/gpdatacached/types.py
git commit -m "refactor: rename _binding_owner_name to _binding_name, update encode_element signature"
```

---

## Task 6: 重写 container._touch() 支持 owner 链向上传播

**Files:**
- Modify: `src/gpdatacached/container.py`
- Test: `tests/test_inherited_lifecycle.py`（已有，需要更新）
- Test: `tests/test_ttl_expiry.py`（已有，需要更新）

- [ ] **Step 1: Write the failing test**

在 `tests/test_inherited_lifecycle.py` 中追加（或更新现有测试）：

```python
def test_touch_propagates_up_owner_chain(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.meta import ObjectMeta
    from gpdatacached.keys import make_object_key
    from gpdatacached.ttl import TTLPolicy

    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns.config(default_cache_mode="sliding", default_ttl_seconds=60)
        ns["parent"] = [{"a": 1}]
        parent = ns.get_object("parent")
        child = ns["parent"][0]
        child_key = child._canonical_redis_key()
        child_meta_before = ObjectMeta.load(redis_client, child_key)
        assert child_meta_before.owner == "parent"

        child.append("new_item")
        child_meta_after = ObjectMeta.load(redis_client, child_key)
        assert child_meta_after.cache_mode == "sliding"
        remaining = redis_client.ttl(child_key)
        assert remaining > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_inherited_lifecycle.py::test_touch_propagates_up_owner_chain -v`
Expected: FAIL（因为 `_touch()` 尚未重写）

- [ ] **Step 3: Rewrite `_touch()` in `container.py`**

替换 `GPDCContainerObject._touch()` 方法实现，支持 owner 链向上查找：

```python
def _touch(self) -> None:
    from .ttl import apply_lifecycle, propagate_lifecycle, policy_from_meta, policy_for_sliding_touch
    from .meta import ObjectMeta

    key = self._canonical_redis_key()
    now = datetime.datetime.now(datetime.UTC)
    r = self._redis()
    item_count = self._current_item_count()
    meta = ObjectMeta.load(r, key)
    if meta is None:
        raise ObjectGoneError(self.canonical_uri)
    meta.touch(now, item_count=item_count).save(r, key)

    if meta.owner == self.canonical_name:
        current = policy_from_meta(meta)
        new_policy = policy_for_sliding_touch(current)
        if new_policy is not current:
            self.set_lifecycle(new_policy)
    else:
        owner_name = meta.owner
        while owner_name:
            owner_obj_key = make_object_key(self.domain.name, self.namespace.name, owner_name)
            owner_meta = ObjectMeta.load(r, owner_obj_key)
            if owner_meta is None:
                break
            if owner_meta.owner == owner_name:
                current = policy_from_meta(owner_meta)
                new_policy = policy_for_sliding_touch(current)
                if new_policy is not current:
                    owner_obj = self.namespace.get_object(owner_name)
                    owner_obj.set_lifecycle(new_policy)
                break
            owner_name = owner_meta.owner
```

- [ ] **Step 4: Run tests to verify**

Run: `./venv/bin/python -m pytest tests/test_inherited_lifecycle.py tests/test_ttl_expiry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/container.py tests/test_inherited_lifecycle.py
git commit -m "feat: rewrite _touch() with owner chain upward propagation"
```

---

## Task 7: 更新 namespace.py — owner 初始化 + 容器覆盖行为

**Files:**
- Modify: `src/gpdatacached/namespace.py`
- Test: `tests/test_namespace.py`

- [ ] **Step 1: Write the failing tests**

在 `tests/test_namespace.py` 中追加：

```python
def test_named_object_has_owner_set(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["obj"] = 42
        obj = ns.get_object("obj")
        assert obj.meta.owner == "obj"


def test_named_container_has_owner_set(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["lst"] = [1, 2, 3]
        obj = ns.get_object("lst")
        assert obj.meta.owner == "lst"


def test_container_overwrite_uses_namespace_defaults(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    with GPDCDomain("test", redis=redis_client) as domain:
        domain.config(default_cache_mode="ttl", default_ttl_seconds=120)
        ns = domain.namespace("ns")
        ns["lst"] = [1, 2, 3]
        ns.get_object("lst").set_lifecycle(
            __import__("gpdatacached.ttl", fromlist=["TTLPolicy"]).TTLPolicy(
                cache_mode="ttl", declared_ttl=60, expire_seconds=60
            )
        )
        ns["lst"] = [4, 5, 6]
        obj = ns.get_object("lst")
        assert obj.meta.ttl == 120


def test_alias_ref_key_override(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.keys import make_ref_key
    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["lst"] = [1, 2, 3]
        ns["alias"] = ns["lst"]
        obj = ns.get_object("alias")
        expected_ref_key = make_ref_key("test", "ns", "lst")
        assert obj.ref_key == expected_ref_key
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_namespace.py::test_named_object_has_owner_set tests/test_namespace.py::test_container_overwrite_uses_namespace_defaults tests/test_namespace.py::test_alias_ref_key_override -v`
Expected: FAIL

- [ ] **Step 3: Update `_write_scalar_meta` and `_write_container_meta` to include `owner`**

在 `namespace.py` 的两个方法中，ObjectMeta 构造时增加 `owner=obj_key.split(":obj:")[1]`（即 key 名称）。需要从调用处传入 key name。

修改签名：`_write_scalar_meta(self, obj_key, entry, encoded_value, policy, now, *, is_new, obj_name)` 和类似地 `_write_container_meta`。在 ObjectMeta 构造中增加 `owner=obj_name`。

- [ ] **Step 4: Update `__setitem__` container overwrite logic**

在 `namespace.py` 的 `__setitem__` 中，当容器覆盖（`existing.binding_kind == "owner" and existing.type.startswith("collection:")`）时，使用 namespace 默认 policy（不继承旧 policy），因为命名对象覆盖视同删除+新建。

- [ ] **Step 5: Update `_get_object` to override ref_key for alias containers**

在 `namespace.py` 的 `_get_object` 中，对 alias 容器增加 `obj.ref_key` 覆盖：

```python
if isinstance(obj, GPDCContainerObject):
    stored_raw_key = resolution.canonical_raw_key
    if stored_raw_key:
        obj.raw_key = stored_raw_key
    if resolution.binding_kind == "reference":
        canonical_ref_key = make_ref_key(self._domain.name, self._name, resolution.canonical_name)
        obj.ref_key = canonical_ref_key
```

- [ ] **Step 6: Run tests to verify**

Run: `./venv/bin/python -m pytest tests/test_namespace.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/gpdatacached/namespace.py tests/test_namespace.py
git commit -m "feat: namespace owner init, container overwrite, alias ref_key fix"
```

---

## Task 8: 更新 scalar.py — value setter 使用 set_lifecycle

**Files:**
- Modify: `src/gpdatacached/scalar.py`
- Test: `tests/test_ttl_expiry.py`（已有，确认 sliding scalar 续期仍工作）

- [ ] **Step 1: Update scalar value setter**

修改 `src/gpdatacached/scalar.py` 中 `GPDCScalarObject.value` setter，将 `apply_lifecycle` 替换为 `set_lifecycle`：

```python
@value.setter
def value(self, new_value: object) -> None:
    from .types import GPDCTypeRegistry
    from .ttl import policy_from_meta, policy_for_sliding_touch
    from .meta import ObjectMeta

    entry = GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE)
    expected_python_type = entry.python_type
    if not isinstance(new_value, expected_python_type):
        raise TypeError(f"Cannot assign {type(new_value).__name__!r} to {self.TYPE}")
    encoded = entry.codec_class.encode_value(new_value)
    key = self._canonical_redis_key()
    now = datetime.datetime.now(datetime.UTC)
    r = self._redis()
    meta = ObjectMeta.load(r, key)
    if meta is None:
        raise ObjectGoneError(self.canonical_uri)
    meta.with_updates(value=encoded, updated_at=now).save(r, key)
    current = policy_from_meta(meta)
    new_policy = policy_for_sliding_touch(current)
    if new_policy is not current:
        self.set_lifecycle(new_policy)
```

- [ ] **Step 2: Run tests to verify**

Run: `./venv/bin/python -m pytest tests/test_ttl_expiry.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/gpdatacached/scalar.py
git commit -m "refactor: scalar value setter uses set_lifecycle"
```

---

## Task 9: 更新 container __setitem__ 的匿名子对象覆盖行为

**Files:**
- Modify: `src/gpdatacached/container.py`
- Test: `tests/test_nested_containers.py`（追加测试）

- [ ] **Step 1: Write the failing test**

在 `tests/test_nested_containers.py` 中追加：

```python
def test_overwrite_taken_ownership_child_preserves_old(redis_client, clean_redis):
    from gpdatacached.domain import GPDCDomain
    from gpdatacached.meta import ObjectMeta
    from gpdatacached.keys import make_object_key

    with GPDCDomain("test", redis=redis_client) as domain:
        ns = domain.namespace("ns")
        ns["parent"] = [{"a": 1}]
        ns["ref"] = ns["parent"][0]
        ns["ref"].take_ownership()
        child = ns["parent"][0]
        child_key = child._canonical_redis_key()

        old_meta = ObjectMeta.load(redis_client, child_key)
        assert old_meta.owner == "ref"

        ns["parent"][0] = {"b": 2}

        old_meta_after = ObjectMeta.load(redis_client, child_key)
        assert old_meta_after is not None, "Old taken-ownership object should not be deleted"
        assert old_meta_after.owner == "ref"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_nested_containers.py::test_overwrite_taken_ownership_child_preserves_old -v`
Expected: FAIL

- [ ] **Step 3: Update `__setitem__` in ListObject and DictObject**

在 `container.py` 的 `ListObject.__setitem__` 和 `DictObject.__setitem__` 中，覆盖前检查旧 ref 的 owner：

```python
# In __setitem__, before _unbind_old_ref:
if self._is_container_ref(old_encoded):
    old_obj_key = old_encoded[len("ref:"):]
    old_meta = ObjectMeta.load(r, old_obj_key)
    if old_meta and old_meta.owner != self._binding_name():
        pass  # owner 已转移，不删除
    else:
        self._unbind_old_ref(old_encoded)
else:
    ...
```

- [ ] **Step 4: Run tests to verify**

Run: `./venv/bin/python -m pytest tests/test_nested_containers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/container.py tests/test_nested_containers.py
git commit -m "feat: preserve taken-ownership children on container overwrite"
```

---

## Task 10: 修复旧测试并运行全量测试

**Files:**
- Modify: 各种测试文件中的旧 `inherited`/`effective_policy` 引用
- Modify: `tests/conftest.py`（如有需要）

此任务是清理性的，确保所有旧测试适配新模型。

- [ ] **Step 1: Run full test suite, capture failures**

Run: `./venv/bin/python -m pytest --tb=short 2>&1 | tail -50`
记录所有失败测试。

- [ ] **Step 2: Fix failing tests**

常见需要修复的模式：
- 测试中创建 `cache_mode="inherited"` 的 ObjectMeta → 改为有效值 + owner
- 测试中调用 `effective_policy()` → 直接用 `policy_from_meta()`
- 测试中调用旧签名 `propagate_lifecycle(r, domain, namespace, parent_name, visited)` → 新签名 `propagate_lifecycle(r, domain, namespace, parent_name, policy, visited)`
- 测试中调用 `_binding_owner_name()` → `_binding_name()`
- 测试中检查 `is_inherited()` → 检查 `meta.owner`

- [ ] **Step 3: Run full test suite again**

Run: `./venv/bin/python -m pytest --tb=short 2>&1 | tail -20`
Expected: ALL PASS

- [ ] **Step 4: Run lint and format**

Run: `./venv/bin/python -m ruff check src tests && ./venv/bin/python -m ruff format src tests`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: fix all tests for new lifecycle owner model"
```

---

## Task 11: 独立小修复 — setdefault, pop/update, discard, GPDCContainerCodec export

**Files:**
- Modify: `src/gpdatacached/container.py`
- Modify: `src/gpdatacached/__init__.py`
- Test: 对应测试文件

这些是评审报告中的低优先级修复，与生命周期重构独立但在此一并处理。

- [ ] **Step 1: Fix setdefault return value**

在 `container.py` `DictObject.setdefault` 中，将 `return default` 改为 `return self[key]`。

- [ ] **Step 2: Fix pop/update semantics**

`DictObject.pop`: 改为 `pop(self, key, _SENTINEL=object())` 模式。
`DictObject.update`: 将 `if other:` 改为 `if other is not None:`。

- [ ] **Step 3: Fix discard no-op touch**

在 `SetObject.discard` 中，检查 `srem` 返回值，有实际删除才 `_touch()`。

- [ ] **Step 4: Export GPDCContainerCodec**

在 `__init__.py` 中补充导出 `GPDCContainerCodec`。

- [ ] **Step 5: Run full test suite**

Run: `./venv/bin/python -m pytest --tb=short`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/container.py src/gpdatacached/__init__.py tests/
git commit -m "fix: setdefault return, pop/update semantics, discard no-op, GPDCContainerCodec export"
```

---

## Task 12: 更新进度文档

**Files:**
- Modify: `dev_docs/progress.md`

- [ ] **Step 1: Update progress.md**

将进度文档更新为反映实现完成的状态。

- [ ] **Step 2: Commit**

```bash
git add dev_docs/progress.md
git commit -m "docs: update progress tracking"
```

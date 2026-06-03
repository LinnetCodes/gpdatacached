# 生命周期管理重构设计方案

日期: 2026-06-02 (v2 — 头脑风暴精炼版)
动机: 评审报告 `review/2026-06-01-code-review-report.zh-CN.md` 中 #1-#5, #10 均涉及生命周期管理，根本原因是多入口、多路径导致状态不一致。本方案重新设计生命周期管理模型。

---

## 一、设计原则

以下原则由讨论确认，作为后续所有设计的约束：

**P1. owner 字段明确标记生命周期管理者**
在元数据中增加 `owner: str` 字段，存放生命周期管理者的 canonical name。`owner` 字段替代原有的 `inherited: bool` 和 `cache_mode="inherited"` 两种标记方式，用统一的名称引用清晰地标识每个对象的生命周期由谁管理。`cache_mode` 和 `ttl` 字段始终保存有效值。

**P2. 生命周期传播仅作用于 owned 子对象**
传播时，只影响 `owner == 当前对象 canonical_name` 的子对象。`owner` 指向其他对象或不匹配的子对象不受影响。

**P3. 匿名对象的 owner 只能通过显式 API 变更**
匿名对象的 `owner` 只能通过 `take_ownership()` 显式变更。`take_ownership()` 无参数，仅命名引用对象可调用，自动将 underlying 匿名对象的 owner 设为调用者的 canonical_name。不能在 alias 创建时自动转换——用户必须明确知晓自己承担了生命周期管理责任。

**P4. 命名对象的 owner 为自身且不可更改**
所有命名对象（不以 `~` 开头）从创建起 `owner = 自身 canonical_name`。命名对象的 owner 任何情况下都不允许更改。

**P5. expire_in/expire_at 仅适用于 ttl 模式**
调用 `expire_in()` / `expire_at()` 时，如果对象 `cache_mode` 不是 `"ttl"`，抛出异常（包括 sliding 也不允许）。变更传递给所有 owned 子对象。

**P6. persist() 同步改为 permanent**
`persist()` 将 `cache_mode` 改为 `"permanent"`，`ttl` 改为 permanent 的缺省值（`0`），保持语义统一。变更传递给所有 owned 子对象。

**P7. cache_mode/ttl 变更传递到 owned 子对象**
对 `cache_mode` 和 `ttl` 的更改传递给所有 `owner == 当前对象 canonical_name` 的子对象。owner 不匹配的子对象跳过（生命周期管理权已被其他命名引用接管）。

**P8. 命名对象的 owner 不可转移**
命名对象的 `owner` 始终为自身 canonical name，不可被转移。只有匿名对象可以通过 `take_ownership()` 将 owner 转移到一个命名引用。

**P9. 无变化不传播**
如果计算后发现 cache_mode 和 expire_seconds 与当前值相同，跳过 `apply_lifecycle` 和 `propagate_lifecycle`，避免不必要的 Redis 操作。

**P10. 权限检查统一基于 meta.owner**
所有修改生命周期的方法（`cache_mode` setter、`ttl` setter、`expire_in`、`expire_at`、`persist`）在执行前检查 `meta.owner == self.canonical_name`。不匹配时抛出异常。命名对象 owner 始终为自身，因此始终有权限。匿名对象 owner 为 parent，因此默认无权限。命名引用通过 `take_ownership()` 获得 owner 权限。

---

## 二、核心模型变更

### 2.1 ObjectMeta 增加 `owner` 字段，移除 `inherited` 概念

```python
class ObjectMeta(BaseModel):
    type: str
    value: str = ""
    cache_mode: Literal["permanent", "ttl", "sliding"] = "permanent"
    ttl: int = 0
    owner: str = ""                  # 新增：生命周期管理者的 canonical name
    created_at: datetime | None = None
    updated_at: datetime | None = None
    item_count: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
```

`cache_mode` 不再包含 `"inherited"` 值。`cache_mode` 始终保存该对象的有效生命周期策略。`owner` 字段存放生命周期管理者的 canonical name：
- 命名对象：`owner = 自身 canonical_name`（自管理）
- 匿名对象：`owner = 直接上层 parent 的 canonical_name`（由 parent 管理）
- 匿名对象通过 `take_ownership()` 可将 owner 改为命名引用的 canonical_name

Redis 存储格式变化：
- 旧: `cache_mode=inherited, ttl=0`
- 新: `cache_mode=<有效值>, ttl=<有效值>, owner=<管理者canonical_name>`

`from_redis` / `to_redis_mapping` 需同步更新。`owner` 映射为 Redis hash 中的 `"owner"` 字段。需要向后兼容：读到 `cache_mode=inherited` 的旧数据时，将 `owner` 设为空字符串（无法确定原始 parent），`cache_mode` 回退到 permanent。

### 2.2 删除 `effective_policy()` 函数

子对象的 meta 中已经保存了有效的 `cache_mode` 和 `ttl`，不再需要向上回溯计算。`policy_from_meta()` 直接读取即可。

### 2.3 收敛为单一生命周期变更入口

所有生命周期变更通过一个统一方法 `set_lifecycle()` 完成：

```python
def set_lifecycle(self, policy: TTLPolicy, *, propagate: bool = True) -> None:
    """唯一的生命周期变更路径。

    1. 校验 policy 合法性（解决 #3）
    2. 更新 meta（cache_mode, ttl, updated_at）并 save
    3. apply_lifecycle 设 Redis EXPIRE/PERSIST
    4. 如果 propagate=True，遍历 owned 子对象（owner == 当前对象 canonical_name），更新 meta + EXPIRE
    """
```

### 2.4 propagate_lifecycle 简化

```python
def propagate_lifecycle(r, domain, namespace, parent_name, policy, visited):
    """将 policy 传递给所有 owned 子对象（owner == parent_name）。"""
    parent_obj_key = make_object_key(domain, namespace, parent_name)
    parent_meta = ObjectMeta.load(r, parent_obj_key)
    if parent_meta is None or not parent_meta.is_collection():
        return

    child_names = collect_ref_names_from_raw(r, parent_meta.value, parent_meta.type)
    for child_name in child_names:
        if child_name in visited:
            continue
        visited.add(child_name)

        child_obj_key = make_object_key(domain, namespace, child_name)
        child_meta = ObjectMeta.load(r, child_obj_key)
        if child_meta is None or child_meta.owner != parent_name:
            continue  # P2: 非 owned 子对象跳过

        # P7: 更新子对象 meta 为 parent 的 policy
        child_meta.with_updates(
            cache_mode=policy.cache_mode,
            ttl=policy.declared_ttl,
            updated_at=datetime.now(UTC),
        ).save(r, child_obj_key)

        # 设 Redis TTL
        child_raw_key = child_meta.value if child_meta.is_collection() else ""
        child_ref_key = make_ref_key(domain, namespace, child_name)
        apply_lifecycle(r, child_obj_key, child_raw_key, child_ref_key,
                        child_meta.is_collection(), policy)

        # 递归
        propagate_lifecycle(r, domain, namespace, child_name, policy, visited)
```

关键变化：
- 不再调用 `effective_policy()`
- 直接用 parent 传下来的 `policy`
- 检查 `child_meta.owner == parent_name` 决定是否更新（P2）
- 更新子对象 meta 中的 `cache_mode` 和 `ttl`

---

## 三、命名重命名

### 3.1 `_binding_owner_name()` → `_binding_name()`

`_binding_owner_name()` 和 `meta.owner` 职责不同，需要通过命名区分：

| 方法 | 回答的问题 | 用途 |
|------|-----------|------|
| `_binding_name()` | 这个容器绑定的对象是谁？ | ref 管理（`_unbind_old_ref`、`_reference_ref_value`、referrers） |
| `meta.owner` | 谁管理这个对象的生命周期？ | 生命周期管理（权限检查、传播判断） |

两者在命名对象上值相同，在匿名对象上不同：
- 匿名对象 `_binding_name()` = 自身名字（从 raw_key 解析）
- 匿名对象 `meta.owner` = parent 名字

### 3.2 `encode_element` / `_create_anonymous_object` 签名变更

旧签名：
```python
def encode_element(value, namespace, owner_name, cache_mode, ttl) -> str
def _create_anonymous_object(namespace, python_value, parent_name, cache_mode, ttl) -> tuple[str, str]
```

新签名：
```python
def encode_element(value, namespace, owner_name, policy: TTLPolicy) -> str
def _create_anonymous_object(namespace, python_value, parent_name, policy: TTLPolicy) -> tuple[str, str]
```

`owner_name` 仍用于 ref 管理（`_binding_name()`），`policy` 用于创建时设置有效的 `cache_mode`/`ttl`。所有调用点（`container.py` 的 `__setitem__`、`append`、`insert`、`extend` 等）改为传入 `policy` 而非分开传 `cache_mode`/`ttl`。

---

## 四、各入口的新行为

### 4.1 新对象创建

**命名对象**: `owner = 自身 canonical_name`，`cache_mode`/`ttl` 从 namespace/domain 默认值获取。`_write_scalar_meta` 和 `_write_container_meta` 构造 ObjectMeta 时自动设置 `owner = key`。

**匿名子对象** (`_create_anonymous_object`):
- `owner = 直接 parent 的 canonical_name`（即 `_binding_name()` 返回值）
- `cache_mode` = parent 的 effective cache_mode（不再用 `"inherited"` 字符串）
- `ttl` = parent 的 effective ttl
- `apply_lifecycle` 用 parent 的 policy 设 Redis TTL

### 4.2 cache_mode setter

```python
@cache_mode.setter
def cache_mode(self, mode: str) -> None:
    ctx = self._load_lifecycle_context()
    if ctx.meta.owner != self.canonical_name:
        raise LifecyclePermissionError(...)  # P10: 非 owner 不能修改
    policy = policy_for_cache_mode_change(policy_from_meta(ctx.meta), mode)
    self.set_lifecycle(policy)
```

### 4.3 ttl setter

```python
@ttl.setter
def ttl(self, seconds: int) -> None:
    ctx = self._load_lifecycle_context()
    if ctx.meta.owner != self.canonical_name:
        raise LifecyclePermissionError(...)  # P10
    policy = policy_for_ttl_change(policy_from_meta(ctx.meta), seconds)
    self.set_lifecycle(policy)
```

### 4.4 meta setter

```python
@meta.setter
def meta(self, new_meta: ObjectMeta) -> None:
    policy = policy_from_meta(new_meta)
    self.set_lifecycle(policy)  # 内部提取 policy 并传播
```

### 4.5 _touch() (sliding)

`_touch()` 的行为根据 `meta.owner` 分两种情况：

**Case 1: `owner == self.canonical_name`（命名对象、已 take_ownership 的对象）**
```python
def _touch(self) -> None:
    key = self._canonical_redis_key()
    now = datetime.datetime.now(datetime.UTC)
    r = self._redis()
    item_count = self._current_item_count()
    meta = ObjectMeta.load(r, key)
    if meta is None:
        raise ObjectGoneError(self.canonical_uri)
    meta.touch(now, item_count=item_count).save(r, key)
    current = policy_from_meta(meta)
    new_policy = policy_for_sliding_touch(current)
    if new_policy is not current:
        self.set_lifecycle(new_policy)
```

**Case 2: `owner != self.canonical_name`（匿名子对象，owner 是 parent）**
匿名子对象沿 owner 链向上查找，直到遇到 `owner == 自身 canonical_name` 的对象，对该顶层 owner 对象调用 `set_lifecycle()`。propagate 会沿 owned 子对象树向下传播，刷新整条链的 TTL。

```python
def _touch(self) -> None:
    key = self._canonical_redis_key()
    now = datetime.datetime.now(datetime.UTC)
    r = self._redis()
    item_count = self._current_item_count()
    meta = ObjectMeta.load(r, key)
    if meta is None:
        raise ObjectGoneError(self.canonical_uri)
    meta.touch(now, item_count=item_count).save(r, key)

    if meta.owner == self.canonical_name:
        # Case 1: 自己是 owner，直接 set_lifecycle
        current = policy_from_meta(meta)
        new_policy = policy_for_sliding_touch(current)
        if new_policy is not current:
            self.set_lifecycle(new_policy)
    else:
        # Case 2: 沿 owner 链向上找到顶层 owner
        owner_name = meta.owner
        while owner_name:
            owner_obj_key = make_object_key(self.domain.name, self.namespace.name, owner_name)
            owner_meta = ObjectMeta.load(r, owner_obj_key)
            if owner_meta is None:
                break
            if owner_meta.owner == owner_name:
                # 找到顶层 owner，对它执行 set_lifecycle
                current = policy_from_meta(owner_meta)
                new_policy = policy_for_sliding_touch(current)
                if new_policy is not current:
                    # 需要通过 GPDCDataObject 实例调用 set_lifecycle
                    owner_obj = self.namespace.get_object(owner_name)
                    owner_obj.set_lifecycle(new_policy)
                break
            owner_name = owner_meta.owner
```

### 4.6 expire_in / expire_at

```python
def expire_in(self, seconds: int) -> None:
    ctx = self._load_lifecycle_context()
    if ctx.meta.cache_mode != "ttl":  # P5: 仅 ttl，sliding 也不允许
        raise TypeError("expire_in() only applies to objects with cache_mode='ttl'")
    if ctx.meta.owner != self.canonical_name:
        raise LifecyclePermissionError(...)  # P10
    policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds, expire_seconds=seconds)
    self.set_lifecycle(policy)
```

### 4.7 persist()

```python
def persist(self) -> None:
    ctx = self._load_lifecycle_context()
    if ctx.meta.owner != self.canonical_name:
        raise LifecyclePermissionError(...)  # P10
    policy = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
    self.set_lifecycle(policy)
```

---

## 五、take_ownership 详细设计

### 5.1 方法定义

`take_ownership()` 定义在 `GPDCDataObject` 基类上。

```python
def take_ownership(self) -> None:
    """将 underlying 匿名对象的 owner 转移给当前命名引用。

    前提条件：
    - 调用者必须是命名引用对象（is_reference == True 且 name 不以 ~ 开头）
    - underlying 对象当前 owner != 调用者的 canonical_name

    效果：
    - 将 underlying 匿名对象的 meta.owner 设为 self.canonical_name
    - 原 owner 的 propagate 将不再影响该子对象
    - 调用者获得对该子对象的生命周期管理权
    """
    if not self.is_reference:
        raise TypeError("take_ownership() can only be called on named reference objects")
    if self.name.startswith("~"):
        raise TypeError("take_ownership() can only be called on named reference objects")

    key = self._canonical_redis_key()
    r = self._redis()
    meta = ObjectMeta.load(r, key)
    if meta is None:
        raise ObjectGoneError(self.canonical_uri)

    if meta.owner == self.canonical_name:
        return  # 已经是 owner，无需操作

    meta.with_updates(owner=self.canonical_name).save(r, key)
```

### 5.2 调用示例

```python
ns["lst"] = [{"a": "test"}]   # 创建命名容器 + 匿名子容器
ns["ref"] = ns["lst"][0]      # 创建命名引用
ns["ref"].take_ownership()    # ref 获得子容器的生命周期管理权
ns["ref"].cache_mode = "ttl"  # 现在可以修改子容器的生命周期
ns["ref"].ttl = 60
```

### 5.3 命名对象调用

命名对象调用 `take_ownership()` 时抛 `TypeError`，因为命名对象的 owner 始终为自身（P4/P8），不需要也不能转移。

### 5.4 匿名对象直接调用

`ns["lst"][0]` 返回的匿名对象调用 `take_ownership()` 时抛 `TypeError`，因为 `ns["lst"][0]` 不是命名引用。只有通过 `ns["ref"] = ns["lst"][0]` 建立引用后，`ns["ref"]` 才能调用 `take_ownership()`。

---

## 六、容器覆盖行为（由 meta.owner 决定）

容器覆盖时，根据被覆盖子对象的 `meta.owner` 决定行为：

### 6.1 命名对象覆盖

场景：`ns["lst"] = [1, 2, 3]` 覆盖现有命名容器

行为：视同删除旧对象 + 新建。新对象用 namespace 默认 policy，`owner = 自身 name`。

### 6.2 匿名子对象覆盖 — owner == parent

场景：`ns["lst"][0] = {"a": 1}` 覆盖匿名子容器，且该子容器 `owner == parent_name`

行为：替换引用，创建新匿名对象沿用旧 policy（和旧匿名对象的 policy 相同）。新匿名对象 `owner = parent_name`。

### 6.3 匿名子对象覆盖 — owner != parent（已被 take_ownership）

场景：`ns["lst"][0] = {"a": 1}` 覆盖匿名子容器，但该子容器已被 `take_ownership()`

行为：**不删除**旧匿名对象（因为它有自己的 owner），仅将当前容器中的引用更新为新创建的匿名对象。新匿名对象 `owner = parent_name`。

### 6.4 标量子元素覆盖

场景：`ns["lst"][0] = 42`

行为：纯值替换，无匿名对象概念。

### 6.5 实现要点

在 `container.py` 的 `__setitem__` 中，覆盖前先检查旧值的 ref：

```python
# 伪代码
if self._is_container_ref(old_encoded):
    old_obj = decode_element(old_encoded, self._namespace)
    old_meta = ObjectMeta.load(r, old_obj._canonical_redis_key())
    if old_meta and old_meta.owner == self._binding_name():
        # owner 是 parent，可以删除
        self._unbind_old_ref(old_encoded)  # 正常删除
    else:
        # owner 已转移，仅更新引用
        pass  # 跳过删除，只替换 encoded 值
```

---

## 七、set_lifecycle 统一入口详细设计

### 7.1 set_lifecycle 方法

```python
def set_lifecycle(self, policy: TTLPolicy, *, propagate: bool = True) -> None:
    """
    所有生命周期变更的唯一入口。

    Step 1: 校验 policy 合法性
    Step 2: 计算是否有实际变化（P9: 无变化则不传播）
    Step 3: 更新 meta 并 save
    Step 4: apply_lifecycle 设 Redis EXPIRE/PERSIST
    Step 5: 仅在有实际变化时传播到 owned 子对象
    """
    ctx = self._load_lifecycle_context()

    # Step 1: 校验
    _validate_policy(policy)

    # Step 2: 检查是否有实际变化
    current = policy_from_meta(ctx.meta)
    changed = (current.cache_mode != policy.cache_mode
               or current.expire_seconds != policy.expire_seconds)

    # Step 3: 更新 meta
    updated_meta = ctx.meta.with_updates(
        cache_mode=policy.cache_mode,
        ttl=policy.declared_ttl,
        updated_at=datetime.now(UTC),
    )
    updated_meta.save(ctx.r, ctx.key)

    # Step 4: 设 Redis TTL（仅在有变化时）
    if changed:
        apply_lifecycle(ctx.r, ctx.key, ctx.raw_key, ctx.ref_key,
                        ctx.is_collection, policy)

    # Step 5: 传播（仅在有变化时，避免不必要的 Redis 操作）
    if changed and propagate:
        from .ttl import propagate_lifecycle
        propagate_lifecycle(
            ctx.r, self.domain.name, self.namespace.name,
            self.canonical_name, policy, set(),
        )
```

注意：`set_lifecycle` 传播时使用 `self.canonical_name`（而非 `_binding_name()`），因为传播是基于生命周期 owner 概念的。

### 7.2 propagate_lifecycle 使用 pipeline 优化

```python
def propagate_lifecycle(r, domain, namespace, parent_name, policy, visited):
    """将 policy 传递给所有 owned 子对象（owner == parent_name，pipeline 优化）。"""
    parent_obj_key = make_object_key(domain, namespace, parent_name)
    parent_meta = ObjectMeta.load(r, parent_obj_key)
    if parent_meta is None or not parent_meta.is_collection():
        return

    child_names = collect_ref_names_from_raw(r, parent_meta.value, parent_meta.type)

    # 第一遍：收集需要更新的子对象
    updates = []
    for child_name in child_names:
        if child_name in visited:
            continue
        visited.add(child_name)

        child_obj_key = make_object_key(domain, namespace, child_name)
        child_meta = ObjectMeta.load(r, child_obj_key)
        if child_meta is None or child_meta.owner != parent_name:
            continue

        # 检查是否需要更新
        current = policy_from_meta(child_meta)
        if (current.cache_mode == policy.cache_mode
                and current.expire_seconds == policy.expire_seconds):
            continue

        updates.append((child_name, child_obj_key, child_meta))

    # 第二遍：pipeline 批量更新
    if updates:
        pipe = r.pipeline(transaction=False)
        now = datetime.now(UTC)
        for child_name, child_obj_key, child_meta in updates:
            updated = child_meta.with_updates(
                cache_mode=policy.cache_mode,
                ttl=policy.declared_ttl,
                updated_at=now,
            )
            updated.save(pipe, child_obj_key)  # 注意: 传 pipeline 而非 r

            child_raw_key = child_meta.value if child_meta.is_collection() else ""
            child_ref_key = make_ref_key(domain, namespace, child_name)
            apply_lifecycle(pipe, child_obj_key, child_raw_key, child_ref_key,
                            child_meta.is_collection(), policy)
        pipe.execute()

    # 第三遍：递归（子对象的子对象）
    for child_name, _, _ in updates:
        propagate_lifecycle(r, domain, namespace, child_name, policy, visited)
```

---

## 八、对评审意见的覆盖

| 评审# | 问题 | 本方案如何解决 |
|--------|------|----------------|
| #1 | effective_policy 对 inherited parent 返回 permanent | 删除 effective_policy，子对象 meta 自身携带有效值 |
| #2 | 生命周期变更不传播 | set_lifecycle 统一入口自动传播 |
| #3 | with_updates 绕过校验 | with_updates 改用 model_validate |
| #4 | 容器覆盖重置生命周期 | 按 meta.owner 区分覆盖行为（见第六节） |
| #5 | expire_in/expire_at 不同步 meta | set_lifecycle 统一更新 meta |
| #10 | alias 容器 ref_key 错误 | 生命周期不再依赖 ref_key 回溯，影响面大幅缩小 |
| #11 | setdefault 返回值 | 独立修复，与生命周期无关 |

---

## 九、alias 容器 ref_key 问题

#10 在新设计下影响面缩小但未完全消除。`_touch()` 中 `apply_lifecycle(ctx.r, ctx.key, ctx.raw_key, ctx.ref_key, ...)` 仍会对 alias 容器自身（非子对象）设置 TTL，此时 `self.ref_key` 是基于 alias name 的。

修复方案：在 `_get_object()` 中对 alias 容器同时覆盖 `obj.ref_key` 为 canonical ref key。这是一个独立的小修复，不依赖本方案的其他部分。

---

## 十、向后兼容

`ObjectMeta.from_redis` 需要处理旧格式数据：
- 读到 `cache_mode="inherited"` 时：设 `owner=""`（无法确定原始 parent），`cache_mode` 回退为 `"permanent"`，`ttl=0`
- 新数据：`owner` 字段从 Redis hash 读取，`cache_mode` 始终为有效值

外部 API 无变化。`effective_policy()` 和旧 `propagate_lifecycle()` 签名内部替换，不导出为公共 API。

---

## 十一、新增异常类型

```python
class LifecyclePermissionError(GPDCError):
    """Raised when attempting to modify lifecycle of an object not owned by the caller.

    The caller must first call take_ownership() on a named reference to gain
    lifecycle management rights for the underlying anonymous object.
    """
```

所有 P10 权限检查失败时抛出此异常。

---

## 十二、变更影响文件清单

| 文件 | 变更类型 | 变更内容 |
|------|---------|---------|
| `meta.py` | 修改 | ObjectMeta 增加 owner 字段，cache_mode Literal 移除 "inherited"，from_redis/to_redis_mapping 增加 owner 处理，移除 is_inherited()，with_updates 改用 model_validate |
| `ttl.py` | 修改 | 删除 effective_policy()，重写 propagate_lifecycle() 增加 owner 检查和 pipeline 优化，修改 policy_from_meta() 适配新模型 |
| `objects.py` | 修改 | 新增 set_lifecycle()、take_ownership()；cache_mode/ttl/expire_in/expire_at/persist 改用 set_lifecycle() + P10 权限检查；meta.setter 改用 set_lifecycle()；_touch() 支持 owner 链向上传播 |
| `container.py` | 修改 | _binding_owner_name() → _binding_name()；_touch() 重写支持 owner 链向上传播；所有 encode_element 调用点改为传 policy；__setitem__ 增加 meta.owner 覆盖行为判断 |
| `namespace.py` | 修改 | _write_scalar_meta/_write_container_meta 增加 owner 参数；__setitem__ 中容器覆盖行为按 meta.owner 处理；_get_object() 中 alias 容器覆盖 ref_key |
| `types.py` | 修改 | encode_element 签名变更（cache_mode/ttl → policy）；_create_anonymous_object 签名变更，ObjectMeta 构造使用有效值 + owner |
| `exceptions.py` | 修改 | 新增 LifecyclePermissionError |
| `scalar.py` | 修改 | value setter 中 sliding touch 改用 set_lifecycle()，增加 P10 权限检查 |

---

## 十三、已确认事项

1. **expire_in/expire_at**: 仅 `cache_mode="ttl"` 的对象可调用，sliding 也不允许。简化设计。
2. **take_ownership()**: 无参数，仅命名引用对象可调用。自动将 underlying 匿名对象的 owner 设为调用者的 canonical_name。
3. **pipeline 优化**: `propagate_lifecycle` 使用 pipeline 批量更新子对象，减少 Redis round trips。且仅在有实际变化时才执行传播。
4. **`updated_at` 保留**: 讨论后确认保留。去掉它对减少 Redis 写入开销收益很小（`item_count` 更新仍需每次写操作查询计数 + HSET），而 `updated_at` 提供了有用的自省能力。
5. **`item_count` 保留**: 用于 GC 区分"空容器的 raw key 被删除"和"有数据的容器 raw key 丢失"。Redis 不能持久化空容器，没有 `item_count` 就无法区分这两种情况。
6. **`_binding_name()` vs `meta.owner`**: 职责分离。`_binding_name()` 用于 ref 管理（谁绑定的对象），`meta.owner` 用于生命周期管理（谁管我的生命周期）。
7. **命名对象创建**: `_write_scalar_meta` 和 `_write_container_meta` 自动设置 `owner = key`，不需要用户传参。
8. **`meta.setter`**: 内部提取 `policy_from_meta(new_meta)` 然后走 `set_lifecycle()` 传播。
9. **容器覆盖**: 按 `meta.owner` 区分行为。命名对象覆盖用 namespace 默认 policy；匿名子对象覆盖沿用旧 policy；已被 take_ownership 的子对象不删除，仅更新引用。
10. **权限模型**: 统一基于 `meta.owner == self.canonical_name` 检查，不匹配时抛 `LifecyclePermissionError`。

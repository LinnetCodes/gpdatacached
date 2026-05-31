# Code Review Progress Tracker

## 背景

评审报告: `review/code-review-report.md`，覆盖 gpdatacached 全部源码，共 23 个问题。

### 处理原则

- 每次对话只解决一个问题，充分讨论后再改动。
- 不盲信评审报告，客观分析每个问题是否是真正的问题并有必要解决。
- 评审员可能未完全了解设计意图，已关闭的 1.1 就是例子。

## 进度

| # | 问题 | 优先级 | 状态 | 备注 |
|---|------|--------|------|------|
| **P0** | | | | |
| 1.1 | `_owner_still_refs_encoded` 遗漏 set 类型 | P0 | ✅ 已关闭 | by design: SetObject 不允许存放容器元素，加注释说明 |
| 1.3 | pydantic 未声明为显式依赖 | P0 | ✅ 已修复 | 在 dependencies 中显式添加 `pydantic>=2.0` |
| 7.3 | 测试 fixture 不清理 GC 线程 | P0 | ✅ 已修复 | `domain` 和 `domain_with_namespaces` fixture 改为 yield + close() teardown |
| **P1** | | | | |
| 2.1 | `_base.py` 5 个生命周期方法重复 | P1 | ✅ 已修复 | 提取 `_LifecycleContext` NamedTuple + `_load_lifecycle_context()` 辅助方法 |
| 5.1 | `get_object` 命名误导 | P1 | ✅ 已修复 | 拆分为只读 `get_object` + 读写 `ensure_object` |
| 5.2 | `__getitem__` 返回类型不一致 | P1 | ✅ 已关闭 | by design: 标量返回原生值，容器返回对象本身，加注释说明 |
| 5.4 | `_resolve_binding` 返回无类型 dict | P1 | ✅ 已修复 | 提取 `BindingResolution` NamedTuple，所有调用方改为属性访问 |
| 1.2 | `namespace.clear()` 绕过清理逻辑 | P1 | ✅ 已关闭 | by design: 跨命名空间引用被禁止，clear 批量删除安全，加 docstring 说明 |
| 3.5 | `meta_ttl_value` 死代码 | P1 | ✅ 已修复 | 删除函数及其测试，重构遗留死代码 |
| **P2** | | | | |
| 2.3 | ListObject 负索引标准化重复 | P2 | ✅ 已修复 | 提取 `_normalize_index()` 辅助方法，消除 4 处重复 |
| 2.4 | SetObject 四个集合运算方法重复 | P2 | ✅ 已修复 | 提取 `_binop(self, other, op)` 辅助方法，四个方法各变为一行委托 |
| 2.5 | ref name 收集逻辑重复 | P2 | ✅ 已修复 | 统一到 `keys.py` 的 `collect_ref_names_from_raw` + `_extract_ref_name` | |
| 4.1 | `objects.py` 无意义的重导出 | P2 | ✅ 已修复 | 重构：`_base.py` 内容移入 `objects.py`（基类），`__init__.py` 负责汇聚导出，删除旧重导出模块 |
| 4.3 | gc.py 绕过 keys.py 手动拼接 | P2 | ✅ 已修复 | 在 keys.py 补充 make_raw_key_prefix/make_ref_key_prefix，gc.py 改用 keys.py 函数构建所有 key |
| 7.4 | gc.py 154 行单函数 | P2 | ✅ 已修复 | 拆分为 `_gc_scan_obj_keys`、`_gc_scan_raw_keys`、`_gc_scan_ref_keys` 三个子函数 |
| 7.1 | GC 线程自动启动 | P2 | ✅ 已关闭 | by design: `gc_enabled` 默认 `False`，已有 `run_gc()`/`close()`/context manager 控制 |
| **P3** | | | | |
| 3.1 | `ObjectMeta.ext` 包装方法过度设计 | P3 | ✅ 已修复 | 删除六个包装方法，`ext` 直接暴露为 `dict[str, str]`，删除对应测试 |
| 3.2 | `populate_raw` 未使用 | P3 | ✅ 已修复 | 诞生即死代码，安全删除 |
| 3.3 | `_pop_value_from_encoded` 无意义包装 | P3 | ✅ 已修复 | 内联为 `_materialize_decoded_value` 直接调用 |
| 6.1 | `namespaces` 可变默认值 | P3 | ✅ 已修复 | 改为 `dict[str, GPDCNamespaceConfig] = Field(default_factory=dict)`，简化 `_init_predefined_namespaces` |
| 6.6 | `decode_element` 多余 isinstance | P3 | ✅ 已修复 | 移除永远为 True 的 `isinstance(encoded, str)` 检查 |
| 6.7 | DictObject.keys/values/items 返回 list | P3 | ✅ 已关闭 | by design: DictObject 不继承 dict，Redis 远程数据无法支持视图语义，返回 list 快照合理，加 docstring 说明 |
| 7.5 | 考虑移除 pydantic 依赖 | P3 | ✅ 已关闭 | by design: gpconfig.GPConfig 继承 pydantic.BaseModel，pydantic 为不可避免传递依赖；1.3 已显式声明依赖，无实际收益 |
| 7.5a | ObjectMeta 用 pydantic 性能是否合适 | P3 | ✅ 已关闭 | 基准测试：pydantic model_copy 1.1us vs dataclass replace 1.6us，热点路径反而更优；相比 Redis 往返 ~100-1000us 开销可忽略 |

---

## 未列入修改列表的问题

以下问题在评审报告 §1-§7 中提及，但未被收录到 §8 按优先级排序的修改建议中。经逐一验证当前代码状态和影响评估，补充如下：

| # | 问题 | 建议优先级 | 状态 | 评估 |
|---|------|------------|------|------|
| 4.2 | `ListObject.remove` 从自己所在模块导入 (`container.py:260`) | P1 | ✅ 已修复 | 删除自引用 lazy import，直接使用模块级已定义的 `GPDCContainerObject`。 |
| 2.2 | `objects.py` 属性重复加载 meta (`objects.py:83-139`) | P2 | ✅ 已修复 | 增加 `meta` 属性（getter 加载 ObjectMeta，setter 保存并应用 lifecycle），4 个独立 getter 改为委托 `self.meta`。用户可通过 `obj.meta` 一次读取或批量修改 meta，避免多次 Redis 往返。 |
| 7.2 | `__iter__` 逐名调用 `_resolve_binding` 效率低 (`namespace.py:484`) | P2 | ✅ 已修复 | 使用 Redis pipeline 批量 TYPE/GET 命令，每 SCAN 页 O(N) 往返降至 ~3 次 pipeline 往返。hash 类型直接 yield，string 类型（ref 别名）批量解析 canonical key 有效性。语义不变，835 测试全部通过。 |
| 2.6 | `decode_element` 与 `decode_key` 解析逻辑重复 (`types.py:94-120`) | P3 | ✅ 已修复 | 提取 `_decode_scalar(encoded)` 辅助函数，两个函数各委托给它。 |
| 3.4 | `_reject_container_slot_overwrite` / `_reject_value_replacement` 是一次性方法 (`container.py:82-88`) | — | ✅ 已关闭 | 经验证各被调用 3 次（非报告所述 1 次），提取为方法有集中错误消息的合理性。已加注释说明设计意图。 |
| 5.5 | `TupleObject` 注册为 scalar 但行为类似 container (`types.py:292`) | P3 | ✅ 已关闭 | by design: tuple 是不可变的，作为"复合标量"存储为单个 Redis string，拒绝容器元素避免 GC 复杂性。加注释说明。 |
| 6.2 | `GPDCDomain._namespaces` 类型标注为裸 `dict` (`domain.py:32`) | P3 | ✅ 已修复 | 改为 `dict[str, GPDCNamespace]`，通过 `TYPE_CHECKING` 避免循环导入。 |
| 6.3 | `GPDCDomain._redis` 类型过度宽松 (`domain.py:31`) | P3 | ✅ 已修复 | 类型改为 `redis.Redis`，用 `# type: ignore[assignment]` 处理初始化临时 None，消除所有下游 None 检查。 |
| 6.4 | `redis` 属性 getter 返回类型与字段类型不一致 (`domain.py:94-95`) | P3 | ✅ 已修复 | 随 6.3 一起修复，`_redis` 不再是 `| None`，property 返回类型自然一致。 |
| 6.5 | `__len__` 中 `_` 忽略循环变量 (`namespace.py:498`) | — | ✅ 已关闭 | `sum(1 for _ in self)` 是常见 Python 惯用法，`_` 语义为丢弃对象名称。无需修改，加注释说明。 |
| 5.3 | `_MISSING` 哨兵值位置不当 (`namespace.py:27`) | — | ✅ 已解决 | 随 5.1 修复（拆分 `get_object`/`ensure_object`）已移除，不再存在。 |

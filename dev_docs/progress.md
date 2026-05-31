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
| 4.1 | `objects.py` 无意义的重导出 | P2 | 🔲 待讨论 | |
| 4.3 | gc.py 绕过 keys.py 手动拼接 | P2 | 🔲 待讨论 | |
| 7.4 | gc.py 154 行单函数 | P2 | 🔲 待讨论 | |
| 7.1 | GC 线程自动启动 | P2 | 🔲 待讨论 | |
| **P3** | | | | |
| 3.1 | `ObjectMeta.ext` 包装方法过度设计 | P3 | 🔲 待讨论 | |
| 3.2 | `populate_raw` 未使用 | P3 | 🔲 待讨论 | |
| 3.3 | `_pop_value_from_encoded` 无意义包装 | P3 | 🔲 待讨论 | |
| 6.1 | `namespaces` 可变默认值 | P3 | 🔲 待讨论 | |
| 6.6 | `decode_element` 多余 isinstance | P3 | 🔲 待讨论 | |
| 6.7 | DictObject.keys/values/items 返回 list | P3 | 🔲 待讨论 | |
| 7.5 | 考虑移除 pydantic 依赖 | P3 | 🔲 待讨论 | |

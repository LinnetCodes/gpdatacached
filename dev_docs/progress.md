# 进度跟踪

## 当前状态

生命周期管理重构已全部实现，975 测试全部通过。待进行代码审查和合并。

## 已完成

1. **评审报告验证** — 对 `review/2026-06-01-code-review-report.zh-CN.md` 的 27 条意见逐条验证，结果写入 `dev_docs/2026-06-01-review-verification-and-refactoring-plan.md`。14 条确认为真实问题，10 条属实但不修，1 条有误（#16），2 条风格偏好。

2. **生命周期管理现状梳理** — 梳理了当前 7 个生命周期变更入口、Policy 产生和应用的完整流程，确认核心问题是多入口不一致和 `effective_policy()` 对嵌套 inherited 对象的 bug。

3. **新设计方案讨论与确认** — 通过多轮讨论确定了 10 条设计原则（P1-P10），方案写入 `dev_docs/2026-06-02-lifecycle-redesign.md`。将 `inherited: bool` 方案改为 `owner: str` 方案：用明确的 canonical name 标记生命周期管理者，解决多引用冲突问题。

4. **实现计划制定** — 写入 `dev_docs/plans/2026-06-04-lifecycle-redesign.md`，拆分为 12 个有序任务。

5. **生命周期重构实现（Task 1-12 全部完成）**：
   - **Task 1**: 新增 `LifecyclePermissionError` 异常
   - **Task 2**: `ObjectMeta` 增加 `owner: str` 字段，移除 `cache_mode="inherited"`，`with_updates` 改用 `model_validate`
   - **Task 3**: 删除 `effective_policy()`，重写 `propagate_lifecycle()` 增加 owner 检查 + pipeline 优化
   - **Task 4**: 新增 `set_lifecycle()` 统一入口和 `take_ownership()` API，P10 权限检查
   - **Task 5**: `_binding_owner_name()` → `_binding_name()`，`encode_element`/`_create_anonymous_object` 签名改为接收 `policy: TTLPolicy`
   - **Task 6**: `_touch()` 重写支持 owner 链向上传播
   - **Task 7**: `_write_scalar_meta`/`_write_container_meta` 增加 owner 初始化，alias 容器 ref_key 修正
   - **Task 8**: `scalar.py` value setter 改用 `set_lifecycle()`
   - **Task 9**: `_unbind_old_ref` 增加 owner 检查，保护已 take_ownership 的子对象
   - **Task 10**: 修复全量 14 个旧测试，适配新模型
   - **Task 11**: 独立小修复 — setdefault 返回值、pop/update 语义、discard no-op touch、GPDCContainerCodec 导出
   - **Task 12**: 更新进度文档

## 关键设计决策

- **`cache_mode` 不再包含 `"inherited"` 值**，新增 `owner: str` 字段存放生命周期管理者的 canonical name
- **命名对象 `owner = 自身 canonical_name`（不可更改）**，匿名对象 `owner = 直接 parent canonical_name`
- **子对象 meta 直接保存有效的 cache_mode/ttl**，不需要运行时向上回溯
- **删除 `effective_policy()` 函数**，消除嵌套回溯 bug（#1）
- **收敛为单一入口 `set_lifecycle()`**，所有生命周期变更统一走此路径
- **`propagate_lifecycle` 带 pipeline 优化**，仅传播到 `owner == 当前对象 canonical_name` 的子对象
- **`take_ownership()` 无参数，仅命名引用可调用**，自动将 owner 设为调用者 canonical_name
- **`expire_in`/`expire_at` 仅限 `cache_mode="ttl"`**，sliding 也不允许
- **权限检查统一基于 `meta.owner == self.canonical_name`**（P10），`LifecyclePermissionError`
- **`_binding_name()` vs `meta.owner` 职责分离**：ref 管理用前者，生命周期管理用后者
- **`updated_at` 和 `item_count` 保留**

## 已覆盖的评审意见

| 评审# | 问题 | 状态 |
|--------|------|------|
| #1 | effective_policy 对 inherited parent 返回 permanent | ✅ 已修复 |
| #2 | 生命周期变更不传播 | ✅ 已修复 |
| #3 | with_updates 绕过校验 | ✅ 已修复 |
| #4 | 容器覆盖重置生命周期 | ✅ 已修复 |
| #5 | expire_in/expire_at 不同步 meta | ✅ 已修复 |
| #10 | alias 容器 ref_key 错误 | ✅ 已修复 |
| #11 | setdefault 返回值 | ✅ 已修复 |
| #12 | pop/update 语义不一致 | ✅ 已修复 |
| #19 | discard 无修改也刷新 TTL | ✅ 已修复 |
| #25 | GPDCContainerCodec 未导出 | ✅ 已修复 |

## 下一步

1. **代码审查** — 对整个重构进行代码审查
2. **处理评审报告中剩余低优先级项** — #17 Config 跨字段校验、#18 GC 死代码删除、#21 ListObject slice 报错、#26 错误信息包含 canonical_uri
3. **合并到主分支**

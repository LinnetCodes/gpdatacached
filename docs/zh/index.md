# gpdatacached

**gpdatacached** —— *General-Purpose Data Cached* —— 是一个基于 Redis 的**跨进程数据共享与缓存** Python 库。任何能够访问同一 Redis 实例的进程，都可以共享活跃的、可变的、带类型的数据结构 —— 标量、list、dict、set、pydantic 模型、pandas Series/DataFrame —— 并具备引用语义、逐对象生命周期控制以及可选的分布式锁。

本索引是文档的入口。下方每一节都链接到一份专题文档；如果你是初次使用本库，请从“总体概览”开始。

## 从这里开始

- **[总体概览](./overview.md)** —— *设计目的、设计理念与端到端整体架构。* 请先阅读本文，理解 Domain → Namespace → Object 模型、Redis key 布局、owner/reference/匿名对象模型，以及后续每份文档在整体中的位置。

## 参考资料

- **[API 参考](./api.md)** —— *完整的公共 API 参考。* `gpdatacached` 导出的每个类、方法、属性、异常与配置字段，含签名与语义。权威规范。

## 深入概念

- **[生命周期管理](./lifecycle.md)** —— *对象的创建、过期、刷新与删除。* 解释三种缓存模式（`permanent` / `ttl` / `sliding`）、策略来源、滑动窗口刷新、所有权模型，以及匿名对象与引用对象的生命周期。阅读本文以对对象何时出现、何时消失建立合理预期。
- **[可扩展性](./extensibility.md)** —— *类型系统与如何添加自定义类型。* Codec 契约、对象类契约、四步注册配方，以及自定义标量（`Color`）与自定义容器（`Counter`）的完整示例。绝大多数用户用不到本文；需要者还应同时阅读生命周期、加锁与性能指南。

## 运维指引

- **[性能指南](./performance-guide.md)** —— *会拖累性能的用法及其推荐替代。* 涵盖滥用 sliding TTL、过深容器嵌套、不必要地启用 GC、锁的误用、往返次数反模式，以及经核实的每操作成本表。把 GPDC 放上热路径前必读。
- **[多进程加锁](./locking.md)** —— *跨进程的可选分布式锁。* 何时需要加锁、它带来的每操作开销、推荐用法、避免死锁的顺序，以及 GC 与写入者的契约。

## 可选类型扩展

- **[Pydantic 支持](./pydantic-support.md)** —— 缓存 pydantic `BaseModel` 记录。两种存储模式：标量模式（原子 JSON，适用于纯标量模型），容器模式（Redis hash，适用于需要字段级访问或嵌套容器字段的模型）。pydantic 是核心依赖，无需额外安装。
- **[Pandas 支持](./pandas-support.md)** —— 将 pandas `Series` / `DataFrame` 缓存为活跃对象。在线选择性访问器（`.iloc`、`.loc`、`df[col]`）用于一次性子集读取；`.value` 用于完整还原；`extend` 用于追加。需要 `pip install "gpdatacached[pandas]"`。

---

## 建议阅读顺序

| 你的身份 | 建议阅读 |
|---|---|
| 初次使用本库 | 总体概览 → API 参考 → API 参考中的“快速开始” |
| 正在构建真实工作负载 | 再加 生命周期管理 → 性能指南 |
| 走向多进程 | 再加 多进程加锁 |
| 需要自定义类型 | 可扩展性 → （若类型复杂，再加 生命周期、加锁、性能指南） |
| 缓存结构化记录 | Pydantic 支持 |
| 缓存表格数据 | Pandas 支持 |

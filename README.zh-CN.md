# gpdatacached

> *General-Purpose Data Cached*（通用数据缓存）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Development Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://pypi.org/project/gpdatacached/)

一个基于 Redis 的**跨进程数据共享与缓存** Python 库。任何能够访问同一 Redis 实例的进程，都可以共享活跃的、可变的、带类型的数据结构 —— 标量、list、dict、set —— 并具备引用语义、生命周期控制以及可选的分布式锁。

> 其他语言：[English](./README.md)

---

## 为什么需要它

跨进程共享可变状态一直很麻烦：`multiprocessing.Manager` 会形成单进程瓶颈；直接使用 Redis 又需要你手动序列化所有数据，且没有类型安全、没有引用追踪、没有 TTL 级联。`gpdatacached` 在 Redis 之上提供了一套类 dict 的 API，特性包括：

- **带类型的对象** —— 存储 `int`、`str`、`datetime`、`list`、`dict`、`set` …，读取时得到相同类型。
- **活跃容器** —— `ns["tags"].append("x")` 直接修改 Redis，避免对整个结构的“读-改-写”竞态。
- **引用共享** —— 在同一 namespace 内，同一个容器可绑定到多个名称；数据仅存储一份。
- **生命周期控制** —— 每个对象可设置 `permanent` / `ttl` / `sliding`（滑动窗口）缓存模式，并将策略级联传播到所拥有的子树。
- **后台 GC** —— 自动回收过期或成为孤儿的匿名对象。
- **可选的分布式锁** —— 通过 `obj.lock()` 在多进程间串行化写入。
- **基于 URI 的访问** —— 通过 `GPDataCache` 从配置管理多个 domain，并将 `gpdc://{domain}/{namespace}/{name}` 形式的 URI 解析为数据对象，包括对匿名嵌套容器的遍历。
- **可选扩展** —— 可缓存 [pydantic](https://docs.pydantic.dev/) `BaseModel` 记录与 [pandas](https://pandas.pydata.org/) `Series` / `DataFrame`（见[扩展](#扩展)）。

---

## 安装

```bash
pip install gpdatacached
```

可选扩展：

```bash
pip install "gpdatacached[pandas]"   # 支持 pandas DataFrame / Series
```

环境要求：**Python ≥ 3.10**，**Redis ≥ 5.0**。

---

## 快速开始

```python
from gpdatacached import GPDCDomain, GPDCDomainConfig

config = GPDCDomainConfig(
    redis_host="127.0.0.1",
    redis_port=6379,
    redis_password="secret",
)
domain = GPDCDomain(config, name="mydomain")
ns = domain.ensure_namespace("analytics")

# 标量读取出来是原生 Python 值。
ns["counter"] = 42
assert ns["counter"] == 42

# 容器是活跃的、由 Redis 支撑的对象。
ns["tags"] = ["a", "b", "c"]
ns["tags"].append("d")
ns["tags"].pop(0)
assert ns["tags"].value == ["b", "c", "d"]

# 生命周期控制。
ns["temp"] = "x"
ns.get_object("temp").expire_in(60)   # 要求 cache_mode="ttl"

domain.close()
```

---

## 核心概念

```
GPDCDomain ── GPDCNamespace ── GPDCDataObject (标量 | 容器)
```

| 概念 | 角色 |
|---|---|
| **Domain（域）** | 配置根 + Redis 连接池 + 锁/GC 默认值。 |
| **Namespace（命名空间）** | 逻辑隔离空间，行为类似 `MutableMapping`。**不支持**跨 namespace 的引用。 |
| **Data Object（数据对象）** | 一个具名、带类型的值。标量读取返回原生 Python 值；容器返回活跃对象。 |

**缓存模式：** `permanent`（默认，永不过期）、`ttl`（固定过期）、`sliding`（每次变更都刷新）。

---

## 文档

📖 **[完整文档索引](./docs/zh-CN/index.md)** —— 以下所有指南与参考的入口。

- **[总体概览](./docs/zh-CN/overview.md)** —— 设计目的、设计理念与端到端整体架构（从这里开始，从整体上理解本库）。
- **[API 参考](./docs/zh-CN/api.md)** —— 完整的公共 API 参考。
- **[生命周期管理](./docs/zh-CN/lifecycle.md)** —— 对象的创建、过期、刷新与删除如何运作，含匿名对象与引用对象（阅读本文以建立合理预期）。
- **[可扩展性](./docs/zh-CN/extensibility.md)** —— 类型系统详解，含添加自定义标量与容器类型的完整示例（codec、对象类、注册）。
- **[性能指南](./docs/zh-CN/performance-guide.md)** —— 会拖累性能的用法（滥用 sliding TTL、过深嵌套、不必要地启用 GC/加锁）及其推荐替代。把 GPDC 放上热路径前必读。
- **[多进程加锁](./docs/zh-CN/locking.md)** —— 何时需要加锁、性能影响、推荐用法、避免死锁的顺序以及与 GC 的交互。

## 扩展

`gpdatacached.extensions` 下的可选内建扩展：

- **[Pydantic 支持](./docs/zh-CN/pydantic-support.md)** —— 缓存 pydantic `BaseModel` 记录。两种存储模式：标量模式（原子 JSON，适用于纯标量模型）和容器模式（Redis hash，适用于需要字段级访问或嵌套 `list`/`dict`/`set` 字段的模型）。pydantic 是核心依赖，无需额外安装。
- **[Pandas 支持](./docs/zh-CN/pandas-support.md)** —— 将 pandas `Series` / `DataFrame` 缓存为活跃对象。在线选择性访问器（`.iloc`、`.loc`、`df[col]`）用于一次性子集读取；`.value` 用于完整还原；`extend` 用于追加。需要 `pip install "gpdatacached[pandas]"`。

---

## 运行测试

测试套件需要一个**本地 Redis 实例**，可通过 `127.0.0.1:6379` 访问，密码为 `123456`：

- **Linux** —— 直接在主机上运行 Redis。
- **Windows** —— 在 WSL 中运行 Redis；从 Windows 侧可通过 `localhost` / `127.0.0.1` 访问。

> ⚠️ 某些测试会**清空 Redis 数据库**（`FLUSHDB`）。请务必使用专供测试用的 Redis 实例 —— 切勿将测试指向生产数据库。

运行测试：

```bash
pytest
```

你也可以修改 `tests/conftest.py` 中的 Redis 配置来适配自己的实例，但不推荐这样做 —— 运行一个专用的、一次性的测试 Redis 通常成本更低也更安全。

---

## 许可证

[MIT](./LICENSE) © Linnet Codes

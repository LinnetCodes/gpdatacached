# 多进程加锁

`gpdatacached` 提供一个可选的分布式锁，用于在多个进程间串行化对 `GPDCDataObject` 的访问。

> 其他语言：[English](../locking.md)

---

## 目录

- [何时需要加锁](#何时需要加锁)
- [性能影响](#性能影响)
- [用法](#用法)
- [推荐用法](#推荐用法)
- [锁的范围](#锁的范围)
- [获取多把锁](#获取多把锁)
- [非阻塞尝试](#非阻塞尝试)
- [与 GC 的交互](#与-gc-的交互)
- [配置](#配置)
- [修改锁配置](#修改锁配置)
- [失败模式](#失败模式)
- [已知限制](#已知限制)

---

## 何时需要加锁

| 场景 | 是否需要加锁？ |
|---|---|
| 单进程读写 | 否 |
| 多进程读，单进程写 | 否（每条 Redis 命令本身是原子的） |
| 多进程写同一个对象 | **是** —— 使用 `obj.lock()` |
| `gc_enabled=True` 且存在并发写入者 | **是** —— 所有写入者都必须使用 `obj.lock()` |

锁是**可选的、建议性的（advisory）**。GPDC 永远不会替你加锁 —— 变更方法本身不加锁。一把锁只能协调那些主动调用 `obj.lock()` 的进程。只要有一个写入者绕过加锁，保证就不复存在。

**经验法则：** 先不加锁。只有在你测量到某个具体对象存在竞争时，再加锁。

---

## 性能影响

每一次加锁操作相对于不加锁都会付出实实在在的代价。在广泛采用加锁之前，请先理解这一点。

### 每次操作的 Redis 开销

| 操作 | Redis 往返次数 | 说明 |
|---|---|---|
| `obj.lock()`（无竞争快速路径） | **1** | `SET NX EX` |
| `obj.lock()`（有竞争） | 1 + 每次重试 2 次 | `SET NX EX` 失败 → `BLPOP` 等待（一次往返）→ `SET NX EX` 重试。每次重试循环是两次往返（BLPOP 等待 + SET）。 |
| `held.release()` | **1** | 一段 Lua 脚本（`GET`+`DEL`+`RPUSH`+`EXPIRE` 在服务端批量执行） |

因此，**无竞争**的关键区相对于不加锁路径至少多 **+2 次往返**（获取 + 释放）。在典型局域网下约 0.2–2 ms；在广域网下可能达 10–100 ms。

### 竞争下的吞吐

锁会**串行化**访问。某个对象在竞争下的最大吞吐约为：

```
吞吐 ≈ 1 / (关键区持续时间 + 2 × RTT)
```

如果关键区本身很快（例如一次自增），且有 N 个进程竞争，每个进程实际上都在排队。一个被激烈竞争的热点计数器可能成为全局的串行化瓶颈。

### 竞争下的延迟

等待者会在 `BLPOP`（其超时上限为锁的 `ttl`）中阻塞，直到持有者释放（通过 `RPUSH` 唤醒）或 BLPOP 自身的超时到期。超时后，等待者会重试 `SET NX EX`，一旦锁的 TTL 已过期即获成功。等待者最坏情况下的延迟 ≈ 持有者关键区的持续时间（外加唤醒的一次往返）。若持有者在关键区中崩溃，等待者会在约 `ttl` 秒内恢复（BLPOP 超时，随后一次成功的 `SET NX EX` 重试）。

### 隐性的放大因子

- **嵌套加锁** —— 一次逻辑操作获取 K 把锁，获取开销为 K 倍，且整段关键区都要持有全部 K 把锁。
- **热循环中加锁** —— N 次迭代每次都付出 +2 次往返，会盖过真正的工作。请改为批量（用一个锁包住整个循环，或通过 `.value` 把数据拉到本地一份再处理）。
- **`gc_enabled=True`** —— 每个写入者都必须加锁，因此 +2 次往返的开销会落在竞争对象的**每一次**变更上，而不仅是你标记的那些。

### 锁什么时候很便宜

- **无竞争** —— 快速路径只需一次 `SET NX EX`；唤醒队列根本不会触及。
- **关键区很短** —— 锁持有时间短，等待者很少阻塞。
- **到 Redis 的局域网** —— 亚毫秒级的 RTT 使开销可忽略。

> 加锁只是若干性能话题之一。完整图景 —— 滥用 sliding TTL、过深嵌套、不必要地启用 GC、往返次数反模式 —— 见[性能指南](./performance-guide.md)。

---

## 用法

```python
from gpdatacached import GPDCDomain, GPDCDomainConfig

config = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_password="...")
domain = GPDCDomain("mydomain", config)
ns = domain.ensure_namespace("myns")

ns["counter"] = 0
counter = ns.get_object("counter")

# 获取锁、变更、释放。
with counter.lock():
    current = counter.value
    counter.value = current + 1
```

当 `with` 块退出时（包括抛出异常时），锁会被自动释放。

---

## 推荐用法

1. **默认不要加锁。** 单写入者与低竞争工作负载不需要加锁。应当**针对测量到的竞争**被动加锁，而非投机性地预先加锁。
2. **尽量晚地获取锁，尽量早地释放锁。** 在读-改-写之前立即获取，完成后立即释放。持锁期间不要做任何 I/O 或重计算。
3. **关键区保持最小。** 读 → 计算 → 写放在锁内；其他一切（记日志、校验入参、用无关数据构造新值）放在锁外。
4. **不要在热循环中加锁。** 要么把锁提到循环外整体包住，要么通过 `.value` 把数据拉到本地一次处理完，再一次性写回。
5. **多对象操作时，始终按 canonical name 升序获取锁**（见[获取多把锁](#获取多把锁)）。GPDC 自己的 GC 也遵循此规则。
6. **若 `gc_enabled=True`，每个并发写入者都必须加锁** —— 没有例外。见[与 GC 的交互](#与-gc-的交互)。
7. **`ttl` 要匹配你最坏情况的关键区时长。** 如果操作可能合法地超过默认 `ttl`，请显式传入：`obj.lock(ttl=120)`。超过 `ttl` 的操作会在执行途中静默失去锁。
8. **投机路径优先用 `try_lock()`。** 当你可以推迟或跳过这次工作时，`try_lock()` 能避免阻塞，让调用方自行决定。
9. **一次逻辑操作 = 一次加锁。** 同一进程重复获取同一把锁会死锁（不可重入）。

---

## 锁的范围

`obj.lock()` 加锁的是对象的 **canonical name**（数据所有者）。对于引用对象，调用 `lock()` 会解析到它所指向的 canonical 对象的同一把锁。

按约定，持有此锁的写入者对该对象自身的 Redis key（元信息、原始数据、引用者集合）拥有独占访问权。锁是**建议性的** —— 它只协调那些主动调用 `obj.lock()` 的进程；它并不会在物理上阻止绕过加锁的进程访问 Redis。

锁**不会**自动保护父对象方法对匿名子对象所做的引用者修改 —— 见[已知限制](#已知限制)。

---

## 获取多把锁

要在两个对象之间原子地移动元素，你需要同时持有两把锁。**始终按 canonical name 升序获取**，以避免死锁：

```python
# canonical name 排序：'a' 在 'b' 之前
with ns.get_object("a").lock():
    with ns.get_object("b").lock():
        # ... 原子地转移 ...
```

GPDC 内部的 GC 也遵循同样的排序规则。

---

## 非阻塞尝试

```python
held = obj.try_lock()
if held is not None:
    try:
        # ... 关键区 ...
    finally:
        held.release()
else:
    # 别人持有锁；自行决定怎么办
```

---

## 与 GC 的交互

当 `gc_enabled=True` 时，后台 GC 会对它处理的每个对象尝试加锁。如果在 `gc_lock_wait_seconds`（默认 5 秒）内无法获取，GC 会在本周期跳过该对象。（GC 清理什么、何时运行，详见[生命周期管理](./lifecycle.md#10-垃圾回收)。）

**契约：** 若 `gc_enabled=True`，所有并发写入者都必须使用 `obj.lock()`。绕过加锁的写入者可能与 GC 竞态并破坏状态。

---

## 配置

锁配置位于 `GPDCDomainConfig` 上：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `default_lock_ttl_seconds` | 30 | 锁 key 的 TTL。持有者崩溃时自动释放锁。必须大于你最坏情况下的操作时长。 |
| `default_lock_acquire_timeout_seconds` | 30 | `obj.lock()` 在抛出 `LockAcquisitionTimeout` 之前的阻塞时长。 |
| `gc_lock_ttl_seconds` | 30 | GC 获取的锁的 TTL。 |
| `gc_lock_wait_seconds` | 5 | GC 对单个对象的 try-lock 超时。超时则跳过该对象。 |

这四个值在首次打开时写入 Redis 中的 domain meta hash。**所有访问同一 domain 的进程必须使用相同的值。** 若第二个进程以不同的锁配置打开该 domain，会抛出 `GPDCLockConfigInconsistentError`。

---

## 修改锁配置

在以新值打开 domain 之前，使用 `reset_domain_config` 清除 Redis 中已存储的锁配置字段：

```python
from gpdatacached import reset_domain_config, GPDCDomain, GPDCDomainConfig

old_config = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_password="...")
reset_domain_config("mydomain", old_config)

new_config = GPDCDomainConfig(
    redis_host="127.0.0.1", redis_port=6379, redis_password="...",
    default_lock_ttl_seconds=60,
)
domain = GPDCDomain("mydomain", new_config)
```

---

## 失败模式

| 故障 | 行为 |
|---|---|
| 持有者进程崩溃 | 锁在 `ttl` 后自动过期；等待者在 `ttl + ε` 内获取。 |
| 操作超过 `ttl` | 锁在操作途中自动过期；另一个进程可能获取；释放时记录一条警告。请把 `ttl` 设到最坏情况时长。 |
| 配置不一致 | 在构造 domain 时抛出 `GPDCLockConfigInconsistentError`。 |
| BLPOP 错过唤醒 | 等待者在 BLPOP 超时后回退到 `SET NX` 重试；正确，只是更慢。 |

---

## 已知限制

- **仅具建议性。** 锁只串行化那些调用 `obj.lock()` 的进程。直接（通过 `obj.value = ...` 或任何不加锁的变更方法）读写同一批 key 的进程不会被阻塞。要让保证成立，所有写入者都必须主动加入。
- **匿名子对象。** 父对象的变更方法（例如 `dict.__setitem__`）对匿名子对象引用者集合的修改，不会被父对象那把锁自动保护。实践中这很少出问题，因为用户都是通过具名绑定访问数据，而非直接访问匿名对象。如果你在启用 GC 的高并发下观察到 `decode_element` 抛出 `KeyError`，多半就是这个原因。
- **不可重入。** 同一进程重复获取同一把锁会死锁（第二次 `lock()` 会阻塞等待第一次释放）。
- **不自动续约。** 如果你的操作可能超过配置的 `ttl`，请通过 `obj.lock(ttl=...)` 显式设置更大的 `ttl`。

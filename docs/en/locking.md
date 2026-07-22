# Multi-Process Locking

`gpdatacached` provides an opt-in distributed lock for serializing access to a `GPDCDataObject` across multiple processes.

## When you need locking

| Scenario | Locks needed? |
|---|---|
| Single process read/write | No |
| Multiple processes read, single process writes | No (Redis reads/writes are atomic per command) |
| Multiple processes write the same object | **Yes** — use `obj.lock()` |
| `gc_enabled=True` with concurrent writers | **Yes** — all writers must use `obj.lock()` |

Locks are **opt-in and advisory**. GPDC never acquires a lock on your behalf — mutating methods do not lock. A lock only coordinates processes that voluntarily call `obj.lock()`. If even one writer bypasses locking, the guarantee is void.

**Rule of thumb:** start without locks. Add them only when you measure contention on a specific object.

---

## Performance Impact

Every locked operation pays a concrete cost over its unlocked equivalent. Understand this before adopting locks broadly.

### Per-operation Redis overhead

| Operation | Redis round-trips | Notes |
|---|---|---|
| `obj.lock()` (uncontended fast path) | **1** | `SET NX EX` |
| `obj.lock()` (contended) | 1 + 2 per retry | `SET NX EX` fails → `BLPOP` waits (one round-trip) → `SET NX EX` retry. Each retry cycle is two round-trips (BLPOP wait + SET). |
| `held.release()` | **1** | One Lua script (`GET`+`DEL`+`RPUSH`+`EXPIRE` batched server-side) |

So an **uncontended** critical section costs **+2 round-trips minimum** (acquire + release) versus the unlocked path. On a typical LAN that is ~0.2–2 ms; over a WAN it can be 10–100 ms.

### Throughput under contention

Locks **serialise** access. The maximum throughput on a contended object is:

```
throughput ≈ 1 / (critical_section_duration + 2 × RTT)
```

If the critical section itself is fast (e.g. a single increment) and N processes contend, each process effectively waits in a queue. A hot counter under heavy contention can become a global serialisation point.

### Latency under contention

A waiter blocks in `BLPOP` (whose timeout is capped at the lock's `ttl`) until either the holder releases (wake-up via `RPUSH`) or the BLPOP's own timeout elapses. On timeout, the waiter retries `SET NX EX`, succeeding once the lock's TTL has expired. Worst-case waiter latency ≈ the holder's critical-section duration (plus the wake-up round-trip). If the holder crashes mid-section, waiters recover within ~`ttl` seconds (BLPOP timeout, then a successful `SET NX EX` retry).

### Hidden multipliers

- **Nested locks** — acquiring K locks for one logical operation costs K× the acquire overhead and holds all K locks for the full critical section.
- **Locks inside hot loops** — N iterations each paying +2 round-trips dwarfs the actual work. Batch instead (one lock around the whole loop, or move the loop onto a single local copy via `.value`).
- **`gc_enabled=True`** — every writer must lock, so the +2 round-trip cost applies to *every* mutation on contended objects, not just the ones you flagged.

### When locks are cheap

- **No contention** — the fast path is one `SET NX EX`; the wake-up queue is never touched.
- **Short critical sections** — the lock is held briefly, so waiters rarely block.
- **Local-area network to Redis** — sub-millisecond RTTs keep the overhead negligible.

> Locking is one of several performance topics. For the full picture — sliding-TTL overuse, deep nesting, unnecessary GC, round-trip anti-patterns — see the [Performance Guide](./performance-guide.md).

---

## Usage

```python
from gpdatacached import GPDCDomain, GPDCDomainConfig

config = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_password="...")
domain = GPDCDomain(config, name="mydomain")
ns = domain.ensure_namespace("myns")

ns["counter"] = 0
counter = ns.get_object("counter")

# Acquire lock, mutate, release.
with counter.lock():
    current = counter.value
    counter.value = current + 1
```

The lock is released automatically when the `with` block exits, including on exception.

---

## Recommended Usage

1. **Don't lock by default.** Single-writer and low-contention workloads need no locking. Add locks reactively, in response to measured contention — not speculatively.
2. **Lock as late as possible, release as early as possible.** Acquire immediately before the read-modify-write and release the instant it is done. Do no I/O and no heavy computation while holding the lock.
3. **Keep critical sections minimal.** Read → compute → write inside the lock; everything else (logging, validation of inputs, building the new value from unrelated data) outside.
4. **Don't lock inside hot loops.** Either hoist the lock to wrap the whole loop, or pull the data local once via `.value`, work on it, and write the result back in one pass.
5. **For multi-object operations, always acquire locks in ascending canonical-name order** (see [Acquiring multiple locks](#acquiring-multiple-locks)). GPDC's own GC follows this rule.
6. **If `gc_enabled=True`, every concurrent writer MUST lock** — there is no opt-out. See [GC interaction](#gc-interaction).
7. **Size `ttl` to your worst-case critical section.** If the operation may legitimately run longer than the default `ttl`, pass it explicitly: `obj.lock(ttl=120)`. An operation that exceeds `ttl` silently loses the lock mid-flight.
8. **Prefer `try_lock()` for opportunistic paths.** When you can defer or skip the work, `try_lock()` avoids blocking and lets the caller decide.
9. **One logical operation = one lock acquisition.** Re-acquiring the same lock from the same process deadlocks (no reentrancy).

---

## Lock scope

`obj.lock()` locks the object's **canonical name** (the data owner). For a reference object, calling `lock()` resolves to the same lock as the canonical object it points to.

By convention, a writer holding this lock has exclusive access to the object's own Redis keys (metadata, raw data, referrer set). The lock is **advisory** — it only coordinates processes that voluntarily call `obj.lock()`; it does not physically block Redis access from a process that bypasses it.

The lock does **not** automatically protect referrer modifications that parent methods make on anonymous children — see [Known limitations](#known-limitations).

---

## Acquiring multiple locks

To atomically move an element between two objects, you need both locks. **Always acquire in ascending canonical-name order** to avoid deadlock:

```python
# canonical names sorted: 'a' before 'b'
with ns.get_object("a").lock():
    with ns.get_object("b").lock():
        # ... atomically transfer ...
```

GPDC's internal GC follows this same ordering rule.

---

## Non-blocking try

```python
held = obj.try_lock()
if held is not None:
    try:
        # ... critical section ...
    finally:
        held.release()
else:
    # someone else holds the lock; decide what to do
```

---

## GC interaction

When `gc_enabled=True`, the background GC tries to lock each object it processes. If the lock cannot be acquired within `gc_lock_wait_seconds` (default 5s), GC skips that object in the current cycle. (For what GC cleans and when it runs, see [Lifecycle Management](./lifecycle.md#10-garbage-collection).)

**Contract:** if `gc_enabled=True`, all concurrent writers MUST use `obj.lock()`. Writers that bypass locking can race with GC and corrupt state.

---

## Configuration

Lock configuration lives on `GPDCDomainConfig`:

| Field | Default | Meaning |
|---|---|---|
| `default_lock_ttl_seconds` | 30 | TTL on the lock key. Auto-releases the lock if the holder crashes. Must exceed your worst-case operation duration. |
| `default_lock_acquire_timeout_seconds` | 30 | How long `obj.lock()` blocks before raising `LockAcquisitionTimeout`. |
| `gc_lock_ttl_seconds` | 30 | TTL for locks acquired by GC. |
| `gc_lock_wait_seconds` | 5 | GC's per-object try-lock timeout. Skips the object on expiry. |

All four values are written to the domain meta hash in Redis on first open. **All processes accessing the same domain MUST use the same values.** If a second process opens the domain with different lock config, `GPDCLockConfigInconsistentError` is raised.

---

## Changing lock config

Use `reset_domain_config` to clear the lock-config fields from Redis before opening the domain with new values:

```python
from gpdatacached import reset_domain_config, GPDCDomain, GPDCDomainConfig

old_config = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_password="...")
reset_domain_config("mydomain", old_config)

new_config = GPDCDomainConfig(
    redis_host="127.0.0.1", redis_port=6379, redis_password="...",
    default_lock_ttl_seconds=60,
)
domain = GPDCDomain(new_config, name="mydomain")
```

---

## Failure modes

| Failure | Behaviour |
|---|---|
| Holder process crashes | Lock auto-expires after `ttl`; waiter acquires within `ttl + ε`. |
| Operation exceeds `ttl` | Lock auto-expires mid-operation; another process may acquire; release logs a warning. Size `ttl` to worst-case duration. |
| Config mismatch | `GPDCLockConfigInconsistentError` at domain construction. |
| BLPOP missed wake-up | Waiter falls back to `SET NX` retry on BLPOP timeout; correct, just slower. |

---

## Known limitations

- **Advisory only.** The lock serialises only processes that call `obj.lock()`. A process that reads/writes the same keys directly (via `obj.value = ...` or any mutating method without locking) is not blocked. All writers must opt in for the guarantee to hold.
- **Anonymous children.** A parent mutation method (e.g. `dict.__setitem__`) that modifies an anonymous child's referrer set is not automatically protected by the parent's lock. In practice this is rarely a problem because users access data through named bindings, not directly through anons. If you observe `KeyError` from `decode_element` under high concurrency with GC enabled, this is the likely cause.
- **No reentrancy.** The same process acquiring the same lock twice will deadlock (the second `lock()` blocks waiting for the first to release).
- **No auto-renewal.** If your operation may exceed the configured `ttl`, set a larger `ttl` explicitly via `obj.lock(ttl=...)`.

# Multi-Process Locking

`gpdatacached` provides an opt-in distributed lock for serializing access to a `GPDCDataObject` across multiple processes.

## When you need locking

| Scenario | Locks needed? |
|---|---|
| Single process read/write | No |
| Multiple processes read, single process writes | No (Redis reads/writes are atomic per command) |
| Multiple processes write the same object | **Yes** — use `obj.lock()` |
| `gc_enabled=True` with concurrent writers | **Yes** — all writers must use `obj.lock()` |

## Usage

```python
from gpdatacached import GPDCDomain, GPDCDomainConfig

config = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_password="...")
domain = GPDCDomain("mydomain", config)
ns = domain.ensure_namespace("myns")

ns["counter"] = 0
counter = ns.get_object("counter")

# Acquire lock, mutate, release.
with counter.lock():
    current = counter.value
    counter.value = current + 1
```

The lock is released automatically when the `with` block exits, including on exception.

## Lock scope

`obj.lock()` locks the object's **canonical name** (the data owner). For a reference object, calling `lock()` resolves to the same lock as the canonical object it points to.

The lock protects the object's own Redis keys (metadata, raw data, referrer set). It does **not** automatically protect referrer modifications that parent methods make on anonymous children — see "Known limitations" below.

## Acquiring multiple locks

To atomically move an element between two objects, you need both locks. **Always acquire in ascending canonical-name order** to avoid deadlock:

```python
# canonical names sorted: 'a' before 'b'
with ns.get_object("a").lock():
    with ns.get_object("b").lock():
        # ... atomically transfer ...
```

GPDC's internal GC follows this same ordering rule.

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

## GC interaction

When `gc_enabled=True`, the background GC tries to lock each object it processes. If the lock cannot be acquired within `gc_lock_wait_seconds` (default 5s), GC skips that object in the current cycle.

**Contract:** if `gc_enabled=True`, all concurrent writers MUST use `obj.lock()`. Writers that bypass locking can race with GC and corrupt state.

## Configuration

Lock configuration lives on `GPDCDomainConfig`:

| Field | Default | Meaning |
|---|---|---|
| `default_lock_ttl_seconds` | 30 | TTL on the lock key. Auto-releases the lock if the holder crashes. Must exceed your worst-case operation duration. |
| `default_lock_acquire_timeout_seconds` | 30 | How long `obj.lock()` blocks before raising `LockAcquisitionTimeout`. |
| `gc_lock_ttl_seconds` | 30 | TTL for locks acquired by GC. |
| `gc_lock_wait_seconds` | 5 | GC's per-object try-lock timeout. Skips the object on expiry. |

All four values are written to the domain meta hash in Redis on first open. **All processes accessing the same domain MUST use the same values.** If a second process opens the domain with different lock config, `GPDCLockConfigInconsistentError` is raised.

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
domain = GPDCDomain("mydomain", new_config)
```

## Failure modes

| Failure | Behaviour |
|---|---|
| Holder process crashes | Lock auto-expires after `ttl`; waiter acquires within `ttl + ε`. |
| Operation exceeds `ttl` | Lock auto-expires mid-operation; another process may acquire; release logs a warning. Size `ttl` to worst-case duration. |
| Config mismatch | `GPDCLockConfigInconsistentError` at domain construction. |
| BLPOP missed wake-up | Waiter falls back to `SET NX` retry on BLPOP timeout; correct, just slower. |

## Known limitations

- **Anonymous children.** A parent mutation method (e.g. `dict.__setitem__`) that modifies an anonymous child's referrer set is not automatically protected by the parent's lock. In practice this is rarely a problem because users access data through named bindings, not directly through anons. If you observe `KeyError` from `decode_element` under high concurrency with GC enabled, this is the likely cause.
- **No reentrancy.** The same process acquiring the same lock twice will deadlock (the second `lock()` blocks waiting for the first to release).
- **No auto-renewal.** If your operation may exceed the configured `ttl`, set a larger `ttl` explicitly via `obj.lock(ttl=...)`.

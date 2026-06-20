"""Performance benchmarks for the locking feature.

Marked ``slow`` — run explicitly with ``pytest -m slow``.

Targets (see dev_docs/2026-06-21-cross-process-locking-design.md §9.3):
- Uncontended ``with obj.lock(): pass`` overhead: < 1 ms
- Contended throughput (2 processes alternating): > 200 ops/s
"""

from __future__ import annotations

import time

import pytest

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain

pytestmark = [pytest.mark.slow]

UNCONTENDED_TARGET_MS = 1.0


class TestLockBenchmark:
    def test_uncontended_lock_overhead(self, clean_redis):
        """Single-process, no contention: with obj.lock(): pass should be < 1ms."""
        cfg = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_db=1, redis_password="123456")
        d = GPDCDomain("bench_test", cfg)
        try:
            ns = d.ensure_namespace("benchns")
            ns["x"] = 42
            obj = ns.get_object("x")

            # Warmup.
            for _ in range(10):
                with obj.lock():
                    pass

            # Measure.
            n = 100
            start = time.monotonic()
            for _ in range(n):
                with obj.lock():
                    pass
            elapsed = time.monotonic() - start

            per_op_ms = (elapsed / n) * 1000
            print(f"\nUncontended lock overhead: {per_op_ms:.3f} ms/op (target < {UNCONTENDED_TARGET_MS} ms)")
            assert per_op_ms < UNCONTENDED_TARGET_MS, (
                f"Lock overhead {per_op_ms:.3f}ms exceeds target {UNCONTENDED_TARGET_MS}ms"
            )
        finally:
            d.close()

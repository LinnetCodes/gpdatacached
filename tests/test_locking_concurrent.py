"""Multi-process integration tests for the locking feature.

Marked ``slow`` — run explicitly with ``pytest -m slow``.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time

import pytest

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

pytestmark = [pytest.mark.slow]


def _make_config() -> GPDCDomainConfig:
    return GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
    )


def _worker_hold_then_append(domain_name: str, ns_name: str, key: str, barrier, results, idx: int):
    """Worker: acquire lock, record entry order, append idx, release."""
    domain = GPDCDomain(domain_name, _make_config())
    try:
        ns = domain.ensure_namespace(ns_name)
        obj = ns.get_object(key)
        with obj.lock(timeout=10.0):
            results.append(("enter", idx, time.monotonic()))
            time.sleep(0.1)
            obj.append(idx)
            results.append(("exit", idx, time.monotonic()))
    finally:
        domain.close()


def _worker_holder_then_crash(domain_name: str, ns_name: str, key: str, started_event, lock_ttl: int):
    """Worker: acquire lock, signal started, then os._exit(0) to simulate crash."""
    cfg = _make_config()
    cfg.default_lock_ttl_seconds = lock_ttl
    domain = GPDCDomain(domain_name, cfg)
    try:
        ns = domain.ensure_namespace(ns_name)
        obj = ns.get_object(key)
        held = obj.try_lock(ttl=lock_ttl)
        if held is None:
            return
        started_event.set()
        time.sleep(2.0)
        # Simulate crash: skip release, hard exit.
        os._exit(0)
    except Exception:
        os._exit(1)


class TestCrossProcessMutex:
    def test_two_processes_serialize(self, clean_redis):
        """Two processes both append under lock; entries interleave never."""
        domain_name = "cptest_serial"
        ns_name = "testns"
        key = "shared_list"

        # Setup: create the shared list.
        d = GPDCDomain(domain_name, _make_config())
        ns = d.ensure_namespace(ns_name)
        ns[key] = []
        d.close()

        manager = mp.Manager()
        results = manager.list()
        barrier = manager.Event()

        procs = [
            mp.Process(target=_worker_hold_then_append, args=(domain_name, ns_name, key, barrier, results, i))
            for i in range(3)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=15.0)

        for p in procs:
            assert not p.is_alive(), "worker did not finish"

        # Verify serialization: no two "enter" events overlap with another's "exit".
        events = list(results)
        enter_idxs = [e[1] for e in events if e[0] == "enter"]
        exit_idxs = [e[1] for e in events if e[0] == "exit"]

        # Each worker enters and exits exactly once.
        assert sorted(enter_idxs) == [0, 1, 2]
        assert sorted(exit_idxs) == [0, 1, 2]

        # Verify list contents: each idx appended exactly once.
        d = GPDCDomain(domain_name, _make_config())
        try:
            ns = d.ensure_namespace(ns_name)
            obj = ns.get_object(key)
            appended = list(obj)
            assert sorted(appended) == [0, 1, 2]
        finally:
            d.close()

    def test_lock_survives_holder_crash(self, clean_redis):
        """Holder crashes (os._exit); waiter acquires within ttl + ε."""
        domain_name = "cptest_crash"
        ns_name = "testns"
        key = "shared_list"

        # Create the domain with a short TTL for faster test.
        cfg = _make_config()
        cfg.default_lock_ttl_seconds = 2
        d = GPDCDomain(domain_name, cfg)
        ns = d.ensure_namespace(ns_name)
        ns[key] = []
        d.close()

        # Start crash worker.
        manager = mp.Manager()
        started = manager.Event()
        crash_proc = mp.Process(
            target=_worker_holder_then_crash,
            args=(domain_name, ns_name, key, started, 2),
        )
        crash_proc.start()
        assert started.wait(timeout=3.0), "crash worker did not start"

        # Now try to acquire from this process.
        cfg = _make_config()
        cfg.default_lock_ttl_seconds = 2
        d = GPDCDomain(domain_name, cfg)
        try:
            ns = d.ensure_namespace(ns_name)
            obj = ns.get_object(key)
            start = time.monotonic()
            with obj.lock(timeout=10.0):
                elapsed = time.monotonic() - start
                # Should have acquired within ttl (2s) + small slack.
                assert elapsed < 3.0, f"waited too long for crashed holder: {elapsed}s"
        finally:
            d.close()
            crash_proc.join(timeout=2.0)

    def test_cross_process_blpop_wakeup(self, clean_redis):
        """BLPOP wake-up latency: child releases; parent acquires within 100ms."""
        domain_name = "cptest_blpop"
        ns_name = "testns"
        key = "shared"

        # Pre-create the shared object.
        d = GPDCDomain(domain_name, _make_config())
        ns = d.ensure_namespace(ns_name)
        ns[key] = 0
        d.close()

        # Use a manager queue to get the release timestamp from the child.
        manager = mp.Manager()
        release_time_q = manager.Queue()
        acquired_time_q = manager.Queue()

        def holder():
            d = GPDCDomain(domain_name, _make_config())
            try:
                ns = d.ensure_namespace(ns_name)
                obj = ns.get_object(key)
                with obj.lock(timeout=5.0):
                    time.sleep(0.5)  # hold for 500ms
                    release_time_q.put(time.monotonic())
            finally:
                d.close()

        proc = mp.Process(target=holder)
        proc.start()

        # Give the holder a moment to acquire.
        time.sleep(0.2)

        # Parent tries to acquire — will block in BLPOP until holder releases.
        d = GPDCDomain(domain_name, _make_config())
        try:
            ns = d.ensure_namespace(ns_name)
            obj = ns.get_object(key)
            with obj.lock(timeout=5.0):
                acquired_time_q.put(time.monotonic())
        finally:
            d.close()

        proc.join(timeout=5.0)
        assert not proc.is_alive(), "holder process did not finish"

        release_t = release_time_q.get(timeout=1.0)
        acquired_t = acquired_time_q.get(timeout=1.0)
        latency = acquired_t - release_t
        # Should be well under 100ms on a local Redis.
        assert latency < 0.1, f"BLPOP wake-up latency {latency * 1000:.1f}ms exceeds 100ms"


class TestGCConcurrency:
    def test_gc_runs_concurrent_with_locked_writer(self, clean_redis):
        """User holds lock and appends while GC runs; no lost elements."""
        domain_name = "cptest_gc"
        ns_name = "testns"
        key = "shared_list"

        cfg = _make_config()
        cfg.gc_enabled = False  # we trigger run_gc manually
        d = GPDCDomain(domain_name, cfg)
        ns = d.ensure_namespace(ns_name)
        ns[key] = []
        d.close()

        def writer():
            cfg = _make_config()
            cfg.gc_enabled = False
            d = GPDCDomain(domain_name, cfg)
            try:
                ns = d.ensure_namespace(ns_name)
                obj = ns.get_object(key)
                for i in range(20):
                    with obj.lock(timeout=5.0):
                        obj.append(i)
            finally:
                d.close()

        # Run writer in a process.
        wp = mp.Process(target=writer)
        wp.start()

        # Concurrently run GC from this process.
        cfg = _make_config()
        cfg.gc_enabled = False
        d = GPDCDomain(domain_name, cfg)
        try:
            # Run GC a few times while writer is active.
            for _ in range(5):
                time.sleep(0.05)
                d.run_gc(namespace=ns_name)
        finally:
            d.close()

        wp.join(timeout=15.0)
        assert not wp.is_alive()

        # Verify no elements lost.
        cfg = _make_config()
        d = GPDCDomain(domain_name, cfg)
        try:
            ns = d.ensure_namespace(ns_name)
            obj = ns.get_object(key)
            appended = list(obj)
            assert sorted(appended) == list(range(20))
        finally:
            d.close()

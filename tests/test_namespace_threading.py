import threading
import pytest
import redis as redis_lib
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"


@pytest.fixture
def domain():
    r = redis_lib.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
    r.flushdb()
    config = GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
        gc_enabled=False,
    )
    d = GPDCDomain("thread_test", config)
    yield d
    d.close()


class TestNamespaceConcurrentAccess:
    def test_concurrent_writes_no_data_loss(self, domain):
        ns = domain.get_namespace("ns1")
        n_threads = 10
        n_writes = 50
        errors = []

        def writer(thread_id):
            try:
                for i in range(n_writes):
                    key = f"t{thread_id}_k{i}"
                    ns[key] = thread_id * 1000 + i
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        for tid in range(n_threads):
            for i in range(n_writes):
                key = f"t{tid}_k{i}"
                assert ns[key] == tid * 1000 + i

    def test_concurrent_reads_consistent(self, domain):
        ns = domain.get_namespace("ns2")
        for i in range(100):
            ns[f"key{i}"] = i

        results = {}
        errors = []

        def reader(thread_id):
            try:
                local = {}
                for i in range(100):
                    local[f"key{i}"] = ns[f"key{i}"]
                results[thread_id] = local
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader, args=(tid,)) for tid in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        for tid, data in results.items():
            for i in range(100):
                assert data[f"key{i}"] == i

    def test_concurrent_read_write_no_crash(self, domain):
        ns = domain.get_namespace("ns3")
        for i in range(50):
            ns[f"key{i}"] = i

        errors = []

        def writer():
            try:
                for i in range(50, 100):
                    ns[f"key{i}"] = i
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    _ = ns.get(f"key{i}")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == []

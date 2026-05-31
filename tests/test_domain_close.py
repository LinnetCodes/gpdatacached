import logging

import pytest
from gpdatacached.domain import GPDCDomain


class TestGPDCDomainCloseStrict:
    def test_close_is_idempotent(self, domain):
        domain.close()
        domain.close()

    def test_redis_property_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            _ = domain.redis

    def test_get_namespace_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            domain.get_namespace("any")

    def test_contains_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            _ = "x" in domain

    def test_iter_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            list(domain)

    def test_len_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            len(domain)

    def test_getitem_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            _ = domain["x"]

    def test_run_gc_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            domain.run_gc()

    def test_context_manager_closes_domain(self, clean_redis, domain_config):
        with GPDCDomain("testdomain", domain_config) as d:
            _ = d.redis
        with pytest.raises(RuntimeError, match="closed"):
            _ = d.redis

    def test_del_auto_closes_with_warning(self, clean_redis, domain_config, caplog):
        d = GPDCDomain("testdomain", domain_config)
        assert not d._closed
        with caplog.at_level(logging.WARNING, logger="gpdatacached.domain"):
            del d
        assert "not explicitly closed" in caplog.text

from gpdatacached.config import GPDCDomainConfig, GPDCNamespaceConfig


class TestGPDCNamespaceConfig:
    def test_defaults_are_none(self):
        cfg = GPDCNamespaceConfig()
        assert cfg.default_cache_mode is None
        assert cfg.default_ttl_seconds is None

    def test_set_values(self):
        cfg = GPDCNamespaceConfig(default_cache_mode="ttl", default_ttl_seconds=300)
        assert cfg.default_cache_mode == "ttl"
        assert cfg.default_ttl_seconds == 300

    def test_cfg_class_name(self):
        assert GPDCNamespaceConfig.cfg_class_name == "GPDCNamespaceConfig"

    def test_rejects_ttl_mode_with_zero_ttl(self):
        import pytest

        with pytest.raises(ValueError, match="requires ttl > 0"):
            GPDCNamespaceConfig(default_cache_mode="ttl", default_ttl_seconds=0)

    def test_rejects_sliding_mode_with_zero_ttl(self):
        import pytest

        with pytest.raises(ValueError, match="requires ttl > 0"):
            GPDCNamespaceConfig(default_cache_mode="sliding", default_ttl_seconds=0)

    def test_allows_ttl_mode_none_ttl(self):
        cfg = GPDCNamespaceConfig(default_cache_mode="ttl")
        assert cfg.default_ttl_seconds is None

    def test_allows_permanent_mode_with_zero_ttl(self):
        cfg = GPDCNamespaceConfig(default_cache_mode="permanent", default_ttl_seconds=0)
        assert cfg.default_cache_mode == "permanent"


class TestGPDCDomainConfig:
    def test_defaults(self):
        cfg = GPDCDomainConfig()
        assert cfg.redis_host == "localhost"
        assert cfg.redis_port == 6379
        assert cfg.redis_db == 0
        assert cfg.redis_password == ""
        assert cfg.default_cache_mode == "permanent"
        assert cfg.default_ttl_seconds == 0
        assert cfg.gc_enabled is False
        assert cfg.gc_interval_seconds == 300
        assert cfg.namespaces == {}

    def test_set_redis_connection(self):
        cfg = GPDCDomainConfig(
            redis_host="192.168.1.100",
            redis_port=6380,
            redis_db=2,
            redis_password="secret",
        )
        assert cfg.redis_host == "192.168.1.100"
        assert cfg.redis_port == 6380
        assert cfg.redis_db == 2
        assert cfg.redis_password == "secret"

    def test_set_cache_policy(self):
        cfg = GPDCDomainConfig(default_cache_mode="ttl", default_ttl_seconds=3600)
        assert cfg.default_cache_mode == "ttl"
        assert cfg.default_ttl_seconds == 3600

    def test_cfg_class_name(self):
        assert GPDCDomainConfig.cfg_class_name == "GPDCDomainConfig"

    def test_rejects_ttl_mode_with_zero_ttl(self):
        import pytest

        with pytest.raises(ValueError, match="requires ttl > 0"):
            GPDCDomainConfig(default_cache_mode="ttl", default_ttl_seconds=0)

    def test_rejects_sliding_mode_with_zero_ttl(self):
        import pytest

        with pytest.raises(ValueError, match="requires ttl > 0"):
            GPDCDomainConfig(default_cache_mode="sliding", default_ttl_seconds=0)

    def test_allows_permanent_mode_with_zero_ttl(self):
        cfg = GPDCDomainConfig(default_cache_mode="permanent", default_ttl_seconds=0)
        assert cfg.default_cache_mode == "permanent"
        assert cfg.default_ttl_seconds == 0

    def test_rejects_invalid_cache_mode(self):
        import pytest

        with pytest.raises(ValueError):
            GPDCDomainConfig(default_cache_mode="bad")

    def test_rejects_negative_ttl(self):
        import pytest

        with pytest.raises(ValueError):
            GPDCDomainConfig(default_ttl_seconds=-1)

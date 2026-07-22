from unittest.mock import patch, MagicMock

import pytest
import redis

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain


class TestRedisConnectionFailure:
    def test_connection_error_propagates_on_init(self):
        cfg = GPDCDomainConfig(
            redis_host="127.0.0.1",
            redis_port=6379,
            redis_db=1,
            redis_password="123456",
        )

        with patch("gpdatacached.domain.redis.ConnectionPool") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool_cls.return_value = mock_pool

            mock_redis_instance = MagicMock()
            mock_redis_instance.hget.side_effect = redis.ConnectionError("Connection refused")

            with patch("gpdatacached.domain.redis.Redis", return_value=mock_redis_instance):
                with pytest.raises(redis.ConnectionError, match="Connection refused"):
                    GPDCDomain(cfg, name="failing_domain")

    def test_connection_error_on_pool_creation(self):
        cfg = GPDCDomainConfig(
            redis_host="127.0.0.1",
            redis_port=6379,
            redis_db=1,
            redis_password="123456",
        )

        with patch(
            "gpdatacached.domain.redis.ConnectionPool", side_effect=redis.ConnectionError("Pool creation failed")
        ):
            with pytest.raises(redis.ConnectionError, match="Pool creation failed"):
                GPDCDomain(cfg, name="failing_domain")

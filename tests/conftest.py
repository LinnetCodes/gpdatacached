# tests/conftest.py
import pytest
import redis as redis_lib
from gpdatacached.config import GPDCDomainConfig

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"


@pytest.fixture(scope="session")
def raw_redis():
    """Session-scoped raw Redis client for fixture setup."""
    client = redis_lib.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
    return client


@pytest.fixture
def clean_redis(raw_redis):
    """Flush test DB before each test."""
    raw_redis.flushdb()
    yield raw_redis


@pytest.fixture
def domain_config():
    return GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
    )


@pytest.fixture
def domain(clean_redis, domain_config):
    from gpdatacached.domain import GPDCDomain

    return GPDCDomain("testdomain", domain_config)


@pytest.fixture
def ns(domain):
    return domain.get_namespace("testns")

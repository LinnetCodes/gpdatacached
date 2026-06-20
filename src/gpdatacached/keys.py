"""Redis key builders, URI utilities, and name validators for GPDC."""

from __future__ import annotations

import re

GLOBAL_META_KEY = "gpdc:__meta__"
"""Redis key for global metadata."""

GLOBAL_DOMAINS_KEY = "gpdc:__domains__"
"""Redis key for the set of all registered domains."""


def make_domain_meta_key(domain: str) -> str:
    """Return the Redis key for domain-level metadata."""
    return f"gpdc:{domain}:__meta__"


def make_domain_namespaces_key(domain: str) -> str:
    """Return the Redis key for the set of namespaces within a domain."""
    return f"gpdc:{domain}:__namespaces__"


def make_namespace_meta_key(domain: str, namespace: str) -> str:
    """Return the Redis key for namespace-level metadata."""
    return f"gpdc:{domain}:{namespace}:__meta__"


def make_object_key(domain: str, namespace: str, name: str) -> str:
    """Return the Redis key for a named object within a namespace."""
    return f"gpdc:{domain}:{namespace}:obj:{name}"


def make_raw_key(domain: str, namespace: str, name: str) -> str:
    """Return the Redis key for raw container data."""
    return f"gpdc:{domain}:{namespace}:raw:{name}"


def make_ref_key(domain: str, namespace: str, name: str) -> str:
    """Return the Redis key for reference tracking (Redis Set)."""
    return f"gpdc:{domain}:{namespace}:ref:{name}"


def make_lock_key(domain: str, namespace: str, name: str) -> str:
    """Return the Redis key for a per-object distributed lock."""
    return f"gpdc:{domain}:{namespace}:lock:{name}"


def make_lock_notify_key(domain: str, namespace: str, name: str) -> str:
    """Return the Redis key for the BLPOP wake-up queue of a lock."""
    return f"gpdc:{domain}:{namespace}:locknotify:{name}"


def make_object_key_prefix(domain: str, namespace: str) -> str:
    """Return the prefix for all object keys in a namespace."""
    return f"gpdc:{domain}:{namespace}:obj:"


def make_raw_key_prefix(domain: str, namespace: str) -> str:
    """Return the prefix for all raw data keys in a namespace."""
    return f"gpdc:{domain}:{namespace}:raw:"


def make_ref_key_prefix(domain: str, namespace: str) -> str:
    """Return the prefix for all reference tracking keys in a namespace."""
    return f"gpdc:{domain}:{namespace}:ref:"


def make_uri(domain: str, namespace: str, name: str) -> str:
    """Construct a GPDC URI: gpdc://domain/namespace/name"""
    return f"gpdc://{domain}/{namespace}/{name}"


def uri_to_redis_key(uri: str) -> str:
    """Convert gpdc://domain/ns/name → gpdc:domain:ns:obj:name"""
    if not uri.startswith("gpdc://"):
        raise ValueError(f"Invalid GPDC URI (must start with 'gpdc://'): {uri!r}")
    rest = uri[len("gpdc://") :]
    parts = rest.split("/")
    if len(parts) != 3:
        raise ValueError(f"Invalid GPDC URI (must be gpdc://domain/namespace/name): {uri!r}")
    domain, ns, name = parts
    validate_domain_name(domain)
    validate_namespace_name(ns)
    validate_object_name(name)
    return make_object_key(domain, ns, name)


_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9._]+$")
_RESERVED_PATTERN = re.compile(r"^__.*__$")


def _validate_name(name: str, label: str) -> None:
    if not _NAME_PATTERN.match(name):
        raise ValueError(f"Invalid {label} {name!r}: only letters, digits, '.', '_' are allowed")
    if _RESERVED_PATTERN.match(name):
        raise ValueError(f"Invalid {label} {name!r}: names matching __xxx__ are reserved")


def validate_object_name(name: str) -> None:
    """Validate an object name; raise ValueError if invalid or reserved."""
    _validate_name(name, "object name")


def validate_domain_name(name: str) -> None:
    """Validate a domain name; raise ValueError if invalid or reserved."""
    _validate_name(name, "domain name")


def validate_namespace_name(name: str) -> None:
    """Validate a namespace name; raise ValueError if invalid or reserved."""
    _validate_name(name, "namespace name")


def collect_ref_names_from_raw(r, raw_key: str, raw_type: str) -> list[str]:
    """Scan a container's raw Redis data and return the names of all referenced objects."""
    if not raw_key:
        return []
    names: list[str] = []
    redis_type = r.type(raw_key)
    if redis_type == "hash":
        for val in r.hvals(raw_key):
            _extract_ref_name(val, names)
    elif redis_type == "list":
        for val in r.lrange(raw_key, 0, -1):
            _extract_ref_name(val, names)
    return names


def parse_ref_encoded(encoded: str) -> str | None:
    if not isinstance(encoded, str) or not encoded.startswith("ref:"):
        return None
    obj_key = encoded[len("ref:") :]
    parts = obj_key.split(":obj:")
    if len(parts) != 2:
        return None
    return parts[1]


def _extract_ref_name(encoded: str, names: list[str]) -> None:
    name = parse_ref_encoded(encoded)
    if name is not None:
        names.append(name)

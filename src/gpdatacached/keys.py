"""Redis key builders, URI utilities, and name validators for GPDC."""
from __future__ import annotations

import re

GLOBAL_META_KEY = "gpdc:__meta__"
GLOBAL_DOMAINS_KEY = "gpdc:__domains__"


def make_domain_meta_key(domain: str) -> str:
    return f"gpdc:{domain}:__meta__"


def make_domain_namespaces_key(domain: str) -> str:
    return f"gpdc:{domain}:__namespaces__"


def make_namespace_meta_key(domain: str, namespace: str) -> str:
    return f"gpdc:{domain}:{namespace}:__meta__"


def make_object_key(domain: str, namespace: str, name: str) -> str:
    return f"gpdc:{domain}:{namespace}:obj:{name}"


def make_object_key_prefix(domain: str, namespace: str) -> str:
    """Return the prefix for all object keys in a namespace."""
    return f"gpdc:{domain}:{namespace}:obj:"


def make_uri(domain: str, namespace: str, name: str) -> str:
    """Construct a GPDC URI: gpdc://domain/namespace/name"""
    return f"gpdc://{domain}/{namespace}/{name}"


def uri_to_redis_key(uri: str) -> str:
    """Convert gpdc://domain/ns/name → gpdc:domain:ns:obj:name"""
    if not uri.startswith("gpdc://"):
        raise ValueError(f"Invalid GPDC URI (must start with 'gpdc://'): {uri!r}")
    rest = uri[len("gpdc://"):]
    parts = rest.split("/")
    if len(parts) != 3:
        raise ValueError(
            f"Invalid GPDC URI (must be gpdc://domain/namespace/name): {uri!r}"
        )
    domain, ns, name = parts
    return make_object_key(domain, ns, name)


_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9._]+$")
_RESERVED_PATTERN = re.compile(r"^__.*__$")


def _validate_name(name: str, label: str) -> None:
    if not _NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid {label} {name!r}: only letters, digits, '.', '_' are allowed"
        )
    if _RESERVED_PATTERN.match(name):
        raise ValueError(
            f"Invalid {label} {name!r}: names matching __xxx__ are reserved"
        )


def validate_object_name(name: str) -> None:
    _validate_name(name, "object name")


def validate_domain_name(name: str) -> None:
    _validate_name(name, "domain name")


def validate_namespace_name(name: str) -> None:
    _validate_name(name, "namespace name")

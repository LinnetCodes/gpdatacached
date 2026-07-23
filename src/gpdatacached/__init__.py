# src/gpdatacached/__init__.py

# Software library version (SemVer), tracks feature releases and bugfixes.
# Independent of GPDC_PROTOCOL_VERSION in domain.py.
__version__ = "0.2.0"

from .codec import GPDCCodec, GPDCContainerCodec, GPDCScalarCodec
from .config import GPDCDomainConfig, GPDCNamespaceConfig
from .exceptions import (
    CanonicalObjectReferencedError,
    CyclicReferenceError,
    GPDCLockConfigInconsistentError,
    LockAcquisitionTimeout,
    ObjectGoneError,
)
from .domain import GPDCDomain, reset_domain_config
from .keys import make_uri, uri_to_redis_key, validate_object_name
from .namespace import GPDCNamespace
from .types import GPDCTypeRegistry
from .object import GPDCDataObject
from .scalar import GPDCScalarObject
from .container import GPDCContainerObject
from .data_cache import GPDataCache
from .builtins.bool_object import BoolObject
from .builtins.datetime_object import DateObject, DateTimeObject, TimeObject
from .builtins.dict_object import DictObject
from .builtins.float_object import FloatObject
from .builtins.int_object import IntObject
from .builtins.list_object import ListObject
from .builtins.none_object import NoneObject
from .builtins.set_object import SetObject
from .builtins.str_object import StrObject
from .builtins.tuple_object import TupleObject

# Register GPDCDomain / GPDCDomainConfig with gpconfig's object factory so
# that folder.get_object(name) can construct a GPDCDomain from a config file.
# Both calls are idempotent for the same class (gpconfig guarantees this).
from gpconfig import GPConfigManager as _GPConfigManager

_GPConfigManager.register_configurable_class(GPDCDomain)
_GPConfigManager.register_config_class(GPDCDomainConfig)

from .builtins.bool_object import BoolCodec as BoolCodec  # noqa: F401
from .builtins.datetime_object import DateCodec as DateCodec, DateTimeCodec as DateTimeCodec, TimeCodec as TimeCodec  # noqa: F401
from .builtins.dict_object import DictCodec as DictCodec  # noqa: F401
from .builtins.float_object import FloatCodec as FloatCodec  # noqa: F401
from .builtins.int_object import IntCodec as IntCodec  # noqa: F401
from .builtins.list_object import ListCodec as ListCodec  # noqa: F401
from .builtins.none_object import NoneCodec as NoneCodec  # noqa: F401
from .builtins.set_object import SetCodec as SetCodec  # noqa: F401
from .builtins.str_object import StrCodec as StrCodec  # noqa: F401
from .builtins.tuple_object import TupleCodec as TupleCodec  # noqa: F401

__all__ = [
    "__version__",
    "CanonicalObjectReferencedError",
    "CyclicReferenceError",
    "ObjectGoneError",
    "GPDCLockConfigInconsistentError",
    "LockAcquisitionTimeout",
    "GPDCDomain",
    "GPDataCache",
    "GPDCNamespace",
    "GPDCDomainConfig",
    "GPDCNamespaceConfig",
    "GPDCDataObject",
    "GPDCScalarObject",
    "IntObject",
    "FloatObject",
    "StrObject",
    "BoolObject",
    "DateTimeObject",
    "DateObject",
    "TimeObject",
    "NoneObject",
    "ListObject",
    "DictObject",
    "SetObject",
    "TupleObject",
    "GPDCContainerObject",
    "GPDCCodec",
    "GPDCContainerCodec",
    "GPDCScalarCodec",
    "GPDCTypeRegistry",
    "validate_object_name",
    "make_uri",
    "uri_to_redis_key",
    "reset_domain_config",
]

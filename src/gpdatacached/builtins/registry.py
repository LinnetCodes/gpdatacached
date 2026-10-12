from __future__ import annotations

import datetime
from decimal import Decimal

from ..types import GPDCTypeRegistry
from .bool_object import BoolCodec, BoolObject
from .datetime_object import (
    DateCodec,
    DateObject,
    DateTimeCodec,
    DateTimeObject,
    TimeCodec,
    TimeObject,
)
from .decimal_object import DecimalCodec, DecimalObject
from .dict_object import DictCodec, DictObject
from .float_object import FloatCodec, FloatObject
from .int_object import IntCodec, IntObject
from .list_object import ListCodec, ListObject
from .none_object import NoneCodec, NoneObject
from .set_object import SetCodec, SetObject
from .str_object import StrCodec, StrObject
from .tuple_object import TupleCodec, TupleObject


def _register_builtin_types() -> None:
    GPDCTypeRegistry.register(
        python_type=type(None),
        gpdc_type="scalar:none",
        object_class=NoneObject,
        codec_class=NoneCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=bool,
        gpdc_type="scalar:bool",
        object_class=BoolObject,
        codec_class=BoolCodec,
        priority=10,
    )
    GPDCTypeRegistry.register(
        python_type=int,
        gpdc_type="scalar:int",
        object_class=IntObject,
        codec_class=IntCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=float,
        gpdc_type="scalar:float",
        object_class=FloatObject,
        codec_class=FloatCodec,
        priority=0,
    )
    # Decimal is not an int/float/str subclass, so the default priority
    # cannot be shadowed by (or shadow) another builtin entry.
    GPDCTypeRegistry.register(
        python_type=Decimal,
        gpdc_type="scalar:decimal",
        object_class=DecimalObject,
        codec_class=DecimalCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=str,
        gpdc_type="scalar:str",
        object_class=StrObject,
        codec_class=StrCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=datetime.datetime,
        gpdc_type="scalar:datetime",
        object_class=DateTimeObject,
        codec_class=DateTimeCodec,
        priority=10,
    )
    GPDCTypeRegistry.register(
        python_type=datetime.date,
        gpdc_type="scalar:date",
        object_class=DateObject,
        codec_class=DateCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=datetime.time,
        gpdc_type="scalar:time",
        object_class=TimeObject,
        codec_class=TimeCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=tuple,
        gpdc_type="scalar:tuple",
        object_class=TupleObject,
        codec_class=TupleCodec,
        priority=0,
    )

    GPDCTypeRegistry.register(
        python_type=list,
        gpdc_type="collection:list",
        object_class=ListObject,
        codec_class=ListCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=dict,
        gpdc_type="collection:dict",
        object_class=DictObject,
        codec_class=DictCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=set,
        gpdc_type="collection:set",
        object_class=SetObject,
        codec_class=SetCodec,
        priority=0,
    )

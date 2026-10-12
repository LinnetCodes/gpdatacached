from __future__ import annotations

from decimal import Decimal, InvalidOperation

from ..codec import GPDCScalarCodec
from ..scalar import GPDCScalarObject


class DecimalCodec(GPDCScalarCodec):
    """Lossless decimal.Decimal codec: payload is ``str(value)``.

    ``str(Decimal)`` preserves coefficient/exponent exactly (``Decimal("1.10")``
    stays ``1.10``, extreme exponents use scientific notation), and
    ``Decimal(raw)`` restores it byte-for-byte. Non-finite values (NaN, sNaN,
    ±Infinity) and ``-0`` roundtrip too — same policy as float's nan/inf.
    """

    @classmethod
    def encode_value(cls, value: object) -> str:
        if not isinstance(value, Decimal):
            raise TypeError(f"DecimalCodec cannot encode {type(value).__name__!r}")
        return f"decimal:{value}"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        type_name, raw = cls._parse_encoded(encoded)
        if type_name != "decimal":
            raise ValueError(f"DecimalCodec cannot decode type {type_name!r}")
        try:
            return Decimal(raw)
        except InvalidOperation:
            # Decimal("garbage") raises decimal.InvalidOperation (an
            # ArithmeticError), not ValueError like int()/float() — normalise
            # to the error type every other scalar codec raises.
            raise ValueError(f"Invalid scalar value for type 'decimal': {raw!r}")


class DecimalObject(GPDCScalarObject):
    TYPE = "scalar:decimal"

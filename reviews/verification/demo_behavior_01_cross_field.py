"""Demo: why cross-field validators matter for behavior.01 (Pydantic __setitem__).

Shows the gap between option 1 (full-snapshot validation) and option 2
(single-field TypeAdapter validation) using a model_validator that enforces a
relationship BETWEEN fields ("end must be > start").

Run:
    .venv/Scripts/python.exe reviews/verification/demo_behavior_01_cross_field.py

No source files modified — this only exercises the CURRENT (unvalidated) code
path to show the problem, then simulates what each fix option would catch.
"""
from __future__ import annotations

import redis as redis_lib
from pydantic import BaseModel, model_validator

from gpdatacached import GPDCTypeRegistry
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.extensions.pydantic_support import BaseModelContainerObject, PydanticModelContainerCodec

HOST, PORT, DB, PWD = "127.0.0.1", 6379, 1, "123456"
DOMAIN, NS = "beh01cross", "ns"


def r():
    return redis_lib.Redis(host=HOST, port=PORT, db=DB, password=PWD, decode_responses=True)


def banner(t):
    print("\n" + "=" * 80)
    print(t)
    print("=" * 80)


def main():
    banner("behavior.01 — cross-field validators: why single-field validation isn't enough")

    # A model with a CROSS-FIELD constraint: end > start.
    class Interval(BaseModel):
        start: int
        end: int

        @model_validator(mode="after")
        def _end_after_start(self):
            if self.end <= self.start:
                raise ValueError(f"end ({self.end}) must be > start ({self.start})")
            return self

    # register as a container
    GPDCTypeRegistry.register(
        python_type=Interval,
        gpdc_type="collection:IntervalCross",
        object_class=type("IntervalObject", (BaseModelContainerObject,), {"TYPE": "collection:IntervalCross"}),
        codec_class=PydanticModelContainerCodec,
    )

    r().flushdb()
    from gpdatacached.domain import GPDCDomain

    cfg = GPDCDomainConfig(redis_host=HOST, redis_port=PORT, redis_db=DB, redis_password=PWD)
    dom = GPDCDomain(cfg, name=DOMAIN)
    ns = dom.ensure_namespace(NS)

    # ── setup: a valid interval start=10, end=20 ─────────────────────────────
    print("\n[1] Store a valid Interval(start=10, end=20). The cross-field validator passes.")
    ns["iv"] = Interval(start=10, end=20)
    obj = ns["iv"]
    print(f"    obj.value = {obj.value}")

    # ── THE BUG: write end=5 via __setitem__ (current code, no validation) ────
    print("\n[2] CURRENT behaviour: obj['end'] = 5  (start=10, end=5 violates end>start)")
    print("    The cross-field validator should reject this, but __setitem__ doesn't run it:")
    obj["end"] = 5  # succeeds today — no validation at all
    readback = obj["end"]
    print(f"    obj['end'] written and read back = {readback!r}  (write succeeded, no error)")
    print("    >>> Redis now holds an INVALID state: start=10, end=5, end <= start.")
    print("    >>> The error only surfaces when someone later reads .value:")
    try:
        _ = obj.value
        print("    .value succeeded (unexpected)")
    except Exception as e:
        print(f"    .value raised {type(e).__name__}: {str(e)[:100]}")

    # ── What option 2 (single-field TypeAdapter) would catch / miss ───────────
    print("\n[3] What option 2 (single-field TypeAdapter validation) would do:")
    print("    Option 2 validates the VALUE against the FIELD type only.")
    print("    For obj['end'] = 5: '5' IS a valid int -> option 2 ACCEPTS it.")
    print("    The cross-field constraint (end > start) is NOT checked, because the")
    print("    validator needs BOTH start and end, and option 2 only looks at 'end'.")
    print("    >>> So option 2 would STILL let the invalid state (start=10, end=5) into Redis.")
    print("    >>> Option 2 catches TYPE mismatches (str into int slot) but NOT cross-field rules.")

    # ── What option 1 (full-snapshot validation) would catch ──────────────────
    print("\n[4] What option 1 (full-snapshot validation) would do:")
    print("    Option 1 builds a candidate snapshot {start: 10, end: 5} and constructs")
    print("    Interval(start=10, end=5) to validate. The @model_validator runs and sees")
    print("    end (5) <= start (10) -> raises ValueError. The write is REJECTED at the")
    print("    call site; Redis is never touched.")
    print("    >>> Option 1 catches BOTH type mismatches AND cross-field rules.")
    print()
    print("    Demonstrate by actually constructing the candidate (what option 1 does internally):")
    try:
        Interval(start=10, end=5)  # this is what option 1's validation step does
        print("    Interval(10, 5) constructed OK (unexpected)")
    except Exception as e:
        print(f"    Interval(start=10, end=5) raised {type(e).__name__}: {str(e)[:100]}")
        print("    >>> Option 1 would propagate THIS error from __setitem__ and NOT write.")

    # ── VERDICT ───────────────────────────────────────────────────────────────
    banner("SUMMARY")
    print("  - Cross-field validators (like @model_validator 'end > start') need the WHOLE")
    print("    model snapshot to evaluate. They cannot be checked by looking at one field.")
    print("  - Current code: no validation at all -> invalid state persists, error deferred to .value.")
    print("  - Option 2 (single-field TypeAdapter): catches type mismatches (str into int),")
    print("    but CANNOT catch cross-field rules. The start=10/end=5 case would still slip through.")
    print("  - Option 1 (full-snapshot): constructs the candidate model, runs ALL validators")
    print("    (field + model + cross-field), rejects the write at the call site. Strongest guarantee.")
    print("  - The cost of option 1: every __setitem__ reads + reconstructs the whole model")
    print("    (expensive for large nested-container models).")

    dom.close()


if __name__ == "__main__":
    main()

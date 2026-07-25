"""Detailed walkthrough of Finding inconsistency.02: SeriesObject.name hides the
base-class binding identity.

Run:
    .venv/Scripts/python.exe reviews/verification/demo_inconsistency_02_detail.py

Shows the identity-property contradiction on SeriesObject, contrasts it with
DataFrameObject and other object types (which honor the base contract), and
demonstrates the concrete impact on generic code. No source files modified.
"""
from __future__ import annotations

import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig

HOST, PORT, DB, PWD = "127.0.0.1", 6379, 1, "123456"
DOMAIN, NS = "inc02detail", "ns"


def r():
    return redis_lib.Redis(host=HOST, port=PORT, db=DB, password=PWD, decode_responses=True)


def banner(t):
    print("\n" + "=" * 80)
    print(t)
    print("=" * 80)


def show(label, value):
    print(f"    {label}: {value!r}")


def main():
    banner("inconsistency.02 — SeriesObject.name hides the base binding identity")
    r().flushdb()
    import pandas as pd
    from gpdatacached.extensions.pandas_support import register as register_pandas
    from gpdatacached.domain import GPDCDomain

    register_pandas()
    cfg = GPDCDomainConfig(redis_host=HOST, redis_port=PORT, redis_db=DB, redis_password=PWD)
    dom = GPDCDomain(cfg, name=DOMAIN)
    ns = dom.ensure_namespace(NS)

    # ── STEP 1: the base contract — what .name/.uri/.canonical_name SHOULD mean ─
    print("\n[1] Base contract (GPDCDataObject): .name = binding name; .uri and")
    print("    .canonical_name are built from it. All three describe ONE identity.")
    ns["mylist"] = [1, 2, 3]
    lst = ns.get_object("mylist")
    show("list_obj.name", lst.name)             # 'mylist'  <- binding
    show("list_obj.canonical_name", lst.canonical_name)  # 'mylist'
    show("list_obj.uri", lst.uri)               # gpdc://.../mylist
    print("  >>> All three agree on the identity 'mylist'. ✓")

    # ── STEP 2: the contradiction — SeriesObject ──────────────────────────────
    print("\n[2] Now store a Series: binding='binding', pandas name='payload_name'")
    s = pd.Series([1, 2, 3], name="payload_name")
    ns["binding"] = s
    series_obj = ns.get_object("binding")
    show("series_obj.name", series_obj.name)             # 'payload_name' !! pandas name
    show("series_obj.canonical_name", series_obj.canonical_name)  # 'binding'
    show("series_obj.uri", series_obj.uri)               # gpdc://.../binding
    print("  >>> READ THE ABOVE:")
    print("  >>>   .name           = 'payload_name'   (pandas payload metadata)")
    print("  >>>   .canonical_name = 'binding'        (the real GPDC binding)")
    print("  >>>   .uri            = 'gpdc://.../binding'")
    print("  >>> So .name disagrees with .uri and .canonical_name. The three")
    print("  >>> identity properties NO LONGER describe one identity. ✗")
    print("  >>> The base class (object.py:42-44) defines .name = self._name (the")
    print("  >>> binding). SeriesObject overrides it (pandas_support.py:1458-1460)")
    print("  >>> to read the pandas 'name' field from Redis.")

    # ── STEP 3: DataFrameObject does NOT have this problem (the asymmetry) ─────
    print("\n[3] DataFrameObject (same module) does NOT override .name — it honors the base contract:")
    df = pd.DataFrame({"v": [1, 2]}, index=pd.RangeIndex(2))
    ns["mydf"] = df
    df_obj = ns.get_object("mydf")
    show("df_obj.name", df_obj.name)               # 'mydf' <- binding ✓
    show("df_obj.canonical_name", df_obj.canonical_name)  # 'mydf'
    show("df_obj.uri", df_obj.uri)                 # gpdc://.../mydf
    print("  >>> DataFrame's three identity properties all agree on 'mydf'. ✓")
    print("  >>> So Series is the ONLY object type that breaks the base contract.")

    # ── STEP 4: the concrete impact on generic code ───────────────────────────
    print("\n[4] Concrete impact: a generic helper that logs / looks up by .name")
    def find_and_log(obj: object) -> None:
        """Generic code that accepts any GPDCDataObject and trusts .name as identity."""
        name = getattr(obj, "name", None)
        uri = getattr(obj, "uri", None)
        print(f"    generic helper sees: name={name!r}, uri={uri!r}")
        # Naive: re-fetch by .name (works for every type EXCEPT Series):
        try:
            refetched = ns.get_object(name)
            print(f"    re-fetch by .name -> {type(refetched).__name__}, uri={getattr(refetched, 'uri', None)!r}")
        except Exception as e:
            print(f"    re-fetch by .name -> {type(e).__name__}: {e}")

    print("  -- with a ListObject (works):")
    find_and_log(lst)
    print("  -- with a DataFrameObject (works):")
    find_and_log(df_obj)
    print("  -- with a SeriesObject (BREAKS):")
    find_and_log(series_obj)
    print("  >>> The generic helper re-fetches by .name and gets the WRONG object")
    print("  >>> (or a KeyError) for Series, because .name is 'payload_name', not")
    print("  >>> the binding 'binding'. For every other object type it works.")

    # ── STEP 5: the pandas name is still reachable via .value.name ────────────
    print("\n[5] The pandas name is NOT lost — it's reachable via .value.name:")
    show("series_obj.value.name", series_obj.value.name)  # 'payload_name'
    print("  >>> So even if .name returned the binding (base contract), the pandas")
    print("  >>> label would still be accessible via .value.name (and a proposed")
    print("  >>> .series_name convenience accessor).")

    # ── VERDICT ──────────────────────────────────────────────────────────────
    banner("SUMMARY")
    print("  - GPDCDataObject.name is documented (api.md:418) as 'the binding name")
    print("    this object was fetched under'. .uri and .canonical_name are built from it.")
    print("  - SeriesObject overrides .name to return the pandas payload name, so the")
    print("    three identity properties disagree — only for Series.")
    print("  - DataFrameObject (same module) honors the base contract. Series is the")
    print("    sole exception.")
    print("  - Generic code that trusts .name as identity breaks only for Series.")
    print("  - The pandas name is still reachable via .value.name regardless.")
    print("  - Decision: should .name be the binding (base contract, with pandas name")
    print("    on a new .series_name), or stay as pandas name (with a new .binding_name)?")

    dom.close()


if __name__ == "__main__":
    main()

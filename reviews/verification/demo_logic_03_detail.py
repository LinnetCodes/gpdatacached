"""Detailed walkthrough of Finding logic.03: non-unit RangeIndex mishandled.

Run:
    .venv/Scripts/python.exe reviews/verification/demo_logic_03_detail.py

Shows: (A) DataFrame __len__ ignores step; (B) Series extend corrupts range_stop
(missing * step); (C) the DF-vs-Series gate asymmetry that makes the Series bug
reachable while the DF one is latent; (D) round-trip/.iloc are already correct.
No source files modified.
"""
from __future__ import annotations

import redis as redis_lib
import pandas as pd

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.extensions.pandas_support import register as register_pandas

HOST, PORT, DB, PWD = "127.0.0.1", 6379, 1, "123456"


def r():
    return redis_lib.Redis(host=HOST, port=PORT, db=DB, password=PWD, decode_responses=True)


def banner(t):
    print("\n" + "=" * 80)
    print(t)
    print("=" * 80)


def main():
    banner("logic.03 — non-unit RangeIndex mishandled (step assumed == 1)")
    register_pandas()
    r().flushdb()
    from gpdatacached.domain import GPDCDomain

    cfg = GPDCDomainConfig(redis_host=HOST, redis_port=PORT, redis_db=DB, redis_password=PWD)
    dom = GPDCDomain(cfg, name="logic03detail")
    ns = dom.ensure_namespace("ns")

    # The reference: RangeIndex(5, 20, 5) -> labels [5, 10, 15], cardinality 3.
    ref = pd.RangeIndex(5, 20, 5)
    print(f"\n  Reference: pd.RangeIndex(5, 20, 5) -> labels {list(ref)}, len={len(ref)}")

    # ── [A] DataFrame __len__ ignores step ────────────────────────────────────
    print("\n[A] DataFrameObject.__len__ ignores range_step (pandas_support.py:998)")
    df = pd.DataFrame({"v": [10, 20, 30]}, index=pd.RangeIndex(5, 20, 5))
    ns["df"] = df
    cached_df = ns["df"]
    reported = len(cached_df)
    true_rows = len(df)
    print(f"    source DataFrame rows   = {true_rows}  (correct)")
    print(f"    len(cached_df) reported = {reported}   {'<- WRONG (should be 3)' if reported != 3 else ''}")
    print(f"    cached_df.value materializes correctly with {len(cached_df.value)} rows: {list(cached_df.value.index)}")
    print("    >>> __len__ computes (stop-start) = 20-5 = 15, ignoring step. The stored")
    print("    >>> range_step=5 is never read. This is why len() disagrees with .value.")
    # column assignment rejected because __len__ is wrong:
    try:
        cached_df["w"] = [100, 200, 300]  # correct length 3
        print("    3-value column assignment: SUCCEEDED")
    except ValueError as e:
        print(f"    3-value column assignment REJECTED: {e}")
        print("    >>> A correct-length assignment is rejected because __len__ says 15, not 3.")

    # ── [B] Series extend corrupts range_stop (missing * step) ────────────────
    print("\n[B] SeriesObject.extend corrupts range_stop (pandas_support.py:1581)")
    s = pd.Series([10, 20, 30], index=pd.RangeIndex(5, 20, 5), name="x")
    ns["s"] = s
    cached_s = ns["s"]
    print(f"    source Series: index={list(s.index)} (stop=20, step=5)")
    appended = pd.Series([40, 50], index=pd.RangeIndex(20, 30, 5), name="x")  # 2 values
    print(f"    appending 2 values; correct new range would be range(5, 30, 5) -> stop=30")
    cached_s.extend(appended)
    # inspect the stored range_stop directly
    stored_stop = r().hget(cached_s.raw_key, "range_stop")
    print(f"    stored range_stop after extend = {stored_stop}  (correct would be 30)")
    print(f"    >>> Code did range_stop + len(new_data) = 20 + 2 = 22, missing '* step'.")
    print(f"    >>> Should be 20 + 2*5 = 30. Now the index is wrong:")
    try:
        val = cached_s.value
        print(f"    .value materialized OK (unexpected): len={len(val)}, index={list(val.index)}")
    except Exception as e:
        print(f"    .value raised {type(e).__name__}: {e}")
        print("    >>> 4 data values but range(5,22,5)=[5,10,15,20] is only 4 labels for... wait,")
        print("    >>> the mismatch surfaces as a pandas length error on materialization.")

    # ── [C] The DF-vs-Series gate asymmetry ───────────────────────────────────
    print("\n[C] Why DF is safe but Series is not: the gate asymmetry")
    print("    DataFrameObject.extend gate (pandas_support.py:912):")
    print("        if range_step == 1 and appended_is_range:   # <- checks STORED step")
    print("            ... new_stop = range_stop + n  (line 924, same *step bug)")
    print("    >>> DF's buggy line 924 is GATED behind step==1, so a non-unit RangeIndex")
    print("    >>> NEVER reaches it — DF falls through to promote-to-levels (safe).")
    print()
    print("    SeriesObject.extend gate (pandas_support.py:1562):")
    print("        if not _is_range_indexed_series(other):   # <- does NOT check stored step!")
    print("            ... new_stop = range_stop + len(new_data)  (line 1581, the live bug)")
    print("    >>> Series takes the fast path for ANY range-indexed input, regardless of")
    print("    >>> the STORED step. So a non-unit RangeIndex Series hits the buggy line.")
    print("    >>> This asymmetry is why the Series bug is reachable and the DF bug is not.")
    # Demonstrate DF extend on non-unit step does NOT corrupt (promotes instead):
    print()
    ns["df2"] = pd.DataFrame({"v": [1, 2, 3]}, index=pd.RangeIndex(5, 20, 5))
    df2 = ns["df2"]
    kind_before = r().hget(df2.raw_key, "~index_kind")
    df2.extend(pd.DataFrame({"v": [4, 5]}, index=pd.RangeIndex(20, 30, 5)))
    kind_after = r().hget(df2.raw_key, "~index_kind")
    print(f"    DF extend on non-unit step: index_kind {kind_before!r} -> {kind_after!r} (promoted, no corruption)")
    try:
        v = df2.value
        print(f"    DF .value after extend OK: rows={len(v)}, index={list(v.index)}")
    except Exception as e:
        print(f"    DF .value after extend raised: {e}")

    # ── [D] Already-correct paths (no fix needed) ─────────────────────────────
    print("\n[D] Paths that already handle step correctly (no fix needed):")
    ns["s2"] = pd.Series([10, 20, 30], index=pd.RangeIndex(5, 20, 5), name="x")
    s2 = ns["s2"]
    print(f"    round-trip (.value): {list(s2.value.index)}  <- correct (uses stored step)")
    print(f"    .iloc[0:2] index:    {list(s2.iloc[0:2].index)}  <- correct (uses stored step)")
    print("    >>> _build_index / read_raw / _positional_index_slice all pass step to pd.RangeIndex.")
    print("    >>> Only __len__ and extend's new_stop math are wrong.")

    # ── VERDICT ───────────────────────────────────────────────────────────────
    banner("SUMMARY")
    print("  - DataFrameObject.__len__ ignores range_step -> wrong row count (15 vs 3).")
    print("  - SeriesObject.extend computes new_stop without * step -> corrupts the index.")
    print("  - DF extend has the same *step bug but it's gated behind step==1 (latent).")
    print("  - Asymmetry: DF gate checks stored step (promotes non-unit); Series gate does not.")
    print("  - round-trip / .iloc already correct (they use the stored step).")
    print("  - Fix decision: keep non-unit ranges compact (fix math + widen DF gate), OR")
    print("    align Series with DF (add step==1 gate -> promote non-unit). The former is")
    print("    more consistent with the already-stored range_step and correct round-trip.")

    dom.close()


if __name__ == "__main__":
    main()

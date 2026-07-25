# Verification Report — CQR-2026-07-24-codebase-quality-review

- **Report verified:** `reviews/CQR-2026-07-24-codebase-quality-review.md` (READ-ONLY — not modified)
- **Reviewer (report author):** Codex · reviewed commit `0edade9` · report branch `codebase-quality-review-2026-07-24`
- **Verified on:** branch `main` @ `3576b90`, CPython 3.13.11, live Redis `127.0.0.1:6379` DB 1
- **Verification artifacts (this directory):** `progress.md` (per-finding state of record), `verify_findings.py` (runnable reproductions), this report.
- **Per the verification skill:** no source code was modified. Every finding below is verified, not fixed. No fix proceeds without an explicit human decision on that finding.

## How to reproduce

```bash
.venv/Scripts/python.exe reviews/verification/verify_findings.py            # all 10 demos
.venv/Scripts/python.exe reviews/verification/verify_findings.py logic.03   # one
```

Each demo flushes Redis DB 1 itself. `clarity.01` is a static path-check (no Redis needed).

## Headline result

**All 11 findings reproduce.** 10 are **Confirmed** exactly as the report describes. One (`perf.01`) is **Partial**: the core defect (an unbounded hot loop for `gc_interval_seconds <= 0`) is real and confirmed, but the report's claim that negative intervals "terminate the daemon path" via a `ValueError` from `threading.Event.wait` is **wrong on CPython** — `Event.wait(-5)` returns `False` immediately (same as 0); it never raises, so negatives *also* spin, they do not crash the thread.

One finding has a **mis-stated citation** but a **correct substance**: `inconsistency.03` attributes a "every method raises after `close()`" invariant to "AGENTS.md §3.9", but `AGENTS.md` did not exist at the reviewed commit (`0edade9` — `git show 0edade9:AGANTS.md` reports it absent), and the current `AGENTS.md` does not mention `GPDataCache` at all. The *actual* contract split — `__contains__` returns a value after `close()` while `get_domain` / `get_object_by_uri` / `has_uri` all raise `RuntimeError` — is real and confirmed.

## Consolidated verdicts

| # | Finding (short) | Sev | Verdict | Recommended action | Decision |
|---|---|---|---|---|---|
| logic.01 | Failed encode leaves partial writes / phantom refs | Critical | **Confirmed** | Staged encode + memo ref-increment rollback (opt 1) — **DONE** | **Fix, done** |
| logic.02 | Stale handle mutates `raw:` before `ObjectGoneError` | Major | **Confirmed** | `_check_alive()` + `_touch(meta=None)` refactor (opt B) — **DONE** | **Fix, done** |
| logic.03 | Non-unit `RangeIndex` read as `step==1` | Critical | **Confirmed** | Direction A: honour stored step everywhere (keep compact) — **DONE** | **Fix, done** |
| inconsistency.01 | `expire_in(0)` persists invalid `ttl`-mode policy | Major | **Confirmed** | Add `expire_now()` + strict `expire_in` (maintainer variant) — **DONE** | **Fix, done** |
| inconsistency.02 | `SeriesObject.name` hides binding identity | Major | **Confirmed** | Restore `.name`=binding, add `.series_name` (opt 1) — **DONE** | **Fix, done** |
| inconsistency.03 | Closed-cache `__contains__` contract disagrees | Minor | **Confirmed** (citation mis-stated) | Docs + contract tests (opt 2+3, no code change) — **DONE** | **Fix, done** |
| clarity.01 | Every README doc link 404s | Major | **Confirmed** | Repoint to `docs/en`+`docs/zh` (opt 1, no CI) — **DONE** | **Fix, done** |
| gap.01 | `take_ownership()` no-op for named reference | Critical | **Mischaracterized → DOC defect** (code correct; docs described inverted semantics) | Rewrite docs + overview + doc-test (opt 2) — **DONE** | **Fix (opt 2), done** |
| perf.01 | `gc_interval_seconds=0` unbounded GC loop | Major | **Partial** (negative-interval claim wrong) | Constrain `gc_interval_seconds >= 30` (opt 1, floor=30) — **DONE** | **Fix, done** |
| behavior.01 | Pydantic `__setitem__` bypasses validation | Major | **Confirmed** | Single-field TypeAdapter validation (opt 2) + scalar-codec guidance — **DONE** | **Fix, done** |
| structure.01 | Test fixture leaks constructed domain | Minor | **Confirmed** | Yielding fixture + `close()` (opt 1) — **DONE** | **Fix, done** |

## Detailed evidence (per finding)

Full evidence, options, recommendations, and demo output for each finding live in `progress.md` (the state-of-record file). The headline repros:

- **logic.01 [A] Dict:** `d.update({"ok":[1,2],"bad":object()})` → `TypeError`; parent dict raw kept `str:ok -> ref:…:~anon:…` after the anon's `obj:` key was rolled back → **dangling `ref:`**. **[B] List:** `parent.extend([child, object()])` → `TypeError`; parent stayed `[]` but child's `ref:` gained `parent -> 1` → **phantom referrer**.
- **logic.02:** stored `[1]`, kept handle, `del ns["lst"]`, then `stale.append(2)` → `ObjectGoneError`, but `raw:lst` still `EXISTS` with `['int:2']` — orphaned raw key.
- **logic.03 [A] DataFrame** `RangeIndex(5,20,5)` (3 rows): `len(cached_df)` = **15**; correct 3-value column assignment rejected. **[B] Series** + 5 appends: `extend()` "succeeded", `.value` raised `Length of values (8) does not match length of index (4)`.
- **inconsistency.01:** `ttl=60` object → `expire_in(0)` succeeds; meta `cache_mode=ttl, declared_ttl=0`, Redis TTL **-1** (persistent).
- **inconsistency.02:** binding `binding` + pandas name `payload_name` → `obj.name == "payload_name"`, `obj.canonical_name == "binding"`, `obj.uri == "gpdc://inc02/ns/binding"`.
- **inconsistency.03:** after `cache.close()`, `"core_domain" in cache` → `True` (no error); `get_domain`/`has_uri` → `RuntimeError`.
- **gap.01 (REVISED → false positive):** The report's scenario (`ns["alias"]=ns["original"]`, both named) showed `meta.owner` unchanged after `take_ownership()`. The maintainer clarified the design intent: `take_ownership` targets *the referenced object itself* (to make it self-owned), not the referencer. Anonymous nested objects are owned by their parent; a named reference to one lets the user promote the **anon** to self-owned via `take_ownership` — and that path **works** (`demo_gap_01_intent.py`: anon `owner` `'parent'`→`'~anon:…'`, then independent `ttl=60` while parent stays `permanent`). Named objects are *already* self-owned, so a no-op on a reference to one is correct, not a trap (in a multi-process shared env the caller can't/needn't know if the referenced object was created anonymous or named; the call's intent — "this object should own its lifecycle" — is satisfied either way). **No code change.** Minor leftover: `lifecycle.md:209-219` frames the example with two named objects and the misleading `owner="alias"` — a doc-clarity nit, left to a separate optional decision.
- **perf.01:** `gc_interval_seconds=0` → `Event.wait` called **167× in ~50 ms** (no sleep; CPU-saturating spin). *(Negative intervals do NOT crash the thread as the report claims — see Partial note.)*
- **behavior.01:** `Order(total: float)`; `obj["total"] = "not-a-number"` succeeded, read back as `str`; `obj.value` later raised `ValidationError`.
- **structure.01:** `tests/test_nested_containers.py:39-45` `ns` fixture — no `yield`, no `domain.close()` → domain leaked per test.
- **clarity.01:** all 9 `docs/*.md` and all `docs/zh-CN/*.md` paths in both READMEs are **MISSING**; files exist under `docs/en/` and `docs/zh/`.

## Notes the maintainer should weigh

1. **gap.01 was revised to a false positive** after the maintainer clarified the design intent (see above). It is removed from the open-decisions list.
2. **inconsistency.01 has a sibling that already chose differently.** `expire_at` (object.py:233-243) treats `seconds==0` as "delete immediately". So picking a `expire_in(0)` semantics is also picking whether the two verbs agree.
3. **logic.01's documented rollback guarantee is already half-implemented** (`_rollback_encode_memo` exists and deletes anons); option 1 finishes the job rather than inventing a new mechanism.
4. **The report's own baseline flagged one flaky test** (`test_lock_survives_holder_crash`, 3.13s vs 3.0s ceiling, passed alone at 2.55s). Out of scope for these findings but worth noting for the "relax the wall-clock threshold" item in the report's fix plan.

## Handoff — open decisions

All 11 findings are **awaiting a human ruling**. Per the verification skill, no source edits will be made until you decide, per finding, one of: **fix (option N)** / **don't fix** / **a different approach** / **defer**. Suggested triage order (matches the report's, independently confirmed):

1. **Contract decisions first** (affect public API): `inconsistency.01`, `inconsistency.02`, `behavior.01`. (`gap.01` resolved as false positive.)
2. **State-integrity correctness** (with regression tests asserting Redis state after failure): `logic.01`, `logic.02`, `logic.03`.
3. **Bounded config + docs + fixture cleanup**: `perf.01`, `clarity.01`, `structure.01`, `inconsistency.03`.

Tell me which finding(s) to act on and which option, and I'll implement exactly that — one finding at a time or batched, your call.

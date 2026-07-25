# Codebase Quality Review Verification Progress

- **Review report source:** `reviews/CQR-2026-07-24-codebase-quality-review.md` (READ-ONLY, never edit)
- **Branch:** fixes are committed on **`codebase-review-fix`** (NOT `main` — the harness auto-branched off `main`); `main` itself is untouched at `3576b90`. The report reviewed commit `0edade9`.
- **Output directory:** `reviews/verification/`
- **Date started:** 2026-07-24
- **Last updated:** 2026-07-26 (review fully complete — all 11 findings + OOS-1 + all 3 open-decision items done)

## Session status (resume here next time)

### ✅ DONE — all 11 CQR findings + OOS-1 + all 3 open-decision items are fixed and committed on `codebase-review-fix`

14 commits total on `codebase-review-fix` (off `main`, which is untouched). Commit list (`git log main..codebase-review-fix --oneline`):
```
f1a17aa docs(anchors): durable explicit anchors + CI gate; tighten rollback wording   [OOS-2 + item 3]
5022f36 docs(zh): fix dead §7 cross-ref anchors; rename §7 heading to pure English   [OOS-1]
77caa93 test(nested): close the domain in the ns fixture (structure.01)
783a413 docs(data_cache): document __contains__ config-snapshot semantics; pin tests  [inconsistency.03]
50d9dfa fix(config): require gc_interval_seconds >= 30 to prevent GC busy-loop        [perf.01]
822e2bc docs(readme): repoint README doc links to docs/en and docs/zh                 [clarity.01]
9d5c42d fix(encode): stage multi-element encodes; reverse ref increments on failure   [logic.01]
e2bdfc1 fix(pandas): honour non-unit RangeIndex step in __len__ and extend            [logic.03]
18049a6 fix(list): guard _delete_index (covers pop/remove) for logic.02               [logic.02 audit]
52ada4a fix(container): guard every mutator with _check_alive before writing raw:     [logic.02]
caa9b8c feat(pyandic): validate container-mode __setitem__ field type before write    [behavior.01]
9221ce4 fix(pandas): SeriesObject.name is the binding; expose pandas label as series_name [inconsistency.02]
8162a31 feat(object): add expire_now(); reject expire_in(ttl<=0)                      [inconsistency.01]
4a34b49 docs(lifecycle): correct take_ownership() semantics — operates on referenced  [gap.01]
```

Test status: full suite green at **1448 passed** (maintainer-confirmed; the agent's own full-suite run was inconclusive mid-review due to a Redis-was-down incident, but every fix was individually verified + ruff clean throughout). Note: the test count grew across fixes as regression tests were added (started ~1386 at the report baseline; ended ~1448). The OOS-2 + item-3 work (commit `f1a17aa`) is docs-only — no tests affected; verified instead by `mkdocs build --strict` exiting 0 with 0 warnings.

Findings verdicts at a glance: 8 fixed-as-confirmed; `gap.01` reclassified false-positive-as-CODE → fixed-as-DOC; `perf.01` Partial (report's negative-interval "ValueError" claim was wrong, but the core busy-loop defect was real and fixed); `inconsistency.03` confirmed-but-citation-mis-stated (no code change needed — documented + tested).

### ✅ DONE — the three open-decision items (2026-07-26)

All three items deferred from the 2026-07-25 session are now resolved (maintainer decisions recorded in their detail sections below):

1. **OOS-2 (doc anchors) — DONE:** maintainer chose a **durable explicit-anchor scheme** (not any of the original 4 options, but a refined option 3: explicit `{ #id }` attributes + a CI gate). Implemented in `f1a17aa`. See the OOS-2 detail section.
2. **pandas promote helpers — DONE:** maintainer chose **leave as-is** (option 1). No code change; the docstring's admission remains the source of truth. Recorded in the logic.01 detail section's "Out-of-scope note".
3. **Doc over-claim "leaves Redis untouched" — DONE:** maintainer chose **tighten the wording now** (option 1). Implemented in `f1a17aa`. See the logic.01 detail section's "Doc sync" note (now resolved).

### Lessons logged for next session
- **Always `PING` Redis before running the suite** — the test Redis (`127.0.0.1:6379` DB 1 password `123456`) is not always running; a down Redis makes pytest hang on connect and the harness auto-backgrounds it with no visible output. Ping first; if down, ask the maintainer to start it.
- The Git Bash shell snapshot in this environment intermittently fails with a sourcing error; when a raw `grep`/`git` one-liner returns exit 127 with a `snapshot-bash-*.sh` traceback, just retry, or use the Explore agent / Edit tool instead.
- Reproduction scripts (all flush DB 1; no source modified): `verify_findings.py` (all 10 findings), plus per-finding `demo_*_detail.py` walkthroughs and `verify_logic_01_points.py` (the 3 logic.01 review points). These still demonstrate the original bugs if needed.

## Verification environment

- Python venv: `.venv/Scripts/python.exe` (CPython 3.13.11)
- Redis: `127.0.0.1:6379` DB `1` password `123456` (flushed before each demo)
- Reproduction scripts (all flush DB 1 themselves; no source files modified):
  - `verify_findings.py` — all 10 original findings, one-shot.
  - `demo_gap_01_detail.py` / `demo_gap_01_intent.py` — gap.01 (the report's wrong scenario vs. the intended anon path).
  - `demo_inconsistency_01_detail.py` / `demo_inconsistency_02_detail.py` — inconsistency.01/02 walkthroughs.
  - `demo_logic_01_detail.py` / `demo_logic_02_detail.py` / `demo_logic_03_detail.py` — logic.01/02/03 walkthroughs.
  - `demo_behavior_01_cross_field.py` — behavior.01 cross-field validator demo.
  - `verify_logic_01_points.py` — the 3 review points (single-element, nested session, create_raw cleanup).

## How to reproduce

```bash
.venv/Scripts/python.exe reviews/verification/verify_findings.py            # all
.venv/Scripts/python.exe reviews/verification/verify_findings.py logic.03   # one
```

## Headline verdicts

**All 11 findings reproduce.** 10 are Confirmed exactly as described; 1 (perf.01) is **Partial** — the core defect (unbounded hot loop) is real for zero AND negative intervals, but the report's claim that negative intervals "terminate the daemon path" via a `ValueError` from `Event.wait` is **incorrect** on CPython (negative timeouts return `False` immediately, just like 0 — they never raise). One finding's *citation* is wrong but its *substance* is right: inconsistency.03 cites "AGENTS.md §3.9", but `AGENTS.md` did not exist at the reviewed commit (`0edade9`); the real contract split is between `__contains__` (no `_check_open`, matches the design spec) and its sibling methods (`get_domain`/`get_object_by_uri`/`has_uri` all raise `RuntimeError` after `close()`).

## Summary table

| # | Finding (short) | Severity | Verdict | Recommended action | Human decision | Fix status |
|---|---|---|---|---|---|---|
| logic.01 | Failed multi-element encodes leave partial writes / phantom refs | Critical | **Confirmed** | Staged encode + memo ref-increment rollback (option 1) | **Fix (option 1)** | **done** |
| logic.02 | Stale handle mutates Redis before `ObjectGoneError` | Major | **Confirmed** | `_check_alive()` + `_touch(meta=None)` refactor (option B) | **Fix (option B)** | **done** |
| logic.03 | Non-unit `RangeIndex` interpreted as `step==1` | Critical | **Confirmed** | Direction A: honour stored step everywhere (keep compact) | **Fix (direction A)** | **done** |
| inconsistency.01 | `expire_in(0)` persists invalid ttl-mode policy | Major | **Confirmed** | Add `expire_now()` + strict-validate `expire_in` (option: new API) | **Fix** | **done** |
| inconsistency.02 | `SeriesObject.name` hides base binding identity | Major | **Confirmed** | Restore `.name`=binding, add `.series_name` (option 1) | **Fix (option 1)** | **done** |
| inconsistency.03 | Closed-cache `__contains__` contract disagrees | Minor | **Confirmed (citation mis-stated)** | Docs + contract tests (option 2+3, no code change) | **Fix (2+3, no code change)** | **done** |
| clarity.01 | Every README doc link points to non-existent path | Major | **Confirmed** | Repoint links to `docs/en` & `docs/zh` (option 1, no CI) | **Fix (option 1, no CI)** | **done** |
| gap.01 | `take_ownership()` no-op for named reference | Critical | **Mischaracterized → DOC defect** (code correct; docs described inverted semantics) | Rewrite docs + overview + doc-test (option 2) | **Fix (option 2)** | **done** |
| perf.01 | `gc_interval_seconds=0` unbounded GC loop | Major | **Partial** (negative-interval claim wrong) | Constrain `gc_interval_seconds >= 30` (option 1, floor=30) | **Fix (floor=30)** | **done** |
| behavior.01 | Pydantic `__setitem__` bypasses validation | Major | **Confirmed** | Single-field TypeAdapter validation (option 2) + doc scalar-codec guidance | **Fix (option 2)** | **done** |
| structure.01 | Test fixture leaks constructed domain | Minor | **Confirmed** | Yielding fixture + `domain.close()` (option 1) | **Fix (option 1)** | **done** |

## Out-of-scope issues discovered during verification

These are NOT CQR-report findings. They surfaced while verifying/fixing findings above. Tracked separately so they aren't lost; each needs its own human decision. No action taken on any of them.

### OOS-1 — Stale §7 anchor in Chinese cross-references
- **Source:** discovered while fixing gap.01 (doc rewrite).
- **What (original):** the ZH `lifecycle.md` §7 heading was the mixed-language `## 7. Ownership（所有者）模型`; cross-references used the truncated `#7-ownership`, which never matched MkDocs' slug `#7-ownership所有者模型` → dead links. Affected: `docs/zh/lifecycle.md:170,314`, `docs/zh/api.md:412`, plus the gap.01-added `docs/zh/overview.md:257` (which used the full Chinese slug).
- **Verification (2026-07-25):** installed MkDocs 1.6.1 into the venv temporarily and ran its actual `slugify_unicode` (MkDocs' forced default) on the headings. Confirmed: MkDocs generates `#7-ownership所有者模型` for the mixed heading (matches GitHub). Also confirmed a wider problem — see OOS-2.
- **Human decision:** **Fix — rename the ZH §7 heading to pure English `## 7. The Ownership Model` (anchor `#7-the-ownership-model`, pure ASCII, stable across MkDocs + GitHub, and identical to the EN §7 anchor), and repoint all 4 ZH §7 references to `#7-the-ownership-model`.** Maintainer's standing principle: "anchors should not contain Chinese" (MkDocs slug rules for CJK are error-prone). MkDocs uninstalled after verification; `pyproject.toml` unchanged.
- **Implementation status:** **done (2026-07-25).** Changes: `docs/zh/lifecycle.md` §7 heading → `## 7. The Ownership Model`; 4 references (`zh/lifecycle.md:170,314`, `zh/api.md:412`, `zh/overview.md:257`) now use `#7-the-ownership-model`. Pure doc change; no code/tests affected.

### OOS-2 — Wider dead-anchor problem in ZH docs (originally framed as "all `[§N](#N)` cross-refs are dead")
- **Source:** discovered while verifying OOS-1 (2026-07-25).
- **What (original framing):** the ZH `lifecycle.md` uses pure-Chinese section headings (e.g. `## 8. 匿名对象的生命周期`), but its in-page cross-references use **truncated pure-number anchors** like `[§8](#8)`, `[§5](#5)`. The 2026-07-25 session recorded these as dead, attributing it to "MkDocs' `slugify_unicode` generates full Chinese slugs".
- **⚠️ Correction (2026-07-26, empirical):** the 2026-07-25 framing was **wrong about the mechanism**. Verified by reading the actual `mkdocs.yml` + building the real site with MkDocs 1.6.1 + pymdown-extensions + mkdocs-static-i18n + mkdocs-material:
  - `mkdocs.yml`'s `toc:` extension has **no `slugify` key**, so Python-Markdown uses its **default Unicode-*stripping* `slugify`** (`unicodedata.normalize('NFKD').encode('ascii','ignore')`), NOT `slugify_unicode`. MkDocs 1.6.1 does not even ship a `slugify` module; the slugifier lives in `markdown/extensions/toc.py`.
  - Under the default slugifier, `## 8. 匿名对象的生命周期` → `id="8"` (the CJK is stripped, the leading digit survives), so `[§8](#8)` **actually resolves**. An exhaustive render-test (`mkdocs build` + inspect rendered HTML) confirmed **all 12 `[§N](#N)` links resolved** on the deployed config.
  - So the links were **fragile** (correct-by-accident; depend on the default slugifier stripping CJK) but **not broken**. The real defects were elsewhere: 6 genuinely-broken links (`#get_domain`, `#scalar-mode-atomic-json`, `#快速开始`, `#5-滑动窗口刷新` × locales) + the latent fragility (any future `slugify_unicode` config would break all 12 instantly) + 190 CJK headings producing empty/`_N` auto-dedup slugs (un-addressable from links).
- **Full inventory (exhaustive audit, 92 anchor links across 9 ZH files):** 46 stable-ASCII (code identifiers, already fine); 34 `[§N](#N)`-style (resolve by accident under default slugify, fragile); 2 hand-written CJK anchors (`#快速开始`, `#5-滑动窗口刷新` — broken); 10 `_N` auto-dedup links (`#_3`/`#_5`/`#_8`/`#_9`/`#_11`/`#_16` — resolve by position, fragile + wrong-target-prone); + 2 genuinely-broken (`#get_domain`, `#scalar-mode-atomic-json`).
- **Severity:** Minor (doc nav; no behavioural impact), broad, and pre-existing.
- **Options considered (2026-07-26, refined):**
  1. Leave as-is (links resolve today; the fragility is a latent config-coupling issue).
  2. Fix to full Chinese slugs `#N-中文标题` (would require `slugify_unicode`; anchors contain Chinese — against the OOS-1 principle; 44 links to rewrite).
  3. **Pick an intentional slugify strategy and pin it in `mkdocs.yml`, then migrate links.** Sub-options:
     - 3a. Pin default (Unicode-stripping) explicitly — 12 links to rewrite, CJK sub-headings stay un-addressable.
     - 3b. Switch to `slugify_unicode` — 44 links to rewrite, all CJK headings become addressable but anchors contain Chinese.
     - 3c. Explicit `{ #ascii-id }` attributes per heading (via `attr_list`) — anchors stay ASCII, decoupled from heading text, all CJK headings addressable; ~46 links to rewrite + ~190 headings to attribute.
- **Recommendation (agent):** option 3c — the only option that solves fragility, the `_N`-anchor bug, AND the un-addressable-CJK-heading issue without putting Chinese in anchors.
- **Human decision:** **Fix — option 3c (durable explicit-anchor scheme), 2026-07-26.** Rationale: the user asked specifically for a *durable* solution because "these docs are frequently edited, so an unstable anchor strategy will rot". Explicit `{ #id }` attributes decouple anchors from heading text (editing a title never breaks a link), and a CI gate catches any future orphaned link. Maintainer's four sub-decisions: (1) accept `attr_list` syntax in doc source; (2) CI `mkdocs build --strict` failure = block merge (maintainer runs a local pre-test before pushing to `mkdocs-pre-publish`); (3) prefix-grouped anchor names (`lifecycle-*`, `locking-*`, `perf-*`, `extend-*`, `pandas-*`, `pydantic-*`, `api-*`); (4) EN mirrors ZH's anchor changes (same `{ #id }` names on both sides) + fix the 2 EN broken links; EN's other already-stable headings left alone. Docs release flow unchanged (local pre-test → push `mkdocs-pre-publish`); `docs.yml` needs **zero** changes because it already runs `mkdocs build --strict`.
- **Implementation status:** **done (2026-07-26, commit `f1a17aa`).** Changes:
  - `mkdocs.yml`: added `validation:` block (`anchors: warn` + `not_found`/`omitted_files`/`absolute_links: warn`) so `mkdocs build --strict` actually fails on broken anchors (MkDocs 1.6.1 defaults broken in-page anchors to `INFO`, which `--strict` ignores — the `validation.anchors: warn` promotion is what makes the gate work). Added `exclude_docs: superpowers/` so the git-tracked design/plan docs under `docs/superpowers/` don't trigger `omitted_files` warnings (they ship in the sdist but must not appear in nav or block the gate). `attr_list` was already explicit at `mkdocs.yml:114`; `toc:` left unchanged (no `slugify` key — explicit anchors make slugify irrelevant).
  - 38 explicit anchors added (19 ZH + 19 EN, identical `{ #id }` names per concept) to every heading that is a link target, via `{ #prefix-name }` `attr_list` syntax.
  - 62 links rewritten (34 `[§N](#N)` bare-number + 10 `_N` auto-dedup + 2 hand-written CJK + 6 genuinely-broken + the EN mirrors) to point at the new explicit anchors. EN's 2 genuinely-broken links (`#get_domain`, `#scalar-mode-atomic-json`) also fixed.
- **Verification (2026-07-26):**
  - `mkdocs build --strict` → **exit 0, 0 warnings** (was 19 warnings after adding anchors, before the link rewrite; 0 after).
  - Anchor audit: 19/19 expected anchors present per locale; 0 fragile-link residues (no `#N`, no `#_N`, no CJK anchors remain); 0 hand-written CJK anchors.
  - mkdocs + extensions **left installed in the venv** (maintainer: "will be used frequently going forward"). `pyproject.toml` already declares `mkdocs>=1.6` / `mkdocs-material>=9.7` / `mkdocs-static-i18n>=1.3` in the `docs` extra; `pymdown-extensions` comes transitively via `mkdocs-material`. No `pyproject.toml` change needed.
- **Note on the docs release workflow:** `origin/mkdocs-pre-publish:.github/workflows/docs.yml` already runs `mkdocs build --strict` on push to `mkdocs-pre-publish`. Before this fix, `--strict` never caught broken in-page anchors (they were `INFO`). After adding `validation.anchors: warn` to `mkdocs.yml`, that same `--strict` step now fails on any orphaned anchor — so the existing CI became the durability gate with zero workflow changes.

## Findings (detail)

### Finding logic.01 — Failed multi-element encodes leave partial writes / phantom refs
- **Report claim:** `encode_session` rolls back anonymous objects only; parent raw writes, ref-count increments on pre-existing containers, and old-ref releases are not rolled back. `DictObject.update` writes per-pair during encode; `ListObject.extend` increments refs during encode before its list write. A later encoding error leaves Redis in a state no successful op could produce.
- **Original severity:** Critical
- **Verdict:** **Confirmed**
- **Evidence:**
  - `src/gpdatacached/types.py:165-177` `_rollback_encode_memo` only deletes anonymous `obj:`/`raw:`/`ref:` keys; never reverses parent `hset`/`rpush` or `add_referrer` from `_ref_existing_container` (`types.py:209-223`).
  - `src/gpdatacached/builtins/dict_object.py:174-185` `update` does `r.hset(...)` per pair inside the encode loop; a failure mid-loop leaves already-written pairs.
  - `src/gpdatacached/builtins/list_object.py:134-138` `extend` encodes (and references children) inside `encode_session`, then `rpush`es *after*; on failure the list stays empty but the child ref is already bumped.
  - Demo `demo_logic_01`:
    - **[A] Dict:** `d.update({"ok":[1,2],"bad":object()})` → `TypeError`; parent raw kept `str:ok -> ref:gpdc:…:obj:~anon:…` while that anon `obj:` key was deleted by rollback → **dangling `ref:` pointer** in the parent hash.
    - **[B] List:** `parent.extend([child, object()])` → `TypeError`; parent raw stayed `[]` but `child`'s `ref:` hash gained `parent -> 1` → **phantom referrer** preventing child reclamation.
- **Options considered:**
  1. **Stage all encoded values + all ref deltas in memory, commit parent writes and ref-count changes atomically only after the full encode succeeds.** (Lowest risk; matches the documented "leaves Redis untouched" guarantee. Cost: restructure `update`/`extend`/`create_raw` to defer Redis writes.)
  2. Extend `encode_session` with a Redis undo journal (anon creation, existing-ref increments, raw writes, old-ref releases) and reverse it on failure. (More general; more bookkeeping; rollback itself is not atomic.)
  3. Encode into temp keys, atomically swap (RENAME) into place after validation. (Extra keys, cleanup complexity, but strongest atomicity.)
- **Recommendation:** Option 1 — direct, testable, matches the existing `encode_session` rollback intent; failure-injection tests that assert Redis state after a failed encode should accompany it.
- **Human decision:** **Fix (option 1 — staged encode + memo ref-increment rollback), 2026-07-24.** Maintainer approved the staged-then-committed approach.
- **Investigation (2026-07-24):** exhaustive audit found **5 affected sites**, broader than the report's 2:
  - (1) `DictObject.update` → (a) parent hset mid-loop + (c) `_release_element_ref` mid-loop.
  - (2) `ListObject.extend` → (b) only: its parent write (rpush) is already post-session (no (a)), but `_ref_existing_container`'s `add_referrer` still happens at encode time (demo [B] confirmed).
  - (3) `DataFrameObject.__setitem__` → (c): old column refs released at lines 1148/1152 BEFORE the encode session.
  - (4) pandas promote helpers (`_promote_df_range_to_labels`/`_promote_range_to_labels`) → a (a) variant (pre-commit index representation change). **Left unfixed (see note below)** — semantically valid (no corrupt/leak/loss), a representation-only change.
  - (5) `namespace.__setitem__` container branch → orphan `obj:` meta (written before the encode session, no cleanup on failure).
  - Why staging over an undo journal: (c)'s `_release_element_ref` can trigger `_cascade_delete_anonymous` (irreversible data loss); staging avoids releasing before success, so cascade never happens on the failure path. (a)/(b) reversed by the staging + a memo extension respectively.
  - Documented guarantee is contradictory: `overview.md`/`extensibility.md` over-claim "leaves Redis untouched"; `lifecycle.md:279` quietly narrows it to "no orphan anons" and concedes the `obj:` meta leak. None mention (a)/(b)/(c).
- **Implementation status:** **done (2026-07-24).** Changes (option 1):
  - `src/gpdatacached/types.py`: added a `_ref_increments_var` ContextVar; `_ref_existing_container` now records each `add_referrer` (target ref_key + referrer_name) into the active session's list; `_rollback_encode_memo` now also reverses those increments via `release_ref` (handles (b) for `ListObject.extend` and any other path that references pre-existing containers). Nested encode sessions inherit the outer list so all increments are tracked to the outermost session.
  - `src/gpdatacached/builtins/dict_object.py`: `update` now stages all `(encoded_key, encoded_val, old_encoded)` tuples in memory inside the encode session, and only commits (`hset` + `_release_element_ref`) after the session closes successfully — eliminating (a) partial parent write and (c) early release.
  - `src/gpdatacached/extensions/pandas_support.py`: `DataFrameObject.__setitem__` now captures old column/category refs before the encode session and releases them only after the session succeeds — eliminating (c).
  - `src/gpdatacached/namespace.py`: the container branch of `__setitem__` now wraps `create_raw` in try/except that deletes `obj:`/`raw:`/`ref:` on failure, so a failed first-store encode leaves no orphan `obj:` meta.
  - Tests: +6 (`test_encode_memo.py::TestFailedEncodeLeavesRedisUntouchedLogic01` — 5 cases covering [A] partial-write-reverted, [C] old-ref-not-released, [B] phantom-referrer-reverted, namespace orphan-meta-cleanup, and a normal-update sanity check; plus `test_pandas_dataframe_support.py::TestFailedEncodeLogic01DataFrame` — DF `__setitem__` column-replace-with-cyclic-value leaves the old column intact).
- **Verification (2026-07-24):**
  - `ruff check src tests` → All checks passed.
  - Full suite `pytest -q -p no:cacheprovider` → **1437 passed** (+6 net).
  - `demo_logic_01_detail.py` post-fix: [A] `'str:ok'` absent after failure (parent hash == before); [B] child `ref:` empty after failure (no phantom); [C] `'str:k'` unchanged and old anon still exists.
- **Out-of-scope note (flagged, decided 2026-07-26 — LEAVE AS-IS):** the pandas promote helpers (`_promote_df_range_to_labels` at pandas_support.py:810-839, `_promote_range_to_labels` at 1644-1661) pre-commit an index-representation change (range→levels) before the row-append pipeline. Their own docstrings admit this ("if the subsequent append fails, the index stays promoted with no rows added — semantically valid but representationally changed"). Unlike (a)/(b)/(c), this is **not** a corrupt/leak/loss — the object remains readable and consistent, just in a different storage layout. **Maintainer decision (2026-07-26): option 1 — leave as-is.** Rationale: the docstring's admission is honest, the failure mode is benign, and making the promote deferrable would add complexity for no correctness gain. No code change. The promote-helper behaviour is now also documented in `extensibility.md` as the one pre-encode write not covered by `encode_session` (see item 3 below).
- **Doc sync — DONE (2026-07-26, commit `f1a17aa`):** `overview.md:306-312` and `extensibility.md:489` previously over-claimed "leaves Redis untouched". Tightened in EN+ZH to state the rollback precisely. **Important: the logic.01 fix changed the actual behaviour, so the old wording was wrong in *both* directions** — verified against code (`namespace.__setitem__` try/except at namespace.py:369-384 + `_rollback_encode_memo` at types.py:181-207), not inferred:
  - **First-time store failure** (`ns[name] = value` with a bad value): the binding's `obj:`/`raw:`/`ref:` keys ARE cleaned up by the `namespace.__setitem__` wrapper → genuinely no orphan meta. (The old `lifecycle.md:279` caveat "the parent container's own metadata key … is not rolled back" was **obsolete for this path** after logic.01.)
  - **Re-encode inside an existing container** (`dict.update`/`list.extend` failure): only anons + ref-increments roll back; the container's own pre-existing `obj:`/`raw:` meta is intentionally kept (it holds valid data).
  - `extensibility.md:489` also now notes the pandas RangeIndex→levels promote helper as the one pre-encode write not covered by `encode_session` (the leave-as-is decision above).
  - Updated sites: `overview.md:312`, `extensibility.md:489`, `lifecycle.md:279` (caveat), `lifecycle.md:388` (troubleshooting table row) — all in EN+ZH.
- **Committed** in `9d5c42d` (the logic.01 fix) and `f1a17aa` (this doc sync).

### Finding logic.02 — Stale handle mutates Redis before raising `ObjectGoneError`
- **Report claim:** Mutators write the `raw:` key first, call `_touch()` afterward; `_touch()` is where missing canonical meta is detected, so a stale handle recreates/changes raw storage before reporting the object is gone.
- **Original severity:** Major
- **Verdict:** **Confirmed**
- **Evidence:**
  - `src/gpdatacached/container.py:46-63` `_touch()` loads canonical meta and raises `ObjectGoneError` only at lines 61-62 — i.e. *after* the caller's Redis write.
  - `src/gpdatacached/builtins/list_object.py:121-126` `append` does `rpush` (125) then `_touch()` (126). Same pattern in `insert`, `__setitem__`, dict/set mutators.
  - Demo `demo_logic_02`: stored `[1]`, kept the `ListObject`, `del ns["lst"]`, then `stale.append(2)` → `ObjectGoneError`, but `raw:lst` still `EXISTS` with value `['int:2']` — an orphaned raw key unreachable via namespace APIs.
- **Options considered:**
  1. **Add a canonical-meta existence check immediately before every mutating write; keep `_touch()` as the post-write metadata update.** (Cheapest; one extra `HEXISTS`/meta load per mutation. Risk: TOCTOU between check and write, but acceptable for the "stale handle" class.)
  2. Move mutation + liveness guard into per-op Lua scripts (atomic check+write). (Strongest; more Lua surface to maintain.)
  3. Optimistic `WATCH` on the canonical meta key, retry/raise if it disappears. (Correct but adds retry complexity and a pessimistic path.)
- **Recommendation:** Option 1 — minimal, consistent across all container subclasses; gate `_touch`'s own `ObjectGoneError` so the same check serves both write-then-touch and touch paths.
- **Human decision:** **Fix (option B — `_check_alive()` helper + `_touch(meta=None)` refactor), 2026-07-24.** Maintainer chose the more thorough variant: add a reusable liveness guard AND refactor `_touch` to accept a pre-loaded meta, eliminating the duplicate `ObjectMeta.load` that mutators + `_touch` were both doing per mutation (net performance win on the ~17 mutators that already loaded meta; ~14 set/clear/del mutators add one `HGETALL`, same cost `_touch` already paid, just moved earlier).
- **Investigation (2026-07-24):** confirmed **all 31 element-mutators** across `list_object.py`, `dict_object.py`, `set_object.py`, `pandas_support.py`, `pydantic_support.py` are write-then-touch. Existing stale-handle tests (only 3 of 31) asserted only the raise, NOT `raw:` integrity — so they passed despite the bug. `_touch`'s `meta is None → raise` check was introduced at `305feb9e` (silent return) and upgraded to a raise at `064303d6`; it was always at the end (post-write). For pandas, the `_promote_*_range_to_labels` helpers write `self.raw_key` inside `extend` before the main pipeline — so the guard must be at the method top, not just on the levels path.
- **Implementation status:** **done (2026-07-24).** Changes:
  - `src/gpdatacached/container.py`: new `_check_alive(r=None)` helper (loads canonical meta, raises `ObjectGoneError` if None, returns the meta for reuse); `_touch(self, meta=None)` now skips its internal `ObjectMeta.load` when a meta is passed in (the liveness re-check is retained as defense-in-depth for the self-load path).
  - All 31 mutators refactored to call `_check_alive()` at the top (before the first Redis write) and pass the returned `meta` to `_touch(meta=...)`. Two patterns: mutators that already loaded meta (`__setitem__`/`insert`/`append`/`extend`/`update` in list/dict, pandas extend/`__setitem__`, pydantic `__setitem__`) reuse it (zero added Redis calls, one fewer load than before); mutators that didn't (set's 9 ops, `clear`/`__delitem__`/`reverse`/`sort`, pandas `__delitem__`) add one `HGETALL`. Pandas `extend` guards placed at method top (before any promote helper).
  - Cleaned up now-unused `ObjectMeta` imports in `list_object.py`, `dict_object.py`, `pandas_support.py`, and the now-unused `policy_from_defaults` local imports (the `if meta else policy_from_defaults(...)` fallback is gone since `_check_alive` always returns a real meta).
  - Tests: strengthened the existing 3 stale-handle tests (now also assert `raw:` contents are unchanged via a `_raw_snapshot` helper, not just that the raise occurs) and added 12 new stale-handle tests covering list `extend`/`__setitem__`/`__delitem__`, dict `update`/`__delitem__`, set `discard`/`__ior__`, Series `extend`, DataFrame `extend`/`__setitem__`/`__delitem__`, and pydantic `__setitem__` — each asserting the stale write leaves `raw:` byte-identical.
- **Verification (2026-07-24):**
  - `ruff check src tests` → All checks passed.
  - Full suite `pytest -q -p no:cacheprovider` → **1417 passed** (+15 net: 12 original stale-handle tests + 3 audit-follow-up tests for pop/remove).
  - `demo_logic_02_detail.py` post-fix: `raw: key EXISTS? False` after stale `append(2)` (was `True` with `['int:2']` before the fix). Demo narrative updated to describe post-fix behaviour.
- **Audit follow-up (2026-07-24, after maintainer review):** the initial fix missed `ListObject.pop` and `ListObject.remove`. Both delegate their writes to `_delete_index`, which had NO `_check_alive` (it relied on `_touch()`'s post-write check = the bug). An exhaustive audit of all 5 files confirmed ONLY these were missing: `ListObject._delete_index` (the shared helper) + its two callers `pop`/`remove`. Fix: pushed `_check_alive()` down into `_delete_index` itself (one guard covers `__delitem__`/`pop`/`remove`), and removed the now-redundant standalone `_check_alive` that `__delitem__` had. Everything else (`DictObject.pop`/`setdefault`/`popitem` via `__delitem__`/`__setitem__`; all `SetObject` incl. `pop` which is COVERED direct; pandas `_promote_*` via `extend`; pydantic ABC mutators which raise TypeError before writing) was already COVERED. Added 3 regression tests (`test_list_pop_*`, `test_list_remove_*`).
- **Note:** `_touch(meta=None)` is a signature change to an underscore-prefixed internal API. It is backward-compatible (meta is optional), and no external code should depend on `_touch`.
- **Committed** in `52ada4a` (main fix) + `18049a6` (audit follow-up for `pop`/`remove` via `_delete_index`).

### Finding logic.03 — Non-unit `RangeIndex` interpreted as `step==1`
- **Report claim:** `DataFrameObject.__len__` computes `stop - start` instead of `range(start, stop, step)` cardinality; `SeriesObject.extend` bumps `range_stop` by appended-row count instead of `count * step`. Mutation tests cover only unit steps.
- **Original severity:** Critical
- **Verdict:** **Confirmed**
- **Evidence:**
  - `src/gpdatacached/extensions/pandas_support.py:993-998` `DataFrameObject.__len__` returns `~range_stop - ~range_start` (ignores `~range_step`).
  - `pandas_support.py:1568` `SeriesObject.extend` sets `new_stop = range_stop + len(new_data)` (should be `+ len(new_data) * step`).
  - Demo `demo_logic_03`:
    - **[A] DataFrame** `RangeIndex(5,20,5)` (3 rows: 5,10,15): `len(cached_df)` reported **15** (true 3); a correct 3-value column assignment was rejected with `column length 3 does not match row count 15`.
    - **[B] Series** `RangeIndex(5,20,5)` + 5 appended values (index 20..40 step 5): `extend()` "succeeded", then `.value` raised `ValueError: Length of values (8) does not match length of index (4)`.
- **Options considered:**
  1. **Centralize RangeIndex cardinality/extension using Python `range` semantics; use the helper in both Series and DataFrame paths.** (Correct, minimal surface; `len(range(start,stop,step))` for `__len__`; `new_stop = start + (cardinality_after * step)` for extend.)
  2. Promote every non-unit RangeIndex to labelled-index storage before any append/row-count-dependent mutation. (Uniform storage; loses the compact range encoding for a supported case.)
  3. Reject non-unit RangeIndex at creation and narrow the documented contract. (Breaking capability reduction.)
- **Recommendation:** Option 1 — `range_step` is already stored (`~range_step`/`range_step` fields exist), so the fix is using it, not adding storage. Add non-unit and negative-step cases to the round-trip AND mutation tests.
- **Investigation (2026-07-24):** exhaustive audit of all `range_*` usages found **two live bugs + one latent + a DF/Series gate asymmetry the report didn't flag**:
  - `DataFrameObject.__len__` (line 998): `stop - start`, ignores step — **live bug**.
  - `SeriesObject.extend` (line 1581): `range_stop + len(new_data)`, missing `* step` — **live bug**.
  - `DataFrameObject.extend` (line 924): same missing `* step`, but gated behind `range_step == 1 and appended_is_range` (line 912) so a non-unit step falls through to promote-to-levels → **latent / dead code** for non-unit step.
  - **Asymmetry:** DF extend's fast path checks the *stored* step (promotes non-unit, safe); Series extend's fast path checks only `_is_range_indexed_series(other)` (does NOT consult stored step) → non-unit Series hits the buggy line. This is why the Series bug is reachable and the DF one is not.
  - Already correct (no fix needed): round-trip reconstruction (`_build_index`, `SeriesCodec.read_raw`), `.iloc`/`_positional_index_slice`, promote helpers, create_raw storage — all pass `range_step` to `pd.RangeIndex(...)`. This is why `.value` materializes the correct rows while `__len__`/`extend` were wrong.
  - Test gap: only one non-unit-step test existed (`test_custom_step_range_index_round_trip`, Series round-trip only, via codec); `__len__`/`extend`/`.iloc` had zero non-unit coverage.
- **Human decision:** **Fix direction A (keep non-unit RangeIndex compact), 2026-07-24** — with the condition "implement A, run rigorous tests, and report back if A causes any other problem; maintainer decides whether to roll back to direction B (promote non-unit, align Series with DF)". Direction A chosen because `range_step` is already stored and round-trip already supports arbitrary step, so A is the consistent choice; promote (B) would be a storage-layout change for existing non-unit data.
- **Implementation status:** **done (2026-07-24).** Changes (direction A):
  - `DataFrameObject.__len__`: now `len(range(start, stop, step))` (handles non-unit, negative, and empty ranges uniformly via Python's range()).
  - `SeriesObject.extend` range fast path: reads stored `range_step`, computes `new_stop = range_stop + len(new_data) * range_step`.
  - `DataFrameObject.extend` range fast path: gate widened from `range_step == 1 and appended_is_range` to just `appended_is_range` (non-unit ranges now stay compact instead of promoting); `new_stop = range_stop + n * range_step`.
  - Tests: +14 (7 Series, 7 DataFrame) covering `__len__`/`extend`/`.iloc`/column-set for non-unit AND negative step, plus repeated extend (consistency across successive appends) and an explicit assertion that DF extend on non-unit step stays `index_kind == "range"` (the direction-A invariant).
- **Verification (2026-07-24):**
  - `ruff check src tests` → All checks passed.
  - Full suite `pytest -q -p no:cacheprovider` → **1431 passed** (+14 net).
  - End-to-end direction-A consistency check (separate script): Series + DF, incl. negative step + repeated extend + column-set — all consistent, **no other problems found**. Per the maintainer's condition, direction A stands.
  - `demo_logic_03_detail.py` post-fix: `len(cached_df)=3` (was 15); Series extend `range_stop=30` (was 22) and `.value` materializes 5 rows; DF extend on non-unit step stays `range` (was promoting to `levels`).
- **Note:** direction A is a behaviour change for DF `extend` on a non-unit RangeIndex (previously promoted to levels; now stays compact). This only affects non-unit-step DataFrames being extended — a previously-broken/mis-handled case — and now matches Series + round-trip semantics. Flagged for the maintainer.
- **Committed** in `e2bdfc1`.

### Finding inconsistency.01 — `expire_in(0)` persists an invalid ttl-mode policy
- **Report claim:** User-facing validation requires `ttl > 0` for `ttl`/`sliding`, but `expire_in` constructs a permissive `TTLPolicy` that accepts zero; applying it writes `cache_mode='ttl', declared_ttl=0` and persists the keys.
- **Original severity:** Major
- **Verdict:** **Confirmed**
- **Evidence:**
  - `src/gpdatacached/lifecycle.py:34-35` `TTLPolicy.__post_init__` calls only `_validate_basic` (allows `ttl=0`); `validate_cache_mode_and_ttl` (line 22-26) is the strict path that rejects it, but `expire_in` doesn't use it.
  - `src/gpdatacached/object.py:216-224` `expire_in` builds `TTLPolicy("ttl", seconds)` directly with no strict validation. `expire_seconds` (lifecycle.py:37-41) returns `None` for `(ttl, 0)` → `apply_ttl` calls `PERSIST`.
  - Notably `expire_at` (object.py:233-243) *does* treat `seconds==0` as "delete immediately" — so the two sibling APIs disagree on what zero means.
  - Demo `demo_inconsistency_01`: on a `ttl=60` object, `expire_in(0)` succeeded; meta became `cache_mode=ttl, declared_ttl=0` while Redis TTL = **-1** (persistent). An object the lifecycle docs say can never be written now exists in Redis.
- **Options considered:**
  1. **Validate `seconds` with `validate_cache_mode_and_ttl("ttl", seconds)` before constructing/applying the policy; add zero/negative API tests.** (Tightest; matches the documented invariant. Note: this makes `expire_in(0)` raise, which is a behaviour change but aligns with `update_cache_mode_and_ttl`.)
  2. Define `expire_in(0)` as explicit immediate-delete (delegate to namespace `__delitem__`, mirroring `expire_at`'s zero handling) and document/test it. (Consistent with `expire_at`; more surprising for a "set TTL" verb.)
  3. Make `TTLPolicy` itself strict and introduce a separate internal representation only where zero is genuinely required. (Broad; touches `policy_from_meta` readers.)
- **Recommendation:** Option 1 — the documented contract is "`ttl`/`sliding` require `ttl > 0`"; enforcing it at the one bypass point closes the hole with one line. If the maintainer prefers "0 = immediate delete", that's option 2 and must be explicitly chosen (it's a real semantic decision, not an obvious fix).
- **Human decision:** **Fix (maintainer-chosen variant, 2026-07-24):** Add a new `GPDCDataObject.expire_now()` method (explicit, mode-independent "delete now" — equivalent to `del ns[canonical_name]`), AND make `expire_in` strictly reject `ttl <= 0`. `expire_at` keeps its current "past timestamp = delete" semantics (no breaking change). Rationale: with a dedicated, unambiguous `expire_now()`, `expire_in(0)` has no legitimate use, so rejecting it costs no functionality; and `expire_at`'s time-based delete is left intact to avoid breakage. Maintainer also required EN/ZH doc sync.
- **Implementation status:** **done (2026-07-24).** Changes:
  - `src/gpdatacached/object.py`:
    - `expire_in` now calls `validate_cache_mode_and_ttl("ttl", seconds)` → `expire_in(0)` and `expire_in(-n)` raise `ValueError` (closes the silent-persist hole).
    - New `expire_now()` public method + shared `_expire_now()` helper: full teardown via `del ns[canonical_name]` (obj:/raw:/ref: + cascade anon); works in any cache_mode; requires ownership; raises `CanonicalObjectReferencedError` if still referenced.
    - `expire_at`'s `seconds==0` branch refactored to call `self._expire_now()` (behaviour unchanged — same `del` delegation, just deduplicated).
  - `tests/test_namespace.py`: 4 new tests — `expire_now` full-teardown on a container, works in `permanent` mode (no `ttl` precondition), cascades anon subtree, raises `CanonicalObjectReferencedError` when referenced, raises `LifecyclePermissionError` without ownership. Added `LifecyclePermissionError` import.
  - `tests/test_objects.py`: 3 new tests — `expire_in(0)` raises ValueError, `expire_in(-10)` raises ValueError, `expire_now()` raises `ObjectGoneError` when the object is gone.
  - Docs EN+ZH synced: `api.md` + `lifecycle.md` method tables now list `expire_now()`; `expire_in` rows note `seconds > 0` and point to `expire_now()`; the "user-facing mutators" subsection heading widened from "four" to a count-neutral title and the delegation sentence distinguishes the 4 policy mutators from `expire_now()` (delete-via-`__delitem__`, not `set_lifecycle`).
- **Verification (2026-07-24):**
  - `ruff check src tests` → All checks passed.
  - `pytest -q -p no:cacheprovider` (full suite) → **1398 passed** (was 1390 before this finding's new tests; +8 net).
  - Targeted: `pytest tests/test_namespace.py tests/test_objects.py tests/test_docs.py tests/test_lifecycle.py -q` → 318 passed.
- **Committed** in `8162a31`.

### Finding inconsistency.02 — `SeriesObject.name` hides the base binding identity
- **Report claim:** Base `GPDCDataObject.name` is the namespace binding name, but `SeriesObject` overrides it with the pandas payload name; `.name`, `.uri`, `.canonical_name` then disagree.
- **Original severity:** Major
- **Verdict:** **Confirmed**
- **Evidence:**
  - `src/gpdatacached/object.py:42-44` base `name` returns `self._name` (binding). `src/gpdatacached/extensions/pandas_support.py:1458-1460` `SeriesObject.name` overrides it to read the pandas `name` field from Redis.
  - Demo `demo_inconsistency_02`: Series stored under binding `binding` with pandas name `payload_name` → `obj.name == "payload_name"` while `obj.canonical_name == "binding"` and `obj.uri == "gpdc://inc02/ns/binding"`. Generic code keyed on `.name` gets the wrong identity.
- **Options considered:**
  1. **Restore inherited `.name` (binding); expose the pandas label as `.series_name`, with a documented deprecation path for current callers.** (Restores the base identity contract; one-time migration pain.)
  2. Keep pandas `.name`, add a universal `.binding_name` property, and revise the base API contract to use it everywhere. (Largest blast radius; every generic caller must migrate.)
  3. Return pandas metadata only via `.value.name`, removing the live-object convenience property. (Cleanest contract; loses the convenience accessor.)
- **Recommendation:** Option 1 — the base identity contract (`object.py` docstring/`api.md`) is the load-bearing one; the pandas override is the anomaly. A deprecation shim (warn on `.series_name`-via-`.name` for one release) softens the break.
- **Git history (investigated 2026-07-24 at maintainer request):** The override was introduced in commit **`e0860737`** (2026-06-18, "feat(pandas): implement SeriesObject core (value/len/metadata/deepcopy)") — the *first* `SeriesObject` implementation. It added `dtype`/`name`/`index_name` together as a group of pandas-metadata accessors, deliberately mirroring `pd.Series` attribute names. `dtype` and `index_names` don't conflict (no base-class names), but `name` does (base class already defines `.name` = binding). `DataFrameObject` (added later) chose NOT to override `.name`, honoring the base contract — so Series is the sole exception. The pinning test `test_len_dtype_name_index_name` (deliberately distinct binding `"s"` vs pandas `"vals"`) encoded this decision. No design doc records the rationale. Conclusion: the override was an intentional pandas-API-style choice that conflicts with the base identity contract; history confirms it's a design tradeoff to correct, not a typo.
- **Human decision:** **Fix (option 1, no deprecation warning), 2026-07-24.** Restore `.name` = binding; expose the pandas label as `.series_name`. Rationale: `SeriesObject` is not in the public `__init__.py` namespace (small blast radius), `DataFrameObject` already honors the base contract (fix restores Series/DataFrame symmetry), and the pandas name stays reachable via `.series_name`/`.value.name`. No deprecation warning because it can't reliably distinguish caller intent (would just add noise).
- **Implementation status:** **done (2026-07-24).** Changes:
  - `src/gpdatacached/extensions/pandas_support.py`: removed the `SeriesObject.name` override (now inherits `GPDCDataObject.name` = binding); added `series_name` property returning the stored pandas `Series.name`, with a docstring explaining the identity-vs-label distinction.
  - `tests/test_pandas_series_support.py`: updated `test_len_dtype_name_index_names` to assert `obj.name == "s"` (binding) and `obj.series_name == "vals"` (pandas label); added `test_name_is_binding_identity` regression guard asserting `.name` agrees with `.canonical_name`/`.uri` (and differs from `.series_name`).
  - Docs EN+ZH (`docs/en/pandas-support.md`, `docs/zh/pandas-support.md`): SeriesObject properties table now lists `name` (str, binding, inherited) and `series_name` (Any, pandas label) as separate rows; added an "Identity vs pandas label" note above the table.
- **Verification (2026-07-24):**
  - `ruff check src tests` → All checks passed.
  - `pytest tests/test_pandas_*.py -q` → 271 passed.
  - Full suite `pytest -q -p no:cacheprovider` → **1399 passed** (+1 net: the new regression test).
- **Committed** in `9221ce4`.

### Finding inconsistency.03 — Closed-cache `__contains__` contract disagrees
- **Report claim:** "Project memory states that every `GPDataCache` method raises after `close()`, but `__contains__` deliberately performs its config snapshot without `_check_open()`. The design spec also shows the no-check implementation."
- **Original severity:** Minor
- **Verdict:** **Confirmed (but the citation is mis-stated)**
- **Evidence:**
  - `src/gpdatacached/data_cache.py:42-50` `__contains__` has no `_check_open()`. `get_domain` (59), `get_object_by_uri` (78), `has_uri` (116) all call `self._check_open()` → raise `RuntimeError("GPDataCache is closed")` after `close()` (214).
  - `docs/superpowers/specs/2026-07-23-gpdatacache-design.md:295-299` shows the no-check `__contains__` (matches code); the spec also never claims "every method raises after close".
  - **Citation problem:** the report attributes the "every method raises" invariant to "AGENTS.md §3.9", but **`AGENTS.md` did not exist at the reviewed commit `0edade9`** (`git show 0edade9:AGENTS.md` → "exists on disk, but not in `0edade9`"). The post-review `AGENTS.md` (current HEAD) does not mention `GPDataCache`/`data_cache` at all. So the "AGENTS.md says X" framing in the report is **not substantiated**.
  - **Real defect stands regardless:** `__contains__` silently returns a value after `close()` while its three siblings raise. There is no test pinning either behaviour. That is a genuine contract split the maintainer should resolve.
  - Demo `demo_inconsistency_03`: after `cache.close()`, `"core_domain" in cache` returned `True` (no error), while `cache.get_domain("core_domain")` and `cache.has_uri(...)` both raised `RuntimeError: GPDataCache is closed`.
- **Options considered:**
  1. If "all access raises after close" is the intended invariant: call `_check_open()` first in `__contains__`, add a closed-membership test, note the exception in the docs.
  2. If pure membership is intentionally usable after close: narrow the public docs to state that exception explicitly (so maintainers don't "fix" it).
  3. **Define a small explicit post-close matrix (`close`, `__contains__`, resource-touching methods) and test every row.** (Resolves the ambiguity definitively regardless of which way it goes.)
- **Recommendation:** Option 3 — the underlying problem is *unpinned* behaviour, not a specific choice; a tested matrix makes whichever choice the maintainer picks durable. My *leaning* was option 1 (consistency with siblings), but that is the maintainer's call.
- **Human decision:** **Fix (option 2 + option 3, NO code change), 2026-07-25.** Maintainer clarified the design intent: `__contains__`'s semantics is "does a config for this domain exist?", which is purely a config-snapshot question — it has nothing to do with whether the instance has been created or whether the cache is open. So `__contains__` correctly returns a value after `close()`; it should NOT be changed. Action: document this intent explicitly + add contract tests pinning it. Maintainer also asked to verify the "lazy instance creation on first access" design was implemented — verified (see below).
- **Lazy-create verification (2026-07-25):** confirmed `get_domain` (data_cache.py:59-62) implements lazy creation — `if domain_name not in self._domains: self._domains[domain_name] = self._folder.get_object(domain_name)`. Verified at runtime: first `get_domain(name)` constructs and caches; second returns the same instance; `__contains__` is True both before (no instance) and after (instance cached). Design is correctly implemented.
- **Implementation status:** **done (2026-07-25).** Changes (docs + tests only, NO code change):
  - `docs/en/api.md` + `docs/zh/api.md` (`GPDataCache` dunder-protocols block): `name in cache` row rewritten to spell out that membership is a pure config-snapshot check independent of instance creation; added a `> **close() behaviour — the membership exception.**` admonition stating that the resource-touching methods raise `RuntimeError` after `close()` while `__contains__` is the deliberate exception (config snapshot, no resources); `get_domain`/`get_object_by_uri`/`has_uri` rows now note "raises RuntimeError after close()".
  - `tests/test_data_cache.py`: new `TestGPDataCacheContainsContract` class, +6 tests pinning: (a) `__contains__` reflects config not instance (True before any instance); (b) unchanged after instance created; (c) False for missing config; (d) `get_domain` lazy-creates + caches same instance; (e) `__contains__` works after close() (the documented exception); (f) `get_domain`/`get_object_by_uri`/`has_uri` all raise RuntimeError after close().
- **Verification (2026-07-25):**
  - `ruff check src tests` → All checks passed.
  - `pytest tests/test_data_cache.py::TestGPDataCacheContainsContract -v` → 6 passed.
  - No code changed, so no regression risk; full suite left to maintainer's already-green run.
- **Committed** in `783a413`.

### Finding clarity.01 — Every documentation link in both root READMEs points to a non-existent path
- **Report claim:** English README links to `docs/*.md` but docs live under `docs/en/`; Chinese README links to `docs/zh-CN/*.md` but the dir is `docs/zh/`.
- **Original severity:** Major
- **Verdict:** **Confirmed**
- **Evidence:**
  - `README.md:96-110` links `./docs/index.md`, `./docs/api.md`, …; `README.zh-CN.md:96-101+` links `./docs/zh-CN/*.md`.
  - Static check: all 9 advertised `docs/*.md` and all `docs/zh-CN/*.md` paths **MISSING**; the files exist under `docs/en/` and `docs/zh/` respectively (verified `index.md`, `api.md`, `lifecycle.md`, `extensibility.md`, `performance-guide.md`, `locking.md`, `pydantic-support.md`, `pandas-support.md`, `overview.md` in both).
  - Every doc link on both onboarding pages is a 404.
- **Options considered:**
  1. **Repoint English links to `docs/en/…` and Chinese links to `docs/zh/…`; add a CI Markdown link check.** (Direct; prevents regression.)
  2. Rename/restructure the doc dirs to match the advertised paths and update MkDocs nav. (Larger; changes doc tree.)
  3. Add stub index/redirect pages at the advertised locations. (Duplicate nav files; band-aid.)
- **Recommendation:** Option 1 — one-line-per-link fix, and a link-check CI step is cheap insurance. (No runtime demo needed; this is a static-path check.)
- **Human decision:** **Fix (option 1, NO CI link check), 2026-07-25.** Maintainer chose to repoint the links only and explicitly declined the CI link check. Additional maintainer constraint: do NOT add any links into `docs/superpowers/` or `reviews/` — those are temporary dev-doc dirs that will be deleted after this review.
- **Implementation status:** **done (2026-07-25).** Changes:
  - `README.md:96-110`: 9 doc links repointed `./docs/*.md` → `./docs/en/*.md` (index, overview, api, lifecycle, extensibility, performance-guide, locking, pydantic-support, pandas-support).
  - `README.zh-CN.md:96-110`: 9 doc links repointed `./docs/zh-CN/*.md` → `./docs/zh/*.md` (same 9 files).
  - No other links touched (badges, language switcher, `#extensions`/`#扩展` anchors, external URLs, `LICENSE` were already correct). No links added to `docs/superpowers/` or `reviews/`.
- **Verification (2026-07-25):**
  - Residual `docs/zh-CN` references in `README.zh-CN.md` = 0.
  - All 9 new targets verified to exist under both `docs/en/` and `docs/zh/`.
  - Pure doc change — no code/tests affected; ruff/pytest not applicable.

### Finding gap.01 — `take_ownership()` is a no-op for the named-reference case it claims to support
- **Report claim:** For a named alias, `canonical_name` is the original's name; the method returns immediately when `meta.owner == self.canonical_name` (the normal state), and its write branch assigns `owner=self.canonical_name` (the canonical, not the alias). Docs promise the alias becomes an independent lifecycle owner.
- **Original severity:** Critical
- **Verdict:** **MISCHARACTERIZED → reclassified as a DOCUMENTATION defect.** The report's code-level claim ("`take_ownership` is broken") is a **false positive** — on the intended path (named reference → anonymous nested object) the code works exactly as designed (demo `demo_gap_01_intent.py`). **BUT** the report was *misled by the docs*, which actively describe the wrong semantics. So `gap.01` remains a real issue: the **fix target is the documentation**, not the code.
- **Maintainer's design intent (decisive context, 2026-07-24):**
  - `take_ownership` operates on **the referenced data object itself** (making *it* its own owner), NOT on the referencer. The actor of "becoming owner" is the referenced object.
  - Anonymous objects are created as part of a parent container; their lifecycle is attached to (owned by) the parent. When a named reference points at such an anonymous object, `take_ownership` promotes **the anonymous object** to self-owned (`owner` = its own `~anon:…` canonical name), giving it an independent lifecycle.
  - Named objects are *already* self-owned at creation. Calling `take_ownership` on a reference to a named object is **redundant but not erroneous** — it is a correct silent no-op. In a multi-process shared environment a caller cannot/need-not know whether a referenced object was created anonymous or named; the call's intent ("this referenced object should own its lifecycle") is satisfied either way.
- **Why the report (and my first demo) were misled — DOCUMENTATION DEFECTS (the real issue):**
  Documentation *actively* describes semantics opposite to the design intent. Verified at:
  - **`docs/en/lifecycle.md:219`** & **`docs/zh/lifecycle.md:219`**: *"Only named references can do this — anonymous objects (`~anon:…`) cannot"* / *"只有具名引用可以这么做 —— 匿名对象（`~anon:…`）不能"*. **This is exactly backwards** — anonymous objects are the *primary* target of `take_ownership` (the only case where it actually promotes owner=parent → owner=self). This line is the direct root cause of the reviewer's misjudgement.
  - **`docs/en/lifecycle.md:211`** & **ZH 211**: *"A named reference (alias) can promote **itself** to own its data"* / *"具名引用（别名）可以把自己提升为数据的所有者"*. Wrong actor: the *referenced object* becomes owner, not the *reference/alias itself*.
  - **`docs/en/lifecycle.md:214-216`** & **ZH 214-216**: example uses `ns["alias"] = ns["original"]` (**two named objects** — a no-op scenario) and promises `owner = "alias"` (the referencer's name). That `owner` value is **never produced** (named objects keep their own owner). Any user copying this example gets confused.
  - **`docs/en/lifecycle.md:304`** & **ZH 304**: *"use `take_ownership()` … to promote **a reference** to a self-owned object"* — same wrong actor.
  - **`docs/en/lifecycle.md:351`** (summary) & **ZH 351**: *"Promote a named reference to own its data"* — wrong actor.
  - **`docs/en/api.md:449`** & **`docs/zh/api.md:409`**: *"Promote a reference object to be the canonical owner of its own data. Only valid for named references."* — wrong actor; "Only valid for named references" again implies anonymous objects can't use it (backwards).
  - **Code error message** `object.py:180,182`: `"take_ownership() can only be called on named reference objects"` — technically correct (the *caller* must be a named reference) but ambiguous phrasing that reinforces the doc confusion (reads as "the referenced object must be named").
  - **`docs/en/lifecycle.md:202-203`** ownership table is itself accurate (self-owned vs owned-by-parent) — the contradiction is *only* in the take_ownership subsections.
- **Evidence the code is correct on the intended path (`demo_gap_01_intent.py`):**
  - `ns['parent'] = {'inner':[1,2,3]}` → `inner` is `~anon:…`, `owner='parent'`.
  - `ns['named'] = inner` → named reference; `ref:` on the anon gains `named→1`.
  - `named_obj.take_ownership()` → anon `meta.owner` becomes `'~anon:…'` (self-owned). **`object.py:193` `meta.with_updates(owner=self.canonical_name)` is correct** because for this reference `self.canonical_name == ~anon:…`.
  - `named_obj.update_cache_mode_and_ttl('ttl', 60)` → anon gets `cache_mode=ttl, ttl=60`; **parent stays `permanent`, TTL −1, untouched** — independent lifecycle confirmed.
- **Evidence the code correctly no-ops on the report's scenario (`demo_gap_01_detail.py`):**
  - `ns['alias'] = ns['original']` (both named). `alias.canonical_name='original'`; `original` self-owned → `meta.owner=='original'` → `object.py:190-191` early-return (correct no-op).
- **Retraction:** my original "Confirmed" verdict and my claim that `tests/test_objects.py` "encodes the same bug" were both wrong. That test encodes the *correct* named-object no-op behaviour. Retracted.
- **Options considered (DOCUMENTATION fix):**
  1. **Rewrite the take_ownership subsections in both languages to state the real semantics:** the *referenced object* becomes self-owned; anonymous objects are the primary use case; named-object references are a correct no-op. Replace the two-named-objects example with a named-reference-to-anonymous-object example. Touch `lifecycle.md` §7/§9 + summary table, `api.md` method row, in EN and ZH. Optionally reword the code error message to be unambiguous. (Directly fixes the root cause of the misjudgement; ~6 doc locations × 2 languages.)
  2. Same rewrite as option 1, but also add a short "Ownership model" note to `overview.md` and a regression **doc-test** that asserts the documented example actually produces the documented `owner` value (so the docs can't silently drift back). (Stronger guardrail; more work.)
  3. Minimal: fix only the single most-wrong line (`lifecycle.md:219` "anonymous objects cannot") in both languages, leave the rest. (Smallest change; but leaves the wrong-actor wording and bad example in place — partial fix.)
  4. Defer — leave docs as-is.
- **Recommendation:** Option 1. The wrong-actor wording and the two-named-objects example are the load-bearing mistakes; fixing only line 219 (option 3) leaves the example still demonstrating a no-op as if it were a promotion. Option 2's doc-test is a nice-to-have if the maintainer wants a permanent guardrail. **This is a documentation-only change — no production code semantics change.**
- **Human decision:** **Fix (option 2 — docs rewrite + overview note + doc regression test), 2026-07-24.** Reclassified from code defect to documentation defect after maintainer clarified design intent.
- **Implementation status:** **done (2026-07-24).** Changes:
  - `docs/en/lifecycle.md` §7 take_ownership subsection rewritten (correct actor = referenced object; anon = primary use case; named-object ref = correct no-op with multi-process rationale); corrected example (named ref → anon nested object); §9 ref-binding paragraph + summary-table row fixed.
  - `docs/zh/lifecycle.md` — corresponding rewrite (terminology: 自持/owner/具名引用/匿名对象/canonical owner).
  - `docs/en/api.md` + `docs/zh/api.md` — `take_ownership` method-table row corrected (referenced object, primary use case, no-op caveat, cross-ref to lifecycle §7).
  - `docs/en/overview.md` + `docs/zh/overview.md` — new "### Promoting an anonymous object: `take_ownership()`" subsection after the Anonymous-objects section.
  - `src/gpdatacached/object.py:180,182` — error message reworded to `"take_ownership() must be called via a named reference handle"` (logic unchanged; message string only; preserves the `"named reference"` substring that `tests/test_objects.py:183` matches on).
  - `tests/test_docs.py` (NEW) — 4 real-Redis regression tests executing the doc example verbatim and asserting the documented owner values (anon promoted to self-owned; promoted anon has independent lifecycle; named-object ref is a correct no-op; non-reference call raises TypeError).
- **Verification (2026-07-24):**
  - `ruff check src tests` → All checks passed.
  - `pytest tests/test_docs.py tests/test_objects.py -q` → 48 passed.
  - `pytest tests/test_lifecycle.py tests/test_nested_containers.py -q` → 104 passed (the `testdomain_nest` warnings are the unrelated, separately-tracked structure.01 fixture leak).
  - EN/ZH diff reviewed for 1:1 semantic correspondence — confirmed symmetric.
- **Out-of-scope note (RESOLVED):** this flagged the stale ZH `#7-ownership` cross-references. **Fixed in OOS-1** (commit `5022f36` — ZH §7 heading renamed to pure English + 4 refs repointed to `#7-the-ownership-model`) and **fully resolved repo-wide in OOS-2** (commit `f1a17aa` — all `[§N](#N)` and fragile links migrated to durable explicit anchors).
- **Committed** in `4a34b49`.

### Finding perf.01 — `gc_interval_seconds=0` creates an unbounded background GC loop
- **Report claim:** Config accepts zero/negative intervals; `Event.wait(0)` returns immediately so an enabled domain scans with no delay; negative values are "also invalid for `Event.wait` and can terminate the daemon path unexpectedly."
- **Original severity:** Major
- **Verdict:** **Partial** — the zero-interval hot loop is **Confirmed**; the negative-interval claim is **incorrect**.
- **Evidence:**
  - `src/gpdatacached/config.py:53-54` `gc_interval_seconds: int = 300` has **no validator** — 0 and negatives are accepted.
  - `src/gpdatacached/domain.py:281-282` `_gc_loop`: `while not self._gc_stop_event.wait(timeout=interval)`.
  - Demo `demo_perf_01`: with `gc_interval_seconds=0`, instrumenting `_gc_stop_event.wait` counted **167 calls in ~50 ms** — a tight CPU-saturating spin (no sleep), each iteration running `for ns_name in self:` (a `SCAN` over namespaces + per-namespace GC).
  - **Correction to the report:** `threading.Event.wait` does **not** raise on negative timeout. On CPython 3.13.11 (and across supported versions), `Event.wait(-5)` simply returns `False` immediately — same as 0. Verified: `e.wait(0)`, `e.wait(-1)`, `e.wait(-5)` all return `False`, none raise. So negative intervals **also produce the hot loop**, not "terminate the daemon path". The report's severity (a hot loop) is correct for both signs; the stated mechanism for negatives is wrong.
- **Options considered:**
  1. **Constrain `gc_interval_seconds >= 1` in `GPDCDomainConfig` (a `field_validator`), add boundary tests + a documented minimum.** (Closes both zero and negative; aligns with the existing `>= 1` lock-TTL validators.)
  2. Treat 0 as an explicit synonym for "GC disabled", reject negatives, document startup behaviour. (More semantics to document; risk of silent misunderstanding.)
  3. Clamp non-positive values to a safe minimum with a warning. (Silent behaviour change — less predictable.)
- **Recommendation:** Option 1 — mirrors the existing `default_lock_ttl_seconds`/`gc_lock_ttl_seconds` `>= 1` validators and removes the foot-gun entirely. The minimum should probably be small but ≥ 1 (e.g. 1s) to honour "configurable interval".
- **Human decision:** **Fix (option 1, floor raised to 30s), 2026-07-25.** Maintainer chose a larger floor than the report's implied 1s: 30 seconds. Rationale: GC is a safety net (orphaned anons, expired TTL keys), not a latency-sensitive path, so a half-minute minimum keeps Redis pressure low. Default is 300s, well above the floor, so the default config stays valid.
- **Implementation status:** **done (2026-07-25).** Changes:
  - `src/gpdatacached/config.py`: new module-level constant `MIN_GC_INTERVAL_SECONDS = 30`; new `field_validator("gc_interval_seconds")` rejects `< 30`. The validator comment explicitly notes that the negative case is also closed here (contrary to the report's "Event.wait raises on negative" claim — it does NOT on CPython; it returns False like 0, so a validator guard is the only reliable fix).
  - `tests/test_config.py`: +5 tests — default is 300, rejects 0, rejects -5, rejects `MIN_GC_INTERVAL_SECONDS - 1` (29), accepts `MIN_GC_INTERVAL_SECONDS` and above (30/31/300/3600). The negative-rejection test docstring records the CPython `Event.wait` behaviour correction.
  - Docs EN+ZH (`api.md` config tables): `gc_interval_seconds` row now states "must be ≥ 30" with the reason (smaller values busy-loop the GC thread and flood Redis).
- **Verification (2026-07-25):**
  - `ruff check src tests` → All checks passed.
  - `pytest tests/test_config.py -q` → 31 passed (+5).
  - Validator boundary check: 0/-5/29 rejected; 30/60/300 accepted; default 300 valid.
  - Full suite confirmed green by the maintainer (the agent's own full-suite run was inconclusive due to the Redis-server-was-down incident; maintainer ran it manually).
- **Note:** test-config files `tests/configs/gpdc/core_domain.yaml` (300) and `edge_domain.yaml` (60) already use values ≥ 30, so no test-fixture change was needed.
- **Committed** in `50d9dfa`.

### Finding behavior.01 — Pydantic container field writes bypass Pydantic validation
- **Report claim:** `BaseModelContainerObject.__setitem__` only checks the field name exists, then encodes any GPDC-supported value; Pydantic field types/coercion/validators/cross-field constraints are not applied until `.value` materializes.
- **Original severity:** Major
- **Verdict:** **Confirmed**
- **Evidence:**
  - `src/gpdatacached/extensions/pydantic_support.py:140-153` `__setitem__`: checks `key in self._field_names` (141), then generic `encode_element` (150) + `hset` (151). No Pydantic validation of the value against the field type.
  - Demo `demo_behavior_01`: model `Order(total: float, name: str)`; `obj["total"] = "not-a-number"` **succeeded**, read back as `str`; `obj.value` then raised `ValidationError: Input should be a valid number, unable to parse string`. Invalid state persisted at write time, error deferred to a later read.
- **Options considered:**
  1. **Validate a full candidate model snapshot before committing** (read current fields, apply the candidate, construct the model, only `hset` if it validates). (Preserves field + model + cross-field validators; cost: O(n) read+validate per write.)
  2. Validate the individual field via Pydantic's `TypeAdapter`/field schema before writing (document that cross-field validators are not enforced on single-field writes). (Cheaper; partial guarantee.)
  3. Explicitly document raw/unvalidated field mutation and add a separate validated atomic-update API. (Honest about the weaker default; doesn't fix the silent-invalid-state foot-gun.)
- **Recommendation:** Option 1 — the value of storing a *Pydantic* model (vs a plain dict) is the validation guarantee; without it, long-lived shared invalid state is the likely failure mode. O(n) per write is acceptable for the model-container use case (models are typically small).
- **Human decision:** **Fix (option 2 — single-field TypeAdapter validation), 2026-07-24.** Maintainer rationale: option 2 has predictable, bounded cost (does not scale with model size / nested-container depth). Cross-field validation already has a proper home — `PydanticModelScalarCodec` (scalar mode stores the model atomically and validates on every read), so users who need cross-field rules should register their model with the scalar codec. Option 1's "read-all + reconstruct model" cost would exceed even the scalar-mode cost, defeating container mode's reason for existing (field-level access). The chosen design split: **container mode → single-field type validation; scalar mode → full validation incl. cross-field.** A cross-field demo (`demo_behavior_01_cross_field.py`, `Interval(start,end)` with `@model_validator` `end>start`) confirmed option 2 does not catch cross-field violations while option 1 would.
- **Implementation status:** **done (2026-07-24).** Changes:
  - `src/gpdatacached/extensions/pydantic_support.py`: `__setitem__` now calls a new `_validate_field_value(key, value)` helper BEFORE any Redis write; the helper builds `TypeAdapter(field.annotation)` from the model's `model_fields[key]` and calls `validate_python(value)` with pydantic's default (coercing) config. A type mismatch raises `pydantic.ValidationError` at the call site and Redis is untouched. Cross-field validators are intentionally NOT run (documented). `__getitem__` unchanged.
  - `tests/test_pydantic_support.py`: +3 tests — `test_field_set_type_mismatch_rejected` (`obj["total"] = "not-a-number"` raises ValidationError, Redis untouched), `test_field_set_coercible_value_accepted` (`obj["total"] = 5` int→float coercion accepted), `test_field_set_cross_field_constraint_not_enforced` (documents the intentional contract: a type-correct value passes even when a cross-field `@model_validator` would reject the snapshot; the violation still surfaces on `.value`).
  - Docs EN+ZH (`pydantic-support.md`): the mapping-interface table row for `obj["field"] = value` now states the value is validated against the field's type before the Redis write; added a `!!! warning` admonition explaining container mode validates one field at a time and directing users needing cross-field validation to the scalar codec (`PydanticModelScalarCodec`).
- **Verification (2026-07-24):**
  - `ruff check src tests` → All checks passed.
  - `pytest tests/test_pydantic_support.py -q` → 48 passed (+3).
  - Full suite `pytest -q -p no:cacheprovider` → **1402 passed** (+3 net).
- **Committed** in `caa9b8c`.

### Finding structure.01 — A module-scoped pattern in nested-container tests leaks every constructed domain
- **Report claim:** The `ns` fixture constructs a `GPDCDomain` and returns only its namespace; never yields, never calls `domain.close()`.
- **Original severity:** Minor
- **Verdict:** **Confirmed**
- **Evidence:**
  - `tests/test_nested_containers.py:39-45` `ns` fixture: `domain = GPDCDomain(config, name=DOMAIN); return domain.ensure_namespace(NS)` — no `yield`, no `close()`.
  - Demo `demo_structure_01` (source check): fixture parsed → `uses 'yield'? False`, `calls 'domain.close()'? False` → **leaked**. Matches the report's quoted warning `GPDCDomain 'testdomain_nest' was not explicitly closed`.
- **Options considered:**
  1. **Convert `ns` to a yielding fixture and `domain.close()` in `finally`.** (Local, minimal; mirrors other fixtures.)
  2. Reuse a shared domain fixture from `conftest.py` whose teardown already owns `close()`.
  3. `with GPDCDomain(...) as domain:` around a yielding namespace fixture.
- **Recommendation:** Option 1 — smallest change, removes the warning noise that masks real close/lifecycle regressions. Note: the sibling `r` fixture *does* yield+cleanup, so the pattern is inconsistent within the same file.
- **Human decision:** **Fix (option 1), 2026-07-25.**
- **Implementation status:** **done (2026-07-25).** `tests/test_nested_containers.py` `ns` fixture changed from `return domain.ensure_namespace(NS)` to `yield domain.ensure_namespace(NS)` followed by `domain.close()` (the yield+close pattern the sibling `r` fixture already uses).
- **Verification (2026-07-25):**
  - `ruff check src tests` → All checks passed.
  - `pytest tests/test_nested_containers.py -q` → 64 passed.
  - Leak warning `GPDCDomain 'testdomain_nest' was not explicitly closed` count in the nested-suite output = **0** (was emitted once per test before the fix).
- **Committed** in `77caa93`.

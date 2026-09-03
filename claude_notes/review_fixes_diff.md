# PR-review fixes: local re-run diff (2026-09-02, method 0.4.1)

Seven review fixes implemented (see PR discussion), then the calibration
machinery re-run against the three local stores:
`calibration_backfill --statuses weight_recovery_failed` on antarctica
(49 frames), `saturation_pass` on all three, calibration-variable attrs
stamped. Snapshots via `claude_notes/calib_state_snapshot.py`
(before/after in the session scratchpad).

## Headline

**The published *measurements* are unchanged.** Across all three stores:
zero frame-status transitions, zero per-trace `img_comb_offset_dB` changes,
zero `surface_source_image_index` changes. The only data change is the
intended fix-5 effect:

| store | change |
|---|---|
| greenland | none (margins 97,877 unchanged; method attr 0.4.0→0.4.1; array attrs added) |
| antarctica | 2018_Antarctica_DC8/img2: fit_ok(−18.8) → **no_plateau** → 1,428 margins NaN'd (91,867→90,439) |
| ase | 2018_Antarctica_DC8/img2: fit_ok(−21.2) → **no_plateau** → 651 margins NaN'd (16,199→15,548) |

Science verification: PASS on all three stores (original arrays
byte-identical; snapshot verify).

## Per-fix outcomes

1. **Staleness ordering** — backfill now marks `saturation.stale` when it
   rewrites `surface_source_image_index`; `saturation_pass` clears it.
   Verified live: antarctica read stale=True after the 49-frame backfill,
   fresh after the pass. Docs order swapped (backfill → saturation).
2. **All-or-nothing backfill writes** — any array-length mismatch now aborts
   the frame update with nothing written (status untouched, frame stays
   retryable). No real-data effect (no mismatches exist).
3. **Pair coverage vs expected images** — the pair loop now spans
   max(expected, loaded) images (bounded by the declared img_comb length), so
   a trailing missing image yields `partial_images`, never `ok`. Zero
   real-data relabels in this re-run (only the 49 wrf frames were
   re-measured; the sample audit predicted ≈0 store-wide).
4. **NaN-trace weight poisoning** — per-trace and aggregate medians now
   filter non-finite values. **All 49 antarctica `weight_recovery_failed`
   frames re-ran and all 49 remain weight_recovery_failed with identical
   values** — they are genuine large-spread cases (frame-varying effective
   weights, mostly 2017_Antarctica_Basler-era), not NaN artifacts. The fix is
   defensive only.
5. **Two-sided flat band** (−12 ≤ slope ≤ +12 dB/decade) — rising envelopes
   are no longer ceilings. Exactly one real fit affected: 2018_Antarctica_DC8
   img2 (slope +13.6), which flips to no_plateau in both stores that carry it.
   All other fits identical.
6. **Single-session saturation pass** — fit and write now share one writable
   session (no fit-from-one-snapshot / stamp-another race). No data effect.
7. **Metadata/docs** — CALIBRATION_VAR_ATTRS templates (units, sign
   convention, provenance framing) now applied at every array-creation path
   and stamped onto all three local stores (0→4 arrays with attrs each);
   changelog gains `partial_images` + corrected NaN-offset claim + the
   "img1 vs pooled higher-gain (index ≥ 2)" clarification; stale piecewise
   module docstring fixed; METHOD_VERSION 0.4.1.

## Tests

81 passed (76 existing + 5 new: staleness marking/clearing, mismatch
all-or-nothing, trailing-404 → partial_images, NaN-trace weights, rising
envelope rejection). One existing test edited: the mismatch test's no-op
session cannot be committed (icechunk refuses empty commits), asserted
explicitly.

## Not yet done (post-review)

- Commits + push of the fixes; S3 stores still carry method 0.4.0 and
  attr-less arrays until the small syncs are re-run after approval.
- The changelog rollout table will need its method reference refreshed when
  the S3 sync happens.

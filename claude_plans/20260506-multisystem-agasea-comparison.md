# Plan: MultisystemAGASEA Cross-Dataset Comparison

**Date:** 2026-05-06  
**Status:** Ready to execute

## Context

We have two datasets covering the Amundsen Sea Embayment (ASE) region of Antarctica:

### OPR ASE store (`config/config.yaml`)
- Instrument: CReSIS MCoRDS, ~195 MHz center frequency
- Region: `subregion: G-H`
- 771 frames, 27,243 total traces (22,143 QC-passing)
- Stored in icechunk S3 at `s3://opr-radar-metrics/icechunk/ase`
- Variables: `surface_elevation`, `bed_elevation`, `ice_thickness` (derived), `surface_power_dB`, `bed_power_dB`, `required_surface_snr_dB`
- **Survey period: 2012–2018** (five campaigns; frame counts by year below)

| Year | Frames | Traces |
|------|-------:|-------:|
| 2012 |    186 |  6,120 |
| 2013 |      2 |     82 |
| 2014 |    208 |  7,590 |
| 2016 |    176 |  6,373 |
| 2018 |    199 |  7,078 |
| **Total** | **771** | **27,243** |

### MultisystemAGASEA dataset
- **Source paper:** Chu et al. (2021), JGR Earth Surface, doi:10.1029/2021JF006296
- **Data path (read-only):** `/media/thomasteisberg/Data/MultisystemAGASEA/Results/`
- **143 NetCDF files**, 1,642,011 total traces (98.9% have valid reflectivity)
- **Instruments:**
  - 128 HiCARS files (prefix Y, X, DRP): UTIG HiCARS, **60 MHz**, Thwaites Glacier
  - 15 PASIN files (prefix b): BAS PASIN, **150 MHz**, Pine Island Glacier
- **Variables per trace** (dimension: `along-track sample`):
  - `latitude` (°, WGS84)
  - `longitude` (°, WGS84)
  - `radar_height` (m) — AGL clearance above ice surface (NOT absolute aircraft altitude)
  - `ice_thickness` (m)
  - `reflectivity` (dB) — relative bed reflectivity, corrected for two-way geometric spreading + one-way attenuation
  - `reflectivity_unc` (dB) — ± uncertainty
  - `atten_rate` (dB/km) — one-way empirical attenuation rate
  - `atten_unc` (dB/km) — ± uncertainty
- **Survey period:** 2004–2005 (BBAS + AGASEA campaigns)
- **Geographic extent:** ~74–81°S, ~86–128°W

### What is and is not comparable

| Quantity | OPR ASE | AGASEA | Comparable? |
|---|---|---|---|
| `ice_thickness` | derived from TWTT | direct pick | **Yes, direct** |
| `required_surface_snr_dB` | yes | derivable (see below) | **Yes, via inversion** |
| `bed_elevation` | yes | no (only AGL, no abs. altitude) | No |
| `surface_elevation` | yes | no | No |
| `bed_power_dB` / `surface_power_dB` | yes | no (not stored) | No |
| `reflectivity` (attenuation-corrected) | no | yes | No direct equivalent |
| `atten_rate` | no | yes | No direct equivalent |

### Deriving RSSNR from AGASEA reflectivity

AGASEA `reflectivity` is defined as:
```
reflectivity [dB] = P_bed − P_surf
                    + 20·log10(r_bed_eff / r_surf)        ← geometry correction
                    + 2 · atten_rate [dB/km] · ice_thickness [km]  ← two-way attenuation
```

OPR `required_surface_snr_dB` is:
```
RSSNR [dB] = P_surf − P_bed + 20·log10(r_surf) − 20·log10(r_bed_eff)
           = −(geometry-corrected bed/surface ratio, no attenuation correction)
```

The system constant (transmit power, antenna gain, cable losses) appears identically in P_surf and P_bed for each instrument and cancels in any ratio — RSSNR is calibration-free within each dataset. **However, there is a ~40 dB systematic offset between the two datasets that does not cancel:**

OPR uses **CSARP coherent SAR processing**, which yields a very large coherent gain for the specular surface return (nearly perfectly flat ice-air interface → constructive coherent integration) but relatively little gain for the diffuse bed return (rough interface, volume scatter → limited coherent gain). Measured values: OPR surface ~−35 dBm, OPR bed ~−108 dBm, OPR RSSNR median ~64 dB. AGASEA uses incoherent stacking; rssnr_equiv median ~18 dB. The ~46 dB gap is the differential SAR coherent gain and is not removable without knowledge of the per-target coherent integration gain.

**Conclusion: absolute RSSNR values are not comparable between the two datasets.** The RSSNR comparison in `multisystem_vs_opr.py` produces `rssnr_diff` (AGASEA − OPR) with a mean of ≈ −38 dB. This is real but driven by processing methodology, not physics of the bed. Spatial anomalies (relative to each dataset's own mean) could still be qualitatively compared.

Inverting the attenuation correction gives the equivalent RSSNR:
```
rssnr_equiv = −(reflectivity − 2 · atten_rate [dB/km] · ice_thickness [km])
```

where `atten_rate` is the one-way rate stored in the NetCDF and the factor of 2 accounts for the two-way path through ice.

**Important caveats:**
1. **Time gap:** AGASEA is 2004–2005; OPR spans 2012–2018. ASE is highly dynamic; Thwaites and Pine Island glaciers have thinned by tens to hundreds of metres in this period. Ice thickness differences reflect real change, not error. When matching spatially, record the OPR frame year so the time gap is explicit in the output CSV — pairs matched to 2018 data have a larger gap (13–14 years) than those matched to 2012 (7–8 years).
2. **Frequency-dependent physics:** 60 MHz (HiCARS) vs 150 MHz (PASIN) vs ~195 MHz (MCoRDS). Different frequencies give different Fresnel zone sizes, different englacial scattering, and different Fresnel reflection coefficients at the ice-bed interface. These cause physically real differences in RSSNR that are not instrument artefacts.
3. **Spatial sampling:** AGASEA data is at full along-track resolution (~5 m). The OPR store is decimated to 10 s intervals (~500–700 m). Match within a spatial threshold.

---

## Goals

1. **Internal crossover analysis of AGASEA** — assess self-consistency of the 2004–2005 dataset on `ice_thickness` and `rssnr_equiv`, using the same crossover methodology as `scripts/analysis/crossovers.py`
2. **Cross-dataset spatial comparison** — find AGASEA traces co-located (within threshold) with OPR ASE traces, compare `ice_thickness` and `rssnr_equiv` vs `required_surface_snr_dB`

---

## Implementation Plan

### Script 1: `scripts/analysis/multisystem_crossovers.py`

Internal crossover analysis of the AGASEA dataset. Reuse the geometry logic from `crossovers.py` (copy/import `_bearing`, `_acute_angle`, `_components`, `find_crossovers`, `make_scatter`, `make_differences`, `make_summary`, `print_summary`); replace only the data loading.

**Data loading:**
```python
def load_agasea_data(data_dir):
    """Load all Results/*.nc files; return data dict + flight_names list."""
    import xarray as xr
    from pathlib import Path
    
    files = sorted(Path(data_dir).glob("*.nc"))
    rows = {"lat": [], "lon": [], "ice_thickness": [],
            "reflectivity": [], "atten_rate": [], "frame_index": []}
    flight_names = []
    
    for f in files:
        ds = xr.open_dataset(f)
        n = ds.sizes["along-track sample"]
        fi = len(flight_names)
        flight_names.append(ds.attrs["Flight Transect"])
        rows["lat"].extend(ds["latitude"].values.tolist())
        rows["lon"].extend(ds["longitude"].values.tolist())
        rows["ice_thickness"].extend(ds["ice_thickness"].values.tolist())
        rows["reflectivity"].extend(ds["reflectivity"].values.tolist())
        rows["atten_rate"].extend(ds["atten_rate"].values.tolist())
        rows["frame_index"].extend([fi] * n)
        ds.close()
    
    data = {k: np.array(v) for k, v in rows.items()}
    # Derive rssnr_equiv: invert attenuation correction, negate to match RSSNR sign convention
    data["rssnr_equiv"] = -(data["reflectivity"] - 2.0 * data["atten_rate"] * data["ice_thickness"] / 1000.0)
    return data, flight_names
```

**VARIABLES dict** (replace the one from crossovers.py):
```python
VARIABLES = {
    "ice_thickness": {"label": "Ice Thickness",  "unit": "m"},
    "rssnr_equiv":   {"label": "RSSNR (equiv)",  "unit": "dB"},
}
```

Note: `find_crossovers()` from crossovers.py expects `data["frame_index"]` (int array indexing into `frame_names`). The AGASEA loader above produces that directly.

**QC filter:** drop traces where `ice_thickness`, `reflectivity`, or `atten_rate` is NaN before passing to `find_crossovers`. Use a boolean mask applied to all arrays. Approximately 1.1% of traces have NaN reflectivity; also guard against `atten_rate == 0` in some PASIN files (DRP02a shows min=0). A zero attenuation rate is likely a missing-data sentinel — treat as NaN.

**CLI:** 
```
uv run python scripts/analysis/multisystem_crossovers.py \
    /media/thomasteisberg/Data/MultisystemAGASEA/Results \
    --threshold 2000 \
    --output outputs/agasea_crossovers \
    -v
```

**Output files:**
- `outputs/agasea_crossovers/crossovers.csv`
- `outputs/agasea_crossovers/summary.csv`
- `outputs/agasea_crossovers/map.png`
- `outputs/agasea_crossovers/scatter.png`
- `outputs/agasea_crossovers/differences.png`

**Milestone check 1:** After running, verify:
- N crossovers > 0 (with 143 flight lines, many crossings expected — likely hundreds to thousands)
- Ice thickness crossover RMS < 200 m (same-campaign picks; this is an internal consistency check, not a physics comparison)
- `rssnr_equiv` crossover RMS < 10 dB (same-campaign, same-instrument crossovers should agree well; larger values suggest QC issues or bad atten_rate estimates)
- Print N crossovers separately for HiCARS–HiCARS, PASIN–PASIN, and HiCARS–PASIN pairs (tag each row with `instrument_a`, `instrument_b` using the `b`-prefix rule)

---

### Script 2: `scripts/analysis/multisystem_vs_opr.py`

Spatial matching between AGASEA and OPR ASE traces.

**Algorithm:**
1. Load AGASEA data via `load_agasea_data()` (from script 1, either import or duplicate)
2. Load OPR ASE store using `open_or_create_repo` + icechunk (same as `crossovers.py` `load_data(config)`)
3. Project both to EPSG:3031 using pyproj
4. Build `cKDTree` from OPR trace coordinates
5. For each AGASEA trace, query nearest OPR trace
6. Keep pairs where distance < `--threshold` (default 2000 m)
7. Also record: AGASEA instrument type (PASIN or HiCARS, from flight name prefix `b` vs other)

**Config:** Accept the OPR config path as a CLI argument (use `config/config.yaml` for the ASE store).

**Derived column:** After loading AGASEA, compute `rssnr_equiv` (same formula as above). Load OPR `required_surface_snr_dB` directly from the icechunk store.

**Comparison plots** (two variables: `ice_thickness` and `rssnr_equiv` / `required_surface_snr_dB`):
- **Map** (cartopy SouthPolarStereo, 2×2 panels): matched pair midpoints for each variable, colored by signed difference (AGASEA minus OPR), separate rows for HiCARS and PASIN matches
- **Scatter** (2 panels): AGASEA vs OPR for each variable; color by `distance_m`; 1:1 line
- **Histogram** (2 panels): signed differences per variable
- **Summary table:** per-instrument (HiCARS, PASIN), per-variable: N, mean diff, std, RMS

**Instrument column:** tag each matched pair with `instrument` = `"HiCARS"` or `"PASIN"` (flight name prefix `b` → PASIN, else HiCARS). Report stats separately — frequency-dependent RSSNR differences between instruments are physics, not error.

**CLI:**
```
uv run python scripts/analysis/multisystem_vs_opr.py \
    /media/thomasteisberg/Data/MultisystemAGASEA/Results \
    config/config.yaml \
    --threshold 2000 \
    --output outputs/multisystem_vs_opr \
    -v
```

**Output files:**
- `outputs/multisystem_vs_opr/matched_pairs.csv`
- `outputs/multisystem_vs_opr/summary.csv`
- `outputs/multisystem_vs_opr/map.png`
- `outputs/multisystem_vs_opr/scatter.png`
- `outputs/multisystem_vs_opr/differences.png`

**Milestone check 2:**
- N matched pairs > 100 at 2 km threshold (if fewer, increase to 5 km and note in output)
- Ice thickness scatter should be broadly correlated (R > 0.8) despite time gap; systematic negative bias (AGASEA thicker than OPR 2012) is expected from glacier thinning
- `rssnr_equiv` vs `required_surface_snr_dB` will show a ~40 dB systematic offset (AGASEA lower) due to OPR CSARP coherent SAR gain on the specular surface return; this is a known processing methodology difference and not an error
- Ice thickness bias patterns should be similar for HiCARS and PASIN (ice dynamics is the driver, not frequency)

---

## Code reuse strategy

Move the reusable crossover functions into `src/radar_return_statistics/crossovers.py` (new module) so both analysis scripts import from the package rather than duplicating code. After any changes to `src/`, re-run the full test suite (`uv run pytest tests/unit tests/test_web_build.py -v`) and confirm it still passes before proceeding.

Functions to move into `src/radar_return_statistics/crossovers.py`:
- `_bearing`, `_acute_angle`, `_components` (geometry helpers)
- `find_crossovers(data, frame_names, threshold, verbose)` (works on any data dict with the right keys)
- `make_scatter`, `make_differences`, `make_summary`, `print_summary` (plotting/stats; accept any `VARIABLES` dict as a parameter rather than using a module-level constant)
- `make_map` may also be moved but it uses cartopy — keep if tests pass with it, skip if it adds a heavy test dependency

Update `scripts/analysis/crossovers.py` to import from `radar_return_statistics.crossovers` instead of defining the functions locally.

The new `scripts/analysis/multisystem_crossovers.py` and `scripts/analysis/multisystem_vs_opr.py` then import the same functions. Each script defines only its own `VARIABLES` dict and data-loading logic.

---

## File outputs summary

```
outputs/
  agasea_crossovers/
    crossovers.csv          # one row per crossover (includes instrument_a, instrument_b)
    summary.csv             # per-variable stats (ice_thickness, rssnr_equiv)
    map.png                 # multipanel map, one panel per variable
    scatter.png             # scatter A vs B, colored by crossing angle
    differences.png         # histograms of A-B differences
  multisystem_vs_opr/
    matched_pairs.csv       # one row per AGASEA-OPR matched pair (includes instrument col)
    summary.csv             # per-instrument, per-variable stats
    map.png                 # 2×2: {ice_thickness, rssnr} × {HiCARS, PASIN}
    scatter.png             # 2 panels: one per variable
    differences.png         # 2 panels: one per variable
```

---

## Execution order

```bash
# Step 1: AGASEA internal crossovers
uv run python scripts/analysis/multisystem_crossovers.py \
    /media/thomasteisberg/Data/MultisystemAGASEA/Results \
    --threshold 2000 --output outputs/agasea_crossovers -v

# Inspect summary.csv; verify milestones before proceeding

# Step 2: Cross-dataset comparison
uv run python scripts/analysis/multisystem_vs_opr.py \
    /media/thomasteisberg/Data/MultisystemAGASEA/Results \
    config/config.yaml \
    --threshold 2000 --output outputs/multisystem_vs_opr -v
```

---

## Key gotchas for the implementer

1. **No time coordinate in AGASEA NetCDF** — flight identity is only from the filename (use `ds.attrs["Flight Transect"]`). The `along-track sample` dimension has no associated time or position index.
2. **Large dataset:** 1.64M traces across 143 flights. `find_crossovers` with Shapely buffer should still complete in reasonable time (< 10 min) since it eliminates non-intersecting pairs early. Tested at 22K traces / 771 frames for OPR ASE in the previous session.
3. **PASIN has significant NaN reflectivity** in some files (b01 has 5506 NaN out of 19064 traces = 29%). Filter NaN before running crossovers.
4. **reflectivity/atten_rate outliers:** Some files show reflectivity min = −158 dB and atten_rate min = 0.0 (likely missing-data sentinels). Apply QC: drop traces where `atten_rate <= 0` or `np.isnan(reflectivity)`. Also consider dropping `|reflectivity| > 60 dB` as a sanity bound — inspect the histogram of `rssnr_equiv` after loading to confirm the distribution looks reasonable before proceeding.
5. **No absolute altitude in AGASEA** — `radar_height` is AGL to ice surface; absolute elevations are not recoverable.
6. **Instrument tagging:** PASIN flights have names starting with `b` (e.g., `b01`, `b02`); all others are HiCARS. Tag at load time.
7. **The OPR store requires S3 credentials for write.** Load read-only with `repo.readonly_session(branch="main")` — same as in `crossovers.py`.

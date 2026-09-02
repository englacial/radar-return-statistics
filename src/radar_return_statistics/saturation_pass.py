"""Saturation second pass: fit season-level ceilings over a completed store and
write per-trace surface_ceiling_margin_dB + season attrs back to it.

  uv run python -m radar_return_statistics.saturation_pass <config.yaml>

Fits img1-sourced and img2-sourced populations separately where
surface_source_image_index is populated (relaxed range-span minimum), falling
back to a single all-traces fit otherwise; the cross-cap step is the season's
empirical img2 bias estimate. Margins are computed against the trace's own
source population's ceiling when that fit is fit_ok, else NaN.
"""
import logging
import math
from datetime import datetime, timezone

import click
import numpy as np
import scipy.constants
import zarr

from . import calibration as cal
from . import store as store_mod
from .config import load_config

logger = logging.getLogger(__name__)

METHOD_VERSION = "0.4.0"  # 0.4.0: piecewise partial-saturation model removed (flat rule only); 0.3.0: sparse-bin merging


def _jsonable(obj):
    """np scalars -> python, NaN -> None, tuples -> lists, recursively."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def run_saturation_pass(root: zarr.Group) -> tuple[np.ndarray, dict]:
    """Compute per-trace margins and season attrs from store arrays."""
    power = root["surface_power_dB"][:]
    twtt = root["surface_twtt"][:]
    r = scipy.constants.c * twtt / 2.0
    qc_key = "qc_surface_pass" if "qc_surface_pass" in root else "qc_pass"
    qc = root[qc_key][:].astype(bool) if qc_key in root else np.ones(power.shape, bool)
    if "surface_source_image_index" in root:
        src = root["surface_source_image_index"][:]
    else:
        src = np.full(power.shape, -1, dtype=np.int8)

    attrs = root.attrs.asdict()
    colls = attrs.get("frame_collections")
    if colls and "frame_index" in root:
        seasons = np.asarray(colls)[root["frame_index"][:]]
    else:
        seasons = np.full(power.shape, "", dtype=object)

    margins = np.full(power.shape, np.nan, dtype=np.float32)
    season_results = {}
    for season in np.unique(seasons):
        m = seasons == season
        fit_mask = m & qc  # fits use QC-passing traces only
        season_fit = cal.fit_season_by_source(power[fit_mask], r[fit_mask], src[fit_mask])
        # margins for every trace of the season with finite power
        margins[m] = cal.margins_by_source(power[m], src[m], season_fit).astype(np.float32)
        season_results[str(season)] = season_fit
        pops = season_fit["populations"]
        logger.info("%s [%s]: %s; cross-cap step %s dB", season or "(no season)",
                    season_fit["ceiling_fit_population"],
                    {k: f"{v['status']}({v['level']:.1f})" if math.isfinite(v["level"])
                     else v["status"] for k, v in pops.items()},
                    f"{season_fit['cross_cap_step_db']:.1f}"
                    if math.isfinite(season_fit["cross_cap_step_db"]) else "n/a")

    saturation_attrs = _jsonable({
        "method_version": METHOD_VERSION,
        "fitted": datetime.now(timezone.utc).isoformat(),
        "params": cal.SAT_DEFAULTS,
        "seasons": season_results,
    })
    return margins, saturation_attrs


@click.command()
@click.argument("config_path", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Fit and report, but write nothing")
@click.option("-v", "--verbose", is_flag=True)
def main(config_path: str, dry_run: bool, verbose: bool) -> None:
    """Fit season saturation ceilings and write margins back to the store."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(config_path)
    repo = store_mod.open_or_create_repo(config["store"])

    root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")
    if "surface_power_dB" not in root:
        raise click.ClickException("Store has no surface_power_dB — run the pipeline first")
    margins, saturation_attrs = run_saturation_pass(root)

    if dry_run:
        click.echo("dry run: no store writes")
        return
    session = repo.writable_session("main")
    store_mod.write_saturation_results(session, margins, saturation_attrs)
    store_mod.commit_session(session, "[calibration] saturation second pass")
    click.echo(f"wrote surface_ceiling_margin_dB ({np.isfinite(margins).sum()} finite margins)")


if __name__ == "__main__":
    main()

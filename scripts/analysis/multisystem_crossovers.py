"""Internal crossover analysis of the MultisystemAGASEA dataset."""

import sys
from pathlib import Path

import click
import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from radar_return_statistics.crossovers import (
    find_crossovers, make_map, make_scatter, make_differences, make_summary, print_summary,
)

VARIABLES = {
    "ice_thickness": {"label": "Ice Thickness", "unit": "m"},
    "rssnr_equiv":   {"label": "RSSNR (equiv)", "unit": "dB"},
}


def _instrument(flight_name):
    return "PASIN" if flight_name.startswith("b") else "HiCARS"


def load_agasea_data(data_dir):
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
    data["rssnr_equiv"] = -(data["reflectivity"] - 2.0 * data["atten_rate"] * data["ice_thickness"] / 1000.0)

    # QC: drop NaN ice_thickness, NaN/zero atten_rate, NaN reflectivity
    valid = (
        np.isfinite(data["ice_thickness"]) &
        np.isfinite(data["reflectivity"]) &
        np.isfinite(data["atten_rate"]) &
        (data["atten_rate"] > 0)
    )
    for k in data:
        data[k] = data[k][valid]

    return data, flight_names


@click.command()
@click.argument("data_dir", type=click.Path(exists=True))
@click.option("--threshold", default=2000.0, help="Crossover distance threshold in metres")
@click.option("--output", "output_dir", default="outputs/agasea_crossovers", type=click.Path())
@click.option("--verbose", "-v", is_flag=True)
def main(data_dir, threshold, output_dir, verbose):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    click.echo("Loading AGASEA data...")
    data, flight_names = load_agasea_data(data_dir)
    click.echo(f"Loaded {len(data['lat'])} QC-passing traces across {len(flight_names)} flights")

    click.echo(f"Finding crossovers (threshold={threshold} m)...")
    df, pairs_checked = find_crossovers(data, flight_names, threshold, verbose, VARIABLES)
    click.echo(f"Found {len(df)} crossovers from {pairs_checked} flight pairs checked")

    if df.empty:
        click.echo("No crossovers found — nothing to plot.")
        return

    # Tag instrument per frame
    df["instrument_a"] = df["frame_a"].apply(_instrument)
    df["instrument_b"] = df["frame_b"].apply(_instrument)

    csv_path = output_dir / "crossovers.csv"
    df.to_csv(csv_path, index=False)
    click.echo(f"Saved: {csv_path}")

    summary = make_summary(df, VARIABLES)
    summary_path = output_dir / "summary.csv"
    summary.to_csv(summary_path, index=False)
    click.echo(f"Saved: {summary_path}")
    click.echo()
    print_summary(summary)
    click.echo()

    # Per-instrument-pair breakdown
    for pair in [("HiCARS", "HiCARS"), ("PASIN", "PASIN"), ("HiCARS", "PASIN"), ("PASIN", "HiCARS")]:
        mask = ((df["instrument_a"] == pair[0]) & (df["instrument_b"] == pair[1])) | \
               ((df["instrument_a"] == pair[1]) & (df["instrument_b"] == pair[0]))
        sub = df[mask]
        if len(sub):
            click.echo(f"{pair[0]}–{pair[1]}: {len(sub)} crossovers")

    click.echo("\nGenerating plots...")
    make_map(df, output_dir, VARIABLES)
    click.echo(f"Saved: {output_dir / 'map.png'}")
    make_scatter(df, output_dir, VARIABLES)
    click.echo(f"Saved: {output_dir / 'scatter.png'}")
    make_differences(df, output_dir, VARIABLES)
    click.echo(f"Saved: {output_dir / 'differences.png'}")


if __name__ == "__main__":
    main()

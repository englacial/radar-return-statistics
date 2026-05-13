"""Rechunk per-trace zarr arrays in an existing icechunk store.

Existing stores have ~38-element chunks (one chunk per frame, inherited from
xarray's default chunking on first write). That blows up the per-variable
HTTP-request count in the viewer — for the Greenland store, loading one
variable fires ~2,500 requests. Rechunking to e.g. 10,000 elements/chunk
drops that to ~10 requests per variable.

This script reads each per-trace array, recreates it with a sensible chunk
size, and commits the result.

In-progress safety:
- If another `radar_return_statistics` process is writing to the same store,
  the rechunk + pipeline commits would race (whoever commits first wins,
  losing hours of work for the loser). We scan /proc for an active runner
  pointed at the same config and refuse to run unless --force is passed.
- We also rely on icechunk's optimistic concurrency: if a commit lands
  between our read and write, our own commit raises and we report cleanly.

Usage:
    uv run python scripts/migrations/rechunk_per_trace_arrays.py \\
        --config config/config_greenland.yaml [--chunk-size 10000] [--dry-run]
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click
import numpy as np
import zarr

# Make src/ importable when invoked from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from radar_return_statistics import store as store_mod  # noqa: E402
from radar_return_statistics.config import load_config  # noqa: E402

logger = logging.getLogger("rechunk_migration")


def _find_active_pipeline_pids(config_path: Path) -> list[int]:
    """Return PIDs of running radar_return_statistics runs targeting config_path."""
    target = str(config_path.resolve())
    target_name = config_path.name
    found: list[int] = []
    my_pid = os.getpid()
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        pid = int(pid_dir.name)
        if pid == my_pid:
            continue
        try:
            cmdline = (pid_dir / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if "radar_return_statistics" not in cmdline:
            continue
        if target in cmdline or target_name in cmdline:
            found.append(pid)
    return found


def _per_trace_arrays(root: zarr.Group, n_traces: int) -> list[str]:
    """Return names of top-level arrays whose first dimension is n_traces."""
    names = []
    for key in root.array_keys():
        arr = root[key]
        if not isinstance(arr, zarr.Array):
            continue
        if arr.shape and arr.shape[0] == n_traces:
            names.append(key)
    return names


@click.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, path_type=Path),
              help="Pipeline config YAML identifying the store to rechunk.")
@click.option("--chunk-size", default=10000, type=int, show_default=True,
              help="New chunk size (first dimension) for per-trace arrays.")
@click.option("--dry-run", is_flag=True, help="Report what would change without committing.")
@click.option("--force", is_flag=True, help="Run even if an active pipeline is detected.")
def main(config_path: Path, chunk_size: int, dry_run: bool, force: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    config = load_config(config_path)
    store_cfg = config["store"]
    label = store_cfg.get("s3_prefix") or store_cfg.get("path") or "<unknown>"
    logger.info("Target store: %s", label)

    active = _find_active_pipeline_pids(config_path)
    if active:
        msg = (
            f"Detected active pipeline process(es) for {config_path.name}: PIDs {active}. "
            "Rechunking now would race their commit and could destroy in-progress work."
        )
        if not force:
            logger.error(msg + " Re-run with --force only after confirming it is safe.")
            sys.exit(2)
        logger.warning(msg + " Proceeding because --force was given.")

    repo = store_mod.open_or_create_repo(store_cfg)

    # Read current state via a readonly session to inspect chunking.
    ro_session = repo.readonly_session("main")
    ro_root = zarr.open_group(ro_session.store, mode="r")
    n_traces = ro_root["latitude"].shape[0]
    targets = _per_trace_arrays(ro_root, n_traces)

    plan: list[tuple[str, tuple, tuple]] = []
    for name in targets:
        arr = ro_root[name]
        new_chunks = (min(chunk_size, n_traces),)
        if arr.chunks == new_chunks:
            continue
        plan.append((name, arr.chunks, new_chunks))

    if not plan:
        logger.info("Nothing to do: all %d per-trace arrays already match chunks=%s",
                    len(targets), (min(chunk_size, n_traces),))
        return

    logger.info("Will rechunk %d arrays (n_traces=%d):", len(plan), n_traces)
    for name, old, new in plan:
        logger.info("  %-30s %s -> %s", name, old, new)

    if dry_run:
        logger.info("Dry run — not committing.")
        return

    # Open writable session, rewrite arrays, commit.
    session = repo.writable_session("main")
    root = zarr.open_group(session.store, mode="a")
    for name, _, new_chunks in plan:
        arr = root[name]
        attrs = dict(arr.attrs)
        data = np.asarray(arr[:])
        # overwrite=True recreates the array in place with the new chunking.
        root.create_array(name, data=data, chunks=new_chunks, overwrite=True)
        root[name].attrs.update(attrs)
        logger.info("  rechunked %s", name)

    try:
        snapshot = session.commit(f"Rechunk {len(plan)} per-trace arrays to {new_chunks}")
    except Exception as e:
        logger.error("Commit failed (likely a concurrent writer): %s", e)
        sys.exit(3)
    logger.info("Done. Snapshot: %s", snapshot)


if __name__ == "__main__":
    main()

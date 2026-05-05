"""Migration: add frame_index + frame_names to an existing store.

Needed for the browser viewer, which cannot parse zarr-python v3's numpy.str_
dtype used by the native frame_id array. This writes frame_index (uint16 per
trace) and frame_names (JSON list on the root group attribute) which zarrita
can read.

New stores get these automatically via the processing pipeline. Run this once
to backfill an older store.

Usage:
    uv run python scripts/migrations/add_frame_index.py config/config.yaml
"""
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "src"))
from radar_return_statistics.store import open_or_create_repo, update_frame_index, commit_session


def main(config_path: str) -> None:
    with open(config_path) as f:
        config = yaml.safe_load(f)

    repo = open_or_create_repo(config["store"])
    session = repo.writable_session(branch="main")
    update_frame_index(session)
    commit_session(session, "Migration: add frame_index + frame_names for browser viewer")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run python scripts/migrations/add_frame_index.py <config.yaml>")
        sys.exit(1)
    main(sys.argv[1])

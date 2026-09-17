"""Refresh pbsjatos studyId discovery cache and print migration status.

Run before nightly pull or after importing new OA/OB/OC studies on pbsjatos.

Usage:
  python scripts/refresh_jatos_ids.py
  python scripts/refresh_jatos_ids.py --no-write
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from jatos_study_ids import (  # noqa: E402
    handler_ids_from_titles,
    migration_status,
    refresh_discovered,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--no-write",
        action="store_true",
        help="Probe only; do not update jatos_discovered_ids.json",
    )
    args = ap.parse_args()

    token = os.environ.get("JATOS_TOKEN", "").strip()
    if not token:
        raise SystemExit("JATOS_TOKEN required")

    titles = refresh_discovered(
        token=token,
        write_cache=not args.no_write,
    )
    status = migration_status(titles)
    handler = handler_ids_from_titles(titles)

    print(f"pbsjatos titles discovered: {status['n_present']}/{status['n_expected']}")
    print(f"OBS studies still missing on pbsjatos: {status['n_missing_obs']}")
    if status["missing_obs_titles"]:
        print("missing OBS titles (first 20):")
        for t in status["missing_obs_titles"][:20]:
            print(f"  - {t}")
        if len(status["missing_obs_titles"]) > 20:
            print(f"  ... +{len(status['missing_obs_titles']) - 20} more")

    print("per-task pbs studyId counts:")
    for task, ids in sorted(handler.items()):
        print(f"  {task}: {len(ids)} ids -> {ids}")


if __name__ == "__main__":
    main()

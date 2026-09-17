"""JATOS studyId registry: pbsjatos discovery + legacy old-server fallback.

Site order in LEGACY_HANDLER arrays: IA, IB, IC, OA, OB, OC (indices 0–5).
pbsjatos titles use ``{SITE}_{TASK}`` (e.g. ``OA_NF``, ``IB_AF``).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests

TASKS = (
    "AF",
    "ATS",
    "DSST",
    "DWL",
    "FN",
    "LC",
    "NF",
    "NNB",
    "NTS",
    "PC",
    "SM",
    "VNB",
    "WL",
)

SITES = ("OA", "OB", "OC", "IA", "IB", "IC")
LEGACY_SITE_ORDER = ("IA", "IB", "IC", "OA", "OB", "OC")
OBS_SITES = ("OA", "OB", "OC")

# Old jatos.psychology.uiowa.edu Handler.IDs (pre-pbsjatos migration).
LEGACY_HANDLER: dict[str, list[int]] = {
    "AF": [945, 960, 990, 898, 919, 932],
    "ATS": [947, 961, 984, 918, 920, 933],
    "DSST": [949, 975, 986, 901, 959, 935],
    "DWL": [948, 974, 985, 900, 921, 934],
    "FN": [950, 964, 987, 902, 923, 936],
    "LC": [951, 976, 988, 903, 924, 937],
    "NF": [980, 981, 982, 978, 979, 977],
    "NNB": [946, 967, 989, 905, 929, 939],
    "NTS": [953, 968, 991, 906, 930, 940],
    "PC": [954, 969, 992, 912, 925, 941],
    "SM": [955, 970, 993, 916, 926, 996],
    "VNB": [957, 971, 994, 915, 928, 943],
    "WL": [958, 972, 995, 910, 927, 944],
}

# Baseline pbsjatos map (2026-08-31 probe). Discovery may extend this at runtime.
PBS_HANDLER: dict[str, list[int]] = {
    "AF": [11, 30, 42],
    "ATS": [8, 22, 41],
    "DSST": [10, 21, 43],
    "DWL": [17, 24, 38],
    "FN": [81, 18, 28, 39],
    "LC": [14, 31, 44],
    "NF": [47, 12, 33, 46],
    "NNB": [67, 83, 15, 27, 36],
    "NTS": [19, 25, 40],
    "PC": [20, 26, 37],
    "SM": [13, 32, 45],
    "VNB": [85, 16, 29, 34],
    "WL": [7, 23, 35],
}

TITLE_RE = re.compile(r"^(OA|OB|OC|IA|IB|IC)_(AF|ATS|DSST|DWL|FN|LC|NF|NNB|NTS|PC|SM|VNB|WL)$")

_CACHE_PATH = Path(__file__).resolve().parent / "jatos_discovered_ids.json"


def expected_titles() -> list[str]:
    return [f"{site}_{task}" for task in TASKS for site in SITES]


def title_to_task_site(title: str) -> tuple[str, str] | None:
    m = TITLE_RE.match(title.strip())
    if not m:
        return None
    return m.group(2), m.group(1)


def discover_pbsjatos(
    token: str,
    base_url: str = "https://pbsjatos.psychology.uiowa.edu",
    id_min: int = 1,
    id_max: int = 200,
    batch_size: int = 20,
    timeout: int = 60,
) -> dict[str, int]:
    """Scan studyId range on pbsjatos; return ``{SITE_TASK: studyId}``."""
    base = base_url.rstrip("/")
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    discovered: dict[str, int] = {}
    for start in range(id_min, id_max + 1, batch_size):
        batch = list(range(start, min(start + batch_size, id_max + 1)))
        try:
            r = requests.post(
                f"{base}/jatos/api/v1/results/metadata",
                headers=headers,
                json={"studyIds": batch},
                timeout=timeout,
            )
            r.raise_for_status()
        except requests.RequestException:
            continue
        for study in r.json().get("data") or []:
            title = study.get("studyTitle")
            sid = study.get("studyId")
            if not title or sid is None:
                continue
            if title_to_task_site(str(title)):
                discovered[str(title)] = int(sid)
    return discovered


def load_discovered_cache() -> dict[str, int]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in data.get("titles", {}).items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def save_discovered_cache(titles: dict[str, int]) -> None:
    payload = {
        "source": "pbsjatos",
        "titles": titles,
    }
    _CACHE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def refresh_discovered(
    token: str | None = None,
    base_url: str | None = None,
    write_cache: bool = True,
) -> dict[str, int]:
    token = (token or os.environ.get("JATOS_TOKEN", "")).strip()
    if not token:
        return load_discovered_cache()
    base_url = (
        base_url
        or os.environ.get("JATOS_BASE_URL", "https://pbsjatos.psychology.uiowa.edu")
    ).rstrip("/")
    if os.environ.get("JATOS_DISCOVER_IDS", "1").strip().lower() in ("0", "false", "no"):
        return load_discovered_cache() or _pbs_from_baseline()
    discovered = discover_pbsjatos(token, base_url)
    cached = load_discovered_cache()
    merged = {**cached, **discovered}
    if write_cache and merged:
        save_discovered_cache(merged)
    if merged:
        return merged
    return {}


def handler_ids_from_titles(titles: dict[str, int]) -> dict[str, list[int]]:
    """Build per-task studyId lists from discovered ``SITE_TASK`` titles."""
    out: dict[str, list[int]] = {task: [] for task in TASKS}
    for title, sid in sorted(titles.items()):
        parsed = title_to_task_site(title)
        if not parsed:
            continue
        task, _site = parsed
        if sid not in out[task]:
            out[task].append(sid)
    # Fall back to baseline when discovery returns nothing for a task.
    for task in TASKS:
        if not out[task]:
            out[task] = list(PBS_HANDLER.get(task, []))
    return out


def legacy_obs_ids_for_task(task: str, pbs_titles: set[str]) -> list[int]:
    """Old-server OA/OB/OC studyIds for sites not yet on pbsjatos."""
    legacy = LEGACY_HANDLER.get(task, [])
    if len(legacy) != 6:
        return []
    out: list[int] = []
    for site, sid in zip(LEGACY_SITE_ORDER, legacy):
        if site in OBS_SITES and f"{site}_{task}" not in pbs_titles:
            out.append(sid)
    return out


def migration_status(titles: dict[str, int] | None = None) -> dict[str, Any]:
    titles = titles or load_discovered_cache()
    expected = expected_titles()
    present = [t for t in expected if t in titles]
    missing = [t for t in expected if t not in titles]
    missing_obs = [t for t in missing if t.startswith(("OA_", "OB_", "OC_"))]
    return {
        "n_expected": len(expected),
        "n_present": len(present),
        "n_missing": len(missing),
        "n_missing_obs": len(missing_obs),
        "missing_obs_titles": missing_obs,
        "complete": len(missing) == 0,
    }

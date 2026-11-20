#!/usr/bin/env python3
"""
PROGRAM:       power_policy.py
AUTHOR:    {{AUTHOR}}
LAST MODIFIED: 2026-11-19
PURPOSE:       ONE reader for data/power/policy.json, shared by the root applier
               (mbp14_power_apply.py) and the user-level checker (mbp14_power_heal.py), so the two
               can never disagree about a power or fan target again.

WHY: on 2026-11-19 the root applier set EPP=balance_power on battery while the heal script demanded
     EPP=power, so the heal unit reported drift forever. Both now read this file.

SAFETY: values are CLAMPED to the "limits" block (and to hard-coded fallback limits if that block is
     missing or edited), because the root applier acts on whatever this file says. An invalid or
     missing file falls back to the built-in defaults below, never to "no limits".

CHANGE HISTORY:
  2026-11-19  {{AUTHOR}}  Created.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

# Repo copy (the source of truth, edited by people) and the root-owned installed copy (read by the root unit).
REPO_POLICY = Path(__file__).resolve().parent.parent / "data" / "power" / "policy.json"
INSTALLED_POLICY = Path("/etc/mbp14/power-policy.json")

# Built-in defaults = the policy {{OWNER}} chose on 2026-11-19; used when the file is missing or invalid.
DEFAULTS: Dict[str, Dict[str, Any]] = {
    "bat": {"epp": "power", "no_turbo": 1, "max_freq_khz": 2300000, "pl1_uw": 9000000,
            "pl2_uw": 13000000, "fan_min_rpm": 1200, "fan_policy": "auto"},
    "ac": {"epp": "balance_performance", "no_turbo": 0, "max_freq_khz": 3600000, "pl1_uw": 15000000,
           "pl2_uw": 25000000, "fan_min_rpm": 2400, "fan_policy": "auto"},
}
# Hard limits that apply even if the file's own "limits" block is tampered with.
HARD = {"epp": {"power", "balance_power", "balance_performance", "performance", "default"},
        "max_freq_khz": (800000, 3600000), "pl_uw": (5000000, 28000000), "fan_min_rpm": (1200, 3500)}


def _clamp(v: Any, lo: int, hi: int, fallback: int) -> int:
    """Return int(v) clamped to [lo, hi]; fallback when v is not a number."""
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return fallback


def load(path: Path | None = None) -> Dict[str, Any]:
    """Read and sanitise the policy. Returns {"bat": {...}, "ac": {...}} of plain values (no 'why')."""
    out = {m: dict(v) for m, v in DEFAULTS.items()}
    for candidate in ([Path(path)] if path else [INSTALLED_POLICY, REPO_POLICY]):
        try:
            raw = json.loads(candidate.read_text())
            modes = raw["modes"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for mode in ("bat", "ac"):
            block = modes.get(mode) if isinstance(modes, dict) else None
            if not isinstance(block, dict):
                continue
            def val(key):
                item = block.get(key)
                return item.get("value") if isinstance(item, dict) else item
            d = DEFAULTS[mode]
            epp = val("epp")
            out[mode]["epp"] = epp if epp in HARD["epp"] else d["epp"]
            out[mode]["no_turbo"] = 1 if str(val("no_turbo")) in ("1", "True", "true") else 0 if str(val("no_turbo")) in ("0", "False", "false") else d["no_turbo"]
            out[mode]["max_freq_khz"] = _clamp(val("max_freq_khz"), *HARD["max_freq_khz"], d["max_freq_khz"])
            out[mode]["pl1_uw"] = _clamp(val("pl1_uw"), *HARD["pl_uw"], d["pl1_uw"])
            out[mode]["pl2_uw"] = _clamp(val("pl2_uw"), *HARD["pl_uw"], d["pl2_uw"])
            out[mode]["fan_min_rpm"] = _clamp(val("fan_min_rpm"), *HARD["fan_min_rpm"], d["fan_min_rpm"])
            out[mode]["fan_policy"] = "auto"  # only automatic fan control is supported; no manual fan speeds
        return out  # first readable file wins
    return out


def target(mode: str, path: Path | None = None) -> Dict[str, Any]:
    """Targets for one mode ('bat' or 'ac'); anything else is treated as 'ac'."""
    return load(path)["bat" if mode == "bat" else "ac"]

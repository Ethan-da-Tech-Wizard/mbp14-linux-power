#!/usr/bin/env python3
# PROGRAM:       scripts/power_stability.py - is the power policy HELD over time, not just right in one snapshot?
# AUTHOR:    {{AUTHOR}}
# LAST MODIFIED: 2026-11-20
# PURPOSE:       mbp14_power_heal.py takes ONE snapshot, so it reports "COMPLIANT" even while the laptop firmware keeps
#                rewriting the CPU package power limit (PL1) to 85-100 W for seconds at a time (measured 2026-11-20:
#                PL1 outside the 15 W policy in 36% of 0.1 s samples on AC with the 5 s hold loop). This tool samples the
#                real hardware settings for N seconds and reports the PERCENT OF TIME each one matched the policy,
#                how often PL1 flipped, the longest out-of-policy stretch and the highest value seen.
# SAFETY:        READ-ONLY. It never writes to /sys, never starts or stops a service and needs no root. A value it cannot
#                read is reported "unavailable" and counted as NOT verified, never as OK.
# USAGE:         python3 scripts/power_stability.py [--seconds 30] [--interval 0.1] [--mode auto|ac|bat] [--json] [--receipt]
# EXIT CODE:     0 = every knob in policy >= 99% of the time; 1 = drifting or partial; 2 = nothing could be read.
"""Sample the live power knobs and compare them with data/power/policy.json over time."""
from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

OK_PERCENT = 99.0
KNOBS = ("epp", "no_turbo", "max_freq_khz", "pl1_uw", "pl2_uw", "fan_min_rpm")
LABELS = {"epp": "energy preference", "no_turbo": "turbo off", "max_freq_khz": "max frequency (kHz)",
          "pl1_uw": "PL1 sustained limit (uW)", "pl2_uw": "PL2 burst limit (uW)", "fan_min_rpm": "fan minimum (RPM)"}


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def detect_mode(sysroot: str = "/") -> Optional[str]:
    """'ac' when any mains supply reports online=1, 'bat' when one reports 0, None when none can be read."""
    seen = []
    for path in sorted(glob.glob(os.path.join(sysroot, "sys/class/power_supply/A*/online"))):
        value = _read(path)
        if value in ("0", "1"):
            seen.append(value)
    if not seen:
        return None
    return "ac" if "1" in seen else "bat"


def sample_once(sysroot: str = "/") -> Dict[str, Optional[str]]:
    """One reading of every knob as the policy would express it; None where the file is missing or unreadable."""
    def p(*parts: str) -> str:
        return os.path.join(sysroot, *parts)
    epps = sorted({v for v in (_read(f) for f in glob.glob(p("sys/devices/system/cpu/cpu[0-9]*/cpufreq/energy_performance_preference"))) if v})
    maxf = sorted({v for v in (_read(f) for f in glob.glob(p("sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_max_freq"))) if v})
    fan = _read(p("sys/devices/platform/applesmc.768/fan1_min"))
    if fan is None:
        for f in sorted(glob.glob(p("sys/class/hwmon/hwmon*/fan1_min"))):
            fan = _read(f)
            if fan is not None:
                break
    return {
        "epp": ",".join(epps) if epps else None,   # more than one distinct value across cores shows as "a,b" and fails
        "no_turbo": _read(p("sys/devices/system/cpu/intel_pstate/no_turbo")),
        "max_freq_khz": ",".join(maxf) if maxf else None,
        "pl1_uw": _read(p("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw")),
        "pl2_uw": _read(p("sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw")),
        "fan_min_rpm": fan,
    }


def analyze(samples: List[Dict[str, Optional[str]]], target: Dict[str, Any], interval: float) -> Dict[str, Any]:
    """Pure function: percent of samples in policy per knob, PL1 flips, longest out-of-policy PL1 stretch, PL1 maximum."""
    result: Dict[str, Any] = {"samples": len(samples), "interval_s": interval, "knobs": {}}
    for knob in KNOBS:
        want = str(target.get(knob))
        got = [s.get(knob) for s in samples]
        readable = [g for g in got if g is not None]
        if not readable:
            result["knobs"][knob] = {"want": want, "status": "unavailable", "in_policy_percent": None}
            continue
        match = sum(1 for g in readable if g == want)
        percent = 100.0 * match / len(readable)
        result["knobs"][knob] = {"want": want, "status": "ok" if percent >= OK_PERCENT else "drifting",
                                 "in_policy_percent": round(percent, 1), "unreadable_samples": len(got) - len(readable),
                                 "distinct_values": sorted(set(readable))[:6]}
    pl1 = [s.get("pl1_uw") for s in samples]
    want_pl1 = str(target.get("pl1_uw"))
    flips = streak = longest = 0
    prev: Optional[bool] = None
    for value in pl1:
        if value is None:
            continue
        inside = value == want_pl1
        if prev is not None and inside != prev:
            flips += 1
        streak = 0 if inside else streak + 1
        longest = max(longest, streak)
        prev = inside
    numeric = []
    for value in pl1:
        try:
            numeric.append(int(value))
        except (TypeError, ValueError):
            pass
    result["pl1"] = {"flips": flips, "longest_out_of_policy_s": round(longest * interval, 2),
                     "max_watts_seen": round(max(numeric) / 1e6, 1) if numeric else None}
    states = [k["status"] for k in result["knobs"].values()]
    if all(s == "unavailable" for s in states):
        result["verdict"] = "UNAVAILABLE"
    elif "drifting" in states:
        result["verdict"] = "DRIFTING"
    elif "unavailable" in states:
        result["verdict"] = "PARTIAL"   # nothing wrong where it could be read, but not everything could be read
    else:
        result["verdict"] = "STABLE"
    return result


def settle_report(samples: List[Dict[str, Optional[str]]], targets: Dict[str, Dict[str, Any]], interval: float) -> List[Dict[str, Any]]:
    """For every plug or unplug seen in the samples (their "_mode" key changes), how long until every knob except PL1 matched
    the NEW mode's policy, and which energy-preference values were passed through on the way (the old writer's value shows up
    here). PL1 is left out of the settle test because the firmware keeps flipping it; it is judged separately per mode."""
    out = []
    previous = None
    for index, sample in enumerate(samples):
        mode = sample.get("_mode")
        if previous is not None and mode in targets and mode != previous:
            want = {k: str(targets[mode].get(k)) for k in KNOBS if k != "pl1_uw"}
            settled = None
            seen_epp = []
            for later in range(index, len(samples)):
                epp = samples[later].get("epp")
                if epp and epp not in seen_epp:
                    seen_epp.append(epp)
                if all(samples[later].get(k) == v for k, v in want.items()):
                    settled = round((later - index) * interval, 2)
                    break
            out.append({"at_s": round(index * interval, 2), "from": previous, "to": mode, "settled_after_s": settled,
                        "epp_values_passed_through": seen_epp})
        if mode in ("ac", "bat"):
            previous = mode
    return out


def run(seconds: float, interval: float, mode: str, sysroot: str = "/", policy_path: Optional[Path] = None,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Sample for `seconds`. With mode "auto" the charger state is read on EVERY sample, so a plug or unplug during the run is
    judged against the right policy (each stretch against its own mode's targets) and its settle time is reported."""
    import power_policy
    start_mode = detect_mode(sysroot)
    if mode == "auto" and start_mode is None:
        return {"verdict": "UNAVAILABLE", "reason": "cannot tell whether the charger is connected", "mode": None}
    targets = {m: power_policy.target(m, policy_path) for m in ("ac", "bat")}
    samples: List[Dict[str, Optional[str]]] = []
    begin = clock()
    while True:
        reading = sample_once(sysroot)
        reading["_mode"] = detect_mode(sysroot) if mode == "auto" else mode
        samples.append(reading)
        if clock() - begin >= seconds:
            break
        sleep(interval)
    out: Dict[str, Any] = {"samples": len(samples), "interval_s": interval, "seconds": seconds,
                           "host": platform.node().split(".")[0], "mode_detected": start_mode,
                           "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "segments": {}}
    order: List[str] = []
    for sample in samples:
        if sample["_mode"] in ("ac", "bat") and sample["_mode"] not in order:
            order.append(sample["_mode"])
    if not order:
        return {"verdict": "UNAVAILABLE", "reason": "charger state unreadable during the run", "mode": None}
    for m in order:
        part = [s for s in samples if s["_mode"] == m]
        out["segments"][m] = analyze(part, targets[m], interval)
    verdicts = [seg["verdict"] for seg in out["segments"].values()]
    rank = {"STABLE": 0, "PARTIAL": 1, "DRIFTING": 2, "UNAVAILABLE": 1}
    out["verdict"] = max(verdicts, key=lambda v: rank[v])
    out["mode"] = order[0] if len(order) == 1 else "->".join(order)
    first = out["segments"][order[0]]
    out["knobs"], out["pl1"] = first["knobs"], first["pl1"]          # single-mode runs keep the original shape
    out["transitions"] = settle_report(samples, targets, interval)
    if mode != "auto" and start_mode and start_mode != mode:
        out["warning"] = "mode was forced to %s but the charger state says %s" % (mode, start_mode)
    return out


def render(out: Dict[str, Any]) -> str:
    if "segments" not in out:
        return "POWER STABILITY: UNAVAILABLE (%s)" % out.get("reason", "nothing could be read")
    lines = ["POWER STABILITY on %s over %ss (%d samples): %s" % (out.get("host"), out["seconds"], out["samples"], out["verdict"])]
    for m, seg in out["segments"].items():
        lines.append(" %s mode: %d samples, %s" % (m.upper(), seg["samples"], seg["verdict"]))
        for knob in KNOBS:
            k = seg["knobs"][knob]
            if k["status"] == "unavailable":
                lines.append("   %-28s unavailable (not verified)" % LABELS[knob])
            else:
                lines.append("   %-28s %5.1f%% in policy (want %s)  %s" % (LABELS[knob], k["in_policy_percent"], k["want"], k["status"].upper()))
        p1 = seg["pl1"]
        lines.append("   PL1 flips: %d, longest out-of-policy stretch: %ss, highest seen: %s W" % (
            p1["flips"], p1["longest_out_of_policy_s"], p1["max_watts_seen"]))
    for t in out.get("transitions", []):
        lines.append(" at %ss the charger went %s -> %s: settled %s; energy preference passed through %s" % (
            t["at_s"], t["from"].upper(), t["to"].upper(),
            "after %ss" % t["settled_after_s"] if t["settled_after_s"] is not None else "NEVER within the run",
            ", ".join(t["epp_values_passed_through"]) or "n/a"))
    if out.get("warning"):
        lines.append(" WARNING: " + out["warning"])
    return "\n".join(lines)


def write_receipt(out: Dict[str, Any], root: Path) -> Path:
    """Atomic write of data/health/POWER_STABILITY.<host>.json (temp file then replace)."""
    directory = root / "data" / "health"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("POWER_STABILITY.%s.json" % out.get("host", "host"))
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, default=30.0, help="how long to sample (default 30)")
    ap.add_argument("--interval", type=float, default=0.1, help="seconds between samples (default 0.1, minimum 0.02)")
    ap.add_argument("--mode", choices=("auto", "ac", "bat"), default="auto")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--receipt", action="store_true", help="also write data/health/POWER_STABILITY.<host>.json")
    a = ap.parse_args(argv)
    seconds = min(max(a.seconds, 1.0), 600.0)
    interval = min(max(a.interval, 0.02), 5.0)
    out = run(seconds, interval, a.mode)
    print(json.dumps(out, indent=1) if a.json else render(out))
    if a.receipt and "knobs" in out:
        print("receipt:", write_receipt(out, HERE.parent))
    verdict = out.get("verdict")
    return 0 if verdict == "STABLE" else 2 if verdict == "UNAVAILABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())

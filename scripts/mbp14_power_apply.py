#!/usr/bin/env python3
"""
PROGRAM:       mbp14_power_apply.py   (runs as ROOT from mbp14-power-policy.service; installed to /usr/local/libexec/mbp14/mbp14-power-policy)
AUTHOR:    {{AUTHOR}}
LAST MODIFIED: 2026-11-19
PURPOSE:       Apply the power and fan policy in data/power/policy.json (installed copy: /etc/mbp14/power-policy.json):
               EPP, turbo, max frequency, RAPL PL1/PL2, and the fan MINIMUM, then READ EVERYTHING BACK and report
               honestly what did and did not take. Never claims success it did not read back.

TRIGGERS (installed by scripts/install_power_root_unit.sh): boot, AC plug/unplug (udev), resume from
suspend/hibernate, and a 5-minute timer so drift heals without manual intervention.

FAN: only the applesmc MINIMUM (fan1_min) is set. Speed above that floor stays automatic (thermald + firmware);
no manual fan speed is ever written, so the fan can always cool the machine.

EXIT CODE: 0 when every target reads back correctly (or is unavailable on this hardware and says so), 1 otherwise.
Usage: mbp14-power-policy [auto|ac|bat] [--policy FILE] [--sys ROOT]   (--sys is for tests)
       mbp14-power-policy hold [--interval SECONDS] [--battery-interval SECONDS]   (long-running; used by mbp14-power-policy-hold.service)

HOLD (2026-11-19 evidence): sampled every 5 s, the package long-term limit (PL1) flipped between 100 W and 95 W
continuously, seconds after the one-shot applier had set 15 W, while thermald (the obvious suspect) had used 1 s of CPU in
4 hours and made no wakeups. On our unit the laptop's firmware/EC appeared to re-write MSR_PKG_POWER_LIMIT in a loop (the same behaviour the
`throttled` project exists for), so a one-shot or 5-minute apply cannot hold PL1. `hold` re-asserts ONLY the two RAPL
limits (PL1/PL2) every few seconds, writing only when the value differs, re-reading the mode (AC or battery) and the
policy each pass. It never touches fans, EPP or frequency and never disables any thermal protection.

CHANGE HISTORY:
  2026-11-19  {{AUTHOR}}  Added `hold` (firmware keeps overwriting PL1; see HOLD above).
  2026-11-19  {{AUTHOR}}  Created. Complements (does not replace) /usr/local/libexec/mbp14/mbp14-power-apply, which keeps
                       doing PCI/USB/audio runtime power management.
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import power_policy  # noqa: E402  (shared reader; installed beside this script)


def _read(p: Path):
    """Read a sysfs file as stripped text, or None."""
    try:
        return p.read_text().strip()
    except OSError:
        return None


def _write(p: Path, value) -> bool:
    """Write a value to a sysfs file; False when the file is missing or refuses."""
    try:
        p.write_text(str(value))
        return True
    except OSError:
        return False


def detect_mode(sysr: Path) -> str:
    """'ac' when any mains supply is online, else 'bat'."""
    for f in glob.glob(str(sysr / "class/power_supply/*/online")):
        t = _read(Path(f))
        type_file = Path(f).parent / "type"
        if _read(type_file) in (None, "Mains") and t == "1":
            return "ac"
    return "bat"


def apply(mode: str, policy: dict, sysr: Path):
    """Write each target, read it back, and return a list of (name, wanted, got, ok, note)."""
    t = policy["bat" if mode == "bat" else "ac"]
    rows = []
    cpu = sysr / "devices/system/cpu"
    rapl = sysr / "class/powercap/intel-rapl:0"
    hwmon_fan = None
    for h in glob.glob(str(sysr / "class/hwmon/hwmon*")):
        if _read(Path(h) / "name") == "applesmc":
            hwmon_fan = Path(h)

    def check(name, path, wanted, note=""):
        """Write wanted to path, then read back and compare as strings."""
        if not path.exists():
            rows.append((name, wanted, None, True, "not present on this hardware"))
            return
        _write(path, wanted)
        got = _read(path)
        rows.append((name, wanted, got, str(got) == str(wanted), note))

    check("no_turbo", cpu / "intel_pstate/no_turbo", t["no_turbo"])
    for pol in sorted(glob.glob(str(cpu / "cpu[0-9]*/cpufreq"))):
        pol = Path(pol)
        _write(pol / "scaling_governor", "powersave")
        check(f"epp:{pol.parent.name}", pol / "energy_performance_preference", t["epp"])
        check(f"max_freq:{pol.parent.name}", pol / "scaling_max_freq", t["max_freq_khz"])
    check("rapl_pl1", rapl / "constraint_0_power_limit_uw", t["pl1_uw"],
          "if this does not read back, thermald or firmware owns the limit")
    check("rapl_pl2", rapl / "constraint_1_power_limit_uw", t["pl2_uw"])
    if hwmon_fan:
        check("fan_min_rpm", hwmon_fan / "fan1_min", t["fan_min_rpm"], "floor only; speed above it stays automatic")
    else:
        rows.append(("fan_min_rpm", t["fan_min_rpm"], None, True, "applesmc fan not present"))
    return rows


def hold_once(policy: dict, sysr: Path, mode: str):
    """One hold pass: re-assert RAPL PL1/PL2 when they differ from the target for this mode. Returns the names corrected.
    Writes only on difference so a quiet system causes no sysfs traffic."""
    t = policy["bat" if mode == "bat" else "ac"]
    rapl = sysr / "class/powercap/intel-rapl:0"
    fixed = []
    for name, f, want in (("rapl_pl1", "constraint_0_power_limit_uw", t["pl1_uw"]),
                          ("rapl_pl2", "constraint_1_power_limit_uw", t["pl2_uw"])):
        path = rapl / f
        if _read(path) is None:
            continue  # not present on this hardware: nothing to hold
        if str(_read(path)) != str(want):
            _write(path, want)
            fixed.append(name)
    return fixed


MIN_HOLD_INTERVAL = 0.05   # never spin: 20 passes a second is the floor on how fast the loop may run


def hold(interval: float, sysr: Path, policy_path=None, stop=lambda: False, battery_interval=None) -> int:
    """Loop forever (until stop() is true): hold RAPL limits against firmware rewrites. Logs only changes in state
    (first correction after a quiet period, and write refusals) so the journal is not flooded.

    interval = seconds between passes on AC; battery_interval = seconds between passes on battery (default: same as interval).
    Why two: measured 2026-11-20 on this laptop, under load on AC the firmware rewrites PL1 about once a second
    (38 flips in 40 s; a 1 s loop kept it in policy only 78.8% of the time), while on battery a 1 s loop held it 100% of the
    time (no flips in 5 minutes). So AC runs fast and battery stays gentle, because a wakeup costs battery."""
    import time
    quiet, refused = True, False
    while not stop():
        policy = power_policy.load(Path(policy_path) if policy_path else None)
        mode = detect_mode(sysr)
        fixed = hold_once(policy, sysr, mode)
        if fixed and quiet:
            print(f"mbp14-power-policy hold: firmware changed {', '.join(fixed)}; re-asserted for mode={mode}", flush=True)
        quiet = not fixed
        still_bad = [n for n in fixed if _read(sysr / "class/powercap/intel-rapl:0" /
                     ("constraint_0_power_limit_uw" if n == "rapl_pl1" else "constraint_1_power_limit_uw"))
                     != str(policy["bat" if mode == "bat" else "ac"]["pl1_uw" if n == "rapl_pl1" else "pl2_uw"])]
        if still_bad and not refused:
            print(f"mbp14-power-policy hold: {', '.join(still_bad)} did not read back after writing (something else owns it)", flush=True)
        refused = bool(still_bad)
        time.sleep(battery_interval if (mode == "bat" and battery_interval is not None) else interval)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply the mbp14 power/fan policy (root).")
    ap.add_argument("mode", nargs="?", default="auto", choices=["auto", "ac", "bat", "hold"])
    ap.add_argument("--policy")
    ap.add_argument("--interval", type=float, default=0.2, help="hold mode: seconds between passes on AC (min 0.05)")
    ap.add_argument("--battery-interval", type=float, default=1.0, help="hold mode: seconds between passes on battery (min 0.05)")
    ap.add_argument("--sys", default="/sys")
    a = ap.parse_args(argv)
    sysr = Path(a.sys)
    if a.mode == "hold":
        return hold(max(MIN_HOLD_INTERVAL, a.interval), sysr, a.policy,
                    battery_interval=max(MIN_HOLD_INTERVAL, a.battery_interval))
    mode = detect_mode(sysr) if a.mode == "auto" else a.mode
    policy = power_policy.load(Path(a.policy) if a.policy else None)
    rows = apply(mode, policy, sysr)
    bad = [r for r in rows if not r[3]]
    print(f"mbp14-power-policy: mode={mode}")
    for name, want, got, ok, note in rows:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}: wanted {want}, read {got}" + (f" ({note})" if note else ""))
    print("mbp14-power-policy: " + ("all targets applied and read back" if not bad else f"{len(bad)} target(s) did not take; see FAIL lines"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

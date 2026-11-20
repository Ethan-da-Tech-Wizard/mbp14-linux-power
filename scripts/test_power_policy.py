#!/usr/bin/env python3
"""
PROGRAM:       test_power_policy.py
AUTHOR:    {{AUTHOR}}
LAST MODIFIED: 2026-11-19
PURPOSE:       Proves (1) the shared policy reader clamps and falls back safely, (2) the root applier writes and
               READS BACK every target on a fake sysfs and reports failures honestly, and (3) the hold loop restores the CPU power limits.
Run from the repo root: python3 -m unittest scripts.test_power_policy
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import power_policy as pp
from scripts import mbp14_power_apply as apply_mod


def _fake_sys(root: Path, ac: bool, fan: bool = True) -> Path:
    """Build a minimal fake /sys: 2 CPUs, pstate, RAPL, mains, applesmc fan."""
    def w(rel, text):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    w("class/power_supply/ADP1/online", "1\n" if ac else "0\n")
    w("class/power_supply/ADP1/type", "Mains\n")
    w("devices/system/cpu/intel_pstate/no_turbo", "0")
    for n in (0, 1):
        base = f"devices/system/cpu/cpu{n}/cpufreq/"
        w(base + "scaling_governor", "powersave")
        w(base + "energy_performance_preference", "balance_performance")
        w(base + "scaling_max_freq", "3600000")
    w("class/powercap/intel-rapl:0/constraint_0_power_limit_uw", "50000000")
    w("class/powercap/intel-rapl:0/constraint_1_power_limit_uw", "25000000")
    if fan:
        w("class/hwmon/hwmon4/name", "applesmc\n")
        w("class/hwmon/hwmon4/fan1_min", "1200")
    return root


class PolicyReaderTests(unittest.TestCase):
    def test_battery_is_ten_hour_design_and_ac_has_fan_floor(self):
        bat, ac = pp.target("bat"), pp.target("ac")
        self.assertEqual(bat["epp"], "power")          # {{OWNER}} 2026-11-19: 10-hour design on battery
        self.assertEqual(bat["no_turbo"], 1)
        self.assertEqual(bat["fan_min_rpm"], 1200)      # fan allowed, no forced floor on battery
        self.assertGreater(ac["fan_min_rpm"], bat["fan_min_rpm"])  # plugged in: fan floor raised
        self.assertEqual(ac["no_turbo"], 0)

    def test_values_are_clamped_and_bad_values_fall_back(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "p.json"
            f.write_text(json.dumps({"modes": {"ac": {"pl1_uw": {"value": 999000000}, "fan_min_rpm": {"value": 9999},
                                                       "epp": {"value": "rm -rf"}, "max_freq_khz": {"value": "abc"}}}}))
            t = pp.target("ac", f)
        self.assertEqual(t["pl1_uw"], 28000000)        # clamped to the hard limit
        self.assertEqual(t["fan_min_rpm"], 3500)
        self.assertEqual(t["epp"], pp.DEFAULTS["ac"]["epp"])  # not in the allowed list -> default
        self.assertEqual(t["max_freq_khz"], pp.DEFAULTS["ac"]["max_freq_khz"])
        self.assertEqual(t["fan_policy"], "auto")       # manual fan speeds are never possible

    def test_missing_or_corrupt_file_uses_defaults_not_no_limits(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            bad.write_text("{not json")
            self.assertEqual(pp.target("bat", bad), pp.DEFAULTS["bat"])
            self.assertEqual(pp.target("bat", Path(d) / "missing.json"), pp.DEFAULTS["bat"])

    def test_repo_policy_file_loads_and_matches_defaults(self):
        self.assertEqual(pp.load(pp.REPO_POLICY), pp.load(Path("/nonexistent/none.json")))


class ApplierTests(unittest.TestCase):
    def test_applies_and_reads_back_battery(self):
        with tempfile.TemporaryDirectory() as d:
            sysr = _fake_sys(Path(d), ac=False)
            rows = apply_mod.apply("bat", pp.load(pp.REPO_POLICY), sysr)
            self.assertTrue(all(r[3] for r in rows), rows)
            self.assertEqual((sysr / "devices/system/cpu/cpu0/cpufreq/energy_performance_preference").read_text(), "power")
            self.assertEqual((sysr / "devices/system/cpu/intel_pstate/no_turbo").read_text(), "1")
            self.assertEqual((sysr / "class/powercap/intel-rapl:0/constraint_0_power_limit_uw").read_text(), "9000000")
            self.assertEqual((sysr / "class/hwmon/hwmon4/fan1_min").read_text(), "1200")

    def test_ac_raises_the_fan_floor(self):
        with tempfile.TemporaryDirectory() as d:
            sysr = _fake_sys(Path(d), ac=True)
            self.assertEqual(apply_mod.detect_mode(sysr), "ac")
            apply_mod.apply("ac", pp.load(pp.REPO_POLICY), sysr)
            self.assertEqual((sysr / "class/hwmon/hwmon4/fan1_min").read_text(), "2400")

    def test_missing_fan_is_reported_not_failed(self):
        with tempfile.TemporaryDirectory() as d:
            sysr = _fake_sys(Path(d), ac=True, fan=False)
            rows = apply_mod.apply("ac", pp.load(pp.REPO_POLICY), sysr)
            fan = [r for r in rows if r[0] == "fan_min_rpm"][0]
            self.assertIn("not present", fan[4])

    def test_unwritable_target_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as d:
            sysr = _fake_sys(Path(d), ac=True)
            rapl = sysr / "class/powercap/intel-rapl:0/constraint_0_power_limit_uw"
            rapl.unlink()
            rapl.mkdir()  # a directory: write fails, read-back is None, so it must be a FAIL, not a silent pass
            rows = apply_mod.apply("ac", pp.load(pp.REPO_POLICY), sysr)
            self.assertFalse([r for r in rows if r[0] == "rapl_pl1"][0][3])


class HoldTests(unittest.TestCase):
    """The firmware re-writes PL1 every few seconds; `hold` must put it back, write only on difference, and touch nothing else."""

    def test_hold_restores_pl1_after_firmware_rewrites_it(self):
        with tempfile.TemporaryDirectory() as d:
            sysr = _fake_sys(Path(d), ac=True)
            pol = pp.load(pp.REPO_POLICY)
            apply_mod.apply("ac", pol, sysr)
            pl1 = sysr / "class/powercap/intel-rapl:0/constraint_0_power_limit_uw"
            self.assertEqual(pl1.read_text(), str(pol["ac"]["pl1_uw"]))
            pl1.write_text("100000000")                      # the firmware rewrite seen on the real machine
            self.assertEqual(apply_mod.hold_once(pol, sysr, "ac"), ["rapl_pl1"])
            self.assertEqual(pl1.read_text(), str(pol["ac"]["pl1_uw"]))

    def test_hold_is_quiet_when_nothing_changed_and_ignores_other_knobs(self):
        with tempfile.TemporaryDirectory() as d:
            sysr = _fake_sys(Path(d), ac=True)
            pol = pp.load(pp.REPO_POLICY)
            apply_mod.apply("ac", pol, sysr)
            epp = sysr / "devices/system/cpu/cpu0/cpufreq/energy_performance_preference"
            epp.write_text("performance")                     # not held: hold only owns the RAPL limits
            fan = sysr / "class/hwmon/hwmon4/fan1_min"
            before = fan.read_text()
            self.assertEqual(apply_mod.hold_once(pol, sysr, "ac"), [])
            self.assertEqual(epp.read_text(), "performance")
            self.assertEqual(fan.read_text(), before)

    def test_hold_follows_the_mode_and_survives_missing_rapl(self):
        with tempfile.TemporaryDirectory() as d:
            sysr = _fake_sys(Path(d), ac=False)
            pol = pp.load(pp.REPO_POLICY)
            apply_mod.hold_once(pol, sysr, "bat")
            self.assertEqual((sysr / "class/powercap/intel-rapl:0/constraint_0_power_limit_uw").read_text(), "9000000")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(apply_mod.hold_once(pp.load(pp.REPO_POLICY), Path(d), "ac"), [])  # no RAPL: nothing to do

    def test_hold_loop_runs_and_stops(self):
        with tempfile.TemporaryDirectory() as d:
            sysr = _fake_sys(Path(d), ac=True)
            calls = []
            def stop():
                calls.append(1)
                return len(calls) > 2
            self.assertEqual(apply_mod.hold(1.0 if False else 0.0, sysr, stop=stop), 0)

    def _sleeps_during_hold(self, ac, interval, battery_interval, passes=3):
        """Run `hold` for a few passes with time.sleep replaced; return the sleep lengths it asked for."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            sysr = _fake_sys(Path(d), ac=ac)
            calls, slept = [], []

            def stop():
                calls.append(1)
                return len(calls) > passes

            with mock.patch("time.sleep", lambda secs: slept.append(secs)):
                apply_mod.hold(interval, sysr, stop=stop, battery_interval=battery_interval)
        return slept

    def test_hold_is_fast_on_ac_and_gentle_on_battery(self):
        """2026-11-20: under load on AC the firmware rewrites PL1 about once a second, so AC needs a fast loop; on battery a
        1 s loop held it 100% for 5 minutes and every wakeup costs power."""
        self.assertEqual(self._sleeps_during_hold(True, 0.2, 1.0), [0.2, 0.2, 0.2])
        self.assertEqual(self._sleeps_during_hold(False, 0.2, 1.0), [1.0, 1.0, 1.0])

    def test_hold_without_battery_interval_behaves_as_before(self):
        self.assertEqual(self._sleeps_during_hold(False, 0.5, None), [0.5, 0.5, 0.5])

    def test_cli_defaults_and_floor_never_allow_a_busy_loop(self):
        from unittest import mock
        seen = {}

        def fake_hold(interval, sysr, policy_path=None, stop=lambda: False, battery_interval=None):
            seen.update(interval=interval, battery_interval=battery_interval)
            return 0

        with mock.patch.object(apply_mod, "hold", fake_hold):
            self.assertEqual(apply_mod.main(["hold"]), 0)
            self.assertEqual(seen, {"interval": 0.2, "battery_interval": 1.0})        # defaults
            apply_mod.main(["hold", "--interval", "0", "--battery-interval", "-5"])
            self.assertEqual(seen, {"interval": apply_mod.MIN_HOLD_INTERVAL, "battery_interval": apply_mod.MIN_HOLD_INTERVAL})
        self.assertGreaterEqual(apply_mod.MIN_HOLD_INTERVAL, 0.05)

    def test_repo_unit_runs_the_fast_ac_slow_battery_loop(self):
        unit = (Path(__file__).resolve().parent.parent / "data/power/units/mbp14-power-policy-hold.service").read_text()
        self.assertIn("hold --interval 0.2 --battery-interval 1", unit)


if __name__ == "__main__":
    unittest.main()

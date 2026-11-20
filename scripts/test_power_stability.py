# PROGRAM:       scripts/test_power_stability.py - tests for scripts/power_stability.py (read-only power policy sampler)
# NOTE:          Uses a fake sysfs tree under a temp folder; never touches the real hardware or the real receipts.
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import power_stability as ps  # noqa: E402

POLICY = {"schema": 1, "modes": {
    "ac": {"epp": {"value": "balance_performance"}, "no_turbo": {"value": 0}, "max_freq_khz": {"value": 3600000},
           "pl1_uw": {"value": 15000000}, "pl2_uw": {"value": 25000000}, "fan_min_rpm": {"value": 2400}},
    "bat": {"epp": {"value": "power"}, "no_turbo": {"value": 1}, "max_freq_khz": {"value": 2300000},
            "pl1_uw": {"value": 9000000}, "pl2_uw": {"value": 13000000}, "fan_min_rpm": {"value": 1200}}}}


class FakeSys:
    """A throwaway sysfs tree with the files power_stability reads."""

    def __init__(self, tmp, online="1", pl1="15000000", epp="balance_performance", ncpu=2):
        self.root = Path(tmp) / "sysroot"
        self.policy = Path(tmp) / "policy.json"
        self.policy.write_text(json.dumps(POLICY))
        self.put("sys/class/power_supply/ADP1/online", online)
        self.put("sys/devices/system/cpu/intel_pstate/no_turbo", "0")
        for i in range(ncpu):
            self.put("sys/devices/system/cpu/cpu%d/cpufreq/energy_performance_preference" % i, epp)
            self.put("sys/devices/system/cpu/cpu%d/cpufreq/scaling_max_freq" % i, "3600000")
        self.put("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw", pl1)
        self.put("sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw", "25000000")
        self.put("sys/devices/platform/applesmc.768/fan1_min", "2400")

    def put(self, rel, value):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value) + "\n")

    def tree(self):
        return {str(p.relative_to(self.root)): p.read_text() for p in sorted(self.root.rglob("*")) if p.is_file()}


class T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fake = FakeSys(self.td.name)

    def target(self, mode="ac"):
        import power_policy
        return power_policy.target(mode, self.fake.policy)

    def test_detect_mode(self):
        self.assertEqual(ps.detect_mode(str(self.fake.root)), "ac")
        self.fake.put("sys/class/power_supply/ADP1/online", "0")
        self.assertEqual(ps.detect_mode(str(self.fake.root)), "bat")
        self.assertIsNone(ps.detect_mode(str(Path(self.td.name) / "empty")))

    def test_sample_reads_every_knob(self):
        s = ps.sample_once(str(self.fake.root))
        self.assertEqual(s["epp"], "balance_performance")
        self.assertEqual(s["no_turbo"], "0")
        self.assertEqual(s["pl1_uw"], "15000000")
        self.assertEqual(s["pl2_uw"], "25000000")
        self.assertEqual(s["fan_min_rpm"], "2400")

    def test_cores_disagreeing_on_epp_fail(self):
        self.fake.put("sys/devices/system/cpu/cpu1/cpufreq/energy_performance_preference", "power")
        s = ps.sample_once(str(self.fake.root))
        self.assertEqual(s["epp"], "balance_performance,power")
        out = ps.analyze([s, s], self.target(), 0.1)
        self.assertEqual(out["knobs"]["epp"]["status"], "drifting")
        self.assertEqual(out["verdict"], "DRIFTING")

    def test_all_in_policy_is_stable(self):
        s = ps.sample_once(str(self.fake.root))
        out = ps.analyze([s] * 50, self.target(), 0.1)
        self.assertEqual(out["verdict"], "STABLE")
        self.assertEqual(out["pl1"], {"flips": 0, "longest_out_of_policy_s": 0.0, "max_watts_seen": 15.0})

    def test_pl1_flapping_is_measured_not_hidden(self):
        base = ps.sample_once(str(self.fake.root))
        pattern = ["15000000"] * 6 + ["95000000"] * 3 + ["15000000"] * 5 + ["100000000"] * 2
        samples = [dict(base, pl1_uw=v) for v in pattern]
        out = ps.analyze(samples, self.target(), 0.5)
        self.assertEqual(out["verdict"], "DRIFTING")
        k = out["knobs"]["pl1_uw"]
        self.assertEqual(k["in_policy_percent"], round(100 * 11 / 16, 1))
        self.assertEqual(out["pl1"]["flips"], 3)
        self.assertEqual(out["pl1"]["longest_out_of_policy_s"], 1.5)   # 3 samples x 0.5 s
        self.assertEqual(out["pl1"]["max_watts_seen"], 100.0)

    def test_a_single_good_snapshot_would_have_said_stable(self):
        """The reason this tool exists: one snapshot passes even though the limit is out of policy a third of the time."""
        base = ps.sample_once(str(self.fake.root))
        flapping = [dict(base, pl1_uw=v) for v in (["15000000"] * 2 + ["95000000"]) * 10]
        self.assertEqual(ps.analyze(flapping[:1], self.target(), 0.1)["verdict"], "STABLE")
        self.assertEqual(ps.analyze(flapping, self.target(), 0.1)["verdict"], "DRIFTING")

    def test_unreadable_values_are_not_counted_as_ok(self):
        (self.fake.root / "sys/devices/platform/applesmc.768/fan1_min").unlink()
        s = ps.sample_once(str(self.fake.root))
        self.assertIsNone(s["fan_min_rpm"])
        out = ps.analyze([s] * 5, self.target(), 0.1)
        self.assertEqual(out["knobs"]["fan_min_rpm"]["status"], "unavailable")
        self.assertEqual(out["verdict"], "PARTIAL")
        empty = {k: None for k in ps.KNOBS}
        self.assertEqual(ps.analyze([empty] * 3, self.target(), 0.1)["verdict"], "UNAVAILABLE")

    def test_run_samples_over_time_and_never_writes(self):
        before = self.fake.tree()
        ticks = {"n": 0}
        values = ["15000000", "95000000", "15000000", "15000000"]
        clock_now = {"t": 0.0}

        def sleep(seconds):
            ticks["n"] += 1
            clock_now["t"] += seconds
            # simulate the firmware: it changes PL1 between samples (this write is the TEST's, not the tool's)
            self.fake.put("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw", values[ticks["n"] % len(values)])

        out = ps.run(0.5, 0.1, "auto", str(self.fake.root), self.fake.policy, sleep=sleep, clock=lambda: clock_now["t"])
        self.assertEqual(out["mode"], "ac")
        self.assertGreaterEqual(out["samples"], 5)
        self.assertEqual(out["verdict"], "DRIFTING")
        self.assertGreater(out["pl1"]["flips"], 0)
        # everything except PL1 (which the simulated firmware moved) is byte-identical: the tool wrote nothing
        after = self.fake.tree()
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual(changed, {"sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"})

    def test_battery_mode_uses_the_battery_targets(self):
        self.fake.put("sys/class/power_supply/ADP1/online", "0")
        out = ps.run(0.2, 0.1, "auto", str(self.fake.root), self.fake.policy, sleep=lambda s: None,
                     clock=iter([0.0, 0.3]).__next__)
        self.assertEqual(out["mode"], "bat")
        self.assertEqual(out["knobs"]["pl1_uw"]["want"], "9000000")
        self.assertEqual(out["knobs"]["epp"]["want"], "power")
        self.assertEqual(out["verdict"], "DRIFTING")   # the fake machine is still configured for AC values

    def test_forced_mode_that_disagrees_with_the_charger_warns(self):
        out = ps.run(0.2, 0.1, "bat", str(self.fake.root), self.fake.policy, sleep=lambda s: None,
                     clock=iter([0.0, 0.3]).__next__)
        self.assertIn("forced", out["warning"])

    def test_no_charger_info_is_unavailable_not_ok(self):
        out = ps.run(0.2, 0.1, "auto", str(Path(self.td.name) / "empty"), self.fake.policy, sleep=lambda s: None)
        self.assertEqual(out["verdict"], "UNAVAILABLE")

    def real_result(self, **kw):
        return ps.run(0.2, 0.1, "auto", str(self.fake.root), self.fake.policy, sleep=lambda s: None,
                      clock=iter([0.0, 0.1, 0.3]).__next__, **kw)

    def test_render_and_exit_codes(self):
        out = self.real_result()
        text = ps.render(out)
        self.assertIn("AC mode", text); self.assertIn("100.0% in policy", text)
        for verdict, code in (("STABLE", 0), ("DRIFTING", 1), ("PARTIAL", 1), ("UNAVAILABLE", 2)):
            with mock.patch.object(ps, "run", return_value=dict(out, verdict=verdict)):
                with mock.patch("builtins.print"):
                    self.assertEqual(ps.main(["--seconds", "1"]), code, verdict)
        self.assertIn("UNAVAILABLE", ps.render({"verdict": "UNAVAILABLE", "reason": "x"}))

    def test_unplug_during_the_run_is_judged_per_mode_and_settle_time_reported(self):
        """Regression for 2026-11-20: the first version decided AC/battery once at the start, so an unplug mid-run made a
        correctly held battery state print as 'DRIFTING'. The old writer's value must also be visible as it passes through."""
        fake = self.fake
        steps = {"n": 0}
        clock = {"t": 0.0}

        def sleep(seconds):
            steps["n"] += 1
            clock["t"] += seconds
            n = steps["n"]
            if n == 3:      # unplug: the old writer lands first (balance_power), AC-only values change
                fake.put("sys/class/power_supply/ADP1/online", "0")
                for c in (0, 1):
                    fake.put("sys/devices/system/cpu/cpu%d/cpufreq/energy_performance_preference" % c, "balance_power")
                fake.put("sys/devices/system/cpu/intel_pstate/no_turbo", "1")
            if n == 5:      # the policy writer finishes: everything at the battery targets
                for c in (0, 1):
                    fake.put("sys/devices/system/cpu/cpu%d/cpufreq/energy_performance_preference" % c, "power")
                    fake.put("sys/devices/system/cpu/cpu%d/cpufreq/scaling_max_freq" % c, "2300000")
                fake.put("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw", "9000000")
                fake.put("sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw", "13000000")
                fake.put("sys/devices/platform/applesmc.768/fan1_min", "1200")

        out = ps.run(2.0, 0.1, "auto", str(fake.root), fake.policy, sleep=sleep, clock=lambda: clock["t"])
        self.assertEqual(out["mode"], "ac->bat")
        self.assertEqual(set(out["segments"]), {"ac", "bat"})
        self.assertEqual(out["segments"]["ac"]["verdict"], "STABLE")       # before the unplug AC was exactly right
        bat = out["segments"]["bat"]
        self.assertGreater(bat["knobs"]["epp"]["in_policy_percent"], 50.0)  # judged against battery targets, not AC
        self.assertIn("power", bat["knobs"]["epp"]["distinct_values"])
        t = out["transitions"]
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["from"], t[0]["to"]), ("ac", "bat"))
        self.assertIsNotNone(t[0]["settled_after_s"])
        self.assertGreater(t[0]["settled_after_s"], 0)                      # it did not settle instantly
        self.assertEqual(t[0]["epp_values_passed_through"][:2], ["balance_power", "power"])  # the old writer's value shows
        text = ps.render(out)
        self.assertIn("AC -> BAT", text); self.assertIn("balance_power", text)

    def test_a_transition_that_never_settles_says_so(self):
        fake = self.fake
        calls = {"n": 0}
        clock = {"t": 0.0}

        def sleep(seconds):
            calls["n"] += 1
            clock["t"] += seconds
            if calls["n"] == 2:
                fake.put("sys/class/power_supply/ADP1/online", "0")   # unplugged, but nothing else ever changes

        out = ps.run(1.0, 0.1, "auto", str(fake.root), fake.policy, sleep=sleep, clock=lambda: clock["t"])
        self.assertIsNone(out["transitions"][0]["settled_after_s"])
        self.assertEqual(out["segments"]["bat"]["verdict"], "DRIFTING")
        self.assertIn("NEVER", ps.render(out))
        self.assertEqual(out["verdict"], "DRIFTING")

    def test_receipt_is_atomic_and_per_host(self):
        out = {"host": "boxy", "verdict": "STABLE"}
        path = ps.write_receipt(out, Path(self.td.name))
        self.assertEqual(path.name, "POWER_STABILITY.boxy.json")
        self.assertEqual(json.loads(path.read_text())["verdict"], "STABLE")
        self.assertEqual([p.name for p in path.parent.iterdir()], ["POWER_STABILITY.boxy.json"])  # no .tmp left

    def test_source_has_no_sysfs_write_path(self):
        src = (Path(__file__).resolve().parent / "power_stability.py").read_text()
        writes = [line for line in src.splitlines() if "write_text(" in line or "open(" in line and ("'w'" in line or '"w"' in line)]
        self.assertEqual(len(writes), 1)          # only the receipt temp file
        self.assertIn("tmp.write_text", writes[0])


if __name__ == "__main__":
    unittest.main()

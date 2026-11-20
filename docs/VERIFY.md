# Verify: how to check each piece after installing

Nothing here needs root except where `sudo` is shown. Everything is a read. If a check fails, see [CAVEATS.md](CAVEATS.md). This was tried on one unit of the 2017 13-inch two-port laptop whose DMI product_name ends in `BookPro14,1` (`cat /sys/class/dmi/id/product_name`).

## 1. Touchpad and keyboard

```
libinput list-devices                      # find "Apple SPI Touchpad" and "Apple SPI Keyboard"; note the event node
libinput quirks list /dev/input/eventN     # N = the touchpad's node; package libinput-utils
modinfo applespi | grep -E '^parm'         # fnmode, fnremap, iso_layout, touchpad_dimensions (no "debug")
journalctl -k -b | grep -i -E "pxa2xx-spi|Apple SPI"
```

- `quirks list` should show the section names from `/etc/libinput/local-overrides.quirks` ("Apple SPI Touchpad (14,1)" and the keyboard one) next to the shipped Apple quirk. If it prints nothing or an error, the file has a syntax error: libinput then disables ALL quirks.
- `list-devices` for the touchpad: look at "Disable-w-typing". On our unit it said enabled with the override. That the random pastes actually stopped is NOT confirmed (see CAVEATS).
- The dmesg line should name "Apple SPI Touchpad" under `pxa2xx-spi.3`.

PCI runtime power for the four functions (expect `on`):

```
for d in 0000:00:1e.0 0000:00:1e.3 0000:00:15.0 0000:00:15.2; do
  echo "$d $(cat /sys/bus/pci/devices/$d/power/control)"; done
lspci -nn -s 00:1e.3
```

Most other PCI devices should read `auto` (the rule applies when a device is added, and some devices have no such file) (set by `90-mbp14-runtime-pm.rules`; the `99-` file overrides only these four). If the four read `auto`, the udev rules did not apply: `sudo udevadm control --reload && sudo udevadm trigger -s pci`, or reboot.

## 2. The DMA softdep (idma64 before applespi)

```
cat /etc/modprobe.d/mbp14-applespi-dma.conf
lsmod | grep -E 'idma64|spi_pxa2xx|applespi'
journalctl -k -b | grep -i -E "idma64|applespi|pxa2xx"
```

Check that `idma64` is loaded and that its first dmesg line is earlier than the first `applespi` line. If it is not loaded, the softdep did not take effect. This setting is empirical: we found no upstream statement that it fixes lag. Judge by feel and by errors in dmesg; remove the file and reboot to compare.

## 3. Battery files (sysctl and modprobe)

```
sysctl vm.laptop_mode vm.dirty_writeback_centisecs kernel.nmi_watchdog kernel.timer_migration
cat /sys/module/snd_hda_intel/parameters/power_save /sys/module/snd_hda_intel/parameters/power_save_controller
cat /sys/module/nvme_core/parameters/default_ps_max_latency_us
```

Expected: 5, 1500, 0, 0; then `1` and `Y`; then `25000`. The module values appear only after a reboot (or module reload).

## 4. The power policy units

```
systemctl is-active mbp14-power-policy.timer mbp14-power-policy-hold.service
systemctl is-enabled mbp14-power-policy.service mbp14-power-policy-resume.service
systemctl status mbp14-power-policy.service mbp14-power-policy-hold.service
journalctl -u mbp14-power-policy.service -n 20 --no-pager -o cat
journalctl -u mbp14-power-policy-hold.service -n 20 --no-pager -o cat
```

The oneshot service shows "inactive (dead)" between runs; that is normal. The timer and the hold service must be `active`. The applier prints its own read-back, one line per knob:

```
mbp14-power-policy: mode=ac
  ok   <name>: wanted <value>, read <value>
mbp14-power-policy: all targets applied and read back
```

A `FAIL` line means the value was written but did not read back (exit code 1). The hold service logs a line only when it had to re-assert PL1/PL2.

## 5. The stability checker (the main test)

`scripts/power_stability.py` is read-only and needs no root. It samples the live knobs and compares them with `data/power/policy.json` over time, because a single snapshot cannot show a firmware that rewrites a limit every second.

```
python3 scripts/power_stability.py [--seconds 30] [--interval 0.1] [--mode auto|ac|bat] [--json] [--receipt]
```

| Option | Meaning |
|---|---|
| `--seconds` | How long to sample. Default 30, clamped to 1-600. |
| `--interval` | Seconds between samples. Default 0.1, clamped to 0.02-5. |
| `--mode` | `auto` (default) reads the charger on EVERY sample. `ac` / `bat` force one policy and print a warning if the charger disagrees. |
| `--json` | Print the full result as JSON instead of text. |
| `--receipt` | Also write `data/health/POWER_STABILITY.<host>.json` inside the repository. |

### Real sample (10 s, plugged in, idle, one unit; hostname replaced by a placeholder)

```
POWER STABILITY on {{HOSTNAME}} over 10.0s (98 samples): STABLE
 AC mode: 98 samples, STABLE
   energy preference            100.0% in policy (want balance_performance)  OK
   turbo off                    100.0% in policy (want 0)  OK
   max frequency (kHz)          100.0% in policy (want 3600000)  OK
   PL1 sustained limit (uW)     100.0% in policy (want 15000000)  OK
   PL2 burst limit (uW)         100.0% in policy (want 25000000)  OK
   fan minimum (RPM)            100.0% in policy (want 2400)  OK
   PL1 flips: 0, longest out-of-policy stretch: 0.0s, highest seen: 15.0 W
```

Line by line:

1. Header: host, run length, number of samples, overall verdict.
2. `AC mode: ...` one block per mode seen during the run, with that block's own verdict. A run that crosses a plug or unplug has two blocks.
3. Six knob lines: the percent of samples where the live value equalled the policy value ("want" is in the unit shown: kHz, microwatts, RPM). OK means at least 99%; below that it prints DRIFTING; an unreadable file prints `unavailable (not verified)`, never OK.
4. PL1 line: how many times PL1 moved in or out of policy (flips), the longest stretch outside policy, and the highest PL1 seen.
5. After a plug or unplug, an extra line: `at Ns the charger went AC -> BAT: settled after Ss; energy preference passed through ...`. It is the time until every knob except PL1 matched the new mode.

### Verdicts and exit codes

| Verdict | Meaning | Exit |
|---|---|---|
| STABLE | Every knob in policy at least 99% of the time | 0 |
| DRIFTING | At least one knob below 99% | 1 |
| PARTIAL | Nothing wrong where readable, but some knob unreadable | 1 |
| UNAVAILABLE | Nothing readable (or charger state unknown) | 2 |

### AC versus battery

Start the checker with a longer window, then unplug and plug the charger during it:

```
python3 scripts/power_stability.py --seconds 60
```

The mode is detected on each sample, so each stretch is judged against its own policy and the settle time is printed. Use `--mode ac` or `--mode bat` only to force one policy.

### Under load (this heats the laptop)

Warning: this deliberately loads both cores. Watch temperatures, keep the laptop on a hard surface, and stop if it gets uncomfortably hot. Nothing here disables thermal protection.

```
nice -n 19 sh -c 'while :; do :; done' & P1=$!
nice -n 19 sh -c 'while :; do :; done' & P2=$!
python3 scripts/power_stability.py --seconds 40
kill $P1 $P2
```

Measured on our unit with this test (AC, single runs): with a 1 s hold loop PL1 was in policy only 78.8% of the time (38 flips, up to 100 W, package 100 C); with the 0.2 s loop that ships here, 100%, 0 flips, highest 15 W, package ended 91 C. Your unit may differ; that is why the checker exists.

### What a good result looks like, and what DRIFTING means

Good: STABLE, every knob 100% or at least 99%, PL1 highest equal to your PL1. A few 0.2 s blips at idle on AC are expected (we saw 99.0%, 4 blips).

DRIFTING means something rewrote a knob. Do this in order:

1. Note which line drifted. If only PL1, the firmware is probably rewriting it faster than the hold loop re-asserts it; try a smaller hold interval (CONFIGURATION.md).
2. Re-run the applier: `sudo systemctl start mbp14-power-policy.service`, then read its journal for `FAIL`.
3. Check `systemctl is-active mbp14-power-policy-hold.service` and the hold journal.
4. If it is a fan or other knob only, check that the file exists and is writable on your unit (not verified on every unit).
5. Read [CAVEATS.md](CAVEATS.md), then report results as described there.

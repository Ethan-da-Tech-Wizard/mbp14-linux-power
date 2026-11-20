# Configuration

All power numbers live in one file, `data/power/policy.json`, read by both the root applier and the stability checker, so they cannot disagree. The values are the original owner's preferences (battery: a 10-hour design; AC: performance with active cooling), not upstream defaults.

Safety first: scripts run as root, so read them. Change ONE value at a time, re-run the checker and watch temperatures. Bad values can make the laptop hotter or slower. No warranty (GPL-3.0-or-later sections 15-16).

## How to edit and re-apply

```
$EDITOR data/power/policy.json
sudo scripts/install_power_root_unit.sh      # copies it to /etc/mbp14/power-policy.json and runs the applier
python3 scripts/power_stability.py --seconds 30
```

The installed copy in `/etc/mbp14/` is read first, so editing the repository file alone does nothing until you re-run the installer. The installer is safe to run again.

## Clamp limits (cannot be exceeded)

`scripts/power_policy.py` clamps every value, even if the file's own "limits" block is edited: EPP must be one of `power`, `balance_power`, `balance_performance`, `performance`, `default`; max frequency 800000-3600000 kHz; PL1 and PL2 5,000,000-28,000,000 uW (5-28 W); fan minimum 1200-3500 RPM. A bad or missing file falls back to built-in defaults equal to the values below.

## Mode `bat` (on battery)

| Key | Meaning | Limit | Ours | Choosing yours |
|---|---|---|---|---|
| `epp` | CPU energy hint (`energy_performance_preference`) | 5 allowed strings | `power` | `balance_power` feels quicker at some cost to battery |
| `no_turbo` | 1 forbids turbo | 0 or 1 | 1 | 0 gives bursts of speed, more heat and drain |
| `max_freq_khz` | `scaling_max_freq` cap | 800000-3600000 | 2300000 (base clock) | Lower saves power, feels slower |
| `pl1_uw` | Sustained package limit (long term) | 5-28 W | 9000000 | The main battery-life lever |
| `pl2_uw` | Short burst limit | 5-28 W | 13000000 | Keep at or above PL1 |
| `fan_min_rpm` | Fan floor (not a fixed speed) | 1200-3500 | 1200 (firmware minimum) | Raise for cooler/louder |
| `fan_policy` | Descriptive: speed above the floor stays automatic | text | `auto` | Not applied: no manual speed is ever written |

## Mode `ac` (plugged in)

| Key | Meaning | Limit | Ours | Choosing yours |
|---|---|---|---|---|
| `epp` | CPU energy hint | 5 allowed strings | `balance_performance` | `performance` is faster, hotter |
| `no_turbo` | 1 forbids turbo | 0 or 1 | 0 | 1 gives a cooler, quieter, slower laptop |
| `max_freq_khz` | Frequency cap | 800000-3600000 | 3600000 (max turbo per Intel ARK) | Lower to limit heat |
| `pl1_uw` | Sustained limit | 5-28 W | 15000000 (rated TDP per Intel ARK) | Lower is cooler and quieter |
| `pl2_uw` | Burst limit | 5-28 W | 25000000 (our choice; not an Intel-published figure) | Lower reduces short heat spikes |
| `fan_min_rpm` | Fan floor | 1200-3500 | 2400 | Higher keeps temperatures lower, adds noise |
| `fan_policy` | As above | text | `auto` | Not applied |

Each key also has a `why` string; edit it with the value so the file stays honest. Keep `fan_min_rpm` writability in mind: we only know our root script wrote it and read it back on one unit (`ls -l` the `fan1_min` file to check yours).

## How to choose

- Lower PL1/PL2 and no turbo mean less heat, quieter fan, longer battery, less speed. Higher values do the opposite.
- Change one key, run `power_stability.py --seconds 30`, then the loaded test in VERIFY.md, and read temperatures (`sensors`, if installed).
- Battery-life numbers from earlier tests are single runs with about 1 W of noise; measure your own.

## Hold intervals

`data/power/units/mbp14-power-policy-hold.service` runs `... hold --interval 0.2 --battery-interval 1`: PL1 and PL2 are re-asserted every 0.2 s on AC and 1 s on battery. The loop never runs faster than 0.05 s between passes (the applier raises smaller values to 0.05). Edit the `ExecStart` line in the unit file, then `sudo scripts/install_power_root_unit.sh`, which copies the units and restarts the hold service. Faster loops use slightly more CPU time (the unit runs at `Nice=10`).

Other schedule: at boot, after resume, on every plug or unplug (udev rule on the Mains supply) and every 5 minutes (timer).

## Touchpad numbers (`system/libinput/local-overrides.quirks`)

| Key | Ours | Effect |
|---|---|---|
| `AttrSizeHint` | 135x83 | Physical size in mm used for scaling |
| `AttrTouchSizeRange` | 50:30 | Touch size range for press/release detection |
| `AttrPalmSizeThreshold` | 1000 | Touches larger than this count as palms |
| `AttrThumbSizeThreshold` | 800 | Thumb detection threshold |

These are EMPIRICAL values from one unit, not upstream recommendations; the shipped generic Apple quirk uses 104x75, 150:130 and 1600. If palms click or real touches are ignored, change one number, `sudo cp` the file to `/etc/libinput/local-overrides.quirks`, run `libinput quirks list /dev/input/eventN`, and restart your desktop session (log out and in, or reboot). We did not test whether a shorter step, such as `sudo udevadm trigger`, is enough. A syntax error disables all quirks. Format: https://wayland.freedesktop.org/libinput/doc/latest/device-quirks.html

## sysctl and modprobe values

| File | Setting | Value | What it does |
|---|---|---|---|
| sysctl.d | `vm.laptop_mode` | 5 | Batches disk writes to idle the disk longer |
| sysctl.d | `vm.dirty_writeback_centisecs` | 1500 | Flushes dirty pages every 15 s instead of 5 s |
| sysctl.d | `kernel.nmi_watchdog` | 0 | Disables the NMI watchdog (fewer wakeups) |
| sysctl.d | `kernel.timer_migration` | 0 | Keeps timers on their CPU |
| modprobe.d | `snd_hda_intel power_save=1 power_save_controller=Y` | | Audio codec powers down after 1 s of silence |
| modprobe.d | `nvme_core default_ps_max_latency_us=25000` | | Allows NVMe power states with up to 25 ms exit latency |
| modprobe.d | `applespi fnmode=1` | | Driver default: F-keys need Fn (0 = Fn off, 2 = F-keys first) |
| modprobe.d | `softdep ... pre: idma64` | | Loads the DMA engine first; empirical, see CAVEATS |

These are values we chose, not upstream defaults. Apply sysctl with `sudo sysctl --system`; modprobe changes need a reboot. Undo by deleting the file (the uninstall option of `install-system.sh` does this).

# Install and undo

Two independent parts: (A) the touchpad and battery-saving system files, (B) the root power policy. You can install either without the other. Run commands from the repository root (the folder you cloned, shown here as `{{REPO_DIR}}`).

## Prerequisites

- The 2017 13-inch two-port laptop whose DMI product_name ends in `BookPro14,1`: `cat /sys/class/dmi/id/product_name`.
- Linux with systemd and udev. Only one Red Hat-family distribution (releases 44 and 45, kernel 6.19 to 7.3) was tried.
- Python 3 (only 3.15 was tried, on that distribution; older versions are untested).
- Root (`sudo`) for the installers. `libinput-utils` is optional, for checking quirks with `libinput quirks list`.
- Read the scripts before you run them as root. Nothing here disables thermal protection.

Optional first step, touches no hardware:

```
PYTHONPATH=scripts python3 -m unittest scripts.test_power_policy scripts.test_power_stability
```

## A. Touchpad and battery-saving system files

```
sudo bash install-system.sh --dry-run     # show what would be copied, change nothing
sudo bash install-system.sh               # install everything
```

Options:

| Option | Effect |
|---|---|
| `--dry-run` | Print every action, change nothing. Works without root. |
| `--touchpad-only` | Install only the three touchpad files. |
| `--power-only` | Install only the three battery-saving files. |
| `--uninstall` | Remove exactly the files the script installs (combine with `--touchpad-only` or `--power-only` to limit it). |
| `--force` | Skip the model check. |
| `DESTDIR=/tmp/try` | Environment variable: copy into a scratch folder instead of `/etc`. No root and no model check; nothing is activated. Used by the tests. |

Model check: without `--force`, the script exits with an error if `/sys/class/dmi/id/product_name` does not end in `BookPro14,1`. Using `--force` on another model is your own risk; other models are untested.

If a different file already exists at a destination, it is moved to `<name>.mbp14-backup` first, and `--uninstall` puts it back.

What each file does:

| Installed file | Part | One line |
|---|---|---|
| `/etc/libinput/local-overrides.quirks` | touchpad | Touchpad size and palm thresholds (trial values), and marks the SPI keyboard as internal. |
| `/etc/modprobe.d/mbp14-applespi-dma.conf` | touchpad | Loads the `idma64` DMA engine before the SPI driver; sets `applespi fnmode=1` (the default). |
| `/etc/udev/rules.d/99-mbp14-touchpad-awake.rules` | touchpad | Keeps the four controllers feeding the touchpad and Bluetooth, and the touchpad device, out of runtime suspend. |
| `/etc/udev/rules.d/90-mbp14-runtime-pm.rules` | power | Lets every PCI device auto-suspend; SATA link power policy; audio codec power saving. |
| `/etc/modprobe.d/90-mbp14-power.conf` | power | Audio codec power-down after 1 s; NVMe power-state latency up to 25 ms. |
| `/etc/sysctl.d/90-mbp14-power.conf` | power | `vm.laptop_mode`, `vm.dirty_writeback_centisecs`, `kernel.nmi_watchdog`, `kernel.timer_migration`. |

The script reloads udev, triggers PCI devices, and (for the power files) runs `sysctl --system`. **Reboot afterwards**: module options only take effect at the next boot.

## B. Root power policy

```
sudo bash scripts/install_power_root_unit.sh
```

It is safe to run again after editing `data/power/policy.json`. In six numbered steps it:

1. validates `data/power/policy.json`;
2. installs the applier as `/usr/local/libexec/mbp14/mbp14-power-policy` and the reader as `/usr/local/libexec/mbp14/power_policy.py`;
3. copies the policy to `/etc/mbp14/power-policy.json` (root-owned, so the root unit never reads a user-writable file);
4. installs four units into `/etc/systemd/system/` (`mbp14-power-policy.service`, `mbp14-power-policy-resume.service`, `mbp14-power-policy.timer`, `mbp14-power-policy-hold.service`) and the udev rule `/etc/udev/rules.d/92-mbp14-power-policy.rules`;
5. enables them;
6. applies the policy once and prints the result.

How it is triggered: at boot, after resume from sleep or hibernate, on every plug or unplug of the charger (the udev rule), every 5 minutes (the timer), and continuously by the hold loop, which re-asserts only the two package power limits (PL1 and PL2), every 0.2 s on AC and every 1 s on battery.

Reading the output: the applier writes each value, reads it back, and prints an `ok` line or a `FAIL` line for it. A `FAIL` means the hardware or another service refused that value; it is reported again every 5 minutes rather than hidden. The applier exits with code 1 if anything did not take, otherwise 0. The installer finishes with the enabled state of each unit, the timer state, and the hold loop state. Look at the log again any time:

```
journalctl -u mbp14-power-policy.service -n 14 --no-pager -o cat
```

## C. Exact undo

Part A:

```
sudo bash install-system.sh --uninstall
```

Add `--touchpad-only` or `--power-only` to remove just that group. This removes the installed files, restores any `.mbp14-backup` file, and reloads udev. Reboot to undo the module options.

Part B (from the header of `scripts/install_power_root_unit.sh`):

```
sudo systemctl disable --now mbp14-power-policy.timer mbp14-power-policy-resume.service mbp14-power-policy.service mbp14-power-policy-hold.service
sudo rm -f /etc/systemd/system/mbp14-power-policy* /etc/udev/rules.d/92-mbp14-power-policy.rules /etc/mbp14/power-policy.json /usr/local/libexec/mbp14/mbp14-power-policy /usr/local/libexec/mbp14/power_policy.py
sudo systemctl daemon-reload && sudo udevadm control --reload
sudo rm -rf /etc/mbp14 /usr/local/libexec/mbp14
```

(The last `rm -rf` also removes the folders and any `__pycache__` that root created there.) Values the policy already wrote to the hardware reset on the next reboot.

## D. Did it work, and what if it got worse

Follow [docs/VERIFY.md](docs/VERIFY.md). Quick check of the power policy:

```
python3 scripts/power_stability.py --seconds 30
```

It is read-only and needs no root. It prints a verdict (STABLE, DRIFTING, PARTIAL or UNAVAILABLE) and exits with 0, 1 or 2; a value it cannot read counts as not verified, never as OK.

If anything gets worse (touchpad lag, dropped events, errors in `dmesg | grep -i applespi`), remove the DMA softdep file first and reboot:

```
sudo rm -f /etc/modprobe.d/mbp14-applespi-dma.conf
sudo reboot
```

That setting is the least certain one; we found no upstream source saying it helps. Change one thing at a time, and if still unhappy, use the undo steps above. See [docs/CAVEATS.md](docs/CAVEATS.md) for known limits.

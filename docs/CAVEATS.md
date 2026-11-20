# Caveats: what is known and what is not

Everything here was tried on ONE unit of the 2017 13-inch two-port laptop whose DMI product_name ends in `BookPro14,1`, on a Red Hat-family distribution, releases 44/45, with kernels 6.19 to 7.3. Results are single runs. Other models (other 13-inch, 15-inch, Touch Bar models) are probably similar but untested.

Status words: MEASURED (we measured it, one unit), VERIFIED (we checked it directly), READ (taken from documentation or source), EMPIRICAL (chosen by trial, no upstream backing), UNVERIFIED (we do not know), NOT RECOMMENDED.

## Status ledger

| Item | Status | Evidence or source |
|---|---|---|
| CPU is i5-7360U, 15 W TDP, 9.5 W cTDP-down, 2.30 GHz base, 3.60 GHz turbo | READ | https://www.intel.com/content/www/us/en/products/sku/97535/intel-core-i57360u-processor-4m-cache-up-to-3-60-ghz/specifications.html |
| 25 W burst limit | EMPIRICAL | Our choice; not an Intel-published figure |
| `applespi` parameters (fnmode, fnremap, iso_layout, touchpad_dimensions; no debug) | VERIFIED | `modinfo applespi`, kernel 7.3.4 |
| Keyboard and touchpad sit on PCI 00:1e.3 (SPI) | VERIFIED | lspci and dmesg, one unit |
| 00:15.0 and 00:15.2 (I2C) attach to what | UNVERIFIED | Kept awake as a precaution only |
| Touchpad override numbers (135x83, 50:30, 1000, 800) | EMPIRICAL | Trial on one unit; effect not measured; not upstream |
| Keyboard never matches libinput's "internal" quirk (Vendor 0000 vs 0x05AC) | VERIFIED | One unit |
| That this breaks disable-while-typing and causes random pastes | UNVERIFIED | Our inference only |
| With override, disable-while-typing shows enabled | VERIFIED | `libinput list-devices`, one unit |
| That the random pastes actually stopped | UNVERIFIED | Not confirmed |
| idma64 softdep reduces touchpad lag or dropped events | UNVERIFIED | No upstream source; EMPIRICAL local setting |
| DMA problems exist on a different Apple model (2026 patch series, may add force-PIO) | READ | https://lkml.iu.edu/2607.2/05228.html (we do not know if it merged) |
| Runtime PM exemptions keep touchpad/Bluetooth controllers awake | VERIFIED | udev rules; check with VERIFY.md |
| Bluetooth switch flipping off (`BCM: Reset failed (-110)`) fixed by keeping the UART awake | EMPIRICAL | A kernel update landed in the same window; which change mattered was not isolated |
| Firmware rewrites PL1 (85-100 W seen) | MEASURED | One unit |
| Firmware rewrites PL1 on every unit; this laptop's firmware documented to do so | UNVERIFIED | No upstream source; similar behaviour reported for other laptops by https://github.com/erpalma/throttled |
| 1 s hold: PL1 in policy 78.8%, 38 flips, up to 100 W, package 100 C (AC, load, 40 s) | MEASURED | `power_stability.py`, 2026-11-20 |
| 0.2 s hold: 100%, 0 flips, max 15 W, package 91 C (same load) | MEASURED | Same |
| 0.2 s hold idle on AC: 99.0%, 4 blips of at most 0.2 s | MEASURED | Same |
| Battery: PL1 100% over about 100 s, never above 9 W | MEASURED | Same |
| `fan1_min` writable on every unit | UNVERIFIED | Only our unit, written and read back |
| Energy-preference race on unplug | MEASURED | Seen once; an older second writer existed and is not in this pack |
| Updates do not undo settings (files not owned by any RPM) | VERIFIED | `rpm -qf <file>` on our unit; check yours. Sysfs values reset on reboot and are re-applied within 5 minutes. dnf5-automatic: https://man.archlinux.org/man/dnf5-automatic.8 |
| intel_pstate, powercap semantics | READ | https://docs.kernel.org/admin-guide/pm/intel_pstate.html and https://docs.kernel.org/power/powercap/powercap.html |
| Idle draw 13.0-14.1 W start; 6.56 W after fixes at 23% brightness (about 6.9 h, 45 Wh at 82% health) | MEASURED | Earlier tests, kernel 6.19.10, not re-measured on 7.2.13, about 1 W noise |
| Unbinding thunderbolt: 9.76 W to 8.32 W; Pkg pc7/pc6 0.00/0.00 to 53.42/2.47 | MEASURED | Earlier tests; NOT in this pack |
| Effects of unbinding on Thunderbolt, USB 3, video out | UNVERIFIED | Expected to stop, untested |
| `pcie_aspm=force` | NOT RECOMMENDED | Firmware says no ASPM; L1 appeared but 14.1 vs 13.9 W, no change; stability risk; not used |
| thermald and mbpfan | NOT RECOMMENDED | thermald was active on the original machine; mbpfan could not work there (no manual fan file). Not used here. https://github.com/dgraziotin/mbpfan |
| Software tweaks (indexer off, no background downloads) | MEASURED | -0.24 W, inside the noise |
| Python versions | UNVERIFIED | Only 3.15, on that distribution, tried |
| Distributions other than the one tried | UNVERIFIED | Not tried |

## Known limits

- One unit, single runs, one distribution, one kernel family. Temperatures and battery life will differ with age, paste, battery health and room.
- Statements about "why" the firmware behaves as it does are observations, not documentation.
- Unplug timing, fan behaviour and PL1 rewrites may differ on your unit: run the checker.

## Risks

- Install scripts run as root. Read them first. Everything installed is plain text under `/etc` and `/usr/local` and the uninstall steps remove it.
- Bad power or fan values can make the laptop hotter, louder or slower. Nothing here disables thermal protection, but change one value at a time and watch temperatures.
- The under-load test heats the laptop.
- The fan floor is a minimum only; no manual fan speed is ever written.
- No warranty; the authors are not responsible for hardware damage (GPL-3.0-or-later sections 15-16).

## Not covered

Thunderbolt power-down and Bluetooth tweaks are earlier findings and are not part of this pack. Nothing is claimed for other models.

## How to report results

Open an issue with: the full output of `python3 scripts/power_stability.py --seconds 30` (ideally also the loaded run from VERIFY.md), the output of `cat /sys/class/dmi/id/product_name`, your kernel (`uname -r`) and distribution, and what you changed from the shipped values. Remove anything personal first.
